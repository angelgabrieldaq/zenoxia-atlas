"""Servicio del checklist de alta (capa 1a, §6): pasos de pre-alta y override del alta física.

Sub-paso 1 — pasos y su estado: cuando una internación entra en proceso de alta, se
instancian los pasos del catálogo que apliquen (universales + los de su categoría). Cada
paso se completa de forma auditable (quién, cuándo, + HitoAtlas).

Sub-paso 2 — override del alta física: ``dar_alta_fisica_validada`` cruza el checklist con
B2. La transición PROCESO_DE_ALTA → LIMPIEZA_TERMINAL NO se bloquea en la máquina de estados
(sigue siendo válida); es ESTE servicio (que conoce el checklist) el que chequea los pasos
bloqueantes pendientes ANTES y DELEGA el cambio de estado a ``B2.dar_alta_fisica``. B2 se
mantiene genérico e intacto. Si faltan bloqueantes y no se fuerza, rechaza
(``AltaConPasosPendientes``); si se fuerza (``forzar=True`` + ``motivo_override``), procede y
deja un hito de la excepción. No bloquear duro evita los workarounds tipo "cama fantasma".
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    CamaGestion,
    HitoAtlas,
    InternacionLocal,
    PasoAltaCatalogo,
    PasoAltaInternacion,
)
from domain.state_machine import RolOperativo
from domain.transition_service import ServicioTransiciones

# Hito de auditoría al completar un paso del checklist (catálogo §11, string libre).
_HITO_PASO_COMPLETADO = "ATLAS_PASO_ALTA_COMPLETADO"
# Hito de la excepción: alta física forzada con pasos bloqueantes aún pendientes (§6 override).
_HITO_ALTA_FORZADA = "ATLAS_ALTA_FORZADA_PASOS_PENDIENTES"


class AltaConPasosPendientes(Exception):
    """El alta física se intentó con pasos bloqueantes del checklist pendientes y SIN
    override (``forzar=False``). Lleva la lista de códigos de pasos pendientes (atributo
    ``pasos_pendientes`` y en el mensaje) para que el llamador sepa qué falta. NO se tocó
    la base: es un rechazo previo a cualquier escritura."""

    def __init__(self, pasos_pendientes: list[str]) -> None:
        self.pasos_pendientes = list(pasos_pendientes)
        faltan = ", ".join(self.pasos_pendientes) if self.pasos_pendientes else "(ninguno)"
        super().__init__(
            f"No se puede dar el alta física: hay pasos bloqueantes pendientes en el "
            f"checklist ({faltan}). Para forzar el alta, pasar forzar=True con "
            f"motivo_override."
        )


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class ServicioChecklistAlta:
    """Instancia y opera los pasos de alta de una internación (con trazabilidad por hito) y
    valida el alta física contra los pasos bloqueantes, delegando el cambio de estado a B2."""

    def __init__(self, transiciones: ServicioTransiciones | None = None) -> None:
        # B2 es stateless; se inyecta para compartir instancia / testear. El cambio de
        # estado de la cama lo hace SIEMPRE B2 (este servicio NO toca ``estado_gestion``).
        self._transiciones = transiciones or ServicioTransiciones()

    async def instanciar_pasos(
        self,
        session: AsyncSession,
        internacion: InternacionLocal,
    ) -> list[PasoAltaInternacion]:
        """Instancia, para la internación, los pasos del catálogo ACTIVOS que apliquen:
        universales (``categoria_aplica IS NULL``) + los que matcheen ``internacion.categoria``.

        Copia ``era_bloqueante`` desde ``bloqueante`` del catálogo (snapshot). Idempotente:
        NO re-crea pasos ya instanciados para esta internación (matchea por catálogo).
        Devuelve sólo los pasos NUEVOS creados. Un único commit."""
        # Catálogo aplicable: activo + (universal o de la categoría de la internación).
        aplicables = (
            await session.execute(
                select(PasoAltaCatalogo).where(
                    PasoAltaCatalogo.activo.is_(True),
                    (
                        PasoAltaCatalogo.categoria_aplica.is_(None)
                        | (PasoAltaCatalogo.categoria_aplica == internacion.categoria)
                    ),
                )
            )
        ).scalars().all()

        # Ya instanciados para esta internación (idempotencia por catálogo).
        ya_instanciados = set(
            (
                await session.execute(
                    select(PasoAltaInternacion.paso_catalogo_id).where(
                        PasoAltaInternacion.internacion_id == internacion.id
                    )
                )
            ).scalars().all()
        )

        creados: list[PasoAltaInternacion] = []
        for paso_cat in aplicables:
            if paso_cat.id in ya_instanciados:
                continue
            paso = PasoAltaInternacion(
                internacion_id=internacion.id,
                paso_catalogo_id=paso_cat.id,
                era_bloqueante=paso_cat.bloqueante,
                completado=False,
            )
            session.add(paso)
            creados.append(paso)
        if creados:
            await session.commit()
        return creados

    async def completar_paso(
        self,
        session: AsyncSession,
        paso_internacion: PasoAltaInternacion,
        rol: RolOperativo,
        actor_nombre: str | None = None,
    ) -> PasoAltaInternacion:
        """Marca el paso completado (quién + cuándo) y escribe un HitoAtlas de auditoría
        (``ATLAS_PASO_ALTA_COMPLETADO``, con metadata de qué paso). Un único commit."""
        paso_internacion.completado = True
        paso_internacion.completado_por_rol = rol.value
        paso_internacion.completado_por_nombre = actor_nombre
        paso_internacion.completado_at = _ahora()

        # Para la trazabilidad del hito: el código legible del paso (del catálogo).
        paso_cat = await session.get(PasoAltaCatalogo, paso_internacion.paso_catalogo_id)
        # Hito autocontenido: internacion_id redundante en metadata (str, JSONB), + qué paso.
        hito = HitoAtlas(
            internacion_id=paso_internacion.internacion_id,
            cama_gestion_id=None,  # el paso de checklist no es sobre una cama puntual
            hito_codigo=_HITO_PASO_COMPLETADO,
            actor_rol=rol.value,
            actor_nombre=actor_nombre,
            metadata_evento={
                "internacion_id": str(paso_internacion.internacion_id),
                "cama_gestion_id": None,
                "paso_internacion_id": str(paso_internacion.id),
                "paso_catalogo_id": str(paso_internacion.paso_catalogo_id),
                "paso_codigo": paso_cat.codigo if paso_cat is not None else None,
                "era_bloqueante": paso_internacion.era_bloqueante,
            },
            sincronizado_core=False,  # §12: sync real es Fase 3
        )
        session.add(hito)
        await session.commit()
        return paso_internacion

    async def listar_pasos(
        self,
        session: AsyncSession,
        internacion: InternacionLocal,
    ) -> list[PasoAltaInternacion]:
        """Devuelve los pasos de la internación con su estado, ordenados por creación."""
        resultado = await session.execute(
            select(PasoAltaInternacion)
            .where(PasoAltaInternacion.internacion_id == internacion.id)
            .order_by(PasoAltaInternacion.creada_at)
        )
        return list(resultado.scalars().all())

    async def pasos_bloqueantes_pendientes(
        self,
        session: AsyncSession,
        internacion: InternacionLocal,
    ) -> list[PasoAltaInternacion]:
        """Pasos ``era_bloqueante=True`` aún NO completados de la internación.

        Consulta lista para el sub-paso 2 (override del alta física); acá NO se conecta
        todavía a la transición de alta."""
        resultado = await session.execute(
            select(PasoAltaInternacion)
            .where(
                PasoAltaInternacion.internacion_id == internacion.id,
                PasoAltaInternacion.era_bloqueante.is_(True),
                PasoAltaInternacion.completado.is_(False),
            )
            .order_by(PasoAltaInternacion.creada_at)
        )
        return list(resultado.scalars().all())

    async def dar_alta_fisica_validada(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        internacion: InternacionLocal,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        forzar: bool = False,
        motivo_override: str | None = None,
    ) -> HitoAtlas:
        """Da el alta física (PROCESO_DE_ALTA → LIMPIEZA_TERMINAL) cruzando el checklist.

        El cambio de estado lo hace SIEMPRE B2 (``dar_alta_fisica``); este servicio sólo
        decide si procede según los pasos bloqueantes pendientes de la internación:

        * sin bloqueantes pendientes → procede normal (B2 commitea solo); sin hito extra;
        * con bloqueantes pendientes y ``forzar=False`` → ``AltaConPasosPendientes`` (lleva
          los códigos pendientes); NO toca la base;
        * con bloqueantes pendientes y ``forzar=True`` → ``motivo_override`` es OBLIGATORIO;
          procede Y escribe un ``HitoAtlas`` de alta forzada
          (``ATLAS_ALTA_FORZADA_PASOS_PENDIENTES``, con el motivo + los códigos que faltaban).
          La transición de B2 (``commit=False``) y ese hito van en UN solo commit envolvente:
          ante cualquier error, rollback total.

        Devuelve el ``HitoAtlas`` de la transición de alta física (el canónico de §11)."""
        pendientes = await self.pasos_bloqueantes_pendientes(session, internacion)

        # Camino feliz: nada bloqueante pendiente. B2 ejecuta y commitea por su cuenta.
        if not pendientes:
            return await self._transiciones.dar_alta_fisica(
                session, cama, rol, actor_nombre=actor_nombre,
            )

        # Hay bloqueantes pendientes: sus códigos sirven para el rechazo o para el hito.
        codigos = await self._codigos_de_pasos(session, pendientes)

        # Sin override: rechazo de dominio, sin tocar la base.
        if not forzar:
            raise AltaConPasosPendientes(codigos)

        # Override: el motivo es obligatorio (deja constancia de la decisión humana).
        if not (motivo_override and motivo_override.strip()):
            raise ValueError(
                "El override del alta física requiere 'motivo_override' (obligatorio): hay "
                "que registrar por qué se fuerza el alta con pasos bloqueantes pendientes."
            )

        # Alta forzada ATÓMICA: transición B2 (commit=False) + hito de la excepción, en UN
        # solo commit envolvente (mismo patrón que PaseServicio). Si algo falla → rollback total.
        try:
            hito_transicion = await self._transiciones.dar_alta_fisica(
                session, cama, rol, actor_nombre=actor_nombre, commit=False,
            )
            session.add(
                self._hito_alta_forzada(
                    cama, internacion, rol, actor_nombre, motivo_override, codigos
                )
            )
            await session.commit()
        except Exception:
            await session.rollback()  # ni el cambio de estado ni el hito quedan
            raise
        return hito_transicion

    async def _codigos_de_pasos(
        self,
        session: AsyncSession,
        pasos: list[PasoAltaInternacion],
    ) -> list[str]:
        """Códigos legibles (del catálogo) de una lista de pasos, conservando su orden."""
        if not pasos:
            return []
        catalogo_ids = {p.paso_catalogo_id for p in pasos}
        filas = await session.execute(
            select(PasoAltaCatalogo.id, PasoAltaCatalogo.codigo).where(
                PasoAltaCatalogo.id.in_(catalogo_ids)
            )
        )
        codigo_por_id = {cid: codigo for cid, codigo in filas.all()}
        return [codigo_por_id[p.paso_catalogo_id] for p in pasos]

    @staticmethod
    def _hito_alta_forzada(
        cama: CamaGestion,
        internacion: InternacionLocal,
        rol: RolOperativo,
        actor_nombre: str | None,
        motivo_override: str,
        codigos_pendientes: list[str],
    ) -> HitoAtlas:
        """Hito append-only de la excepción: alta física forzada con bloqueantes pendientes.
        Estampa internacion_id/cama_gestion_id redundantes en metadata (str, JSONB) + el
        motivo y los códigos que faltaban (trazabilidad de la decisión humana)."""
        return HitoAtlas(
            internacion_id=internacion.id,
            cama_gestion_id=cama.id,
            hito_codigo=_HITO_ALTA_FORZADA,
            actor_rol=rol.value,
            actor_nombre=actor_nombre,
            metadata_evento={
                "internacion_id": str(internacion.id),
                "cama_gestion_id": str(cama.id),
                "motivo_override": motivo_override,
                "pasos_pendientes": list(codigos_pendientes),
            },
            sincronizado_core=False,  # §12: sync real es Fase 3
        )
