"""Router de egresos — 8 rutas como wrappers finos sobre ServicioEgreso.

Cero lógica de negocio: cada endpoint traduce HTTP ↔ ServicioEgreso.
Las excepciones de dominio se mapean a HTTP en api/main.py (handlers globales),
salvo MantenimientoPendiente que produce 200 con campo informativo.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_egreso, get_session
from api.schemas import (
    AgregarNotaEgresoBody,
    ConfirmarSalidaFisicaBody,
    CrearEgresoBody,
    DiscrepanciaOut,
    EgresoDetalleOut,
    EgresoOut,
    ItemChecklistEgresoOut,
    ItemChecklistLimpiezaOut,
    MarcarItemChecklistBody,
    MarcarItemLimpiezaBody,
    MarcarLimpiezaOut,
    NotaEgresoOut,
    OkAdministrativoBody,
    RegistrarDiscrepanciaBody,
)
from database.models import (
    CamaGestion,
    Discrepancia,
    Egreso,
    InternacionLocal,
    ItemChecklistEgreso,
    ItemChecklistLimpieza,
    NotaEgreso,
)
from domain.discharge_responsibility import computar_responsable
from domain.egreso_service import MantenimientoPendiente, ServicioEgreso

router = APIRouter(tags=["egresos"])


async def _get_egreso_or_404(egreso_id: uuid.UUID, session: AsyncSession) -> Egreso:
    egreso = await session.get(Egreso, egreso_id)
    if egreso is None:
        raise HTTPException(status_code=404, detail=f"Egreso {egreso_id} no encontrado.")
    return egreso


async def _get_internacion_or_404(
    internacion_id: uuid.UUID, session: AsyncSession
) -> InternacionLocal:
    internacion = await session.get(InternacionLocal, internacion_id)
    if internacion is None:
        raise HTTPException(
            status_code=404, detail=f"Internación {internacion_id} no encontrada."
        )
    return internacion


async def _cargar_colecciones(session: AsyncSession, egreso_id: uuid.UUID):
    items = list(
        (await session.execute(
            select(ItemChecklistEgreso).where(ItemChecklistEgreso.egreso_id == egreso_id)
        )).scalars().all()
    )
    items_limpieza = list(
        (await session.execute(
            select(ItemChecklistLimpieza).where(ItemChecklistLimpieza.egreso_id == egreso_id)
        )).scalars().all()
    )
    discrepancias = list(
        (await session.execute(
            select(Discrepancia)
            .where(Discrepancia.egreso_id == egreso_id)
            .order_by(Discrepancia.hora)
        )).scalars().all()
    )
    notas = list(
        (await session.execute(
            select(NotaEgreso)
            .where(NotaEgreso.egreso_id == egreso_id)
            .order_by(NotaEgreso.hora)
        )).scalars().all()
    )
    return items, items_limpieza, discrepancias, notas


def _build_detalle(
    egreso: Egreso,
    items: list,
    items_limpieza: list,
    discrepancias: list,
    notas: list,
) -> EgresoDetalleOut:
    egreso_ns = SimpleNamespace(
        estado=egreso.estado,
        salida_fisica_at=egreso.salida_fisica_at,
        egreso_admin_at=egreso.egreso_admin_at,
        medio_egreso=egreso.medio_egreso,
        items_checklist=items,
        limpieza_checklist=items_limpieza,
    )
    responsable = computar_responsable(egreso_ns)

    minutos_trabado: float | None = None
    if egreso.trabado_desde is not None:
        delta = datetime.now(timezone.utc) - egreso.trabado_desde
        minutos_trabado = round(delta.total_seconds() / 60.0, 1)

    return EgresoDetalleOut(
        **EgresoOut.model_validate(egreso).model_dump(),
        items_checklist=[ItemChecklistEgresoOut.model_validate(i) for i in items],
        discrepancias=[DiscrepanciaOut.model_validate(d) for d in discrepancias],
        notas=[NotaEgresoOut.model_validate(n) for n in notas],
        limpieza_checklist=[ItemChecklistLimpiezaOut.model_validate(i) for i in items_limpieza],
        responsable_actual=(
            {"rol": responsable.rol, "tarea": responsable.tarea}
            if responsable is not None else None
        ),
        minutos_trabado=minutos_trabado,
    )


@router.get("/internaciones/{internacion_id}/egreso-activo", response_model=EgresoDetalleOut)
async def egreso_activo(
    internacion_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    egreso = (
        await session.execute(
            select(Egreso).where(
                Egreso.internacion_local_id == internacion_id,
                Egreso.estado.in_(["info", "bloqueado", "egreso_admin"]),
            )
        )
    ).scalar_one_or_none()
    if egreso is None:
        raise HTTPException(
            status_code=404,
            detail=f"No hay egreso activo para la internación {internacion_id}.",
        )
    items, items_limpieza, discrepancias, notas = await _cargar_colecciones(session, egreso.id)
    return _build_detalle(egreso, items, items_limpieza, discrepancias, notas)


@router.post(
    "/internaciones/{internacion_id}/egreso",
    response_model=EgresoOut,
    status_code=201,
)
async def crear_egreso(
    internacion_id: uuid.UUID,
    body: CrearEgresoBody,
    session: AsyncSession = Depends(get_session),
    egreso_svc: ServicioEgreso = Depends(get_egreso),
):
    internacion = await _get_internacion_or_404(internacion_id, session)
    cama = (
        await session.execute(
            select(CamaGestion).where(CamaGestion.internacion_actual_id == internacion_id)
        )
    ).scalar_one_or_none()
    if cama is None:
        raise HTTPException(
            status_code=409,
            detail="La internación no tiene una cama activa asignada.",
        )
    egreso = await egreso_svc.crear_egreso(
        session, internacion, cama, body.medio_egreso, body.rol,
        actor_nombre=body.actor_nombre,
    )
    return EgresoOut.model_validate(egreso)


@router.get("/egresos/{egreso_id}", response_model=EgresoDetalleOut)
async def detalle_egreso(
    egreso_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    egreso = await _get_egreso_or_404(egreso_id, session)
    items, items_limpieza, discrepancias, notas = await _cargar_colecciones(
        session, egreso_id
    )
    return _build_detalle(egreso, items, items_limpieza, discrepancias, notas)


@router.patch(
    "/egresos/{egreso_id}/checklist/{item_id}",
    response_model=ItemChecklistEgresoOut,
)
async def marcar_item_checklist(
    egreso_id: uuid.UUID,
    item_id: uuid.UUID,
    body: MarcarItemChecklistBody,
    session: AsyncSession = Depends(get_session),
    egreso_svc: ServicioEgreso = Depends(get_egreso),
):
    metadata = {"no_aplica": True} if body.no_aplica else None
    discrepancia = body.discrepancia.model_dump() if body.discrepancia else None
    item = await egreso_svc.marcar_item(
        session, egreso_id, item_id, body.rol,
        actor_nombre=body.actor_nombre, metadata=metadata, discrepancia=discrepancia,
    )
    return ItemChecklistEgresoOut.model_validate(item)


@router.patch("/egresos/{egreso_id}/egreso-admin", response_model=EgresoOut)
async def egreso_admin(
    egreso_id: uuid.UUID,
    body: OkAdministrativoBody,
    session: AsyncSession = Depends(get_session),
    egreso_svc: ServicioEgreso = Depends(get_egreso),
):
    egreso = await egreso_svc.ok_administrativo(
        session, egreso_id, body.rol, actor_nombre=body.actor_nombre,
    )
    return EgresoOut.model_validate(egreso)


@router.patch("/egresos/{egreso_id}/salida-fisica", response_model=EgresoOut)
async def salida_fisica(
    egreso_id: uuid.UUID,
    body: ConfirmarSalidaFisicaBody,
    session: AsyncSession = Depends(get_session),
    egreso_svc: ServicioEgreso = Depends(get_egreso),
):
    egreso = await egreso_svc.confirmar_salida_fisica(
        session, egreso_id, body.rol,
        actor_nombre=body.actor_nombre, metadata=body.metadata,
    )
    return EgresoOut.model_validate(egreso)


@router.patch(
    "/egresos/{egreso_id}/limpieza/{item_id}",
    response_model=MarcarLimpiezaOut,
)
async def marcar_item_limpieza(
    egreso_id: uuid.UUID,
    item_id: uuid.UUID,
    body: MarcarItemLimpiezaBody,
    session: AsyncSession = Depends(get_session),
    egreso_svc: ServicioEgreso = Depends(get_egreso),
):
    try:
        discrepancia = body.discrepancia.model_dump() if body.discrepancia else None
        item = await egreso_svc.marcar_item_limpieza(
            session, egreso_id, item_id, body.rol,
            actor_nombre=body.actor_nombre, discrepancia=discrepancia,
        )
        return MarcarLimpiezaOut.model_validate(item)
    except MantenimientoPendiente:
        # El ítem YA fue commiteado (limpieza OK, mantenimiento bloquea).
        # No es un error del request — devolvemos 200 con aviso.
        item = await session.get(ItemChecklistLimpieza, item_id)
        return MarcarLimpiezaOut(
            id=item.id,
            codigo=item.codigo,
            label=item.label,
            done=item.done,
            hora_marcado=item.hora_marcado,
            autor=item.autor,
            liberacion_bloqueada="mantenimiento_pendiente",
        )


@router.patch("/egresos/{egreso_id}/discrepancia", response_model=DiscrepanciaOut)
async def registrar_discrepancia(
    egreso_id: uuid.UUID,
    body: RegistrarDiscrepanciaBody,
    session: AsyncSession = Depends(get_session),
    egreso_svc: ServicioEgreso = Depends(get_egreso),
):
    disc = await egreso_svc.registrar_discrepancia(
        session, egreso_id, body.motivo, body.nota, body.rol,
        actor_nombre=body.actor_nombre,
    )
    return DiscrepanciaOut.model_validate(disc)


@router.post("/egresos/{egreso_id}/notas", response_model=NotaEgresoOut)
async def agregar_nota(
    egreso_id: uuid.UUID,
    body: AgregarNotaEgresoBody,
    session: AsyncSession = Depends(get_session),
    egreso_svc: ServicioEgreso = Depends(get_egreso),
):
    if body.tipo not in ("reclamo", "novedad"):
        raise HTTPException(
            status_code=422,
            detail=f"tipo '{body.tipo}' inválido. Debe ser 'reclamo' o 'novedad'.",
        )
    nota = await egreso_svc.agregar_nota(
        session, egreso_id, body.tipo, body.texto, body.rol,
        actor_nombre=body.actor_nombre,
    )
    return NotaEgresoOut.model_validate(nota)
