"""Servicio que EJECUTA transiciones de estado de ``CamaGestion`` (capa 1a, paso B2).

Coordina, en UNA transacción async, el flujo completo de una transición:

1. valida la transición con la máquina de estados pura (``domain/state_machine.py``,
   tabla §10 del diseño técnico);
2. autoriza el rol (los roles declarados en la propia transición);
3. actualiza ``internacion_actual_id`` según la semántica de la transición;
4. cambia ``estado_gestion``;
5. escribe el ``HitoAtlas`` de auditoría (catálogo §11);
6. commitea atómicamente (o sólo ``flush`` si un orquestador controla la transacción —
   ver el parámetro ``commit`` de ``ejecutar_transicion``); ante error, rollback total
   cuando él mismo commitea.

La máquina de estados sólo DECIDE (sin tocar la base); este servicio EJECUTA y
persiste. La sincronización con el core es Fase 3: acá es no-op (§12). No modela el
checklist de pre-alta ni los pases (otros pasos de la capa 1a).
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.enums import EstadoCamaGestion, TipoReversion
from database.models import CamaGestion, Egreso, HitoAtlas, InternacionLocal
from domain.state_machine import RolOperativo, TransicionInvalida, validar_transicion

# Estados activos del Egreso (matchea el índice único parcial de DB). La
# constante vive acá por accesibilidad: el hook de reversión la usa para buscar
# el egreso activo a marcar como ``revertido``. ``ServicioEgreso`` expone la
# misma tupla bajo ``ESTADOS_ACTIVOS`` (ambas deben coincidir).
_ESTADOS_EGRESO_ACTIVOS: tuple[str, ...] = ("info", "bloqueado", "egreso_admin")


class RolNoAutorizado(Exception):
    """El rol que intenta disparar la transición no está entre los autorizados (§10).

    Por ahora el rol llega como parámetro; con autenticación real saldrá del usuario
    logueado.
    """


class ReversionSinInternacion(Exception):
    """No se puede revertir un alta tardía: no se pasó una internación ni hay un hito de
    alta física previo para esa cama del cual recuperar al paciente."""


class _EfectoInternacion(enum.Enum):
    """Qué le pasa a ``cama.internacion_actual_id`` en cada transición (§10)."""

    ASIGNA = "ASIGNA"      # set = internacion (la transición requiere una internación)
    LIBERA = "LIBERA"      # set = None
    MANTIENE = "MANTIENE"  # no cambia


# Efecto sobre internacion_actual_id por (origen, destino). Cubre las 12 transiciones.
# Refleja el flujo real validado: el alta física y el bloqueo de una cama ocupada SACAN
# al paciente de la cama (LIBERA); la reversión tardía lo RE-ASIGNA recuperándolo del
# hito de alta (ver ServicioTransiciones.revertir_alta_tardia).
_EFECTO_INTERNACION: dict[
    tuple[EstadoCamaGestion, EstadoCamaGestion], _EfectoInternacion
] = {
    # ASIGNA: la cama toma una internación.
    (EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.RESERVADA): _EfectoInternacion.ASIGNA,
    (EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.OCUPADA): _EfectoInternacion.ASIGNA,
    (EstadoCamaGestion.RESERVADA, EstadoCamaGestion.OCUPADA): _EfectoInternacion.ASIGNA,
    # Reversión tardía: la cama en limpieza ya NO tiene paciente (el alta física lo
    # desvinculó), así que revertir RE-ASIGNA al paciente recuperado del hito de alta.
    (EstadoCamaGestion.LIMPIEZA_TERMINAL, EstadoCamaGestion.OCUPADA): _EfectoInternacion.ASIGNA,
    # LIBERA: la cama suelta su internación.
    (EstadoCamaGestion.RESERVADA, EstadoCamaGestion.DISPONIBLE): _EfectoInternacion.LIBERA,
    (EstadoCamaGestion.LIMPIEZA_TERMINAL, EstadoCamaGestion.DISPONIBLE): _EfectoInternacion.LIBERA,
    (EstadoCamaGestion.BLOQUEADA, EstadoCamaGestion.DISPONIBLE): _EfectoInternacion.LIBERA,
    # Alta física: saca al paciente de la cama, que queda para limpiar SIN paciente.
    (EstadoCamaGestion.PROCESO_DE_ALTA, EstadoCamaGestion.LIMPIEZA_TERMINAL): _EfectoInternacion.LIBERA,
    # Bloqueo de cama ocupada (excepción): no se hace mantenimiento con el paciente en la
    # habitación; al bloquear se lo desvincula (la reubicación es un pase aparte).
    (EstadoCamaGestion.OCUPADA, EstadoCamaGestion.BLOQUEADA): _EfectoInternacion.LIBERA,
    # MANTIENE: el vínculo paciente↔cama no cambia.
    (EstadoCamaGestion.OCUPADA, EstadoCamaGestion.PROCESO_DE_ALTA): _EfectoInternacion.MANTIENE,
    # DISPONIBLE no tiene internación: bloquear una cama libre la deja como está.
    (EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.BLOQUEADA): _EfectoInternacion.MANTIENE,
    # Reversión temprana: todavía no hubo alta física, el paciente sigue ligado.
    (EstadoCamaGestion.PROCESO_DE_ALTA, EstadoCamaGestion.OCUPADA): _EfectoInternacion.MANTIENE,
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
    (EstadoCamaGestion.PROCESO_DE_ALTA, EstadoCamaGestion.OCUPADA): "ATLAS_ALTA_REVERTIDA",  # overrideado por tipo en _revertir_alta (ver _HITO_POR_TIPO_REVERSION)
    (EstadoCamaGestion.LIMPIEZA_TERMINAL, EstadoCamaGestion.OCUPADA): "ATLAS_ALTA_REVERTIDA",  # overrideado por tipo en _revertir_alta (ver _HITO_POR_TIPO_REVERSION)
}

# Código de hito según TipoReversion: el motivo (no el momento) determina el código.
# El reingreso físico es legalmente relevante y debe poder consultarse sin escarbar
# en metadata_evento, así que va a un código de hito propio.
_HITO_POR_TIPO_REVERSION: dict[TipoReversion, str] = {
    TipoReversion.ALTA_INFORMADA_POR_ERROR: "ATLAS_ALTA_REVERTIDA_POR_ERROR",
    TipoReversion.REINGRESO_FISICO: "ATLAS_REINGRESO_FISICO",
}

# Hito que deja el alta física (PROCESO_DE_ALTA → LIMPIEZA_TERMINAL, §11). La reversión
# tardía lo usa para recuperar al paciente cuando no se le pasa la internación.
_HITO_ALTA_FISICA = _HITO_POR_TRANSICION[
    (EstadoCamaGestion.PROCESO_DE_ALTA, EstadoCamaGestion.LIMPIEZA_TERMINAL)
]


class ServicioTransiciones:
    """Ejecuta transiciones de ``CamaGestion`` de forma atómica y auditada.

    El método genérico ``ejecutar_transicion`` es el único que toca la base; los
    métodos semánticos (``reservar``, ``ocupar``, ...) son envoltorios finos que sólo
    fijan el estado destino y los extras propios de cada operación.

    Todos aceptan ``commit`` (default True). Con ``commit=False`` aplican los cambios y
    sólo hacen ``flush`` (sin commit ni rollback), para que un orquestador pueda envolver
    VARIAS transiciones en una única transacción atómica (ej. el futuro PaseServicio).
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
        commit: bool = True,
        hito_codigo_override: str | None = None,
    ) -> HitoAtlas:
        """Ejecuta ``cama`` : ``origen`` → ``estado_destino``.

        Devuelve el ``HitoAtlas`` creado. Lanza ``TransicionInvalida`` (transición no
        contemplada en §10), ``RolNoAutorizado`` (rol sin permiso) o ``ValueError``
        (falta la internación en una transición que asigna) ANTES de tocar la base,
        independientemente de ``commit``.

        ``commit`` (default True) preserva el comportamiento de siempre: una transacción
        atómica autónoma — al final ``session.commit()`` y, ante cualquier error de
        persistencia, ``rollback`` y re-propaga. Con ``commit=False`` aplica los cambios
        y hace sólo ``session.flush()`` (quedan visibles en la sesión sin cerrar la
        transacción) y NO hace commit ni rollback: deja ambas decisiones al orquestador
        que envuelve varias transiciones en una sola transacción.

        ``hito_codigo_override`` (default None) permite que la transición use un código
        de hito distinto al fijo de ``_HITO_POR_TRANSICION[(origen, destino)]``. Sirve
        para casos donde el código depende de un eje extra al par (origen, destino) — hoy
        la reversión de alta, cuyo código depende del ``TipoReversion`` (ver
        ``_HITO_POR_TIPO_REVERSION``). Para el resto de las transiciones queda en None y
        nada cambia.
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
        internacion_a_asignar: InternacionLocal | None = None
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
            internacion_a_asignar = internacion

        # 3-6. Escrituras. Con commit=True es una transacción atómica autónoma; con
        # commit=False el orquestador controla el commit/rollback de la transacción.
        try:
            internacion_previa_id = cama.internacion_actual_id
            internacion_id_hito: uuid.UUID | None = None

            # 3. internacion_actual_id según la transición.
            if efecto is _EfectoInternacion.ASIGNA:
                assert internacion_a_asignar is not None
                cama.internacion_actual_id = internacion_a_asignar.id
                internacion_id_hito = internacion_a_asignar.id
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
                hito_codigo=(
                    hito_codigo_override
                    if hito_codigo_override is not None
                    else _HITO_POR_TRANSICION[(origen, estado_destino)]
                ),
                actor_rol=rol.value,
                actor_nombre=actor_nombre,
                metadata_evento=metadata_evento,
                sincronizado_core=False,  # §12: sync real es Fase 3
            )
            session.add(hito)

            # 6. Finalizar: commit propio (default) o sólo flush si orquesta otro.
            if commit:
                await session.commit()
            else:
                await session.flush()
            return hito
        except Exception:
            if commit:
                await session.rollback()
            raise

    # ------------------------------------------------------------------ #
    # Métodos semánticos. Envoltorios finos sobre ejecutar_transicion: no
    # duplican la lógica transaccional, sólo fijan destino/extras y exigen el
    # origen correcto (un mismo destino puede mapear a hitos distintos).
    # Propagan ``commit`` para que un orquestador pueda encadenarlos.
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
        commit: bool = True,
    ) -> HitoAtlas:
        """DISPONIBLE → RESERVADA. Aparta la cama para una internación que aún no llegó."""
        self._exigir_origen(cama, EstadoCamaGestion.DISPONIBLE)
        return await self.ejecutar_transicion(
            session, cama, EstadoCamaGestion.RESERVADA, rol,
            actor_nombre=actor_nombre, internacion=internacion, metadata=metadata,
            commit=commit,
        )

    async def ocupar(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        internacion: InternacionLocal,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> HitoAtlas:
        """(DISPONIBLE | RESERVADA) → OCUPADA. Ingreso físico del paciente."""
        self._exigir_origen(
            cama, EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.RESERVADA
        )
        return await self.ejecutar_transicion(
            session, cama, EstadoCamaGestion.OCUPADA, rol,
            actor_nombre=actor_nombre, internacion=internacion, metadata=metadata,
            commit=commit,
        )

    async def cancelar_reserva(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> HitoAtlas:
        """RESERVADA → DISPONIBLE. Reserva cancelada o vencida; libera la cama."""
        self._exigir_origen(cama, EstadoCamaGestion.RESERVADA)
        return await self.ejecutar_transicion(
            session, cama, EstadoCamaGestion.DISPONIBLE, rol,
            actor_nombre=actor_nombre, metadata=metadata, commit=commit,
        )

    async def iniciar_alta(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> HitoAtlas:
        """OCUPADA → PROCESO_DE_ALTA. El médico carga el alta médica (arranca la cadena)."""
        self._exigir_origen(cama, EstadoCamaGestion.OCUPADA)
        return await self.ejecutar_transicion(
            session, cama, EstadoCamaGestion.PROCESO_DE_ALTA, rol,
            actor_nombre=actor_nombre, metadata=metadata, commit=commit,
        )

    async def dar_alta_fisica(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> HitoAtlas:
        """PROCESO_DE_ALTA → LIMPIEZA_TERMINAL. Admisión confirma el egreso físico.

        El gating por el checklist de pre-alta (§6) es de un paso posterior de la
        capa 1a; acá sólo se ejecuta la transición.
        """
        self._exigir_origen(cama, EstadoCamaGestion.PROCESO_DE_ALTA)
        return await self.ejecutar_transicion(
            session, cama, EstadoCamaGestion.LIMPIEZA_TERMINAL, rol,
            actor_nombre=actor_nombre, metadata=metadata, commit=commit,
        )

    async def finalizar_limpieza(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> HitoAtlas:
        """LIMPIEZA_TERMINAL → DISPONIBLE. Limpieza aprobada; la cama vuelve al pool."""
        self._exigir_origen(cama, EstadoCamaGestion.LIMPIEZA_TERMINAL)
        return await self.ejecutar_transicion(
            session, cama, EstadoCamaGestion.DISPONIBLE, rol,
            actor_nombre=actor_nombre, metadata=metadata, commit=commit,
        )

    async def bloquear(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        motivo: str,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> HitoAtlas:
        """(DISPONIBLE | OCUPADA) → BLOQUEADA. Mantenimiento; el motivo es obligatorio (§10).

        ``motivo_bloqueo`` se setea en la misma transacción (lo persiste el commit del
        método genérico o del orquestador) y queda registrado en el hito.
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
                commit=commit,
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
        commit: bool = True,
    ) -> HitoAtlas:
        """BLOQUEADA → DISPONIBLE. Mantenimiento finalizado + validación de Operaciones."""
        self._exigir_origen(cama, EstadoCamaGestion.BLOQUEADA)
        motivo_previo = cama.motivo_bloqueo
        cama.motivo_bloqueo = None
        try:
            return await self.ejecutar_transicion(
                session, cama, EstadoCamaGestion.DISPONIBLE, rol,
                actor_nombre=actor_nombre, metadata=metadata, commit=commit,
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
        tipo: TipoReversion,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> HitoAtlas:
        """PROCESO_DE_ALTA → OCUPADA (excepción). El médico deshace su alta médica:
        todavía no hubo alta física y el paciente nunca se desvinculó (MANTIENE), así que
        ``limpieza_ya_ejecutada`` es siempre False y no hay que re-asignar nada."""
        self._exigir_origen(cama, EstadoCamaGestion.PROCESO_DE_ALTA)
        return await self._revertir_alta(
            session, cama, rol, motivo_reversion, tipo,
            limpieza_ya_ejecutada=False, internacion=None,
            actor_nombre=actor_nombre, metadata=metadata, commit=commit,
        )

    async def revertir_alta_tardia(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        motivo_reversion: str,
        tipo: TipoReversion,
        internacion: InternacionLocal | None = None,
        limpieza_ya_ejecutada: bool = False,
        actor_nombre: str | None = None,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> HitoAtlas:
        """LIMPIEZA_TERMINAL → OCUPADA (excepción). Admisión deshace un alta física ya
        dada; el mismo paciente vuelve a la cama.

        Como el alta física ya DESVINCULÓ al paciente (la cama en limpieza no tiene
        internación), esta reversión tiene que RE-ASIGNARLO:

        * si se pasa ``internacion``, se re-vincula esa;
        * si no, se recupera del último hito de alta física de esta cama
          (su ``metadata_evento['internacion_id']``) — ver ``_recuperar_internacion_de_alta``.

        DIFERENCIADOR: esto resuelve, SIN "camas virtuales/fantasma", el problema que
        tiene un HIS cerrado al anular un alta cuando la cama ya pasó a limpieza. Atlas
        conserva la traza cama↔paciente en sus hitos (append-only), así que re-vincula al
        paciente REAL en vez de fabricar un placeholder. El invariante que lo hace seguro:
        la reversión tardía sólo es válida desde LIMPIEZA_TERMINAL, y una cama en limpieza
        no pudo re-ocuparse, por lo que el último hito de alta física es inequívocamente
        el del paciente que se fue.
        """
        self._exigir_origen(cama, EstadoCamaGestion.LIMPIEZA_TERMINAL)
        if internacion is None:
            internacion = await self._recuperar_internacion_de_alta(session, cama)
        return await self._revertir_alta(
            session, cama, rol, motivo_reversion, tipo,
            limpieza_ya_ejecutada=limpieza_ya_ejecutada, internacion=internacion,
            actor_nombre=actor_nombre, metadata=metadata, commit=commit,
        )

    async def _recuperar_internacion_de_alta(
        self, session: AsyncSession, cama: CamaGestion
    ) -> InternacionLocal:
        """Recupera al paciente a re-vincular leyendo el hito de alta física más reciente
        de esta cama (``ATLAS_LIMPIEZA_INICIADA``) y su ``metadata_evento['internacion_id']``.
        Lanza ``ReversionSinInternacion`` si no hay de dónde recuperarlo."""
        stmt = (
            select(HitoAtlas)
            .where(
                HitoAtlas.cama_gestion_id == cama.id,
                HitoAtlas.hito_codigo == _HITO_ALTA_FISICA,
            )
            .order_by(HitoAtlas.registrado_at.desc())
            .limit(1)
        )
        hito = (await session.execute(stmt)).scalars().first()
        internacion_id = (
            hito.metadata_evento.get("internacion_id")
            if hito is not None and hito.metadata_evento
            else None
        )
        if internacion_id is None:
            raise ReversionSinInternacion(
                "No se puede revertir: sin internación provista ni hito de alta previo "
                "para esta cama."
            )
        internacion = await session.get(InternacionLocal, uuid.UUID(str(internacion_id)))
        if internacion is None:
            raise ReversionSinInternacion(
                "No se puede revertir: el hito de alta referencia una internación "
                "inexistente."
            )
        return internacion

    async def _revertir_alta(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        rol: RolOperativo,
        motivo_reversion: str,
        tipo: TipoReversion,
        limpieza_ya_ejecutada: bool,
        internacion: InternacionLocal | None,
        actor_nombre: str | None,
        metadata: dict | None,
        commit: bool = True,
    ) -> HitoAtlas:
        """Lógica común de las dos reversiones (→ OCUPADA). El código de hito sale de
        ``_HITO_POR_TIPO_REVERSION[tipo]`` — el motivo (no el momento) lo determina.
        ``motivo_reversion`` es obligatorio (§11). ``internacion`` se re-asigna sólo en la
        reversión tardía; en la temprana es None y la transición MANTIENE el vínculo.

        Reapertura de internación: ambos tipos reabren la internación si ``finalizada_at``
        está seteado (paciente vuelve a estar internado). En la temprana traemos la
        internación con ``session.get`` desde ``cama.internacion_actual_id`` (que sigue
        ahí porque la transición MANTIENE el vínculo); en la tardía ya viene re-vinculada
        vía ``_recuperar_internacion_de_alta``. Si no hay internación a mano no es error:
        no hay nada que reabrir."""
        if not (motivo_reversion and motivo_reversion.strip()):
            raise ValueError(
                "La reversión de alta requiere 'motivo_reversion' (obligatorio, §11)."
            )
        meta = {
            "motivo_reversion": motivo_reversion,
            "limpieza_ya_ejecutada": bool(limpieza_ya_ejecutada),
            "tipo_reversion": tipo.value,
            **(metadata or {}),
        }
        hito = await self.ejecutar_transicion(
            session, cama, EstadoCamaGestion.OCUPADA, rol,
            actor_nombre=actor_nombre, internacion=internacion, metadata=meta,
            commit=False,
            hito_codigo_override=_HITO_POR_TIPO_REVERSION[tipo],
        )

        # Reapertura de internación, dentro de la misma transacción.
        internacion_a_reabrir = internacion
        if internacion_a_reabrir is None and cama.internacion_actual_id is not None:
            internacion_a_reabrir = await session.get(
                InternacionLocal, cama.internacion_actual_id
            )
        if internacion_a_reabrir is not None and internacion_a_reabrir.finalizada_at is not None:
            internacion_a_reabrir.finalizada_at = None

        # Hook con ``Egreso``: si la cama tenía un Egreso activo (info /
        # bloqueado / egreso_admin), pasa al terminal único ``revertido``. La
        # decisión está cerrada: una reversión cancela el ciclo de egreso en
        # curso (no hay "egreso en pausa"). El id del egreso revertido queda
        # en metadata del hito de reversión para correlación.
        egreso_activo = (await session.execute(
            select(Egreso).where(
                Egreso.cama_gestion_id == cama.id,
                Egreso.estado.in_(_ESTADOS_EGRESO_ACTIVOS),
            )
        )).scalar_one_or_none()
        if egreso_activo is not None:
            egreso_activo.estado = "revertido"
            # Reasignamos el dict (no mutamos in-place): el JSONB no está
            # envuelto en MutableDict, así que SA solo detecta el cambio si la
            # asignación es a un objeto nuevo.
            hito.metadata_evento = {
                **(hito.metadata_evento or {}),
                "egreso_revertido_id": str(egreso_activo.id),
            }

        if commit:
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        else:
            await session.flush()
        return hito
