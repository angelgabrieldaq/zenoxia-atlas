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

_DATABASE_URL = os.environ["DATABASE_URL_TEST"]
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
    sector: str = "Clínica",
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
        sector=sector,
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

    # Marcar limpieza: EJECUCION=LIMPIEZA, SUPERVISION=HOTELERIA (frontera contractual)
    items_limpieza = det2["limpieza_checklist"]
    ejecucion = next(i for i in items_limpieza if i["codigo"] == "EJECUCION")
    supervision = next(i for i in items_limpieza if i["codigo"] == "SUPERVISION")
    r = await client.patch(
        f"/egresos/{egreso_id}/limpieza/{ejecucion['id']}",
        json={"rol": "LIMPIEZA"},
    )
    assert r.status_code == 200
    assert r.json()["liberacion_bloqueada"] is None
    r = await client.patch(
        f"/egresos/{egreso_id}/limpieza/{supervision['id']}",
        json={"rol": "HOTELERIA"},
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

    # Marcar items de limpieza — el de supervisión (HOTELERIA) dispara el bloqueo
    det = (await client.get(f"/egresos/{egreso_id}")).json()
    items_limpieza = det["limpieza_checklist"]
    assert len(items_limpieza) == 2

    ejecucion = next(i for i in items_limpieza if i["codigo"] == "EJECUCION")
    supervision = next(i for i in items_limpieza if i["codigo"] == "SUPERVISION")
    r = await client.patch(
        f"/egresos/{egreso_id}/limpieza/{ejecucion['id']}",
        json={"rol": "LIMPIEZA"},
    )
    assert r.status_code == 200
    assert r.json()["liberacion_bloqueada"] is None
    r = await client.patch(
        f"/egresos/{egreso_id}/limpieza/{supervision['id']}",
        json={"rol": "HOTELERIA"},
    )
    assert r.status_code == 200
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
    items_limp = det["limpieza_checklist"]
    ejecucion = next(i for i in items_limp if i["codigo"] == "EJECUCION")
    supervision = next(i for i in items_limp if i["codigo"] == "SUPERVISION")
    await client.patch(
        f"/egresos/{egreso_id}/limpieza/{ejecucion['id']}",
        json={"rol": "LIMPIEZA"},
    )
    await client.patch(
        f"/egresos/{egreso_id}/limpieza/{supervision['id']}",
        json={"rol": "HOTELERIA"},
    )

    # Egreso liberado no debe aparecer como activo
    r = await client.get(f"/internaciones/{internacion.id}/egreso-activo")
    assert r.status_code == 404


# ------------------------------------------------------------------ #
# PASO 1 — Seguridad: rol incorrecto debe ser rechazado con 403
# ------------------------------------------------------------------ #

async def test_checklist_item_rol_incorrecto_403(
    client: AsyncClient, session: AsyncSession
):
    """MEDICO no puede marcar un item cuyo responsable es 'admision'."""
    _, internacion = await _crear_setup(session)
    # "camina" tiene un item de 'admision': "Verificación administrativa y cobertura"
    egreso = await _crear_egreso(client, internacion.id, "camina")
    det = (await client.get(f"/egresos/{egreso['id']}")).json()

    item_admision = next(
        (i for i in det["items_checklist"] if i["responsable"] == "admision"),
        None,
    )
    assert item_admision is not None, "No se encontró item de admision en el catálogo"

    r = await client.patch(
        f"/egresos/{egreso['id']}/checklist/{item_admision['id']}",
        json={"rol": "MEDICO"},
    )
    assert r.status_code == 403


async def test_ok_admin_rol_medico_403(client: AsyncClient, session: AsyncSession):
    """Solo ADMISION puede dar el OK administrativo; MEDICO debe ser rechazado con 403."""
    _, internacion = await _crear_setup(session)
    egreso = await _crear_egreso(client, internacion.id)
    det = (await client.get(f"/egresos/{egreso['id']}")).json()
    await _marcar_todos_items(client, det)

    r = await client.patch(
        f"/egresos/{egreso['id']}/egreso-admin",
        json={"rol": "MEDICO"},
    )
    assert r.status_code == 403


async def test_limpieza_item_rol_medico_403(client: AsyncClient, session: AsyncSession):
    """MEDICO no puede marcar items de limpieza terminal; debe ser rechazado con 403."""
    _, internacion = await _crear_setup(session)
    egreso_data = await _flujo_hasta_egreso_admin(client, internacion.id)
    egreso_id = egreso_data["id"]

    await client.patch(f"/egresos/{egreso_id}/salida-fisica", json={"rol": "ENFERMERIA"})

    det = (await client.get(f"/egresos/{egreso_id}")).json()
    item_limpieza = det["limpieza_checklist"][0]

    r = await client.patch(
        f"/egresos/{egreso_id}/limpieza/{item_limpieza['id']}",
        json={"rol": "MEDICO"},
    )
    assert r.status_code == 403


# ------------------------------------------------------------------ #
# Override de ADMISION con discrepancia
# ------------------------------------------------------------------ #

async def test_admision_override_checklist_sin_discrepancia_403(
    client: AsyncClient, session: AsyncSession
):
    """ADMISION sin discrepancia no puede marcar item de otro responsable."""
    _, internacion = await _crear_setup(session)
    egreso = await _crear_egreso(client, internacion.id, "camina")
    det = (await client.get(f"/egresos/{egreso['id']}")).json()

    item_medico = next(i for i in det["items_checklist"] if i["responsable"] == "medico")

    r = await client.patch(
        f"/egresos/{egreso['id']}/checklist/{item_medico['id']}",
        json={"rol": "ADMISION"},  # sin discrepancia
    )
    assert r.status_code == 403


async def test_admision_override_checklist_con_discrepancia_200(
    client: AsyncClient, session: AsyncSession
):
    """ADMISION con discrepancia válida puede marcar item de otro responsable.
    POST-condición: GET egreso muestra item done con autor ADMISION + discrepancia registrada."""
    _, internacion = await _crear_setup(session)
    egreso = await _crear_egreso(client, internacion.id, "camina")
    egreso_id = egreso["id"]
    det = (await client.get(f"/egresos/{egreso_id}")).json()

    item_medico = next(i for i in det["items_checklist"] if i["responsable"] == "medico")

    r = await client.patch(
        f"/egresos/{egreso_id}/checklist/{item_medico['id']}",
        json={
            "rol": "ADMISION",
            "actor_nombre": "María Admisión",
            "discrepancia": {"motivo": "demora_responsable", "nota": "Médico de guardia demorado"},
        },
    )
    assert r.status_code == 200
    assert r.json()["done"] is True
    assert r.json()["autor"] == "María Admisión"

    # Verificar que la discrepancia fue persistida
    det2 = (await client.get(f"/egresos/{egreso_id}")).json()
    item_actualizado = next(i for i in det2["items_checklist"] if i["id"] == item_medico["id"])
    assert item_actualizado["done"] is True
    assert item_actualizado["autor"] == "María Admisión"
    assert len(det2["discrepancias"]) == 1
    assert det2["discrepancias"][0]["motivo"] == "demora_responsable"


async def test_admision_override_limpieza_con_discrepancia_200(
    client: AsyncClient, session: AsyncSession
):
    """ADMISION con discrepancia puede marcar item de limpieza terminal (item EJECUCION)."""
    _, internacion = await _crear_setup(session)
    egreso_data = await _flujo_hasta_egreso_admin(client, internacion.id)
    egreso_id = egreso_data["id"]

    await client.patch(f"/egresos/{egreso_id}/salida-fisica", json={"rol": "ENFERMERIA"})

    det = (await client.get(f"/egresos/{egreso_id}")).json()
    ejecucion = next(i for i in det["limpieza_checklist"] if i["codigo"] == "EJECUCION")

    r = await client.patch(
        f"/egresos/{egreso_id}/limpieza/{ejecucion['id']}",
        json={
            "rol": "ADMISION",
            "discrepancia": {"motivo": "demora_responsable", "nota": "Personal de limpieza no disponible"},
        },
    )
    assert r.status_code == 200
    assert r.json()["done"] is True


# ------------------------------------------------------------------ #
# Doble OK — roles diferenciados por ítem (frontera contractual §3)
# ------------------------------------------------------------------ #

async def test_limpieza_marca_supervision_403(client: AsyncClient, session: AsyncSession):
    """LIMPIEZA no puede marcar el ítem de supervisión — ese ítem es de control
    institucional (HOTELERIA). Frontera contractual: tercerizada ejecuta,
    institución controla."""
    _, internacion = await _crear_setup(session)
    egreso_data = await _flujo_hasta_egreso_admin(client, internacion.id)
    egreso_id = egreso_data["id"]

    await client.patch(f"/egresos/{egreso_id}/salida-fisica", json={"rol": "ENFERMERIA"})
    det = (await client.get(f"/egresos/{egreso_id}")).json()
    supervision = next(i for i in det["limpieza_checklist"] if i["codigo"] == "SUPERVISION")

    r = await client.patch(
        f"/egresos/{egreso_id}/limpieza/{supervision['id']}",
        json={"rol": "LIMPIEZA"},
    )
    assert r.status_code == 403


async def test_hoteleria_marca_supervision_con_ejecucion_done_200(
    client: AsyncClient, session: AsyncSession
):
    """HOTELERIA puede marcar SUPERVISION cuando EJECUCION ya está done."""
    _, internacion = await _crear_setup(session)
    egreso_data = await _flujo_hasta_egreso_admin(client, internacion.id)
    egreso_id = egreso_data["id"]

    await client.patch(f"/egresos/{egreso_id}/salida-fisica", json={"rol": "ENFERMERIA"})
    det = (await client.get(f"/egresos/{egreso_id}")).json()
    ejecucion = next(i for i in det["limpieza_checklist"] if i["codigo"] == "EJECUCION")
    supervision = next(i for i in det["limpieza_checklist"] if i["codigo"] == "SUPERVISION")

    await client.patch(
        f"/egresos/{egreso_id}/limpieza/{ejecucion['id']}",
        json={"rol": "LIMPIEZA"},
    )

    r = await client.patch(
        f"/egresos/{egreso_id}/limpieza/{supervision['id']}",
        json={"rol": "HOTELERIA"},
    )
    assert r.status_code == 200
    assert r.json()["done"] is True


async def test_hoteleria_marca_supervision_sin_ejecucion_409(
    client: AsyncClient, session: AsyncSession
):
    """HOTELERIA no puede marcar SUPERVISION si EJECUCION está pendiente.
    No se puede supervisar una limpieza que no terminó."""
    _, internacion = await _crear_setup(session)
    egreso_data = await _flujo_hasta_egreso_admin(client, internacion.id)
    egreso_id = egreso_data["id"]

    await client.patch(f"/egresos/{egreso_id}/salida-fisica", json={"rol": "ENFERMERIA"})
    det = (await client.get(f"/egresos/{egreso_id}")).json()
    supervision = next(i for i in det["limpieza_checklist"] if i["codigo"] == "SUPERVISION")

    r = await client.patch(
        f"/egresos/{egreso_id}/limpieza/{supervision['id']}",
        json={"rol": "HOTELERIA"},
    )
    assert r.status_code == 409


async def test_admision_marca_supervision_con_discrepancia_sin_ejecucion_200(
    client: AsyncClient, session: AsyncSession
):
    """ADMISION override con discrepancia puede marcar SUPERVISION aunque EJECUCION
    esté pendiente (urgencia operativa — la discrepancia documenta el motivo)."""
    _, internacion = await _crear_setup(session)
    egreso_data = await _flujo_hasta_egreso_admin(client, internacion.id)
    egreso_id = egreso_data["id"]

    await client.patch(f"/egresos/{egreso_id}/salida-fisica", json={"rol": "ENFERMERIA"})
    det = (await client.get(f"/egresos/{egreso_id}")).json()
    supervision = next(i for i in det["limpieza_checklist"] if i["codigo"] == "SUPERVISION")

    r = await client.patch(
        f"/egresos/{egreso_id}/limpieza/{supervision['id']}",
        json={
            "rol": "ADMISION",
            "discrepancia": {
                "motivo": "demora_responsable",
                "nota": "Urgencia de cama — se libera sin supervisión de hotelería",
            },
        },
    )
    assert r.status_code == 200
    assert r.json()["done"] is True


# ------------------------------------------------------------------ #
# Orden de traslado — ítem legal con datos logísticos (§4 relevamiento)
# ------------------------------------------------------------------ #

_DATOS_TRASLADO_VALIDOS = {
    "destino_tipo": "sanatorio",
    "destino_direccion": "Calle Falsa 123",
    "prestador": "Swiss Medical",
    "medico_a_bordo": True,
    "acompanante": False,
    "oxigeno": True,
    "accesibilidad_destino": "ascensor",
    "internacion_domiciliaria": "no",
}

_DATOS_TRASLADO_DOMICILIO_DESCONOCIDO = {
    "destino_tipo": "domicilio",
    "destino_direccion": "Calle Ficticia 456",
    "prestador": "OSDE",
    "medico_a_bordo": False,
    "acompanante": True,
    "oxigeno": False,
    "accesibilidad_destino": "escaleras",
    "internacion_domiciliaria": "desconocido",
}

_DATOS_TRASLADO_DOMICILIO_SI = {**_DATOS_TRASLADO_DOMICILIO_DESCONOCIDO, "internacion_domiciliaria": "si"}


async def _flujo_hasta_egreso_admin_ambulancia(
    client: AsyncClient, session: AsyncSession, datos_traslado: dict | None = None
) -> dict:
    """Flujo egreso admin para medio ambulancia, manejando el ítem de orden de traslado."""
    _, internacion = await _crear_setup(session, dni="40000001")
    egreso = await _crear_egreso(client, internacion.id, "ambulancia")
    egreso_id = egreso["id"]
    det = (await client.get(f"/egresos/{egreso_id}")).json()

    for item in det["items_checklist"]:
        rol = _ROL_POR_RESPONSABLE.get(item["responsable"], "MEDICO")
        body: dict = {"rol": rol}
        if item["label"] == "Orden de traslado emitida por el médico":
            body["datos_traslado"] = datos_traslado or _DATOS_TRASLADO_VALIDOS
        r = await client.patch(
            f"/egresos/{egreso_id}/checklist/{item['id']}", json=body,
        )
        assert r.status_code == 200, f"Fallo en item '{item['label']}': {r.text}"

    r = await client.patch(
        f"/egresos/{egreso_id}/egreso-admin", json={"rol": "ADMISION"},
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_marcar_orden_traslado_sin_datos_422(
    client: AsyncClient, session: AsyncSession
):
    """Marcar el ítem de orden sin datos_traslado debe retornar 422."""
    _, internacion = await _crear_setup(session)
    egreso = await _crear_egreso(client, internacion.id, "ambulancia")
    det = (await client.get(f"/egresos/{egreso['id']}")).json()

    item_orden = next(
        i for i in det["items_checklist"]
        if i["label"] == "Orden de traslado emitida por el médico"
    )
    r = await client.patch(
        f"/egresos/{egreso['id']}/checklist/{item_orden['id']}",
        json={"rol": "MEDICO"},  # sin datos_traslado
    )
    assert r.status_code == 422
    assert "datos" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_marcar_orden_traslado_con_datos_200_y_persistido(
    client: AsyncClient, session: AsyncSession
):
    """Marcar el ítem de orden con datos_traslado válidos → 200 y datos persistidos en egreso."""
    _, internacion = await _crear_setup(session)
    egreso = await _crear_egreso(client, internacion.id, "ambulancia")
    egreso_id = egreso["id"]
    det = (await client.get(f"/egresos/{egreso_id}")).json()

    item_orden = next(
        i for i in det["items_checklist"]
        if i["label"] == "Orden de traslado emitida por el médico"
    )
    r = await client.patch(
        f"/egresos/{egreso_id}/checklist/{item_orden['id']}",
        json={"rol": "MEDICO", "datos_traslado": _DATOS_TRASLADO_VALIDOS},
    )
    assert r.status_code == 200
    assert r.json()["done"] is True

    # Verificar que datos_traslado quedó en el egreso
    det2 = (await client.get(f"/egresos/{egreso_id}")).json()
    dt = det2["datos_traslado"]
    assert dt is not None
    assert dt["destino_tipo"] == "sanatorio"
    assert dt["oxigeno"] is True
    assert dt["prestador"] == "Swiss Medical"


@pytest.mark.asyncio
async def test_ok_admin_ambulancia_domicilio_desconocido_409(
    client: AsyncClient, session: AsyncSession
):
    """OK admin con ambulancia + destino=domicilio + internacion_domiciliaria=desconocido → 409."""
    _, internacion = await _crear_setup(session, dni="40000002")
    egreso = await _crear_egreso(client, internacion.id, "ambulancia")
    egreso_id = egreso["id"]
    det = (await client.get(f"/egresos/{egreso_id}")).json()

    for item in det["items_checklist"]:
        rol = _ROL_POR_RESPONSABLE.get(item["responsable"], "MEDICO")
        body: dict = {"rol": rol}
        if item["label"] == "Orden de traslado emitida por el médico":
            body["datos_traslado"] = _DATOS_TRASLADO_DOMICILIO_DESCONOCIDO
        r = await client.patch(
            f"/egresos/{egreso_id}/checklist/{item['id']}", json=body,
        )
        assert r.status_code == 200, f"Fallo en '{item['label']}': {r.text}"

    r = await client.patch(
        f"/egresos/{egreso_id}/egreso-admin", json={"rol": "ADMISION"},
    )
    assert r.status_code == 409
    assert "domiciliaria" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_ok_admin_ambulancia_domicilio_si_200(
    client: AsyncClient, session: AsyncSession
):
    """OK admin con ambulancia + destino=domicilio + internacion_domiciliaria=si → 200."""
    _, internacion = await _crear_setup(session, dni="40000003")
    egreso = await _crear_egreso(client, internacion.id, "ambulancia")
    egreso_id = egreso["id"]
    det = (await client.get(f"/egresos/{egreso_id}")).json()

    for item in det["items_checklist"]:
        rol = _ROL_POR_RESPONSABLE.get(item["responsable"], "MEDICO")
        body: dict = {"rol": rol}
        if item["label"] == "Orden de traslado emitida por el médico":
            body["datos_traslado"] = _DATOS_TRASLADO_DOMICILIO_SI
        r = await client.patch(
            f"/egresos/{egreso_id}/checklist/{item['id']}", json=body,
        )
        assert r.status_code == 200, f"Fallo en '{item['label']}': {r.text}"

    r = await client.patch(
        f"/egresos/{egreso_id}/egreso-admin", json={"rol": "ADMISION"},
    )
    assert r.status_code == 200
    assert r.json()["estado"] == "egreso_admin"


@pytest.mark.asyncio
async def test_egreso_camina_no_tiene_item_orden(
    client: AsyncClient, session: AsyncSession
):
    """El medio 'camina' no genera ítem de orden de traslado en el checklist."""
    _, internacion = await _crear_setup(session, dni="40000004")
    egreso = await _crear_egreso(client, internacion.id, "camina")
    det = (await client.get(f"/egresos/{egreso['id']}")).json()

    labels = [i["label"] for i in det["items_checklist"]]
    assert "Orden de traslado emitida por el médico" not in labels


@pytest.mark.asyncio
async def test_patch_datos_traslado_200_y_persistido(
    client: AsyncClient, session: AsyncSession
):
    """PATCH /egresos/{id}/datos-traslado permite actualizar datos post-marcado."""
    _, internacion = await _crear_setup(session, dni="40000005")
    egreso = await _crear_egreso(client, internacion.id, "ambulancia")
    egreso_id = egreso["id"]

    datos_nuevos = {**_DATOS_TRASLADO_VALIDOS, "destino_direccion": "Dirección Demo 789"}
    r = await client.patch(
        f"/egresos/{egreso_id}/datos-traslado",
        json={"rol": "ADMISION", "datos_traslado": datos_nuevos},
    )
    assert r.status_code == 200
    assert r.json()["datos_traslado"]["destino_direccion"] == "Dirección Demo 789"
    assert r.json()["datos_traslado"]["prestador"] == "Swiss Medical"


# ------------------------------------------------------------------ #
# GET /egresos — lista del día
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_lista_egresos_del_dia(client: AsyncClient, session: AsyncSession):
    """Dos egresos creados hoy aparecen en la lista y tienen la estructura correcta."""
    _, internacion1 = await _crear_setup(session, dni="50000001", nombre_cama="L-01")
    _, internacion2 = await _crear_setup(session, dni="50000002", nombre_cama="L-02")
    await _crear_egreso(client, internacion1.id)
    await _crear_egreso(client, internacion2.id)

    r = await client.get("/egresos")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2

    item = data[0]
    assert "id" in item
    assert "internacion" in item
    assert "cama_codigo" in item["internacion"]
    assert "cama_sector" in item["internacion"]
    assert "paciente_nombre" in item["internacion"]
    assert "responsable_actual" in item
    assert "medio_egreso" in item
    assert item["estado"] == "info"


@pytest.mark.asyncio
async def test_lista_egresos_filtro_activos_y_liberados(
    client: AsyncClient, session: AsyncSession
):
    """estado=activos y estado=liberados filtran correctamente."""
    _, internacion = await _crear_setup(session, dni="50000003")
    egreso_data = await _flujo_hasta_egreso_admin(client, internacion.id)
    egreso_id = egreso_data["id"]

    await client.patch(f"/egresos/{egreso_id}/salida-fisica", json={"rol": "ENFERMERIA"})
    det = (await client.get(f"/egresos/{egreso_id}")).json()
    ejecucion = next(i for i in det["limpieza_checklist"] if i["codigo"] == "EJECUCION")
    supervision = next(i for i in det["limpieza_checklist"] if i["codigo"] == "SUPERVISION")
    await client.patch(f"/egresos/{egreso_id}/limpieza/{ejecucion['id']}", json={"rol": "LIMPIEZA"})
    await client.patch(f"/egresos/{egreso_id}/limpieza/{supervision['id']}", json={"rol": "HOTELERIA"})

    r_activos = await client.get("/egresos?estado=activos")
    assert r_activos.status_code == 200
    assert len(r_activos.json()) == 0

    r_liberados = await client.get("/egresos?estado=liberados")
    assert r_liberados.status_code == 200
    assert len(r_liberados.json()) == 1
    assert r_liberados.json()[0]["estado"] == "liberado"


@pytest.mark.asyncio
async def test_lista_egresos_fecha_pasada_devuelve_vacio(
    client: AsyncClient, session: AsyncSession
):
    """Filtro por fecha de ayer no devuelve egresos creados hoy."""
    from datetime import date, timedelta

    _, internacion = await _crear_setup(session, dni="50000004")
    await _crear_egreso(client, internacion.id)

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = await client.get(f"/egresos?fecha={yesterday}")
    assert r.status_code == 200
    assert len(r.json()) == 0


@pytest.mark.asyncio
async def test_lista_egresos_orden_trabados_primero(
    client: AsyncClient, session: AsyncSession
):
    """Egreso con trabado_desde aparece antes que egreso sin bloqueo."""
    from datetime import datetime, timedelta, timezone

    _, internacion1 = await _crear_setup(session, dni="50000010", nombre_cama="T-01")
    _, internacion2 = await _crear_setup(session, dni="50000011", nombre_cama="T-02")
    e1_data = await _crear_egreso(client, internacion1.id)
    e2_data = await _crear_egreso(client, internacion2.id)

    # Marcar e2 como trabado directo en DB
    e2 = await session.get(Egreso, uuid.UUID(e2_data["id"]))
    e2.trabado_desde = datetime.now(timezone.utc) - timedelta(hours=2)
    await session.commit()

    r = await client.get("/egresos")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert data[0]["id"] == e2_data["id"], "trabado debe ser primero"
    assert data[0]["minutos_trabado"] is not None
    assert data[1]["id"] == e1_data["id"]
    assert data[1]["minutos_trabado"] is None


# ------------------------------------------------------------------ #
# GET /egresos/pendientes — cola por rol
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_pendientes_rol_invalido_422(client: AsyncClient):
    """Rol desconocido retorna 422 con mensaje descriptivo."""
    r = await client.get("/egresos/pendientes?rol=FANTASMA")
    assert r.status_code == 422
    assert "rol" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_pendientes_rol_medico_aparece_solo_para_su_rol(
    client: AsyncClient, session: AsyncSession
):
    """Egreso con responsable MEDICO aparece en cola MEDICO, no en ENFERMERIA."""
    _, internacion = await _crear_setup(session, dni="50000005")
    await _crear_egreso(client, internacion.id, "camina")

    r = await client.get("/egresos/pendientes?rol=MEDICO")
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    assert all("egreso_id" in p and "tarea" in p and "cama" in p for p in data)

    # ENFERMERIA no tiene trabajo (médico tiene la pelota)
    r2 = await client.get("/egresos/pendientes?rol=ENFERMERIA")
    assert r2.status_code == 200
    assert len(r2.json()) == 0


@pytest.mark.asyncio
async def test_pendientes_filtro_sector(client: AsyncClient, session: AsyncSession):
    """sector= filtra la cola por sector de la cama."""
    _, internacion1 = await _crear_setup(session, dni="50000006", nombre_cama="C-01")

    paciente2 = PacienteLocal(dni="50000007", nombre="Carlos", apellido="Ruiz")
    session.add(paciente2)
    await session.commit()
    internacion2 = InternacionLocal(
        paciente_local_id=paciente2.id, categoria=CategoriaInternacion.CLINICA,
    )
    session.add(internacion2)
    await session.commit()
    cama_uti = CamaGestion(
        nombre="UTI-01",
        tipo=TipoCama.CAMA_INTERNACION,
        sector="UTI",
        estado_gestion=EstadoCamaGestion.PROCESO_DE_ALTA,
        internacion_actual_id=internacion2.id,
    )
    session.add(cama_uti)
    await session.commit()

    await _crear_egreso(client, internacion1.id)
    await _crear_egreso(client, internacion2.id)

    r_total = await client.get("/egresos/pendientes", params={"rol": "MEDICO"})
    assert r_total.status_code == 200
    assert len(r_total.json()) == 2

    r_clinica = await client.get(
        "/egresos/pendientes", params={"rol": "MEDICO", "sector": "Clínica"}
    )
    assert r_clinica.status_code == 200
    assert len(r_clinica.json()) == 1
    assert r_clinica.json()[0]["sector"] == "Clínica"

    r_uti = await client.get(
        "/egresos/pendientes", params={"rol": "MEDICO", "sector": "UTI"}
    )
    assert r_uti.status_code == 200
    assert len(r_uti.json()) == 1
    assert r_uti.json()[0]["sector"] == "UTI"


@pytest.mark.asyncio
async def test_pendientes_limpieza_ejecucion_pendiente(
    client: AsyncClient, session: AsyncSession
):
    """Post-salida física: EJECUCION pendiente → aparece en cola LIMPIEZA con item_id."""
    _, internacion = await _crear_setup(session, dni="50000008")
    egreso_data = await _flujo_hasta_egreso_admin(client, internacion.id)
    egreso_id = egreso_data["id"]
    await client.patch(f"/egresos/{egreso_id}/salida-fisica", json={"rol": "ENFERMERIA"})

    r = await client.get("/egresos/pendientes?rol=LIMPIEZA")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["egreso_id"] == egreso_id
    assert data[0]["item_codigo"] == "EJECUCION"
    assert data[0]["item_id"] is not None


@pytest.mark.asyncio
async def test_pendientes_hoteleria_supervision_tras_ejecucion(
    client: AsyncClient, session: AsyncSession
):
    """EJECUCION done, SUPERVISION pending → HOTELERIA la ve; LIMPIEZA no."""
    _, internacion = await _crear_setup(session, dni="50000009")
    egreso_data = await _flujo_hasta_egreso_admin(client, internacion.id)
    egreso_id = egreso_data["id"]
    await client.patch(f"/egresos/{egreso_id}/salida-fisica", json={"rol": "ENFERMERIA"})

    det = (await client.get(f"/egresos/{egreso_id}")).json()
    ejecucion = next(i for i in det["limpieza_checklist"] if i["codigo"] == "EJECUCION")
    await client.patch(
        f"/egresos/{egreso_id}/limpieza/{ejecucion['id']}",
        json={"rol": "LIMPIEZA"},
    )

    r_limpieza = await client.get("/egresos/pendientes?rol=LIMPIEZA")
    assert r_limpieza.status_code == 200
    assert len(r_limpieza.json()) == 0

    r_hoteleria = await client.get("/egresos/pendientes?rol=HOTELERIA")
    assert r_hoteleria.status_code == 200
    data = r_hoteleria.json()
    assert len(data) == 1
    assert data[0]["egreso_id"] == egreso_id
    assert data[0]["item_codigo"] == "SUPERVISION"
    assert data[0]["item_id"] is not None


@pytest.mark.asyncio
async def test_pendientes_medico_ordenados_por_sector(
    client: AsyncClient, session: AsyncSession
):
    """La cola del médico sale ordenada por sector ASC (ronda por ubicación).

    Crea 3 egresos 'camina' en sectores desordenados alfabéticamente; cada uno
    deja ítems de médico sin marcar → MEDICO es el responsable de los 3.
    """
    setups = [
        ("30100001", "U-01", "UTI"),
        ("30100002", "C-01", "Cardiología"),
        ("30100003", "P-01", "Pediatría"),
    ]
    for dni, cama_nombre, sector in setups:
        _, internacion = await _crear_setup(
            session, dni=dni, nombre_cama=cama_nombre, sector=sector
        )
        await _crear_egreso(client, internacion.id, medio="camina")

    r = await client.get("/egresos/pendientes?rol=MEDICO")
    assert r.status_code == 200, r.text
    sectores = [item["sector"] for item in r.json()]

    # Debe venir ordenado alfabéticamente, no en orden de inserción
    assert sectores == sorted(sectores), f"esperado orden ASC, recibido: {sectores}"
    assert sectores == ["Cardiología", "Pediatría", "UTI"]
