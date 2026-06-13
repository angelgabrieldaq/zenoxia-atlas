"""Tests de integración de ServicioNotas (capa 1a, §9b), sobre Postgres real.

Verifican las cuatro operaciones CRUD: crear, editar, desactivar y listar activas.
Cada test queda aislado truncando las tablas relevantes.
"""

import os

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from database.enums import EstadoCamaGestion, TipoCama
from database.models import CamaGestion, NotaCama, PacienteLocal
from domain.note_service import ServicioNotas

load_dotenv()
DATABASE_URL = os.getenv(
    "DATABASE_URL_TEST", "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas_test"
)

_TABLAS = ("nota_cama", "cama_gestion", "paciente_local")
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
    return ServicioNotas()


async def _crear_cama(session: AsyncSession, nombre: str = "UTI-01") -> CamaGestion:
    cama = CamaGestion(
        nombre=nombre, tipo=TipoCama.UTI, sector="UTI",
        estado_gestion=EstadoCamaGestion.DISPONIBLE,
    )
    session.add(cama)
    await session.commit()
    return cama


# --------------------------------------------------------------------------- #
# 1. crear_nota: nota activa, con creada_por estampado.
# --------------------------------------------------------------------------- #
async def test_crear_nota(session, servicio):
    cama = await _crear_cama(session)

    nota = await servicio.crear_nota(
        session, cama, "Paciente con caída reciente, precaución al movilizar.",
        creada_por_rol="ENFERMERIA", creada_por_nombre="María Pérez",
    )

    await session.refresh(nota)
    assert nota.id is not None
    assert nota.cama_gestion_id == cama.id
    assert nota.texto == "Paciente con caída reciente, precaución al movilizar."
    assert nota.creada_por_rol == "ENFERMERIA"
    assert nota.creada_por_nombre == "María Pérez"
    assert nota.creada_at is not None
    assert nota.activa is True
    assert nota.modificada_at is None


# --------------------------------------------------------------------------- #
# 2. editar_nota: texto actualizado, modificada_por/modificada_at estampados.
# --------------------------------------------------------------------------- #
async def test_editar_nota(session, servicio):
    cama = await _crear_cama(session)
    nota = await servicio.crear_nota(
        session, cama, "Texto original.",
        creada_por_rol="MEDICO", creada_por_nombre="Dr. Gómez",
    )
    texto_original = nota.texto

    await servicio.editar_nota(
        session, nota, "Texto corregido.",
        modificada_por_rol="MEDICO", modificada_por_nombre="Dr. Gómez",
    )

    await session.refresh(nota)
    assert nota.texto == "Texto corregido."
    assert nota.texto != texto_original
    assert nota.modificada_por_rol == "MEDICO"
    assert nota.modificada_por_nombre == "Dr. Gómez"
    assert nota.modificada_at is not None
    assert nota.activa is True  # editar no desactiva


# --------------------------------------------------------------------------- #
# 3. desactivar_nota: activa=False, el registro sigue en la base.
# --------------------------------------------------------------------------- #
async def test_desactivar_nota(session, servicio):
    cama = await _crear_cama(session)
    nota = await servicio.crear_nota(session, cama, "Nota a borrar.")
    nota_id = nota.id

    await servicio.desactivar_nota(session, nota)

    # El registro persiste (no DELETE físico).
    nota_db = await session.get(NotaCama, nota_id)
    assert nota_db is not None
    assert nota_db.activa is False
    assert nota_db.texto == "Nota a borrar."  # texto intacto


# --------------------------------------------------------------------------- #
# 4. listar_notas_activas: devuelve solo las activas de esa cama, no las
#    desactivadas, y no las notas de otras camas.
# --------------------------------------------------------------------------- #
async def test_listar_notas_activas(session, servicio):
    cama_a = await _crear_cama(session, nombre="UTI-A")
    cama_b = await _crear_cama(session, nombre="UTI-B")

    nota1 = await servicio.crear_nota(session, cama_a, "Nota 1 de cama A.")
    nota2 = await servicio.crear_nota(session, cama_a, "Nota 2 de cama A.")
    nota_inactiva = await servicio.crear_nota(session, cama_a, "Nota desactivada.")
    await servicio.desactivar_nota(session, nota_inactiva)
    await servicio.crear_nota(session, cama_b, "Nota de cama B, no debe aparecer.")

    activas = await servicio.listar_notas_activas(session, cama_a)

    assert len(activas) == 2
    ids = {n.id for n in activas}
    assert nota1.id in ids
    assert nota2.id in ids
    assert nota_inactiva.id not in ids
