"""Router de camas: tablero de estado + transiciones semánticas."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_checklist, get_notas, get_reservas, get_session, get_transiciones
from api.schemas import (
    AltaFisicaBody,
    BloquearBody,
    CamaDetalleOut,
    CamaOut,
    CancelarReservaBody,
    CompletarPasoBody,
    DesbloquearBody,
    FinalizarLimpiezaBody,
    HitoOut,
    IniciarAltaBody,
    NotaCamaCreate,
    NotaCamaOut,
    OcuparBody,
    PasoAltaInternacionOut,
    ReservarBody,
    RevertirAltaBody,
)
from database.enums import EstadoCamaGestion, EstadoReserva, MotivoReserva
from database.models import (
    CamaGestion,
    HitoAtlas,
    InternacionLocal,
    PasoAltaCatalogo,
    PasoAltaInternacion,
    PaseServicio,
    Reserva,
)
from domain.discharge_checklist_service import ServicioChecklistAlta
from domain.note_service import ServicioNotas
from domain.reservation_service import ServicioReservas
from domain.transition_service import ServicioTransiciones

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


@router.post("/{cama_id}/notas", response_model=NotaCamaOut)
async def crear_nota_cama(
    cama_id: uuid.UUID,
    body: NotaCamaCreate,
    session: AsyncSession = Depends(get_session),
    notas_svc: ServicioNotas = Depends(get_notas),
):
    cama = await _get_cama_or_404(cama_id, session)
    nota = await notas_svc.crear_nota(
        session,
        cama,
        body.texto,
        creada_por_rol=body.rol,
        creada_por_nombre=body.actor_nombre,
    )
    return NotaCamaOut.model_validate(nota)


async def _paso_alta_internacion_out(
    session: AsyncSession,
    paso: PasoAltaInternacion,
) -> PasoAltaInternacionOut:
    paso_cat = await session.get(PasoAltaCatalogo, paso.paso_catalogo_id)
    return PasoAltaInternacionOut(
        id=paso.id,
        internacion_id=paso.internacion_id,
        paso_catalogo_id=paso.paso_catalogo_id,
        codigo=paso_cat.codigo if paso_cat is not None else None,
        nombre=paso_cat.nombre if paso_cat is not None else None,
        era_bloqueante=paso.era_bloqueante,
        completado=paso.completado,
        completado_por_rol=paso.completado_por_rol,
        completado_por_nombre=paso.completado_por_nombre,
        completado_at=paso.completado_at,
        creada_at=paso.creada_at,
    )


@router.post(
    "/internaciones/{internacion_id}/pasos/instanciar",
    response_model=list[PasoAltaInternacionOut],
)
async def instanciar_pasos_internacion(
    internacion_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    checklist: ServicioChecklistAlta = Depends(get_checklist),
):
    internacion = await _get_internacion_or_404(internacion_id, session)
    await checklist.instanciar_pasos(session, internacion)
    pasos = await checklist.listar_pasos(session, internacion)
    return [await _paso_alta_internacion_out(session, paso) for paso in pasos]


@router.get(
    "/internaciones/{internacion_id}/pasos",
    response_model=list[PasoAltaInternacionOut],
)
async def listar_pasos_internacion(
    internacion_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    checklist: ServicioChecklistAlta = Depends(get_checklist),
):
    internacion = await _get_internacion_or_404(internacion_id, session)
    pasos = await checklist.listar_pasos(session, internacion)
    return [await _paso_alta_internacion_out(session, paso) for paso in pasos]


@router.post("/pasos/{paso_id}/completar", response_model=PasoAltaInternacionOut)
async def completar_paso(
    paso_id: uuid.UUID,
    body: CompletarPasoBody,
    session: AsyncSession = Depends(get_session),
    checklist: ServicioChecklistAlta = Depends(get_checklist),
):
    paso = await session.get(PasoAltaInternacion, paso_id)
    if paso is None:
        raise HTTPException(
            status_code=404,
            detail=f"Paso de alta {paso_id} no encontrado.",
        )
    paso = await checklist.completar_paso(
        session,
        paso,
        body.rol,
        actor_nombre=body.actor_nombre,
    )
    return await _paso_alta_internacion_out(session, paso)


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
    checklist: ServicioChecklistAlta = Depends(get_checklist),
):
    cama = await _get_cama_or_404(cama_id, session)
    if cama.internacion_actual_id is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No se puede dar el alta física: la cama no tiene una "
                "internación asociada en este momento."
            ),
        )
    internacion = await _get_internacion_or_404(cama.internacion_actual_id, session)
    await checklist.dar_alta_fisica_validada(
        session,
        cama,
        internacion,
        body.rol,
        actor_nombre=body.actor_nombre,
        forzar=body.forzar,
        motivo_override=body.motivo_override,
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


@router.post("/{cama_id}/cancelar-reserva", response_model=CamaOut)
async def cancelar_reserva_cama(
    cama_id: uuid.UUID,
    body: CancelarReservaBody,
    session: AsyncSession = Depends(get_session),
    reservas: ServicioReservas = Depends(get_reservas),
):
    """Cancela la reserva ACTIVA de una cama (RESERVADA → DISPONIBLE), guardando por qué
    no se ocupó. ``motivo_cancelacion`` es obligatorio.

    Una cama RESERVADA tiene exactamente una reserva ACTIVA (la máquina de estados no
    permite reservar una cama que no esté DISPONIBLE), así que se resuelve por cama_id sin
    ambigüedad. Si esa reserva es la cama-destino de un PaseServicio, NO se cancela acá:
    hay que cancelar el pase (que libera su reserva de forma coordinada). Cancelarla suelta
    dejaría el pase apuntando a una reserva muerta.
    """
    cama = await _get_cama_or_404(cama_id, session)

    reserva = (
        await session.execute(
            select(Reserva).where(
                Reserva.cama_gestion_id == cama_id,
                Reserva.estado == EstadoReserva.ACTIVA,
            )
        )
    ).scalar_one_or_none()

    if reserva is None:
        raise HTTPException(
            status_code=409,
            detail=f"La cama {cama_id} no tiene una reserva activa para cancelar.",
        )

    pase = (
        await session.execute(
            select(PaseServicio).where(PaseServicio.reserva_id == reserva.id)
        )
    ).scalar_one_or_none()

    if pase is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Esta reserva pertenece al pase {pase.id}. Cancelá el pase, no la "
                f"reserva: el pase libera su cama de forma coordinada."
            ),
        )

    await reservas.cancelar_reserva(
        session,
        reserva,
        body.motivo_cancelacion,
        body.rol,
        actor_nombre=body.actor_nombre,
    )
    await session.refresh(cama)
    return CamaOut.model_validate(cama)


@router.post("/{cama_id}/revertir-alta", response_model=CamaOut)
async def revertir_alta(
    cama_id: uuid.UUID,
    body: RevertirAltaBody,
    session: AsyncSession = Depends(get_session),
    transiciones: ServicioTransiciones = Depends(get_transiciones),
):
    """Revierte un alta: la cama vuelve a OCUPADA. Despacha según el estado actual:

    - PROCESO_DE_ALTA → reversión temprana (todavía no hubo alta física; rol MEDICO).
    - LIMPIEZA_TERMINAL → reversión tardía (el alta física ya pasó; rol ADMISION; el
      paciente se recupera del hito de alta física).

    El tipo (ALTA_INFORMADA_POR_ERROR / REINGRESO_FISICO) clasifica el motivo y queda en
    el hito (código distinto por tipo). Reabre la internación si estaba finalizada.
    """
    cama = await _get_cama_or_404(cama_id, session)

    if cama.estado_gestion == EstadoCamaGestion.PROCESO_DE_ALTA:
        await transiciones.revertir_alta_temprana(
            session, cama, body.rol, body.motivo_reversion,
            tipo=body.tipo_reversion, actor_nombre=body.actor_nombre,
        )
    elif cama.estado_gestion == EstadoCamaGestion.LIMPIEZA_TERMINAL:
        await transiciones.revertir_alta_tardia(
            session, cama, body.rol, body.motivo_reversion,
            tipo=body.tipo_reversion, actor_nombre=body.actor_nombre,
        )
    else:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No se puede revertir un alta desde {cama.estado_gestion.value}. "
                f"Sólo desde PROCESO_DE_ALTA (temprana) o LIMPIEZA_TERMINAL (tardía)."
            ),
        )

    await session.refresh(cama)
    return CamaOut.model_validate(cama)
