"""Servicio de PaseServicio (capa 1a, §8): movimientos de un paciente entre camas/niveles.

Es el orquestador del enroque. NO toca ``estado_gestion`` directamente: apoya
``ServicioReservas`` para apartar/ocupar/liberar la cama DESTINO y ``ServicioTransiciones``
(B2) para liberar la cama ORIGEN. Registra el ISBAR como HECHO (que ocurrió), nunca su
contenido clínico.

Punto delicado — ``confirmar_pase`` es ATÓMICO sobre DOS camas: ocupar la destino y
mandar la origen al camino de limpieza tienen que pasar juntas o no pasar. Se logra con
el modo ``commit=False`` de B2 (aplica + flush, sin commit/rollback propios) en ambas
transiciones y UN solo ``session.commit()`` envolvente; ante cualquier error, un único
``rollback`` descarta todo (ni la destino queda ocupada ni la origen liberada).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from database.enums import EstadoPase, EstadoReserva, MotivoReserva, TipoCama
from database.models import CamaGestion, HitoAtlas, InternacionLocal, PaseServicio, Reserva
from domain.reservation_service import ServicioReservas
from domain.state_machine import RolOperativo
from domain.transition_service import ServicioTransiciones


class PaseTipoInvalido(Exception):
    """La cama destino no es del tipo que el pase requiere (validación cruzada §8)."""


class PaseEstadoInvalido(Exception):
    """La operación no aplica al estado actual del pase (ej. confirmar uno que no está
    EN_TRASLADO)."""


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class ServicioPases:
    """Orquesta el ciclo de vida del pase coordinando Reserva (cama destino) y B2 (origen).

    ``confirmar_pase`` usa B2 con ``commit=False`` para sus dos transiciones y un único
    commit envolvente, garantizando atomicidad sobre las dos camas.
    """

    def __init__(
        self,
        reservas: ServicioReservas | None = None,
        transiciones: ServicioTransiciones | None = None,
    ) -> None:
        # Comparten la misma instancia de B2 (es stateless, pero mantiene una sola fuente).
        self._transiciones = transiciones or ServicioTransiciones()
        self._reservas = reservas or ServicioReservas(self._transiciones)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _exigir_estado(pase: PaseServicio, *estados: EstadoPase) -> None:
        if pase.estado not in estados:
            esperados = " o ".join(e.value for e in estados)
            raise PaseEstadoInvalido(
                f"El pase está en {pase.estado.value}; esta operación requiere {esperados}."
            )

    @staticmethod
    def _hito_pase(
        pase: PaseServicio,
        hito_codigo: str,
        rol: RolOperativo,
        actor_nombre: str | None,
        cama_gestion_id,
    ) -> HitoAtlas:
        """Hito propio del pase (no es de transición de cama). Estampa los ids del pase
        en metadata (str, JSONB-serializable), incluidos ambos extremos del movimiento."""
        return HitoAtlas(
            internacion_id=pase.internacion_id,
            cama_gestion_id=cama_gestion_id,
            hito_codigo=hito_codigo,
            actor_rol=rol.value,
            actor_nombre=actor_nombre,
            metadata_evento={
                "pase_id": str(pase.id),
                "internacion_id": str(pase.internacion_id),
                "cama_origen_id": str(pase.cama_origen_id),
                "cama_destino_id": (
                    str(pase.cama_destino_id) if pase.cama_destino_id is not None else None
                ),
                "cama_gestion_id": str(cama_gestion_id) if cama_gestion_id is not None else None,
            },
            sincronizado_core=False,
        )

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #

    async def solicitar_pase(
        self,
        session: AsyncSession,
        internacion: InternacionLocal,
        cama_origen: CamaGestion,
        tipo_cama_destino: TipoCama,
        rol: RolOperativo,
        actor_nombre: str | None = None,
    ) -> PaseServicio:
        """Crea un PaseServicio en SOLICITADO (la cama destino todavía no se eligió)."""
        pase = PaseServicio(
            internacion_id=internacion.id,
            cama_origen_id=cama_origen.id,
            estado=EstadoPase.SOLICITADO,
            tipo_cama_destino=tipo_cama_destino,
        )
        session.add(pase)
        await session.flush()  # asigna pase.id antes de armar el hito
        session.add(
            self._hito_pase(
                pase, "ATLAS_PASE_SOLICITADO", rol, actor_nombre,
                cama_gestion_id=cama_origen.id,
            )
        )
        await session.commit()
        return pase

    async def asignar_cama(
        self,
        session: AsyncSession,
        pase: PaseServicio,
        cama_destino: CamaGestion,
        rol: RolOperativo,
        actor_nombre: str | None = None,
    ) -> PaseServicio:
        """Asigna la cama destino, ATÓMICO en UN solo commit: valida el tipo, RESERVA la
        destino (``ServicioReservas`` con ``commit=False`` → destino RESERVADA, sin
        commitear), enlaza reserva/cama al pase, pasa a CAMA_ASIGNADA y registra el hito
        ISBAR (que el ISBAR ocurrió, antes del traslado; sin contenido clínico).

        Todo va en UNA transacción: ``crear_reserva(commit=False)`` + vínculo + estado +
        hito se cierran con un único ``session.commit()``. Si algo falla, el ``rollback``
        único descarta TODO (ni Reserva, ni cama RESERVADA, ni el pase en CAMA_ASIGNADA)."""
        self._exigir_estado(pase, EstadoPase.SOLICITADO)
        # Validación dura de tipo, ANTES de tocar la base.
        if cama_destino.tipo != pase.tipo_cama_destino:
            raise PaseTipoInvalido(
                f"La cama destino es tipo {cama_destino.tipo.value}, pero el pase "
                f"requiere tipo {pase.tipo_cama_destino.value}."
            )

        internacion = await session.get(InternacionLocal, pase.internacion_id)
        try:
            # Reserva la destino SIN commitear (commit=False): queda flusheada, con id.
            reserva = await self._reservas.crear_reserva(
                session, cama_destino, internacion,
                MotivoReserva.PASE_INTERNO, pase.tipo_cama_destino, rol,
                actor_nombre=actor_nombre, commit=False,
            )
            # Enlaza y avanza el pase + hito ISBAR (todo pendiente).
            pase.reserva_id = reserva.id
            pase.cama_destino_id = cama_destino.id
            pase.estado = EstadoPase.CAMA_ASIGNADA
            session.add(
                self._hito_pase(
                    pase, "ATLAS_PASE_ISBAR_REGISTRADO", rol, actor_nombre,
                    cama_gestion_id=cama_destino.id,
                )
            )
            # UN solo commit envolvente: reserva + cama RESERVADA + vínculo + estado + ISBAR.
            await session.commit()
        except Exception:
            await session.rollback()  # nada persiste: ni reserva, ni cama, ni pase avanzado
            raise
        return pase

    async def iniciar_traslado(
        self,
        session: AsyncSession,
        pase: PaseServicio,
        rol: RolOperativo,
        actor_nombre: str | None = None,
    ) -> PaseServicio:
        """CAMA_ASIGNADA → EN_TRASLADO. El paciente sale de la origen hacia la destino."""
        self._exigir_estado(pase, EstadoPase.CAMA_ASIGNADA)
        pase.estado = EstadoPase.EN_TRASLADO
        await session.commit()
        return pase

    async def confirmar_pase(
        self,
        session: AsyncSession,
        pase: PaseServicio,
        rol: RolOperativo,
        actor_nombre: str | None = None,
    ) -> PaseServicio:
        """EN_TRASLADO → CONFIRMADO. Recepción física confirmada (regla de oro).

        ATÓMICO sobre DOS camas, en UNA transacción (B2 con ``commit=False`` + un solo
        ``session.commit()`` al final):

        a) la cama DESTINO se ocupa (RESERVADA → OCUPADA, re-vincula la internación) y la
           reserva queda CUMPLIDA;
        b) la cama ORIGEN entra al camino de limpieza (OCUPADA → PROCESO_DE_ALTA), NO va a
           DISPONIBLE directo.

        Si CUALQUIERA falla, el ``rollback`` único descarta todo: ni la destino queda
        ocupada ni la origen liberada.

        Roles: las dos transiciones se disparan con sus roles canónicos (ENFERMERIA recibe
        en la destino; el camino de alta de la origen lo dispara MEDICO). El ``rol`` del
        parámetro identifica a quien confirma el pase y queda en el hito del pase.

        Nota (vínculo de la origen): OCUPADA → PROCESO_DE_ALTA MANTIENE
        ``internacion_actual_id`` en la origen (es así en B2 hoy); el vínculo se libera
        cuando la origen completa su alta física → limpieza. Hasta entonces la internación
        figura en la destino (donde está el paciente) y, transitoriamente, también en la
        origen en camino de limpieza.
        """
        self._exigir_estado(pase, EstadoPase.EN_TRASLADO)
        cama_destino = await session.get(CamaGestion, pase.cama_destino_id)
        cama_origen = await session.get(CamaGestion, pase.cama_origen_id)
        internacion = await session.get(InternacionLocal, pase.internacion_id)
        reserva = await session.get(Reserva, pase.reserva_id)

        try:
            # a) DESTINO: RESERVADA → OCUPADA (re-vincula la internación). Sin commit.
            await self._transiciones.ocupar(
                session, cama_destino, internacion, RolOperativo.ENFERMERIA,
                actor_nombre=actor_nombre, commit=False,
            )
            reserva.estado = EstadoReserva.CUMPLIDA
            reserva.resuelta_at = _ahora()

            # b) ORIGEN: OCUPADA → PROCESO_DE_ALTA (camino de limpieza). Sin commit.
            await self._transiciones.iniciar_alta(
                session, cama_origen, RolOperativo.MEDICO,
                actor_nombre=actor_nombre, commit=False,
            )

            # Pase confirmado + hito de recepción física (regla de oro).
            pase.estado = EstadoPase.CONFIRMADO
            pase.confirmado_at = _ahora()
            session.add(
                self._hito_pase(
                    pase, "ATLAS_PASE_CONFIRMADO", rol, actor_nombre,
                    cama_gestion_id=pase.cama_destino_id,
                )
            )

            # UN solo commit envolvente: las dos transiciones + reserva + pase + hito.
            await session.commit()
        except Exception:
            await session.rollback()  # nada persiste: ni destino ocupada ni origen liberada
            raise
        return pase

    async def cancelar_pase(
        self,
        session: AsyncSession,
        pase: PaseServicio,
        motivo_cancelacion: str,
        rol: RolOperativo,
        actor_nombre: str | None = None,
    ) -> PaseServicio:
        """Cancela el pase (motivo OBLIGATORIO). Si ya había reserva sobre la destino, la
        cancela (destino → DISPONIBLE). El paciente se queda en la ORIGEN: no se la toca."""
        if not (motivo_cancelacion and motivo_cancelacion.strip()):
            raise ValueError(
                "Cancelar un pase requiere 'motivo_cancelacion': hay que registrar por qué."
            )
        self._exigir_estado(
            pase,
            EstadoPase.SOLICITADO,
            EstadoPase.CAMA_ASIGNADA,
            EstadoPase.EN_TRASLADO,
        )
        # Si ya se había apartado la destino, liberarla (ServicioReservas → DISPONIBLE).
        if pase.reserva_id is not None:
            reserva = await session.get(Reserva, pase.reserva_id)
            await self._reservas.cancelar_reserva(
                session, reserva, motivo_cancelacion, rol, actor_nombre=actor_nombre
            )
        pase.estado = EstadoPase.CANCELADO
        pase.cancelado_at = _ahora()
        pase.motivo_cancelacion = motivo_cancelacion
        await session.commit()
        return pase
