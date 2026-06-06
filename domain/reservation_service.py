"""Servicio de ciclo de vida de Reserva (capa 1a, §7), sobre el ServicioTransiciones de B2.

Una Reserva aparta una cama para una internación que todavía no llegó. El estado de la
CAMA (DISPONIBLE / RESERVADA / OCUPADA) lo cambia SIEMPRE ``ServicioTransiciones`` —
única fuente de verdad del estado_gestion y de los hitos. Este servicio sólo lleva el
ciclo de vida del registro Reserva (ACTIVA → CUMPLIDA / CANCELADA) y lo coordina con la
transición de cama dentro de la misma transacción (el commit lo hace B2).

VENCIDA existe en el enum pero NO se usa en 1a: vencer una reserva es decisión humana (o
de la capa 2), no hay expiración automática acá.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from database.enums import EstadoReserva, MotivoReserva, TipoCama
from database.models import CamaGestion, InternacionLocal, Reserva
from domain.state_machine import RolOperativo
from domain.transition_service import ServicioTransiciones


class ReservaTipoInvalido(Exception):
    """Se intentó reservar una cama cuyo tipo no coincide con el tipo requerido por la
    reserva (validación cruzada de §7)."""


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class ServicioReservas:
    """Crea y resuelve Reservas, apoyándose en ``ServicioTransiciones`` para mover el
    estado de la cama (nunca toca ``estado_gestion`` directamente)."""

    def __init__(self, transiciones: ServicioTransiciones | None = None) -> None:
        self._transiciones = transiciones or ServicioTransiciones()

    async def crear_reserva(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        internacion: InternacionLocal,
        motivo: MotivoReserva,
        tipo_cama_requerido: TipoCama,
        rol: RolOperativo,
        actor_nombre: str | None = None,
        commit: bool = True,
    ) -> Reserva:
        """Crea una Reserva ACTIVA y deja la cama RESERVADA (DISPONIBLE → RESERVADA vía B2).

        Valida DURO el tipo de cama ANTES de tocar la base: si la cama no es del tipo que
        la reserva requiere, lanza ``ReservaTipoInvalido`` sin efectos.

        ``commit`` (default True) preserva el comportamiento de siempre: transacción
        atómica autónoma (el commit lo hace B2). Con ``commit=False`` propaga el modo a B2
        (que aplica + ``flush`` en vez de commit, dejando la reserva visible y con id) y NO
        hace rollback: deja commit/rollback al orquestador que envuelve esto en su
        transacción (ej. ``PaseServicio.asignar_cama``).
        """
        # 1. Validación dura de tipo (sin tocar la base, independiente de commit).
        if cama.tipo != tipo_cama_requerido:
            raise ReservaTipoInvalido(
                f"No se puede reservar una cama tipo {cama.tipo.value} para una reserva "
                f"que requiere tipo {tipo_cama_requerido.value}."
            )

        # 2. Registro Reserva + transición de cama. Con commit=True B2 commitea; con
        # commit=False B2 sólo flushea (la reserva queda visible, con id, sin commit).
        reserva = Reserva(
            cama_gestion_id=cama.id,
            internacion_id=internacion.id,
            motivo=motivo,
            estado=EstadoReserva.ACTIVA,
            tipo_cama_requerido=tipo_cama_requerido,
        )
        session.add(reserva)
        try:
            await self._transiciones.reservar(
                session, cama, internacion, rol, actor_nombre=actor_nombre, commit=commit
            )
        except Exception:
            if commit:
                await session.rollback()  # descarta también el registro Reserva pendiente
            raise
        return reserva

    async def cumplir_reserva(
        self,
        session: AsyncSession,
        reserva: Reserva,
        rol: RolOperativo,
        actor_nombre: str | None = None,
    ) -> Reserva:
        """Marca la reserva CUMPLIDA y ocupa la cama (RESERVADA → OCUPADA vía B2),
        re-vinculando la internación de la reserva. Un solo commit."""
        cama = await session.get(CamaGestion, reserva.cama_gestion_id)
        if cama is None:
            raise ValueError(
                f"Cama {reserva.cama_gestion_id} no encontrada al cumplir reserva."
            )
        internacion = await session.get(InternacionLocal, reserva.internacion_id)
        if internacion is None:
            raise ValueError(
                f"Internación {reserva.internacion_id} no encontrada al cumplir reserva."
            )
        reserva.estado = EstadoReserva.CUMPLIDA
        reserva.resuelta_at = _ahora()
        try:
            await self._transiciones.ocupar(
                session, cama, internacion, rol, actor_nombre=actor_nombre
            )
        except Exception:
            await session.rollback()
            raise
        return reserva

    async def cancelar_reserva(
        self,
        session: AsyncSession,
        reserva: Reserva,
        motivo_cancelacion: str,
        rol: RolOperativo,
        actor_nombre: str | None = None,
    ) -> Reserva:
        """Marca la reserva CANCELADA (guardando por qué no se ocupó) y libera la cama
        (RESERVADA → DISPONIBLE vía B2). ``motivo_cancelacion`` es OBLIGATORIO: si está
        vacío, lanza ``ValueError`` sin efectos."""
        if not (motivo_cancelacion and motivo_cancelacion.strip()):
            raise ValueError(
                "Cancelar una reserva requiere 'motivo_cancelacion': hay que registrar "
                "por qué la cama no se ocupó."
            )
        cama = await session.get(CamaGestion, reserva.cama_gestion_id)
        if cama is None:
            raise ValueError(
                f"Cama {reserva.cama_gestion_id} no encontrada al cancelar reserva."
            )
        reserva.estado = EstadoReserva.CANCELADA
        reserva.resuelta_at = _ahora()
        reserva.motivo_cancelacion = motivo_cancelacion
        try:
            await self._transiciones.cancelar_reserva(
                session, cama, rol, actor_nombre=actor_nombre
            )
        except Exception:
            await session.rollback()
            raise
        return reserva
