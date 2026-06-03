"""Tests de la API REST (FastAPI) — tablero de camas. Contra Postgres real.

Usa el AsyncClient de httpx + ASGITransport para disparar requests contra la app
sin levantar un servidor real. Cada test trunca las tablas relevantes para aislarse.
"""

import os
import uuid

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from api.main import app
from api.dependencies import get_session
from database.enums import CategoriaInternacion, EstadoCamaGestion, TipoCama
from database.models import CamaGestion, InternacionLocal, PacienteLocal

load_dotenv()

_DATABASE_URL = os.environ["DATABASE_URL"]
_engine = create_async_engine(_DATABASE_URL, poolclass=NullPool)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)

_TABLAS = (
    "hito_atlas",
    "nota_cama",
    "reserva",
    "pase_servicio",
    "paso_alta_internacion",
    "paso_alta_catalogo",
    "internacion_local",
    "paciente_local",
    "cama_gestion",
)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    async with _session_factory() as s:
        await s.execute(
            text(
                f"TRUNCATE {', '.join(_TABLAS)} RESTART IDENTITY CASCADE"
            )
        )
        await s.commit()
        yield s


@pytest_asyncio.fixture
async def client(session: AsyncSession):
    """Cliente httpx que usa la sesión de test (override de la dependency)."""

    async def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

async def _crear_cama(
    session: AsyncSession,
    nombre: str = "H-01",
    tipo: TipoCama = TipoCama.CAMA_INTERNACION,
    estado: EstadoCamaGestion = EstadoCamaGestion.DISPONIBLE,
) -> CamaGestion:
    cama = CamaGestion(nombre=nombre, tipo=tipo, sector="Clínica", estado_gestion=estado)
    session.add(cama)
    await session.commit()
    return cama


async def _crear_internacion(session: AsyncSession) -> InternacionLocal:
    paciente = PacienteLocal(dni="12345678", nombre="Juan", apellido="Pérez")
    session.add(paciente)
    await session.flush()
    internacion = InternacionLocal(
        paciente_local_id=paciente.id,
        categoria=CategoriaInternacion.CLINICA,
    )
    session.add(internacion)
    await session.commit()
    return internacion


# ------------------------------------------------------------------ #
# Tests
# ------------------------------------------------------------------ #

async def test_listar_camas_vacio(client: AsyncClient):
    r = await client.get("/camas")
    assert r.status_code == 200
    assert r.json() == []


async def test_listar_camas_con_datos(
    client: AsyncClient, session: AsyncSession
):
    await _crear_cama(session, "H-01")
    await _crear_cama(session, "H-02")
    r = await client.get("/camas")
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_detalle_cama_devuelve_hitos_y_notas(
    client: AsyncClient, session: AsyncSession
):
    cama = await _crear_cama(session)
    r = await client.get(f"/camas/{cama.id}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == str(cama.id)
    assert "hitos" in data
    assert "notas" in data


async def test_detalle_cama_404(client: AsyncClient):
    r = await client.get(f"/camas/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_ocupar_cama_con_rol_correcto(
    client: AsyncClient, session: AsyncSession
):
    cama = await _crear_cama(session)
    internacion = await _crear_internacion(session)
    r = await client.post(
        f"/camas/{cama.id}/ocupar",
        json={"internacion_id": str(internacion.id), "rol": "ADMISION"},
    )
    assert r.status_code == 200
    assert r.json()["estado_gestion"] == "OCUPADA"


async def test_ocupar_cama_rol_no_autorizado_devuelve_403(
    client: AsyncClient, session: AsyncSession
):
    cama = await _crear_cama(session)
    internacion = await _crear_internacion(session)
    # MEDICO no puede disparar DISPONIBLE → OCUPADA (sólo ADMISION puede).
    r = await client.post(
        f"/camas/{cama.id}/ocupar",
        json={"internacion_id": str(internacion.id), "rol": "MEDICO"},
    )
    assert r.status_code == 403


async def test_ocupar_cama_estado_invalido_devuelve_409(
    client: AsyncClient, session: AsyncSession
):
    # Cama ya OCUPADA — volver a ocupar es transición ilegal.
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(
        session, estado=EstadoCamaGestion.OCUPADA
    )
    cama.internacion_actual_id = internacion.id
    await session.commit()

    r = await client.post(
        f"/camas/{cama.id}/ocupar",
        json={"internacion_id": str(internacion.id), "rol": "ADMISION"},
    )
    assert r.status_code == 409


async def test_crear_internacion(client: AsyncClient):
    r = await client.post(
        "/internaciones",
        json={
            "dni": "99887766",
            "nombre": "María",
            "apellido": "García",
            "categoria": "CLINICA",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["categoria"] == "CLINICA"
    assert data["paciente_dni"] == "99887766"


async def test_listar_internaciones(
    client: AsyncClient, session: AsyncSession
):
    await _crear_internacion(session)
    r = await client.get("/internaciones")
    assert r.status_code == 200
    assert len(r.json()) == 1


async def test_flujo_completo_disponible_a_limpieza(
    client: AsyncClient, session: AsyncSession
):
    """Flujo feliz: DISPONIBLE → OCUPADA → PROCESO_DE_ALTA → LIMPIEZA_TERMINAL."""
    cama = await _crear_cama(session)
    internacion = await _crear_internacion(session)
    cid = str(cama.id)

    r = await client.post(
        f"/camas/{cid}/ocupar",
        json={"internacion_id": str(internacion.id), "rol": "ADMISION"},
    )
    assert r.json()["estado_gestion"] == "OCUPADA"

    r = await client.post(f"/camas/{cid}/iniciar-alta", json={"rol": "MEDICO"})
    assert r.json()["estado_gestion"] == "PROCESO_DE_ALTA"

    r = await client.post(f"/camas/{cid}/alta-fisica", json={"rol": "ADMISION"})
    assert r.json()["estado_gestion"] == "LIMPIEZA_TERMINAL"


async def test_crear_internacion_con_cobertura(client: AsyncClient):
    r = await client.post(
        "/internaciones",
        json={
            "dni": "55443322",
            "nombre": "Rosa",
            "apellido": "Ficticio",
            "categoria": "CLINICA",
            "cobertura": "Obra Social Ejemplo A",
            "plan_cobertura": "Plan 100",
            "numero_socio": "SOC-00001",
            "nota_cobertura": "Requiere autorizacion previa - demo",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["cobertura"] == "Obra Social Ejemplo A"
    assert data["plan_cobertura"] == "Plan 100"
    assert data["numero_socio"] == "SOC-00001"
    assert data["nota_cobertura"] == "Requiere autorizacion previa - demo"

    # Verificar que GET /internaciones también devuelve los campos.
    r2 = await client.get("/internaciones")
    assert r2.status_code == 200
    item = r2.json()[0]
    assert item["cobertura"] == "Obra Social Ejemplo A"
    assert item["plan_cobertura"] == "Plan 100"


async def test_crear_internacion_sin_cobertura_devuelve_nulos(client: AsyncClient):
    """Los 4 campos de cobertura son opcionales y vienen null si no se envían."""
    r = await client.post(
        "/internaciones",
        json={"dni": "11223344", "nombre": "Juan", "apellido": "Demo", "categoria": "CLINICA"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["cobertura"] is None
    assert data["plan_cobertura"] is None
    assert data["numero_socio"] is None
    assert data["nota_cobertura"] is None


async def test_health(client: AsyncClient):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
