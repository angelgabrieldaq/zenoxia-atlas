"""Tests de la API REST (FastAPI) — tablero de camas. Contra Postgres real.

Usa el AsyncClient de httpx + ASGITransport para disparar requests contra la app
sin levantar un servidor real. Cada test trunca las tablas relevantes para aislarse.
"""

import os
import uuid
from typing import AsyncGenerator

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
async def session() -> AsyncGenerator[AsyncSession, None]:
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


async def test_hitos_filtrados_por_internacion_actual(
    client: AsyncClient, session: AsyncSession
):
    """GET /camas/{id} solo devuelve hitos de la internación actual + hitos sin internación.
    Hitos de internaciones anteriores de la misma cama no deben aparecer (traza contaminada)."""
    from database.models import HitoAtlas
    from database.enums import CategoriaInternacion

    # Crear pacientes e internaciones en un solo flush para evitar UPDATE en cama
    pac1 = PacienteLocal(dni="11111111", nombre="Ana", apellido="García")
    pac2 = PacienteLocal(dni="22222222", nombre="Luis", apellido="Torres")
    session.add_all([pac1, pac2])
    await session.flush()

    internacion1 = InternacionLocal(paciente_local_id=pac1.id, categoria=CategoriaInternacion.CLINICA)
    internacion2 = InternacionLocal(paciente_local_id=pac2.id, categoria=CategoriaInternacion.CLINICA)
    session.add_all([internacion1, internacion2])
    await session.flush()

    # Cama con internacion_actual_id ya seteada desde el INSERT (evita UPDATE + onupdate)
    cama = CamaGestion(
        nombre="H-01",
        tipo=TipoCama.CAMA_INTERNACION,
        sector="Clínica",
        estado_gestion=EstadoCamaGestion.PROCESO_DE_ALTA,
        internacion_actual_id=internacion2.id,
    )
    session.add(cama)
    await session.flush()

    h_viejo = HitoAtlas(
        hito_codigo="ATLAS_EGRESO_ABIERTO",
        cama_gestion_id=cama.id,
        internacion_id=internacion1.id,
        actor_rol="MEDICO",
    )
    h_actual = HitoAtlas(
        hito_codigo="ATLAS_CAMA_PROCESO_DE_ALTA",
        cama_gestion_id=cama.id,
        internacion_id=internacion2.id,
        actor_rol="MEDICO",
    )
    h_transicion = HitoAtlas(
        hito_codigo="ATLAS_CAMA_DISPONIBLE",
        cama_gestion_id=cama.id,
        internacion_id=None,
        actor_rol="LIMPIEZA",
    )
    session.add_all([h_viejo, h_actual, h_transicion])
    await session.commit()

    r = await client.get(f"/camas/{cama.id}")
    assert r.status_code == 200
    codigos = [h["hito_codigo"] for h in r.json()["hitos"]]
    assert "ATLAS_CAMA_PROCESO_DE_ALTA" in codigos
    assert "ATLAS_CAMA_DISPONIBLE" in codigos
    assert "ATLAS_EGRESO_ABIERTO" not in codigos


async def test_crear_nota_para_cama(client: AsyncClient, session: AsyncSession):
    cama = await _crear_cama(session)
    r = await client.post(
        f"/camas/{cama.id}/notas",
        json={"texto": "Discrepancia: falta medicación", "rol": "ENFERMERIA"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["texto"] == "Discrepancia: falta medicación"
    assert data["creada_por_rol"] == "ENFERMERIA"


async def test_instanciar_y_listar_pasos_de_alta_por_internacion(
    client: AsyncClient, session: AsyncSession
):
    from database.models import PasoAltaCatalogo

    internacion = await _crear_internacion(session)
    paso = PasoAltaCatalogo(
        codigo="CHECK_01",
        nombre="Confirmar medicación",
        bloqueante=True,
        activo=True,
        orden=1,
    )
    session.add(paso)
    await session.commit()

    r = await client.post(f"/camas/internaciones/{internacion.id}/pasos/instanciar")
    assert r.status_code == 200
    pasos = r.json()
    assert len(pasos) == 1
    assert pasos[0]["codigo"] == "CHECK_01"
    assert pasos[0]["completado"] is False

    r2 = await client.get(f"/camas/internaciones/{internacion.id}/pasos")
    assert r2.status_code == 200
    pasos2 = r2.json()
    assert len(pasos2) == 1
    assert pasos2[0]["codigo"] == "CHECK_01"


async def test_completar_paso_de_checklist(client: AsyncClient, session: AsyncSession):
    from database.models import PasoAltaCatalogo

    internacion = await _crear_internacion(session)
    paso = PasoAltaCatalogo(
        codigo="CHECK_02",
        nombre="Verificar documentos",
        bloqueante=False,
        activo=True,
        orden=1,
    )
    session.add(paso)
    await session.commit()

    r = await client.post(f"/camas/internaciones/{internacion.id}/pasos/instanciar")
    assert r.status_code == 200
    paso_id = r.json()[0]["id"]

    r2 = await client.post(
        f"/camas/pasos/{paso_id}/completar",
        json={"rol": "MEDICO"},
    )
    assert r2.status_code == 200
    data = r2.json()
    assert data["completado"] is True
    assert data["completado_por_rol"] == "MEDICO"


async def test_alta_fisica_endpoint_obsoleto_devuelve_410(
    client: AsyncClient, session: AsyncSession
):
    """El endpoint viejo POST /camas/{id}/alta-fisica fue eliminado (bug: saltaba egreso y
    checklist de limpieza). Debe devolver 410 Gone. El único camino a LIMPIEZA_TERMINAL
    es PATCH /egresos/{id}/salida-fisica."""
    cama = await _crear_cama(session)
    r = await client.post(f"/camas/{cama.id}/alta-fisica", json={"rol": "ADMISION"})
    assert r.status_code == 410


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


async def test_flujo_completo_disponible_a_proceso_alta(
    client: AsyncClient, session: AsyncSession
):
    """Flujo feliz por HTTP: DISPONIBLE → OCUPADA → PROCESO_DE_ALTA.
    LIMPIEZA_TERMINAL se alcanza sólo via PATCH /egresos/{id}/salida-fisica (testeado
    en test_api_egresos.py); el endpoint /alta-fisica fue eliminado (devuelve 410)."""
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


# ------------------------------------------------------------------ #
# Cancelar reserva
# ------------------------------------------------------------------ #

async def test_cancelar_reserva_camino_feliz(
    client: AsyncClient, session: AsyncSession
):
    """Reserva standalone (sin pase): RESERVADA → DISPONIBLE, reserva CANCELADA con
    motivo_cancelacion persistido."""
    from sqlalchemy import select as _select

    from database.enums import EstadoReserva
    from database.models import Reserva

    cama = await _crear_cama(session)
    internacion = await _crear_internacion(session)

    r = await client.post(
        f"/camas/{cama.id}/reservar",
        json={
            "internacion_id": str(internacion.id),
            "tipo_cama_requerido": "CAMA_INTERNACION",
            "rol": "ADMISION",
        },
    )
    assert r.status_code == 200
    assert r.json()["estado_gestion"] == "RESERVADA"

    r = await client.post(
        f"/camas/{cama.id}/cancelar-reserva",
        json={"motivo_cancelacion": "El paciente no llegó", "rol": "ADMISION"},
    )
    assert r.status_code == 200
    assert r.json()["estado_gestion"] == "DISPONIBLE"

    reserva = (
        await session.execute(
            _select(Reserva).where(Reserva.cama_gestion_id == cama.id)
        )
    ).scalar_one()
    assert reserva.estado == EstadoReserva.CANCELADA
    assert reserva.motivo_cancelacion == "El paciente no llegó"


async def test_cancelar_reserva_guard_pase_devuelve_409(
    client: AsyncClient, session: AsyncSession
):
    """Si la reserva pertenece a un PaseServicio (cama-destino del pase), el endpoint
    rechaza con 409 y NO toca la reserva ni la cama."""
    from database.enums import EstadoCamaGestion, EstadoReserva
    from database.models import Reserva
    from domain.pass_service import ServicioPases
    from domain.state_machine import RolOperativo

    internacion = await _crear_internacion(session)
    origen = await _crear_cama(
        session, nombre="UTI-ORIG", tipo=TipoCama.UTI,
        estado=EstadoCamaGestion.OCUPADA,
    )
    origen.internacion_actual_id = internacion.id
    await session.commit()
    destino = await _crear_cama(
        session, nombre="UTI-DEST", tipo=TipoCama.UTI,
        estado=EstadoCamaGestion.DISPONIBLE,
    )

    servicio_pases = ServicioPases()
    pase = await servicio_pases.solicitar_pase(
        session, internacion, origen, TipoCama.UTI, RolOperativo.MEDICO
    )
    await servicio_pases.asignar_cama(
        session, pase, destino, RolOperativo.ADMISION
    )

    r = await client.post(
        f"/camas/{destino.id}/cancelar-reserva",
        json={"motivo_cancelacion": "intento suelto", "rol": "ADMISION"},
    )
    assert r.status_code == 409
    assert str(pase.id) in r.json()["detail"]

    # Sin efectos colaterales: destino sigue RESERVADA, reserva sigue ACTIVA.
    await session.refresh(destino)
    assert destino.estado_gestion == EstadoCamaGestion.RESERVADA
    reserva = await session.get(Reserva, pase.reserva_id)
    assert reserva is not None
    await session.refresh(reserva)
    assert reserva.estado == EstadoReserva.ACTIVA


async def test_cancelar_reserva_sin_reserva_activa_devuelve_409(
    client: AsyncClient, session: AsyncSession
):
    """Cama DISPONIBLE sin reserva activa → 409."""
    cama = await _crear_cama(session)
    r = await client.post(
        f"/camas/{cama.id}/cancelar-reserva",
        json={"motivo_cancelacion": "no aplica", "rol": "ADMISION"},
    )
    assert r.status_code == 409


# ------------------------------------------------------------------ #
# Revertir alta (temprana / tardía) + TipoReversion
# ------------------------------------------------------------------ #

async def _ocupar_cama(session: AsyncSession, cama: CamaGestion, internacion: InternacionLocal):
    cama.estado_gestion = EstadoCamaGestion.OCUPADA
    cama.internacion_actual_id = internacion.id
    await session.commit()


async def test_revertir_alta_temprana_por_error(
    client: AsyncClient, session: AsyncSession
):
    """PROCESO_DE_ALTA + ALTA_INFORMADA_POR_ERROR → OCUPADA, hito
    ATLAS_ALTA_REVERTIDA_POR_ERROR; el vínculo internación↔cama se mantiene."""
    from sqlalchemy import select as _select

    from database.models import HitoAtlas

    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, estado=EstadoCamaGestion.PROCESO_DE_ALTA)
    cama.internacion_actual_id = internacion.id
    await session.commit()

    r = await client.post(
        f"/camas/{cama.id}/revertir-alta",
        json={
            "rol": "MEDICO",
            "tipo_reversion": "ALTA_INFORMADA_POR_ERROR",
            "motivo_reversion": "El alta se cargó por error",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estado_gestion"] == "OCUPADA"
    assert body["internacion_actual_id"] == str(internacion.id)

    hito = (
        await session.execute(
            _select(HitoAtlas)
            .where(HitoAtlas.cama_gestion_id == cama.id)
            .order_by(HitoAtlas.registrado_at.desc())
        )
    ).scalars().first()
    assert hito is not None
    assert hito.hito_codigo == "ATLAS_ALTA_REVERTIDA_POR_ERROR"
    assert isinstance(hito.metadata_evento, dict)
    assert hito.metadata_evento["tipo_reversion"] == "ALTA_INFORMADA_POR_ERROR"
    assert hito.metadata_evento["motivo_reversion"] == "El alta se cargó por error"


async def test_revertir_alta_tardia_reingreso_fisico(
    client: AsyncClient, session: AsyncSession
):
    """Llegá a LIMPIEZA_TERMINAL vía transición directa (setup de test) y revertí con
    REINGRESO_FISICO. La internación se recupera del hito de alta y la cama vuelve a
    OCUPADA con el mismo paciente; hito ATLAS_REINGRESO_FISICO."""
    from sqlalchemy import select as _select

    from database.models import HitoAtlas
    from domain.state_machine import RolOperativo
    from domain.transition_service import ServicioTransiciones

    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session)
    cid = str(cama.id)

    transiciones = ServicioTransiciones()
    await client.post(
        f"/camas/{cid}/ocupar",
        json={"internacion_id": str(internacion.id), "rol": "ADMISION"},
    )
    await client.post(f"/camas/{cid}/iniciar-alta", json={"rol": "MEDICO"})
    await session.refresh(cama)
    await transiciones.dar_alta_fisica(session, cama, RolOperativo.ADMISION)
    await session.commit()

    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.LIMPIEZA_TERMINAL
    assert cama.internacion_actual_id is None  # el alta física lo desvinculó

    r = await client.post(
        f"/camas/{cid}/revertir-alta",
        json={
            "rol": "ADMISION",
            "tipo_reversion": "REINGRESO_FISICO",
            "motivo_reversion": "Paciente volvió al edificio",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estado_gestion"] == "OCUPADA"
    assert body["internacion_actual_id"] == str(internacion.id)  # re-vinculado

    hito = (
        await session.execute(
            _select(HitoAtlas)
            .where(HitoAtlas.cama_gestion_id == cama.id)
            .order_by(HitoAtlas.registrado_at.desc())
        )
    ).scalars().first()
    assert hito is not None
    assert hito.hito_codigo == "ATLAS_REINGRESO_FISICO"
    assert isinstance(hito.metadata_evento, dict)
    assert hito.metadata_evento["tipo_reversion"] == "REINGRESO_FISICO"


async def test_revertir_alta_reabre_internacion_finalizada(
    client: AsyncClient, session: AsyncSession
):
    """Si la internación tenía finalizada_at seteado, la reversión la reabre
    (finalizada_at = None) en la misma transacción."""
    from datetime import datetime, timezone

    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, estado=EstadoCamaGestion.PROCESO_DE_ALTA)
    cama.internacion_actual_id = internacion.id
    internacion.finalizada_at = datetime.now(timezone.utc)
    await session.commit()

    r = await client.post(
        f"/camas/{cama.id}/revertir-alta",
        json={
            "rol": "MEDICO",
            "tipo_reversion": "ALTA_INFORMADA_POR_ERROR",
            "motivo_reversion": "Alta por error con internación ya finalizada",
        },
    )
    assert r.status_code == 200, r.text

    await session.refresh(internacion)
    assert internacion.finalizada_at is None


async def test_revertir_alta_estado_invalido_devuelve_409(
    client: AsyncClient, session: AsyncSession
):
    """Cama DISPONIBLE u OCUPADA → 409 (sólo se revierte desde PROCESO_DE_ALTA o
    LIMPIEZA_TERMINAL)."""
    cama = await _crear_cama(session)  # DISPONIBLE
    r = await client.post(
        f"/camas/{cama.id}/revertir-alta",
        json={
            "rol": "ADMISION",
            "tipo_reversion": "ALTA_INFORMADA_POR_ERROR",
            "motivo_reversion": "no aplica",
        },
    )
    assert r.status_code == 409


async def test_revertir_alta_rol_incorrecto_devuelve_403(
    client: AsyncClient, session: AsyncSession
):
    """En PROCESO_DE_ALTA sólo MEDICO puede revertir; ADMISION → 403."""
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, estado=EstadoCamaGestion.PROCESO_DE_ALTA)
    cama.internacion_actual_id = internacion.id
    await session.commit()

    r = await client.post(
        f"/camas/{cama.id}/revertir-alta",
        json={
            "rol": "ADMISION",
            "tipo_reversion": "ALTA_INFORMADA_POR_ERROR",
            "motivo_reversion": "rol equivocado",
        },
    )
    assert r.status_code == 403


async def test_revertir_alta_motivo_vacio_devuelve_422(
    client: AsyncClient, session: AsyncSession
):
    """Pydantic rebota motivo vacío con 422 antes del endpoint (min_length=1)."""
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, estado=EstadoCamaGestion.PROCESO_DE_ALTA)
    cama.internacion_actual_id = internacion.id
    await session.commit()

    r = await client.post(
        f"/camas/{cama.id}/revertir-alta",
        json={
            "rol": "MEDICO",
            "tipo_reversion": "ALTA_INFORMADA_POR_ERROR",
            "motivo_reversion": "",
        },
    )
    assert r.status_code == 422


async def test_cancelar_reserva_motivo_vacio_devuelve_422(
    client: AsyncClient, session: AsyncSession
):
    """Pydantic rebota motivo vacío o ausente con 422 antes del endpoint."""
    cama = await _crear_cama(session)

    r = await client.post(
        f"/camas/{cama.id}/cancelar-reserva",
        json={"motivo_cancelacion": "", "rol": "ADMISION"},
    )
    assert r.status_code == 422

    r = await client.post(
        f"/camas/{cama.id}/cancelar-reserva",
        json={"rol": "ADMISION"},
    )
    assert r.status_code == 422
