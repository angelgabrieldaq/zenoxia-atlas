"""Tests de integración de la API REST de egresos. Contra Postgres real.

Estilo test_api_camas.py: AsyncClient + ASGITransport, truncate por fixture,
dependency override de get_session.
"""

from __future__ import annotations

import os
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api.main import app
from api.dependencies import get_session
from database.enums import CategoriaInternacion, EstadoCamaGestion, TipoCama
from database.models import CamaGestion, Egreso, HitoAtlas, InternacionLocal, PacienteLocal
from domain.discharge_catalog import CATALOGO_CHECKLIST_EGRESO

load_dotenv()

_DATABASE_URL = os.environ["DATABASE_URL"]
_engine = create_async_engine(_DATABASE_URL, poolclass=NullPool)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)

_TABLAS = (
    "hito_atlas",
    "item_checklist_egreso",
    "item_checklist_limpieza",
    "discrepancias",
    "nota_egreso",
    "egresos",
    "cama_gestion",
    "internacion_local",
    "paciente_local",
)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    async with _session_factory() as s:
        await s.execute(
            text(f"TRUNCATE {', '.join(_TABLAS)} RESTART IDENTITY CASCADE")
        )
        await s.commit()
        yield s


@pytest_asyncio.fixture
async def client(session: AsyncSession):
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

_ROL_POR_RESPONSABLE = {
    "medico": "MEDICO",
    "enfermeria": "ENFERMERIA",
    "admision": "ADMISION",
}


async def _crear_setup(
    session: AsyncSession,
    dni: str = "30111222",
    nombre_cama: str = "H-01",
) -> tuple[CamaGestion, InternacionLocal]:
    """Paciente + internación + cama en PROCESO_DE_ALTA. Devuelve (cama, internacion)."""
    paciente = PacienteLocal(dni=dni, nombre="Ana", apellido="Gómez")
    session.add(paciente)
    await session.commit()
    internacion = InternacionLocal(
        paciente_local_id=paciente.id, categoria=CategoriaInternacion.CLINICA,
    )
    session.add(internacion)
    await session.commit()
    cama = CamaGestion(
        nombre=nombre_cama,
        tipo=TipoCama.CAMA_INTERNACION,
        sector="Clínica",
        estado_gestion=EstadoCamaGestion.PROCESO_DE_ALTA,
        internacion_actual_id=internacion.id,
    )
    session.add(cama)
    await session.commit()
    return cama, internacion


async def _crear_egreso(
    client: AsyncClient, internacion_id: uuid.UUID, medio: str = "camina"
) -> dict:
    r = await client.post(
        f"/internaciones/{internacion_id}/egreso",
        json={"medio_egreso": medio, "rol": "MEDICO"},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _marcar_todos_items(client: AsyncClient, egreso: dict) -> None:
    egreso_id = egreso["id"]
    for item in egreso["items_checklist"]:
        rol = _ROL_POR_RESPONSABLE.get(item["responsable"], "MEDICO")
        r = await client.patch(
            f"/egresos/{egreso_id}/checklist/{item['id']}",
            json={"rol": rol},
        )
        assert r.status_code == 200, r.text


async def _flujo_hasta_egreso_admin(
    client: AsyncClient, internacion_id: uuid.UUID, medio: str = "camina"
) -> dict:
    egreso = await _crear_egreso(client, internacion_id, medio)
    egreso_id = egreso["id"]
    det = (await client.get(f"/egresos/{egreso_id}")).json()
    await _marcar_todos_items(client, det)
    r = await client.patch(
        f"/egresos/{egreso_id}/egreso-admin", json={"rol": "ADMISION"},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ #
# Errores mapeados
# ------------------------------------------------------------------ #

async def test_crear_egreso_internacion_inexistente_404(client: AsyncClient):
    r = await client.post(
        f"/internaciones/{uuid.uuid4()}/egreso",
        json={"medio_egreso": "camina", "rol": "MEDICO"},
    )
    assert r.status_code == 404


async def test_crear_egreso_sin_cama_asignada_409(client: AsyncClient, session: AsyncSession):
    paciente = PacienteLocal(dni="11111111", nombre="X", apellido="Y")
    session.add(paciente)
    await session.commit()
    internacion = InternacionLocal(
        paciente_local_id=paciente.id, categoria=CategoriaInternacion.CLINICA,
    )
    session.add(internacion)
    await session.commit()
    r = await client.post(
        f"/internaciones/{internacion.id}/egreso",
        json={"medio_egreso": "camina", "rol": "MEDICO"},
    )
    assert r.status_code == 409


async def test_crear_egreso_medio_invalido_422(client: AsyncClient, session: AsyncSession):
    _, internacion = await _crear_setup(session)
    r = await client.post(
        f"/internaciones/{internacion.id}/egreso",
        json={"medio_egreso": "teletransporte", "rol": "MEDICO"},
    )
    assert r.status_code == 422


async def test_crear_egreso_duplicado_409(client: AsyncClient, session: AsyncSession):
    _, internacion = await _crear_setup(session)
    await _crear_egreso(client, internacion.id)
    r = await client.post(
        f"/internaciones/{internacion.id}/egreso",
        json={"medio_egreso": "camina", "rol": "MEDICO"},
    )
    assert r.status_code == 409


async def test_get_egreso_inexistente_404(client: AsyncClient):
    r = await client.get(f"/egresos/{uuid.uuid4()}")
    assert r.status_code == 404


async def test_egreso_admin_sin_legales_409_con_items_pendientes(
    client: AsyncClient, session: AsyncSession
):
    _, internacion = await _crear_setup(session)
    egreso = await _crear_egreso(client, internacion.id)
    r = await client.patch(
        f"/egresos/{egreso['id']}/egreso-admin", json={"rol": "ADMISION"},
    )
    assert r.status_code == 409
    data = r.json()
    assert "items_pendientes" in data
    assert len(data["items_pendientes"]) > 0


async def test_salida_fisica_sin_ok_admin_409(client: AsyncClient, session: AsyncSession):
    _, internacion = await _crear_setup(session)
    egreso = await _crear_egreso(client, internacion.id)
    r = await client.patch(
        f"/egresos/{egreso['id']}/salida-fisica", json={"rol": "ENFERMERIA"},
    )
    assert r.status_code == 409


async def test_marcar_item_repetido_409(client: AsyncClient, session: AsyncSession):
    _, internacion = await _crear_setup(session)
    egreso = await _crear_egreso(client, internacion.id)
    det = (await client.get(f"/egresos/{egreso['id']}")).json()
    item = det["items_checklist"][0]
    rol = _ROL_POR_RESPONSABLE.get(item["responsable"], "MEDICO")
    r = await client.patch(
        f"/egresos/{egreso['id']}/checklist/{item['id']}", json={"rol": rol},
    )
    assert r.status_code == 200
    r2 = await client.patch(
        f"/egresos/{egreso['id']}/checklist/{item['id']}", json={"rol": rol},
    )
    assert r2.status_code == 409


async def test_discrepancia_motivo_invalido_422(client: AsyncClient, session: AsyncSession):
    _, internacion = await _crear_setup(session)
    egreso = await _crear_egreso(client, internacion.id)
    r = await client.patch(
        f"/egresos/{egreso['id']}/discrepancia",
        json={"motivo": "motivo_inexistente", "rol": "ADMISION"},
    )
    assert r.status_code == 422


async def test_nota_tipo_invalido_422(client: AsyncClient, session: AsyncSession):
    _, internacion = await _crear_setup(session)
    egreso = await _crear_egreso(client, internacion.id)
    r = await client.post(
        f"/egresos/{egreso['id']}/notas",
        json={"tipo": "queja", "texto": "texto", "rol": "ADMISION"},
    )
    assert r.status_code == 422


# ------------------------------------------------------------------ #
# GET con responsable_actual en tres momentos del flujo
# ------------------------------------------------------------------ #

async def test_get_responsable_actual_en_tres_momentos(
    client: AsyncClient, session: AsyncSession
):
    _, internacion = await _crear_setup(session)
    egreso = await _crear_egreso(client, internacion.id)
    egreso_id = egreso["id"]

    # Momento 1: checklist pendiente → responsable es algún rol del catálogo
    r = await client.get(f"/egresos/{egreso_id}")
    assert r.status_code == 200
    resp_1 = r.json()["responsable_actual"]
    assert resp_1 is not None
    assert resp_1["rol"] in ("medico", "enfermeria", "admision")

    # Momento 2: todos los items marcados, sin ok admin → admision para cerrar
    det = r.json()
    await _marcar_todos_items(client, det)
    r2 = await client.get(f"/egresos/{egreso_id}")
    resp_2 = r2.json()["responsable_actual"]
    assert resp_2 is not None
    assert resp_2["rol"] == "admision"

    # Momento 3: ok admin dado → enfermería para salida física (medio=camina)
    await client.patch(f"/egresos/{egreso_id}/egreso-admin", json={"rol": "ADMISION"})
    r3 = await client.get(f"/egresos/{egreso_id}")
    resp_3 = r3.json()["responsable_actual"]
    assert resp_3 is not None
    assert resp_3["rol"] == "enfermeria"


# ------------------------------------------------------------------ #
# E2E camino feliz — 'camina'
# ------------------------------------------------------------------ #

async def test_e2e_egreso_camina(client: AsyncClient, session: AsyncSession):
    cama, internacion = await _crear_setup(session)

    # Crear egreso
    egreso = await _crear_egreso(client, internacion.id, "camina")
    egreso_id = egreso["id"]
    assert egreso["estado"] == "info"
    assert egreso["medio_egreso"] == "camina"

    # Items del catálogo instanciados
    det = (await client.get(f"/egresos/{egreso_id}")).json()
    assert len(det["items_checklist"]) == len(CATALOGO_CHECKLIST_EGRESO["camina"])

    # Marcar todos
    await _marcar_todos_items(client, det)

    # OK administrativo
    r = await client.patch(
        f"/egresos/{egreso_id}/egreso-admin",
        json={"rol": "ADMISION", "actor_nombre": "María Admisión"},
    )
    assert r.status_code == 200
    assert r.json()["estado"] == "egreso_admin"
    assert r.json()["egreso_admin_at"] is not None

    # Salida física (ENFERMERIA — deuda cerrada por commit 1)
    r = await client.patch(
        f"/egresos/{egreso_id}/salida-fisica", json={"rol": "ENFERMERIA"},
    )
    assert r.status_code == 200
    assert r.json()["salida_fisica_at"] is not None

    # Cama en LIMPIEZA_TERMINAL
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.LIMPIEZA_TERMINAL

    # Items de limpieza creados
    det2 = (await client.get(f"/egresos/{egreso_id}")).json()
    assert len(det2["limpieza_checklist"]) == 2

    # Marcar limpieza
    for item in det2["limpieza_checklist"]:
        r = await client.patch(
            f"/egresos/{egreso_id}/limpieza/{item['id']}",
            json={"rol": "LIMPIEZA"},
        )
        assert r.status_code == 200
        assert r.json()["liberacion_bloqueada"] is None

    # Estado final
    det3 = (await client.get(f"/egresos/{egreso_id}")).json()
    assert det3["estado"] == "liberado"
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.DISPONIBLE


# ------------------------------------------------------------------ #
# E2E — 'defuncion' con metadata de cochería en salida física
# ------------------------------------------------------------------ #

async def test_e2e_egreso_defuncion_metadata_cocheria_llega_al_hito(
    client: AsyncClient, session: AsyncSession
):
    _, internacion = await _crear_setup(session)

    egreso = await _crear_egreso(client, internacion.id, "defuncion")
    egreso_id = egreso["id"]

    # Marcar todos los items + OK admin
    det = (await client.get(f"/egresos/{egreso_id}")).json()
    await _marcar_todos_items(client, det)
    await client.patch(f"/egresos/{egreso_id}/egreso-admin", json={"rol": "ADMISION"})

    # Salida física con metadata de cochería
    metadata_cocheria = {"cocheria": "Cochería San Martín", "hora_retiro": "14:30"}
    r = await client.patch(
        f"/egresos/{egreso_id}/salida-fisica",
        json={"rol": "ENFERMERIA", "metadata": metadata_cocheria},
    )
    assert r.status_code == 200

    # Verificar que la metadata llegó al hito ATLAS_SALIDA_FISICA
    hito = (
        await session.execute(
            select(HitoAtlas).where(HitoAtlas.hito_codigo == "ATLAS_SALIDA_FISICA")
        )
    ).scalar_one()
    assert hito.metadata_evento["cocheria"] == "Cochería San Martín"
    assert hito.metadata_evento["hora_retiro"] == "14:30"
    assert hito.actor_rol == "ENFERMERIA"


# ------------------------------------------------------------------ #
# liberacion_bloqueada por mantenimiento
# ------------------------------------------------------------------ #

async def test_limpieza_bloqueada_por_mantenimiento(
    client: AsyncClient, session: AsyncSession
):
    _, internacion = await _crear_setup(session)
    egreso_data = await _flujo_hasta_egreso_admin(client, internacion.id)
    egreso_id = egreso_data["id"]

    # Marcar mantenimiento_requerido=True directo en DB
    egreso_row = await session.get(Egreso, uuid.UUID(egreso_id))
    egreso_row.mantenimiento_requerido = True
    await session.commit()

    # Salida física
    await client.patch(f"/egresos/{egreso_id}/salida-fisica", json={"rol": "ENFERMERIA"})

    # Marcar items de limpieza — el último debe devolver liberacion_bloqueada
    det = (await client.get(f"/egresos/{egreso_id}")).json()
    items_limpieza = det["limpieza_checklist"]
    assert len(items_limpieza) > 0

    for i, item in enumerate(items_limpieza):
        r = await client.patch(
            f"/egresos/{egreso_id}/limpieza/{item['id']}",
            json={"rol": "LIMPIEZA"},
        )
        assert r.status_code == 200
        if i < len(items_limpieza) - 1:
            assert r.json()["liberacion_bloqueada"] is None
        else:
            assert r.json()["liberacion_bloqueada"] == "mantenimiento_pendiente"
            assert r.json()["done"] is True

    # El egreso NO pasó a liberado (cama sigue en LIMPIEZA_TERMINAL)
    det_final = (await client.get(f"/egresos/{egreso_id}")).json()
    assert det_final["estado"] != "liberado"


# ------------------------------------------------------------------ #
# GET /internaciones/{id}/egreso-activo
# ------------------------------------------------------------------ #

async def test_get_egreso_activo_ok(client: AsyncClient, session: AsyncSession):
    _, internacion = await _crear_setup(session)
    egreso = await _crear_egreso(client, internacion.id)

    r = await client.get(f"/internaciones/{internacion.id}/egreso-activo")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == egreso["id"]
    assert data["estado"] == "info"
    assert "items_checklist" in data
    assert "responsable_actual" in data


async def test_get_egreso_activo_sin_egreso_404(client: AsyncClient, session: AsyncSession):
    _, internacion = await _crear_setup(session)

    r = await client.get(f"/internaciones/{internacion.id}/egreso-activo")
    assert r.status_code == 404


async def test_get_egreso_activo_liberado_no_cuenta_404(
    client: AsyncClient, session: AsyncSession
):
    _, internacion = await _crear_setup(session)
    egreso_data = await _flujo_hasta_egreso_admin(client, internacion.id)
    egreso_id = egreso_data["id"]

    # Salida física → cama a LIMPIEZA_TERMINAL
    await client.patch(f"/egresos/{egreso_id}/salida-fisica", json={"rol": "ENFERMERIA"})

    # Marcar todos los ítems de limpieza → egreso pasa a 'liberado'
    det = (await client.get(f"/egresos/{egreso_id}")).json()
    for item in det["limpieza_checklist"]:
        await client.patch(
            f"/egresos/{egreso_id}/limpieza/{item['id']}",
            json={"rol": "LIMPIEZA"},
        )

    # Egreso liberado no debe aparecer como activo
    r = await client.get(f"/internaciones/{internacion.id}/egreso-activo")
    assert r.status_code == 404
