"""ServicioEgreso — operaciones del proceso de egreso (plano de ESTADO).

Cada operación es atómica: el cambio de estado / timestamp del ``Egreso`` y el
``HitoAtlas`` correspondiente se escriben en la misma transacción. Las
transiciones de la CAMA (``cama_gestion.estado_gestion``) NUNCA se tocan
directo: siempre se delegan a ``ServicioTransiciones``, única fuente de verdad
de la FSM de la cama (§4 del modelo de egreso, Opción A).

Convenciones:

* Actor: ``(rol: RolOperativo, actor_nombre: str | None)`` como params separados,
  consistente con el resto de los servicios de Atlas. El ``autor`` de los items
  / discrepancias / notas se persiste como ``actor_nombre or rol.value`` (las
  filas que requieren ``autor`` NOT NULL caen al rol cuando no hay nombre).
* Guards: cada uno con excepción tipada y mensaje legible para el usuario; el
  índice único parcial de DB es la red de fondo, no el mensaje.
* "No aplica": ``marcar_item`` acepta ``metadata`` que se incluye literal en el
  hito (la convención ``{"no_aplica": True}`` la documenta ``discharge_catalog``).

Reconciliación con la reversión: el hook que marca ``Egreso.estado='revertido''``
cuando una reversión devuelve la cama a OCUPADA vive en
``ServicioTransiciones._revertir_alta`` (no acá). Acá la única consecuencia es
que ``revertido`` es estado terminal: ningún método de este servicio acepta un
egreso revertido.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    CamaGestion,
    Discrepancia,
    Egreso,
    HitoAtlas,
    InternacionLocal,
    ItemChecklistEgreso,
    ItemChecklistLimpieza,
    NotaEgreso,
)
from domain.discharge_catalog import CATALOGO_CHECKLIST_EGRESO, DISCREP_MOTIVOS
from domain.state_machine import RolOperativo
from domain.transition_service import ServicioTransiciones

# Hitos del egreso (string libre, catálogo §11 del diseño técnico).
_HITO_EGRESO_INICIADO = "ATLAS_EGRESO_INICIADO"
_HITO_CHECKLIST_ITEM = "ATLAS_CHECKLIST_ITEM_MARCADO"
_HITO_EGRESO_ADMIN = "ATLAS_EGRESO_ADMIN"
_HITO_SALIDA_FISICA = "ATLAS_SALIDA_FISICA"
_HITO_LIMPIEZA_ITEM = "ATLAS_LIMPIEZA_ITEM_MARCADO"
_HITO_CAMA_LIBERADA = "ATLAS_CAMA_LIBERADA"
_HITO_DISCREPANCIA = "ATLAS_EGRESO_DISCREPANCIA"
_HITO_NOTA_EGRESO = "ATLAS_EGRESO_NOTA"

# Estados activos del egreso. Matchea el índice único parcial de DB
# (postgresql_where = "estado IN ('info','bloqueado','egreso_admin')").
ESTADOS_ACTIVOS: tuple[str, ...] = ("info", "bloqueado", "egreso_admin")

# Catálogo fijo del checklist de limpieza terminal (§2.4 del modelo). Tabla
# análoga al de medio_egreso (constante en código, no DB) y por la misma razón:
# rara vez cambia por institución.
_CATALOGO_LIMPIEZA: tuple[str, ...] = (
    "Cama limpiada según protocolo",
    "Control final — cama OK",
)


# ────────────────────────────────────────────────────────────────────────── #
# Excepciones de dominio
# ────────────────────────────────────────────────────────────────────────── #


class MedioEgresoDesconocido(Exception):
    """``medio_egreso`` no está en ``CATALOGO_CHECKLIST_EGRESO``."""


class EgresoActivoYaExiste(Exception):
    """Ya hay un Egreso en estado activo (info/bloqueado/egreso_admin) para
    esta cama. Mensaje legible (el índice único parcial de DB es la red de
    fondo)."""


class EgresoNoEncontrado(Exception):
    """No existe un Egreso con ese id."""


class EgresoEnEstadoTerminal(Exception):
    """El Egreso está en estado terminal (liberado / revertido) y no admite
    más operaciones."""


class ItemNoEncontrado(Exception):
    pass


class ItemYaMarcado(Exception):
    """Idempotencia explícita: marcar dos veces es un error, no un no-op
    silencioso."""


class ChecklistLegalIncompleto(Exception):
    """OK administrativo intentado con items ``requerido_legal=True`` aún sin
    completar. Lleva los labels pendientes en ``items_pendientes``."""

    def __init__(self, items_pendientes: list[str]) -> None:
        self.items_pendientes = list(items_pendientes)
        faltan = "; ".join(self.items_pendientes) if self.items_pendientes else "(ninguno)"
        super().__init__(
            f"No se puede dar el OK administrativo: faltan items legales "
            f"({faltan})."
        )


class SalidaFisicaSinOkAdmin(Exception):
    """Confirmar salida física sin haber dado el OK administrativo previo
    (validación #3 del modelo de egreso)."""


class MantenimientoPendiente(Exception):
    """Limpieza completa pero ``mantenimiento_requerido=True``: la cama no
    puede pasar a DISPONIBLE hasta resolver el mantenimiento. El guard vive
    en este servicio (no en ``state_machine.py``: Opción A del modelo no agrega
    transiciones nuevas)."""


class MotivoDiscrepanciaInvalido(Exception):
    pass


# ────────────────────────────────────────────────────────────────────────── #
# Utilidades
# ────────────────────────────────────────────────────────────────────────── #


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _autor(rol: RolOperativo, actor_nombre: str | None) -> str:
    """Resuelve el ``autor`` para columnas NOT NULL (Discrepancia / NotaEgreso /
    item.autor cuando se setea). Si no vino nombre, cae al rol."""
    return actor_nombre or rol.value


# ────────────────────────────────────────────────────────────────────────── #
# ServicioEgreso
# ────────────────────────────────────────────────────────────────────────── #


class ServicioEgreso:
    """Orquesta el ciclo de vida del Egreso. Atómico por operación; la FSM de
    la cama se delega siempre a ``ServicioTransiciones``."""

    def __init__(self, transiciones: ServicioTransiciones | None = None) -> None:
        self._transiciones = transiciones or ServicioTransiciones()

    # ------------------------------------------------------------------ #
    # crear_egreso
    # ------------------------------------------------------------------ #

    async def crear_egreso(
        self,
        session: AsyncSession,
        internacion: InternacionLocal,
        cama: CamaGestion,
        medio_egreso: str,
        rol: RolOperativo,
        actor_nombre: str | None = None,
    ) -> Egreso:
        """Abre un proceso de egreso para la cama, materializando el checklist
        del medio. Atómico: Egreso + items + hito en una transacción."""
        if medio_egreso not in CATALOGO_CHECKLIST_EGRESO:
            raise MedioEgresoDesconocido(
                f"medio_egreso '{medio_egreso}' no está en el catálogo. Medios "
                f"válidos: {sorted(CATALOGO_CHECKLIST_EGRESO.keys())}."
            )

        if await self._buscar_egreso_activo(session, cama.id) is not None:
            raise EgresoActivoYaExiste(
                f"La cama {cama.id} ya tiene un egreso activo. Cerralo o "
                f"revertilo antes de abrir uno nuevo."
            )

        egreso = Egreso(
            internacion_local_id=internacion.id,
            cama_gestion_id=cama.id,
            estado="info",
            medio_egreso=medio_egreso,
            mantenimiento_requerido=False,
        )
        session.add(egreso)
        await session.flush()  # necesitamos egreso.id para los items y el hito

        for responsable, label, requerido_legal in CATALOGO_CHECKLIST_EGRESO[medio_egreso]:
            session.add(ItemChecklistEgreso(
                egreso_id=egreso.id,
                responsable=responsable,
                label=label,
                requerido_legal=requerido_legal,
            ))

        session.add(self._hito(
            egreso, cama, internacion, rol, actor_nombre,
            _HITO_EGRESO_INICIADO,
            {"medio_egreso": medio_egreso},
        ))
        await session.commit()
        return egreso

    # ------------------------------------------------------------------ #
    # marcar_item (checklist de egreso)
    # ------------------------------------------------------------------ #

    async def marcar_item(
        self,
        session: AsyncSession,
        egreso_id: uuid.UUID,
        item_id: uuid.UUID,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
    ) -> ItemChecklistEgreso:
        """Marca un item del checklist como done. Guard: no se puede re-marcar
        (idempotencia explícita, no silenciosa)."""
        egreso = await self._cargar_egreso_activo(session, egreso_id)
        item = await session.get(ItemChecklistEgreso, item_id)
        if item is None or item.egreso_id != egreso.id:
            raise ItemNoEncontrado(
                f"Item {item_id} no pertenece al egreso {egreso_id}."
            )
        if item.done:
            raise ItemYaMarcado(
                f"El item '{item.label}' ya estaba marcado como done."
            )

        item.done = True
        item.hora_marcado = _ahora()
        item.autor = _autor(rol, actor_nombre)

        cama = await session.get(CamaGestion, egreso.cama_gestion_id)
        meta = {
            "item_id": str(item.id),
            "label": item.label,
            "responsable": item.responsable,
            "requerido_legal": item.requerido_legal,
        }
        if metadata:
            meta.update(metadata)
        session.add(self._hito(
            egreso, cama, None, rol, actor_nombre,
            _HITO_CHECKLIST_ITEM, meta,
        ))
        await session.commit()
        return item

    # ------------------------------------------------------------------ #
    # ok_administrativo
    # ------------------------------------------------------------------ #

    async def ok_administrativo(
        self,
        session: AsyncSession,
        egreso_id: uuid.UUID,
        rol: RolOperativo,
        actor_nombre: str | None = None,
    ) -> Egreso:
        """Valida que todos los items ``requerido_legal=True`` estén done,
        setea ``egreso_admin_at`` y ``estado='egreso_admin'``. Atómico con
        ``ATLAS_EGRESO_ADMIN``."""
        egreso = await self._cargar_egreso_activo(session, egreso_id)

        items = await self._listar_items(session, egreso.id)
        pendientes = [
            it.label for it in items
            if it.requerido_legal and not it.done
        ]
        if pendientes:
            raise ChecklistLegalIncompleto(pendientes)

        egreso.estado = "egreso_admin"
        egreso.egreso_admin_at = _ahora()
        if egreso.trabado_desde is not None:
            # Si estaba marcado como trabado, el OK admin libera el contador
            # (ya no hay nada bloqueando del lado administrativo).
            egreso.trabado_desde = None

        cama = await session.get(CamaGestion, egreso.cama_gestion_id)
        session.add(self._hito(
            egreso, cama, None, rol, actor_nombre,
            _HITO_EGRESO_ADMIN,
            {"medio_egreso": egreso.medio_egreso},
        ))
        await session.commit()
        return egreso

    # ------------------------------------------------------------------ #
    # confirmar_salida_fisica
    # ------------------------------------------------------------------ #

    async def confirmar_salida_fisica(
        self,
        session: AsyncSession,
        egreso_id: uuid.UUID,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
    ) -> Egreso:
        """Setea ``salida_fisica_at``, instancia el checklist de limpieza, sella
        el hito y dispara la transición ``PROCESO_DE_ALTA → LIMPIEZA_TERMINAL``
        en la misma transacción (Atlas: el estado de la cama lo mueve siempre
        ``ServicioTransiciones``)."""
        egreso = await self._cargar_egreso_activo(session, egreso_id)
        if egreso.egreso_admin_at is None:
            raise SalidaFisicaSinOkAdmin(
                "No se puede confirmar la salida física sin OK administrativo "
                "previo (validación #3 del modelo)."
            )

        egreso.salida_fisica_at = _ahora()
        for label in _CATALOGO_LIMPIEZA:
            session.add(ItemChecklistLimpieza(
                egreso_id=egreso.id,
                label=label,
            ))

        cama = await session.get(CamaGestion, egreso.cama_gestion_id)
        meta = {"medio_egreso": egreso.medio_egreso}
        if metadata:
            meta.update(metadata)
        session.add(self._hito(
            egreso, cama, None, rol, actor_nombre,
            _HITO_SALIDA_FISICA, meta,
        ))

        # Transición de cama dentro de la misma transacción (commit=False). La
        # FSM exige rol ADMISION para PROCESO_DE_ALTA → LIMPIEZA_TERMINAL (LOCKED
        # del STATE_ATLAS: no se toca state_machine.py). El rol real del actor
        # (típicamente ENFERMERIA — la salida física la ejecuta enfermería per
        # commit 1 / cascada del responsable) ya quedó sellado en el hito de
        # servicio (ATLAS_SALIDA_FISICA) líneas arriba.
        await self._transiciones.dar_alta_fisica(
            session, cama, RolOperativo.ADMISION,
            actor_nombre=actor_nombre, commit=False,
        )
        await session.commit()
        return egreso

    # ------------------------------------------------------------------ #
    # marcar_item_limpieza
    # ------------------------------------------------------------------ #

    async def marcar_item_limpieza(
        self,
        session: AsyncSession,
        egreso_id: uuid.UUID,
        item_id: uuid.UUID,
        rol: RolOperativo,
        actor_nombre: str | None = None,
    ) -> ItemChecklistLimpieza:
        """Marca un item de limpieza done; si TODOS quedan done, libera el
        Egreso (``estado='liberado'``) y transiciona ``LIMPIEZA_TERMINAL →
        DISPONIBLE``, todo en una transacción. Guard de mantenimiento aplica
        antes de la liberación (lanza ``MantenimientoPendiente``: la cama queda
        en LIMPIEZA_TERMINAL hasta que se resuelva, sin transiciones nuevas en
        la FSM)."""
        egreso = await self._cargar_egreso_activo(session, egreso_id)
        item = await session.get(ItemChecklistLimpieza, item_id)
        if item is None or item.egreso_id != egreso.id:
            raise ItemNoEncontrado(
                f"Item de limpieza {item_id} no pertenece al egreso {egreso_id}."
            )
        if item.done:
            raise ItemYaMarcado(
                f"El item de limpieza '{item.label}' ya estaba marcado."
            )

        item.done = True
        item.hora_marcado = _ahora()
        item.autor = _autor(rol, actor_nombre)

        cama = await session.get(CamaGestion, egreso.cama_gestion_id)
        session.add(self._hito(
            egreso, cama, None, rol, actor_nombre,
            _HITO_LIMPIEZA_ITEM,
            {"item_id": str(item.id), "label": item.label},
        ))
        await session.flush()

        items_limpieza = await self._listar_items_limpieza(session, egreso.id)
        todos_done = all(it.done for it in items_limpieza)

        if todos_done and egreso.mantenimiento_requerido:
            # Commiteamos el item + su hito (la limpieza SÍ se hizo) y rechazamos
            # la liberación con error explícito. La cama queda en
            # LIMPIEZA_TERMINAL hasta que se resuelva el mantenimiento por el
            # ciclo BLOQUEADA. Guard del modelo en capa de servicio (Opción A:
            # no se agregan transiciones nuevas a la FSM).
            await session.commit()
            raise MantenimientoPendiente(
                "Limpieza completa pero la cama requiere mantenimiento. "
                "Resolverlo vía el ciclo BLOQUEADA antes de liberar."
            )

        if todos_done:
            egreso.estado = "liberado"
            session.add(self._hito(
                egreso, cama, None, rol, actor_nombre,
                _HITO_CAMA_LIBERADA,
                {"medio_egreso": egreso.medio_egreso},
            ))
            await self._transiciones.finalizar_limpieza(
                session, cama, rol, actor_nombre=actor_nombre, commit=False,
            )

        await session.commit()
        return item

    # ------------------------------------------------------------------ #
    # registrar_discrepancia
    # ------------------------------------------------------------------ #

    async def registrar_discrepancia(
        self,
        session: AsyncSession,
        egreso_id: uuid.UUID,
        motivo: str,
        nota: str | None,
        rol: RolOperativo,
        actor_nombre: str | None = None,
    ) -> Discrepancia:
        """Guard: ``motivo`` ∈ ``DISCREP_MOTIVOS``. Insert + hito, sin transición."""
        if motivo not in DISCREP_MOTIVOS:
            raise MotivoDiscrepanciaInvalido(
                f"motivo '{motivo}' inválido. Permitidos: {list(DISCREP_MOTIVOS)}."
            )
        egreso = await self._cargar_egreso_activo(session, egreso_id)

        disc = Discrepancia(
            egreso_id=egreso.id,
            motivo=motivo,
            nota=nota,
            autor=_autor(rol, actor_nombre),
        )
        session.add(disc)
        await session.flush()

        cama = await session.get(CamaGestion, egreso.cama_gestion_id)
        session.add(self._hito(
            egreso, cama, None, rol, actor_nombre,
            _HITO_DISCREPANCIA,
            {
                "discrepancia_id": str(disc.id),
                "motivo": motivo,
            },
        ))
        await session.commit()
        return disc

    # ------------------------------------------------------------------ #
    # agregar_nota
    # ------------------------------------------------------------------ #

    async def agregar_nota(
        self,
        session: AsyncSession,
        egreso_id: uuid.UUID,
        tipo: str,
        texto: str,
        rol: RolOperativo,
        actor_nombre: str | None = None,
    ) -> NotaEgreso:
        """Insert + hito, sin transición. ``tipo`` es 'reclamo' | 'novedad'
        (string libre, validación blanda en capa de presentación)."""
        egreso = await self._cargar_egreso_activo(session, egreso_id)

        nota = NotaEgreso(
            egreso_id=egreso.id,
            tipo=tipo,
            texto=texto,
            autor=_autor(rol, actor_nombre),
        )
        session.add(nota)
        await session.flush()

        cama = await session.get(CamaGestion, egreso.cama_gestion_id)
        session.add(self._hito(
            egreso, cama, None, rol, actor_nombre,
            _HITO_NOTA_EGRESO,
            {
                "nota_id": str(nota.id),
                "tipo": tipo,
            },
        ))
        await session.commit()
        return nota

    # ------------------------------------------------------------------ #
    # Helpers internos
    # ------------------------------------------------------------------ #

    async def _buscar_egreso_activo(
        self, session: AsyncSession, cama_id: uuid.UUID,
    ) -> Egreso | None:
        """El egreso activo de una cama, o None. Matchea el set del índice
        único parcial."""
        stmt = select(Egreso).where(
            Egreso.cama_gestion_id == cama_id,
            Egreso.estado.in_(ESTADOS_ACTIVOS),
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _cargar_egreso_activo(
        self, session: AsyncSession, egreso_id: uuid.UUID,
    ) -> Egreso:
        egreso = await session.get(Egreso, egreso_id)
        if egreso is None:
            raise EgresoNoEncontrado(f"Egreso {egreso_id} no encontrado.")
        if egreso.estado in ("liberado", "revertido"):
            raise EgresoEnEstadoTerminal(
                f"Egreso {egreso_id} está en estado terminal '{egreso.estado}'."
            )
        return egreso

    async def _listar_items(
        self, session: AsyncSession, egreso_id: uuid.UUID,
    ) -> list[ItemChecklistEgreso]:
        stmt = select(ItemChecklistEgreso).where(
            ItemChecklistEgreso.egreso_id == egreso_id
        )
        return list((await session.execute(stmt)).scalars().all())

    async def _listar_items_limpieza(
        self, session: AsyncSession, egreso_id: uuid.UUID,
    ) -> list[ItemChecklistLimpieza]:
        stmt = select(ItemChecklistLimpieza).where(
            ItemChecklistLimpieza.egreso_id == egreso_id
        )
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    def _hito(
        egreso: Egreso,
        cama: CamaGestion | None,
        internacion: InternacionLocal | None,
        rol: RolOperativo,
        actor_nombre: str | None,
        hito_codigo: str,
        metadata_extra: dict | None = None,
    ) -> HitoAtlas:
        """Hito autocontenido: cama_gestion_id + internacion_id duplicados en
        metadata_evento (contrato de HitoAtlas) + egreso_id para correlación."""
        cama_id = cama.id if cama is not None else egreso.cama_gestion_id
        internacion_id = (
            internacion.id if internacion is not None
            else egreso.internacion_local_id
        )
        metadata = {
            "cama_gestion_id": str(cama_id) if cama_id is not None else None,
            "internacion_id": str(internacion_id) if internacion_id is not None else None,
            "egreso_id": str(egreso.id),
        }
        if metadata_extra:
            metadata.update(metadata_extra)
        return HitoAtlas(
            internacion_id=internacion_id,
            cama_gestion_id=cama_id,
            hito_codigo=hito_codigo,
            actor_rol=rol.value,
            actor_nombre=actor_nombre,
            metadata_evento=metadata,
            sincronizado_core=False,
        )
