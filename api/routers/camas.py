"""Router de camas: tablero de estado + transiciones semánticas."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_notas, get_reservas, get_session, get_transiciones
from api.schemas import (
    AltaFisicaBody,
    BloquearBody,
    CamaDetalleOut,
    CamaOut,
    DesbloquearBody,
    FinalizarLimpiezaBody,
    HitoOut,
    IniciarAltaBody,
    NotaCamaOut,
    OcuparBody,
    ReservarBody,
)
from database.enums import MotivoReserva
from database.models import CamaGestion, HitoAtlas, InternacionLocal, NotaCama
from domain.note_service import ServicioNotas
from domain.reservation_service import ReservaTipoInvalido, ServicioReservas
from domain.state_machine import TransicionInvalida
from domain.transition_service import RolNoAutorizado, ServicioTransiciones

router = APIRouter(prefix="/camas", tags=["camas"])


async def _get_cama_or_404(cama_id: uuid.UUID, session: AsyncSession) -> CamaGestion:
    cama = await session.get(CamaGestion, cama_id)
    if cama is None:
        raise HTTPException(status_code=404, detail=f"Cama {cama_id} no encontrada.")
    return cama


async def _get_internacion_or_404(
    internacion_id: uuid.UUID, session: AsyncSession
) -> InternacionLocal:
    internacion = await session.get(InternacionLocal, internacion_id)
    if internacion is None:
        raise HTTPException(
            status_code=404, detail=f"Internación {internacion_id} no encontrada."
        )
    return internacion


@router.get("", response_model=list[CamaOut])
async def listar_camas(session: AsyncSession = Depends(get_session)):
    """Lista todas las camas con su estado de gestión actual."""
    resultado = await session.execute(
        select(CamaGestion).order_by(CamaGestion.sector, CamaGestion.nombre)
    )
    camas = resultado.scalars().all()
    return [CamaOut.model_validate(c) for c in camas]


@router.get("/{cama_id}", response_model=CamaDetalleOut)
async def detalle_cama(
    cama_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    notas_svc: ServicioNotas = Depends(get_notas),
):
    """Detalle de cama: estado + hitos recientes (desc) + notas activas."""
    cama = await _get_cama_or_404(cama_id, session)

    hitos_rows = (
        await session.execute(
            select(HitoAtlas)
            .where(HitoAtlas.cama_gestion_id == cama_id)
            .order_by(HitoAtlas.registrado_at.desc())
            .limit(50)
        )
    ).scalars().all()

    notas_rows = await notas_svc.listar_notas_activas(session, cama)

    return CamaDetalleOut(
        **CamaOut.model_validate(cama).model_dump(),
        hitos=[HitoOut.model_validate(h) for h in hitos_rows],
        notas=[NotaCamaOut.model_validate(n) for n in notas_rows],
    )


@router.post("/{cama_id}/ocupar", response_model=CamaOut)
async def ocupar_cama(
    cama_id: uuid.UUID,
    body: OcuparBody,
    session: AsyncSession = Depends(get_session),
    transiciones: ServicioTransiciones = Depends(get_transiciones),
):
    cama = await _get_cama_or_404(cama_id, session)
    internacion = await _get_internacion_or_404(body.internacion_id, session)
    await transiciones.ocupar(
        session, cama, internacion, body.rol, actor_nombre=body.actor_nombre
    )
    await session.refresh(cama)
    return CamaOut.model_validate(cama)


@router.post("/{cama_id}/reservar", response_model=CamaOut)
async def reservar_cama(
    cama_id: uuid.UUID,
    body: ReservarBody,
    session: AsyncSession = Depends(get_session),
    reservas: ServicioReservas = Depends(get_reservas),
):
    cama = await _get_cama_or_404(cama_id, session)
    internacion = await _get_internacion_or_404(body.internacion_id, session)
    await reservas.crear_reserva(
        session,
        cama,
        internacion,
        MotivoReserva.INGRESO_PROGRAMADO,
        body.tipo_cama_requerido,
        body.rol,
        actor_nombre=body.actor_nombre,
    )
    await session.refresh(cama)
    return CamaOut.model_validate(cama)


@router.post("/{cama_id}/iniciar-alta", response_model=CamaOut)
async def iniciar_alta(
    cama_id: uuid.UUID,
    body: IniciarAltaBody,
    session: AsyncSession = Depends(get_session),
    transiciones: ServicioTransiciones = Depends(get_transiciones),
):
    cama = await _get_cama_or_404(cama_id, session)
    await transiciones.iniciar_alta(
        session, cama, body.rol, actor_nombre=body.actor_nombre
    )
    await session.refresh(cama)
    return CamaOut.model_validate(cama)


@router.post("/{cama_id}/alta-fisica", response_model=CamaOut)
async def alta_fisica(
    cama_id: uuid.UUID,
    body: AltaFisicaBody,
    session: AsyncSession = Depends(get_session),
    transiciones: ServicioTransiciones = Depends(get_transiciones),
):
    cama = await _get_cama_or_404(cama_id, session)
    await transiciones.dar_alta_fisica(
        session, cama, body.rol, actor_nombre=body.actor_nombre
    )
    await session.refresh(cama)
    return CamaOut.model_validate(cama)


@router.post("/{cama_id}/finalizar-limpieza", response_model=CamaOut)
async def finalizar_limpieza(
    cama_id: uuid.UUID,
    body: FinalizarLimpiezaBody,
    session: AsyncSession = Depends(get_session),
    transiciones: ServicioTransiciones = Depends(get_transiciones),
):
    cama = await _get_cama_or_404(cama_id, session)
    await transiciones.finalizar_limpieza(
        session, cama, body.rol, actor_nombre=body.actor_nombre
    )
    await session.refresh(cama)
    return CamaOut.model_validate(cama)


@router.post("/{cama_id}/bloquear", response_model=CamaOut)
async def bloquear_cama(
    cama_id: uuid.UUID,
    body: BloquearBody,
    session: AsyncSession = Depends(get_session),
    transiciones: ServicioTransiciones = Depends(get_transiciones),
):
    cama = await _get_cama_or_404(cama_id, session)
    await transiciones.bloquear(
        session, cama, body.rol, body.motivo_bloqueo, actor_nombre=body.actor_nombre
    )
    await session.refresh(cama)
    return CamaOut.model_validate(cama)


@router.post("/{cama_id}/desbloquear", response_model=CamaOut)
async def desbloquear_cama(
    cama_id: uuid.UUID,
    body: DesbloquearBody,
    session: AsyncSession = Depends(get_session),
    transiciones: ServicioTransiciones = Depends(get_transiciones),
):
    cama = await _get_cama_or_404(cama_id, session)
    await transiciones.desbloquear(
        session, cama, body.rol, actor_nombre=body.actor_nombre
    )
    await session.refresh(cama)
    return CamaOut.model_validate(cama)
