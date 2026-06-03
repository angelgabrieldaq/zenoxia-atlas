"""Router de internaciones: listado y creación."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends

from api.dependencies import get_session
from api.schemas import InternacionCreate, InternacionOut
from database.models import InternacionLocal, PacienteLocal

router = APIRouter(prefix="/internaciones", tags=["internaciones"])


@router.get("", response_model=list[InternacionOut])
async def listar_internaciones(session: AsyncSession = Depends(get_session)):
    """Lista todas las internaciones con datos básicos del paciente."""
    rows = (
        await session.execute(
            select(InternacionLocal).order_by(InternacionLocal.iniciada_at.desc())
        )
    ).scalars().all()

    # Desnormalizar datos del paciente en cada respuesta.
    result = []
    for internacion in rows:
        paciente = await session.get(PacienteLocal, internacion.paciente_local_id)
        out = InternacionOut.model_validate(internacion)
        if paciente is not None:
            out.paciente_dni = paciente.dni
            out.paciente_nombre = paciente.nombre
            out.paciente_apellido = paciente.apellido
        result.append(out)
    return result


@router.post("", response_model=InternacionOut, status_code=201)
async def crear_internacion(
    body: InternacionCreate,
    session: AsyncSession = Depends(get_session),
):
    """Crea una internación. Busca al PacienteLocal por DNI; lo crea si no existe."""
    # Buscar o crear el PacienteLocal por DNI.
    paciente = (
        await session.execute(
            select(PacienteLocal).where(PacienteLocal.dni == body.dni)
        )
    ).scalars().first()

    if paciente is None:
        paciente = PacienteLocal(
            dni=body.dni,
            nombre=body.nombre,
            apellido=body.apellido,
        )
        session.add(paciente)
        await session.flush()  # asigna paciente.id antes de usarlo

    internacion = InternacionLocal(
        paciente_local_id=paciente.id,
        categoria=body.categoria,
        comodidad_requerida=body.comodidad_requerida,
        servicio_codigo=body.servicio_codigo,
        cobertura=body.cobertura,
        plan_cobertura=body.plan_cobertura,
        numero_socio=body.numero_socio,
        nota_cobertura=body.nota_cobertura,
    )
    session.add(internacion)
    await session.commit()

    out = InternacionOut.model_validate(internacion)
    out.paciente_dni = paciente.dni
    out.paciente_nombre = paciente.nombre
    out.paciente_apellido = paciente.apellido
    return out
