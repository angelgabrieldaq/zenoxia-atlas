"""Tests de integración del ServicioReservas (capa 1a, §7), sobre Postgres real.

Verifican el ciclo de vida de la Reserva (ACTIVA → CUMPLIDA / CANCELADA) coordinado con
la transición de la cama vía ServicioTransiciones (B2): la validación dura de tipo de
cama, y que crear/cumplir/cancelar dejen el estado de cama + reserva + hitos consistentes.
Cada test queda aislado truncando las tablas (la fixture `session`).
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

from database.enums import (
    CategoriaInternacion,
    EstadoCamaGestion,
    EstadoReserva,
    MotivoReserva,
    TipoCama,
)
from database.models import (
    CamaGestion,
    HitoAtlas,
    InternacionLocal,
    PacienteLocal,
    Reserva,
)
from domain.state_machine import RolOperativo
from domain.reservation_service import ReservaTipoInvalido, ServicioReservas

load_dotenv()
DATABASE_URL = os.getenv(
    "DATABASE_URL_TEST", "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas_test"
)

# Incluye reserva. CASCADE resuelve las FK; alembic_version queda.
_TABLAS = ("hito_atlas", "reserva", "cama_gestion", "internacion_local", "paciente_local")
_TRUNCATE = f"TRUNCATE {', '.join(_TABLAS)} RESTART IDENTITY CASCADE"


@pytest_asyncio.fixture
async def session():
    """AsyncSession contra la base real, aislada por test (truncate antes y después)."""
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
    return ServicioReservas()


# --- helpers de armado ---

async def _crear_internacion(
    session: AsyncSession, dni: str = "30111222"
) -> InternacionLocal:
    paciente = PacienteLocal(dni=dni, nombre="Ana", apellido="Gómez")
    session.add(paciente)
    await session.commit()
    internacion = InternacionLocal(
        paciente_local_id=paciente.id, categoria=CategoriaInternacion.QUIRURGICA_PROGRAMADA
    )
    session.add(internacion)
    await session.commit()
    return internacion


async def _crear_cama(
    session: AsyncSession,
    tipo: TipoCama = TipoCama.UTI,
    estado: EstadoCamaGestion = EstadoCamaGestion.DISPONIBLE,
    nombre: str = "UTI-03",
) -> CamaGestion:
    cama = CamaGestion(nombre=nombre, tipo=tipo, sector="UTI", estado_gestion=estado)
    session.add(cama)
    await session.commit()
    return cama


async def _contar(session: AsyncSession, modelo) -> int:
    return (await session.execute(select(func.count()).select_from(modelo))).scalar_one()


# --------------------------------------------------------------------------- #
# 1. crear_reserva con tipo correcto: Reserva ACTIVA + cama RESERVADA + hito.
# --------------------------------------------------------------------------- #
async def test_crear_reserva_tipo_correcto(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, tipo=TipoCama.UTI)

    reserva = await servicio.crear_reserva(
        session, cama, internacion,
        MotivoReserva.QUIRURGICA, TipoCama.UTI, RolOperativo.ADMISION,
    )

    await session.refresh(cama)
    await session.refresh(reserva)
    # la reserva
    assert reserva.estado == EstadoReserva.ACTIVA
    assert reserva.motivo == MotivoReserva.QUIRURGICA
    assert reserva.tipo_cama_requerido == TipoCama.UTI
    assert reserva.cama_gestion_id == cama.id
    assert reserva.internacion_id == internacion.id
    # la cama (vía B2)
    assert cama.estado_gestion == EstadoCamaGestion.RESERVADA
    assert cama.internacion_actual_id == internacion.id
    # el hito de la transición
    hitos = (await session.execute(select(HitoAtlas))).scalars().all()
    assert len(hitos) == 1
    assert hitos[0].hito_codigo == "ATLAS_CAMA_RESERVADA"


# --------------------------------------------------------------------------- #
# 2. crear_reserva con tipo equivocado: ReservaTipoInvalido y SIN efectos.
# --------------------------------------------------------------------------- #
async def test_crear_reserva_tipo_equivocado_sin_efectos(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, tipo=TipoCama.UTI)  # cama UTI...

    with pytest.raises(ReservaTipoInvalido):
        # ...pero la reserva exige UCO
        await servicio.crear_reserva(
            session, cama, internacion,
            MotivoReserva.QUIRURGICA, TipoCama.UCO, RolOperativo.ADMISION,
        )

    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.DISPONIBLE  # intacta
    assert cama.internacion_actual_id is None
    assert await _contar(session, Reserva) == 0   # no se creó reserva
    assert await _contar(session, HitoAtlas) == 0  # ni hito


# --------------------------------------------------------------------------- #
# 3. cumplir_reserva: Reserva CUMPLIDA y cama OCUPADA con la internación re-vinculada.
# --------------------------------------------------------------------------- #
async def test_cumplir_reserva_ocupa_cama(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, tipo=TipoCama.UTI)
    reserva = await servicio.crear_reserva(
        session, cama, internacion,
        MotivoReserva.INGRESO_PROGRAMADO, TipoCama.UTI, RolOperativo.ADMISION,
    )

    await servicio.cumplir_reserva(session, reserva, RolOperativo.ENFERMERIA)

    await session.refresh(cama)
    await session.refresh(reserva)
    assert reserva.estado == EstadoReserva.CUMPLIDA
    assert reserva.resuelta_at is not None
    assert cama.estado_gestion == EstadoCamaGestion.OCUPADA
    assert cama.internacion_actual_id == internacion.id


# --------------------------------------------------------------------------- #
# 4. cancelar_reserva con motivo: CANCELADA (guarda motivo) y cama DISPONIBLE.
# --------------------------------------------------------------------------- #
async def test_cancelar_reserva_con_motivo_libera_cama(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, tipo=TipoCama.UTI)
    reserva = await servicio.crear_reserva(
        session, cama, internacion,
        MotivoReserva.QUIRURGICA, TipoCama.UTI, RolOperativo.ADMISION,
    )

    await servicio.cancelar_reserva(
        session, reserva, "el paciente no se presentó", RolOperativo.ADMISION
    )

    await session.refresh(cama)
    await session.refresh(reserva)
    assert reserva.estado == EstadoReserva.CANCELADA
    assert reserva.motivo_cancelacion == "el paciente no se presentó"
    assert reserva.resuelta_at is not None
    assert cama.estado_gestion == EstadoCamaGestion.DISPONIBLE
    assert cama.internacion_actual_id is None


# --------------------------------------------------------------------------- #
# 5. cancelar_reserva sin motivo: error (obligatorio) y SIN efectos.
# --------------------------------------------------------------------------- #
async def test_cancelar_reserva_sin_motivo_sin_efectos(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, tipo=TipoCama.UTI)
    reserva = await servicio.crear_reserva(
        session, cama, internacion,
        MotivoReserva.QUIRURGICA, TipoCama.UTI, RolOperativo.ADMISION,
    )

    with pytest.raises(ValueError):
        await servicio.cancelar_reserva(session, reserva, "   ", RolOperativo.ADMISION)

    await session.refresh(cama)
    await session.refresh(reserva)
    assert reserva.estado == EstadoReserva.ACTIVA          # intacta
    assert reserva.motivo_cancelacion is None
    assert cama.estado_gestion == EstadoCamaGestion.RESERVADA  # intacta
    assert cama.internacion_actual_id == internacion.id


# --------------------------------------------------------------------------- #
# 6. Flujo completo: crear → cumplir (cama ocupada) y crear → cancelar (cama liberada).
# --------------------------------------------------------------------------- #
async def test_flujo_completo_crear_cumplir_y_crear_cancelar(session, servicio):
    # Camino A: crear → cumplir
    int_a = await _crear_internacion(session, dni="40000001")
    cama_a = await _crear_cama(session, tipo=TipoCama.UCO, nombre="UCO-01")
    res_a = await servicio.crear_reserva(
        session, cama_a, int_a,
        MotivoReserva.QUIRURGICA, TipoCama.UCO, RolOperativo.ADMISION,
    )
    await session.refresh(cama_a)
    assert cama_a.estado_gestion == EstadoCamaGestion.RESERVADA

    await servicio.cumplir_reserva(session, res_a, RolOperativo.ENFERMERIA)
    await session.refresh(cama_a)
    await session.refresh(res_a)
    assert res_a.estado == EstadoReserva.CUMPLIDA
    assert cama_a.estado_gestion == EstadoCamaGestion.OCUPADA
    assert cama_a.internacion_actual_id == int_a.id

    # Camino B: crear → cancelar
    int_b = await _crear_internacion(session, dni="40000002")
    cama_b = await _crear_cama(session, tipo=TipoCama.UCO, nombre="UCO-02")
    res_b = await servicio.crear_reserva(
        session, cama_b, int_b,
        MotivoReserva.QUIRURGICA, TipoCama.UCO, RolOperativo.ADMISION,
    )
    await servicio.cancelar_reserva(
        session, res_b, "se suspendió la cirugía", RolOperativo.ADMISION
    )
    await session.refresh(cama_b)
    await session.refresh(res_b)
    assert res_b.estado == EstadoReserva.CANCELADA
    assert cama_b.estado_gestion == EstadoCamaGestion.DISPONIBLE
    assert cama_b.internacion_actual_id is None
