"""Tests de integración del ServicioChecklistAlta (capa 1a, §6, sub-paso 1), Postgres real.

Cubren: instanciación de pasos según categoría (universales vs. de categoría),
idempotencia, completar un paso (con hito de auditoría), y la consulta de bloqueantes
pendientes. Incluye un test de la semilla del catálogo (y su idempotencia). Cada test
queda aislado truncando las tablas relevantes.
"""

import os

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from database.enums import CategoriaInternacion
from database.models import (
    HitoAtlas,
    InternacionLocal,
    PacienteLocal,
    PasoAltaCatalogo,
    PasoAltaInternacion,
)
from database.seeds import PASOS_ALTA_INICIALES, seed_pasos_alta_catalogo
from domain.discharge_checklist_service import ServicioChecklistAlta
from domain.state_machine import RolOperativo

load_dotenv()
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas"
)

_TABLAS = (
    "hito_atlas", "paso_alta_internacion", "paso_alta_catalogo",
    "cama_gestion", "internacion_local", "paciente_local",
)
_TRUNCATE = f"TRUNCATE {', '.join(_TABLAS)} RESTART IDENTITY CASCADE"


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text(_TRUNCATE))
    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    sesion = maker()
    try:
        yield sesion
    finally:
        await sesion.close()
        async with engine.begin() as conn:
            await conn.execute(text(_TRUNCATE))
        await engine.dispose()


@pytest.fixture
def servicio():
    return ServicioChecklistAlta()


# --- helpers ---

async def _crear_internacion(
    session: AsyncSession,
    categoria: CategoriaInternacion,
    dni: str = "30111222",
) -> InternacionLocal:
    paciente = PacienteLocal(dni=dni, nombre="Ana", apellido="Gómez")
    session.add(paciente)
    await session.commit()
    internacion = InternacionLocal(paciente_local_id=paciente.id, categoria=categoria)
    session.add(internacion)
    await session.commit()
    return internacion


async def _contar(session: AsyncSession, modelo) -> int:
    return (await session.execute(select(func.count()).select_from(modelo))).scalar_one()


# --------------------------------------------------------------------------- #
# 1. instanciar_pasos CLINICA: crea los universales, NO el de CRITICA.
# --------------------------------------------------------------------------- #
async def test_instanciar_pasos_clinica_solo_universales(session, servicio):
    await seed_pasos_alta_catalogo(session)
    internacion = await _crear_internacion(session, CategoriaInternacion.CLINICA)

    creados = await servicio.instanciar_pasos(session, internacion)

    # 4 universales (EPICRISIS, MEDICACION, RESUMEN, FACTURACION); el de CRITICA no.
    assert len(creados) == 4
    codigos = {
        (await session.get(PasoAltaCatalogo, p.paso_catalogo_id)).codigo for p in creados
    }
    assert "CONTROL_POST_UTI_AGENDADO" not in codigos
    assert codigos == {
        "EPICRISIS_FIRMADA", "MEDICACION_CONCILIADA",
        "RESUMEN_ALTA_ENTREGADO", "FACTURACION_CERRADA",
    }


# --------------------------------------------------------------------------- #
# 2. instanciar_pasos CRITICA: universales + el de CRITICA.
# --------------------------------------------------------------------------- #
async def test_instanciar_pasos_critica_incluye_el_de_critica(session, servicio):
    await seed_pasos_alta_catalogo(session)
    internacion = await _crear_internacion(session, CategoriaInternacion.CRITICA)

    creados = await servicio.instanciar_pasos(session, internacion)

    assert len(creados) == 5
    codigos = {
        (await session.get(PasoAltaCatalogo, p.paso_catalogo_id)).codigo for p in creados
    }
    assert "CONTROL_POST_UTI_AGENDADO" in codigos


# --------------------------------------------------------------------------- #
# 3. instanciar_pasos idempotente: dos llamadas no duplican.
# --------------------------------------------------------------------------- #
async def test_instanciar_pasos_idempotente(session, servicio):
    await seed_pasos_alta_catalogo(session)
    internacion = await _crear_internacion(session, CategoriaInternacion.CRITICA)

    primera = await servicio.instanciar_pasos(session, internacion)
    segunda = await servicio.instanciar_pasos(session, internacion)

    assert len(primera) == 5
    assert len(segunda) == 0  # no re-crea nada
    assert await _contar(session, PasoAltaInternacion) == 5  # total sin duplicar


# --------------------------------------------------------------------------- #
# 4. completar_paso: completado + quién + hito de auditoría.
# --------------------------------------------------------------------------- #
async def test_completar_paso_marca_y_escribe_hito(session, servicio):
    await seed_pasos_alta_catalogo(session)
    internacion = await _crear_internacion(session, CategoriaInternacion.CLINICA)
    await servicio.instanciar_pasos(session, internacion)
    pasos = await servicio.listar_pasos(session, internacion)
    paso = pasos[0]

    await servicio.completar_paso(
        session, paso, RolOperativo.MEDICO, actor_nombre="Dr. Gómez"
    )

    await session.refresh(paso)
    assert paso.completado is True
    assert paso.completado_por_rol == RolOperativo.MEDICO.value
    assert paso.completado_por_nombre == "Dr. Gómez"
    assert paso.completado_at is not None
    # hito de auditoría con metadata de qué paso
    hitos = (
        await session.execute(
            select(HitoAtlas).where(
                HitoAtlas.hito_codigo == "ATLAS_PASO_ALTA_COMPLETADO"
            )
        )
    ).scalars().all()
    assert len(hitos) == 1
    hito = hitos[0]
    assert hito.internacion_id == internacion.id
    assert hito.metadata_evento["paso_internacion_id"] == str(paso.id)
    assert hito.metadata_evento["paso_codigo"] is not None


# --------------------------------------------------------------------------- #
# 5. pasos_bloqueantes_pendientes: solo bloqueantes sin completar.
# --------------------------------------------------------------------------- #
async def test_pasos_bloqueantes_pendientes(session, servicio):
    await seed_pasos_alta_catalogo(session)
    internacion = await _crear_internacion(session, CategoriaInternacion.CLINICA)
    await servicio.instanciar_pasos(session, internacion)

    # Al inicio: 2 bloqueantes (EPICRISIS_FIRMADA, MEDICACION_CONCILIADA).
    pendientes = await servicio.pasos_bloqueantes_pendientes(session, internacion)
    assert len(pendientes) == 2
    assert all(p.era_bloqueante for p in pendientes)

    # Completar uno de los bloqueantes → queda 1 pendiente.
    await servicio.completar_paso(session, pendientes[0], RolOperativo.MEDICO)
    pendientes_2 = await servicio.pasos_bloqueantes_pendientes(session, internacion)
    assert len(pendientes_2) == 1
    assert pendientes_2[0].id != pendientes[0].id


# --------------------------------------------------------------------------- #
# 6. semilla del catálogo: inserta los pasos esperados y es idempotente.
# --------------------------------------------------------------------------- #
async def test_semilla_catalogo_idempotente(session):
    creados = await seed_pasos_alta_catalogo(session)
    assert len(creados) == len(PASOS_ALTA_INICIALES)  # 5

    # Códigos y reglas esperadas.
    catalogo = (await session.execute(select(PasoAltaCatalogo))).scalars().all()
    por_codigo = {p.codigo: p for p in catalogo}
    assert set(por_codigo) == {
        "EPICRISIS_FIRMADA", "MEDICACION_CONCILIADA", "RESUMEN_ALTA_ENTREGADO",
        "FACTURACION_CERRADA", "CONTROL_POST_UTI_AGENDADO",
    }
    assert por_codigo["EPICRISIS_FIRMADA"].bloqueante is True
    assert por_codigo["EPICRISIS_FIRMADA"].categoria_aplica is None  # universal
    assert por_codigo["RESUMEN_ALTA_ENTREGADO"].bloqueante is False
    assert (
        por_codigo["CONTROL_POST_UTI_AGENDADO"].categoria_aplica
        == CategoriaInternacion.CRITICA
    )

    # Idempotente: segunda llamada no crea ni duplica.
    creados_2 = await seed_pasos_alta_catalogo(session)
    assert len(creados_2) == 0
    assert await _contar(session, PasoAltaCatalogo) == len(PASOS_ALTA_INICIALES)
