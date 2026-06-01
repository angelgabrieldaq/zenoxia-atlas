"""Servicio que EJECUTA transiciones de estado de ``CamaGestion`` (capa 1a, paso B2).

Coordina, en UNA transacción async, el flujo completo de una transición:

1. valida la transición con la máquina de estados pura (``domain/state_machine.py``,
   tabla §10 del diseño técnico);
2. autoriza el rol (los roles declarados en la propia transición);
3. actualiza ``internacion_actual_id`` según la semántica de la transición;
4. cambia ``estado_gestion``;
5. escribe el ``HitoAtlas`` de auditoría (catálogo §11);
6. hace commit atómico — ante cualquier error, rollback total.

La máquina de estados sólo DECIDE (sin tocar la base); este servicio EJECUTA y
persiste. La sincronización con el core es Fase 3: acá es no-op (§12). No modela el
checklist de pre-alta ni los pases (otros pasos de la capa 1a).
"""

from __future__ import annotations

import enum

from sqlalchemy.ext.asyncio import AsyncSession

from database.enums import EstadoCamaGestion
from database.models import CamaGestion, HitoAtlas, InternacionLocal
from domain.state_machine import RolOperativo, TransicionInvalida, validar_transicion


class RolNoAutorizado(Exception):
    """El rol que intenta disparar la transición no está entre los autorizados (§10).

    Por ahora el rol llega como parámetro; con autenticación real saldrá del usuario
    logueado.
    """


class _EfectoInternacion(enum.Enum):
    """Qué le pasa a ``cama.internacion_actual_id`` en cada transición (§10)."""

    ASIGNA = "ASIGNA"      # set = internacion (la transición requiere una internación)
    LIBERA = "LIBERA"      # set = None
    MANTIENE = "MANTIENE"  # no cambia


# Efecto sobre internacion_actual_id por (origen, destino). Cubre las 12 transiciones.
_EFECTO_INTERNACION: dict[
    tuple[EstadoCamaGestion, EstadoCamaGestion], _EfectoInternacion
] = {
    # ASIGNA: la cama toma una internación (reserva u ocupación).
    (EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.RESERVADA): _EfectoInternacion.ASIGNA,
    (EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.OCUPADA): _EfectoInternacion.ASIGNA,
    (EstadoCamaGestion.RESERVADA, EstadoCamaGestion.OCUPADA): _EfectoInternacion.ASIGNA,
    # LIBERA: la cama suelta su internación.
    (EstadoCamaGestion.RESERVADA, EstadoCamaGestion.DISPONIBLE): _EfectoInternacion.LIBERA,
    (EstadoCamaGestion.LIMPIEZA_TERMINAL, EstadoCamaGestion.DISPONIBLE): _EfectoInternacion.LIBERA,
    (EstadoCamaGestion.BLOQUEADA, EstadoCamaGestion.DISPONIBLE): _EfectoInternacion.LIBERA,
    # MANTIENE: el vínculo paciente↔cama no cambia.
    (EstadoCamaGestion.OCUPADA, EstadoCamaGestion.PROCESO_DE_ALTA): _EfectoInternacion.MANTIENE,
    # Alta física → limpieza: el paciente sigue vinculado a la cama hasta que ésta se
    # libere de verdad (LIMPIEZA_TERMINAL→DISPONIBLE). Es lo que permite que la
    # reversión tardía (LIMPIEZA_TERMINAL→OCUPADA, "no cambia") devuelva al MISMO
    # paciente. (§10 no lo lista explícitamente; se deduce de esa restricción.)
    (EstadoCamaGestion.PROCESO_DE_ALTA, EstadoCamaGestion.LIMPIEZA_TERMINAL): _EfectoInternacion.MANTIENE,
    (EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.BLOQUEADA): _EfectoInternacion.MANTIENE,
    # Bloqueo de cama ocupada (excepción): mismo criterio que "→BLOQUEADA queda como
    # está"; el traslado del paciente es un pase aparte, fuera de B2.
    (EstadoCamaGestion.OCUPADA, EstadoCamaGestion.BLOQUEADA): _EfectoInternacion.MANTIENE,
    # Reversiones de alta: vuelve el mismo paciente, no se toca el vínculo.
    (EstadoCamaGestion.PROCESO_DE_ALTA, EstadoCamaGestion.OCUPADA): _EfectoInternacion.MANTIENE,
    (EstadoCamaGestion.LIMPIEZA_TERMINAL, EstadoCamaGestion.OCUPADA): _EfectoInternacion.MANTIENE,
}


# Catálogo §11: código de hito por (origen, destino). Mismo destino puede dar hitos
# distintos según el origen (ej. → DISPONIBLE), por eso la clave es el par completo.
_HITO_POR_TRANSICION: dict[tuple[EstadoCamaGestion, EstadoCamaGestion], str] = {
    (EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.RESERVADA): "ATLAS_CAMA_RESERVADA",
    (EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.OCUPADA): "ATLAS_CAMA_OCUPADA",
    (EstadoCamaGestion.RESERVADA, EstadoCamaGestion.OCUPADA): "ATLAS_CAMA_OCUPADA",
    (EstadoCamaGestion.RESERVADA, EstadoCamaGestion.DISPONIBLE): "ATLAS_RESERVA_LIBERADA",
    (EstadoCamaGestion.OCUPADA, EstadoCamaGestion.PROCESO_DE_ALTA): "ATLAS_PROCESO_ALTA_INICIADO",
    (EstadoCamaGestion.PROCESO_DE_ALTA, EstadoCamaGestion.LIMPIEZA_TERMINAL): "ATLAS_LIMPIEZA_INICIADA",
    (EstadoCamaGestion.LIMPIEZA_TERMINAL, EstadoCamaGestion.DISPONIBLE): "ATLAS_CAMA_DISPONIBLE",
    (EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.BLOQUEADA): "ATLAS_CAMA_BLOQUEADA",
    (EstadoCamaGestion.BLOQUEADA, EstadoCamaGestion.DISPONIBLE): "ATLAS_CAMA_DESBLOQUEADA",
    (EstadoCamaGestion.OCUPADA, EstadoCamaGestion.BLOQUEADA): "ATLAS_CAMA_BLOQUEADA",
    (EstadoCamaGestion.PROCESO_DE_ALTA, EstadoCamaGestion.OCUPADA): "ATLAS_ALTA_REVERTIDA",
    (EstadoCamaGestion.LIMPIEZA_TERMINAL, EstadoCamaGestion.OCUPADA): "ATLAS_ALTA_REVERTIDA",
}


class ServicioTransiciones:
    """Ejecuta transiciones de ``CamaGestion`` de forma atómica y auditada.

    El método genérico ``ejecutar_transicion`` es el único que toca la base; los
    métodos semánticos (``reservar``, ``ocupar``, ...) son envoltorios finos que sólo
    fijan el estado destino y los extras propios de cada operación.
    """

    async def ejecutar_transicion(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        estado_destino: EstadoCamaGestion,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        internacion: InternacionLocal | None = None,
        metadata: dict | None = None,
    ) -> HitoAtlas:
        """Ejecuta ``cama`` : ``origen`` → ``estado_destino`` en una sola transacción.

        Devuelve el ``HitoAtlas`` creado. Lanza ``TransicionInvalida`` (transición no
        contemplada en §10), ``RolNoAutorizado`` (rol sin permiso) o ``ValueError``
        (falta la internación en una transición que asigna) ANTES de tocar la base.
        Si algo falla durante la persistencia, hace rollback completo y re-propaga.
        """
        origen = cama.estado_gestion

        # 1. Validar transición (puro; lanza TransicionInvalida sin tocar la base).
        transicion = validar_transicion(origen, estado_destino)

        # 2. Validar rol (sin tocar la base).
        if rol not in transicion.roles:
            permitidos = ", ".join(sorted(r.value for r in transicion.roles))
            raise RolNoAutorizado(
                f"El rol {rol.value} no puede disparar "
                f"{origen.value} → {estado_destino.value}. Autorizados: {permitidos}."
            )

        # Precondición de asignación (sin tocar la base): si la transición ASIGNA,
        # necesitamos una internación persistida.
        efecto = _EFECTO_INTERNACION[(origen, estado_destino)]
        if efecto is _EfectoInternacion.ASIGNA:
            if internacion is None:
                raise ValueError(
                    f"La transición {origen.value} → {estado_destino.value} asigna una "
                    f"internación a la cama; el parámetro 'internacion' es obligatorio."
                )
            if internacion.id is None:
                raise ValueError(
                    "La internación a asignar debe estar persistida (tener id) "
                    "antes de vincularla a la cama."
                )

        # 3-6. A partir de acá, todo en una sola transacción atómica.
        try:
            internacion_previa_id = cama.internacion_actual_id

            # 3. internacion_actual_id según la transición.
            if efecto is _EfectoInternacion.ASIGNA:
                cama.internacion_actual_id = internacion.id
                internacion_id_hito = internacion.id
            elif efecto is _EfectoInternacion.LIBERA:
                cama.internacion_actual_id = None
                internacion_id_hito = internacion_previa_id  # quién se liberó (auditoría)
            else:  # MANTIENE
                internacion_id_hito = internacion_previa_id

            # 4. Cambio de estado.
            cama.estado_gestion = estado_destino

            # 5. Hito de auditoría (append-only, §11). Contrato del modelo HitoAtlas:
            # estampar internacion_id y cama_gestion_id TAMBIÉN dentro de
            # metadata_evento (id redundante) para que el hito viaje autocontenido al
            # core (Fase 3). Los UUID van como str (JSONB serializable). Los ids
            # canónicos se escriben al final para que no los pise la metadata extra.
            metadata_evento = {
                **(metadata or {}),
                "cama_gestion_id": str(cama.id),
                "internacion_id": (
                    str(internacion_id_hito) if internacion_id_hito is not None else None
                ),
            }
            hito = HitoAtlas(
                internacion_id=internacion_id_hito,
                cama_gestion_id=cama.id,
                hito_codigo=_HITO_POR_TRANSICION[(origen, estado_destino)],
                actor_rol=rol.value,
                actor_nombre=actor_nombre,
                metadata_evento=metadata_evento,
                sincronizado_core=False,  # §12: sync real es Fase 3
            )
            session.add(hito)

            # 6. Commit atómico.
            await session.commit()
            return hito
        except Exception:
            await session.rollback()
            raise

    # ------------------------------------------------------------------ #
    # Métodos semánticos. Envoltorios finos sobre ejecutar_transicion: no
    # duplican la lógica transaccional, sólo fijan destino/extras y exigen el
    # origen correcto (un mismo destino puede mapear a hitos distintos).
    # ------------------------------------------------------------------ #

    @staticmethod
    def _exigir_origen(cama: CamaGestion, *origenes: EstadoCamaGestion) -> None:
        if cama.estado_gestion not in origenes:
            esperados = " o ".join(o.value for o in origenes)
            raise TransicionInvalida(
                f"Operación no aplicable desde {cama.estado_gestion.value}; "
                f"requiere origen {esperados}."
            )

    async def reservar(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        internacion: InternacionLocal,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
    ) -> HitoAtlas:
        """DISPONIBLE → RESERVADA. Aparta la cama para una internación que aún no llegó."""
        self._exigir_origen(cama, EstadoCamaGestion.DISPONIBLE)
        return await self.ejecutar_transicion(
            session, cama, EstadoCamaGestion.RESERVADA, rol,
            actor_nombre=actor_nombre, internacion=internacion, metadata=metadata,
        )

    async def ocupar(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        internacion: InternacionLocal,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
    ) -> HitoAtlas:
        """(DISPONIBLE | RESERVADA) → OCUPADA. Ingreso físico del paciente."""
        self._exigir_origen(
            cama, EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.RESERVADA
        )
        return await self.ejecutar_transicion(
            session, cama, EstadoCamaGestion.OCUPADA, rol,
            actor_nombre=actor_nombre, internacion=internacion, metadata=metadata,
        )

    async def cancelar_reserva(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
    ) -> HitoAtlas:
        """RESERVADA → DISPONIBLE. Reserva cancelada o vencida; libera la cama."""
        self._exigir_origen(cama, EstadoCamaGestion.RESERVADA)
        return await self.ejecutar_transicion(
            session, cama, EstadoCamaGestion.DISPONIBLE, rol,
            actor_nombre=actor_nombre, metadata=metadata,
        )

    async def iniciar_alta(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
    ) -> HitoAtlas:
        """OCUPADA → PROCESO_DE_ALTA. El médico carga el alta médica (arranca la cadena)."""
        self._exigir_origen(cama, EstadoCamaGestion.OCUPADA)
        return await self.ejecutar_transicion(
            session, cama, EstadoCamaGestion.PROCESO_DE_ALTA, rol,
            actor_nombre=actor_nombre, metadata=metadata,
        )

    async def dar_alta_fisica(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
    ) -> HitoAtlas:
        """PROCESO_DE_ALTA → LIMPIEZA_TERMINAL. Admisión confirma el egreso físico.

        El gating por el checklist de pre-alta (§6) es de un paso posterior de la
        capa 1a; acá sólo se ejecuta la transición.
        """
        self._exigir_origen(cama, EstadoCamaGestion.PROCESO_DE_ALTA)
        return await self.ejecutar_transicion(
            session, cama, EstadoCamaGestion.LIMPIEZA_TERMINAL, rol,
            actor_nombre=actor_nombre, metadata=metadata,
        )

    async def finalizar_limpieza(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
    ) -> HitoAtlas:
        """LIMPIEZA_TERMINAL → DISPONIBLE. Limpieza aprobada; la cama vuelve al pool."""
        self._exigir_origen(cama, EstadoCamaGestion.LIMPIEZA_TERMINAL)
        return await self.ejecutar_transicion(
            session, cama, EstadoCamaGestion.DISPONIBLE, rol,
            actor_nombre=actor_nombre, metadata=metadata,
        )

    async def bloquear(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        motivo: str,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
    ) -> HitoAtlas:
        """(DISPONIBLE | OCUPADA) → BLOQUEADA. Mantenimiento; el motivo es obligatorio (§10).

        ``motivo_bloqueo`` se setea en la misma transacción (lo persiste el commit del
        método genérico) y queda registrado en el hito.
        """
        if not (motivo and motivo.strip()):
            raise ValueError("El bloqueo de una cama requiere un motivo obligatorio (§10).")
        self._exigir_origen(
            cama, EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.OCUPADA
        )
        motivo_previo = cama.motivo_bloqueo
        cama.motivo_bloqueo = motivo
        try:
            return await self.ejecutar_transicion(
                session, cama, EstadoCamaGestion.BLOQUEADA, rol,
                actor_nombre=actor_nombre,
                metadata={"motivo_bloqueo": motivo, **(metadata or {})},
            )
        except Exception:
            cama.motivo_bloqueo = motivo_previo  # revertir la mutación en memoria
            raise

    async def desbloquear(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
    ) -> HitoAtlas:
        """BLOQUEADA → DISPONIBLE. Mantenimiento finalizado + validación de Operaciones."""
        self._exigir_origen(cama, EstadoCamaGestion.BLOQUEADA)
        motivo_previo = cama.motivo_bloqueo
        cama.motivo_bloqueo = None
        try:
            return await self.ejecutar_transicion(
                session, cama, EstadoCamaGestion.DISPONIBLE, rol,
                actor_nombre=actor_nombre, metadata=metadata,
            )
        except Exception:
            cama.motivo_bloqueo = motivo_previo
            raise

    async def revertir_alta_temprana(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        motivo_reversion: str,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
    ) -> HitoAtlas:
        """PROCESO_DE_ALTA → OCUPADA (excepción). El médico deshace su alta médica;
        todavía no hubo alta física, así que ``limpieza_ya_ejecutada`` es siempre False."""
        self._exigir_origen(cama, EstadoCamaGestion.PROCESO_DE_ALTA)
        return await self._revertir_alta(
            session, cama, rol, motivo_reversion,
            limpieza_ya_ejecutada=False, actor_nombre=actor_nombre, metadata=metadata,
        )

    async def revertir_alta_tardia(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        motivo_reversion: str,
        limpieza_ya_ejecutada: bool = False,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
    ) -> HitoAtlas:
        """LIMPIEZA_TERMINAL → OCUPADA (excepción). Admisión deshace el alta física ya
        dada; el mismo paciente vuelve. ``limpieza_ya_ejecutada`` marca si la limpieza
        efectiva ya se había hecho (trabajo desperdiciado)."""
        self._exigir_origen(cama, EstadoCamaGestion.LIMPIEZA_TERMINAL)
        return await self._revertir_alta(
            session, cama, rol, motivo_reversion,
            limpieza_ya_ejecutada=limpieza_ya_ejecutada,
            actor_nombre=actor_nombre, metadata=metadata,
        )

    async def _revertir_alta(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        motivo_reversion: str,
        limpieza_ya_ejecutada: bool,
        actor_nombre: str | None,
        metadata: dict | None,
    ) -> HitoAtlas:
        """Lógica común de las dos reversiones (→ OCUPADA, hito ATLAS_ALTA_REVERTIDA).
        ``motivo_reversion`` es obligatorio (§11)."""
        if not (motivo_reversion and motivo_reversion.strip()):
            raise ValueError(
                "La reversión de alta requiere 'motivo_reversion' (obligatorio, §11)."
            )
        meta = {
            "motivo_reversion": motivo_reversion,
            "limpieza_ya_ejecutada": bool(limpieza_ya_ejecutada),
            **(metadata or {}),
        }
        return await self.ejecutar_transicion(
            session, cama, EstadoCamaGestion.OCUPADA, rol,
            actor_nombre=actor_nombre, metadata=meta,
        )
