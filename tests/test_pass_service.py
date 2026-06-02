"""Tests de integración del ServicioPases (capa 1a, §8), sobre Postgres real.

Verifican la orquestación del pase: solicitud, asignación de cama destino (con reserva +
hito ISBAR), confirmación ATÓMICA sobre dos camas (destino ocupada + origen a limpieza),
y cancelación. Incluye la prueba clave de atomicidad: si la liberación de la origen falla,
no queda NADA (la destino tampoco ocupada). Cada test queda aislado truncando las tablas.
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
    EstadoPase,
    EstadoReserva,
    TipoCama,
)
from database.models import (
    CamaGestion,
    HitoAtlas,
    InternacionLocal,
    PaseServicio,
    PacienteLocal,
    Reserva,
)
from domain.state_machine import RolOperativo
from domain.pass_service import PaseTipoInvalido, ServicioPases

load_dotenv()
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas"
)

_TABLAS = (
    "hito_atlas", "pase_servicio", "reserva",
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
    return ServicioPases()


# --- helpers ---

async def _crear_internacion(session: AsyncSession, dni: str = "30111222") -> InternacionLocal:
    paciente = PacienteLocal(dni=dni, nombre="Ana", apellido="Gómez")
    session.add(paciente)
    await session.commit()
    internacion = InternacionLocal(
        paciente_local_id=paciente.id, categoria=CategoriaInternacion.CRITICA
    )
    session.add(internacion)
    await session.commit()
    return internacion


async def _crear_cama(
    session: AsyncSession,
    tipo: TipoCama = TipoCama.UTI,
    estado: EstadoCamaGestion = EstadoCamaGestion.DISPONIBLE,
    internacion_id=None,
    nombre: str = "CAMA",
) -> CamaGestion:
    cama = CamaGestion(
        nombre=nombre, tipo=tipo, sector="UTI",
        estado_gestion=estado, internacion_actual_id=internacion_id,
    )
    session.add(cama)
    await session.commit()
    return cama


async def _contar(session: AsyncSession, modelo) -> int:
    return (await session.execute(select(func.count()).select_from(modelo))).scalar_one()


async def _hito_existe(session: AsyncSession, codigo: str) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(HitoAtlas).where(HitoAtlas.hito_codigo == codigo)
        )
    ).scalar_one()


async def _origen_y_destino(session):
    """internación + cama origen OCUPADA (con el paciente) + cama destino UTI DISPONIBLE."""
    internacion = await _crear_internacion(session)
    origen = await _crear_cama(
        session, tipo=TipoCama.UTI, estado=EstadoCamaGestion.OCUPADA,
        internacion_id=internacion.id, nombre="UTI-ORIGEN",
    )
    destino = await _crear_cama(
        session, tipo=TipoCama.UTI, estado=EstadoCamaGestion.DISPONIBLE, nombre="UTI-DESTINO",
    )
    return internacion, origen, destino


# --------------------------------------------------------------------------- #
# 1. solicitar_pase: crea PaseServicio SOLICITADO (sin cama destino aún).
# --------------------------------------------------------------------------- #
async def test_solicitar_pase_crea_solicitado(session, servicio):
    internacion, origen, _ = await _origen_y_destino(session)

    pase = await servicio.solicitar_pase(
        session, internacion, origen, TipoCama.UTI, RolOperativo.MEDICO
    )

    await session.refresh(pase)
    assert pase.estado == EstadoPase.SOLICITADO
    assert pase.cama_origen_id == origen.id
    assert pase.cama_destino_id is None
    assert pase.reserva_id is None
    assert pase.tipo_cama_destino == TipoCama.UTI
    assert await _hito_existe(session, "ATLAS_PASE_SOLICITADO") == 1


# --------------------------------------------------------------------------- #
# 2. asignar_cama tipo correcto: Reserva creada, destino RESERVADA, pase
#    CAMA_ASIGNADA, hito ISBAR registrado.
# --------------------------------------------------------------------------- #
async def test_asignar_cama_tipo_correcto(session, servicio):
    internacion, origen, destino = await _origen_y_destino(session)
    pase = await servicio.solicitar_pase(
        session, internacion, origen, TipoCama.UTI, RolOperativo.MEDICO
    )

    await servicio.asignar_cama(session, pase, destino, RolOperativo.ADMISION)

    await session.refresh(pase)
    await session.refresh(destino)
    assert pase.estado == EstadoPase.CAMA_ASIGNADA
    assert pase.cama_destino_id == destino.id
    assert pase.reserva_id is not None
    # la reserva existe y está ACTIVA sobre la destino
    reserva = await session.get(Reserva, pase.reserva_id)
    assert reserva.estado == EstadoReserva.ACTIVA
    assert reserva.cama_gestion_id == destino.id
    # destino quedó RESERVADA (vía B2)
    assert destino.estado_gestion == EstadoCamaGestion.RESERVADA
    assert destino.internacion_actual_id == internacion.id
    # hito ISBAR
    assert await _hito_existe(session, "ATLAS_PASE_ISBAR_REGISTRADO") == 1


# --------------------------------------------------------------------------- #
# 3. asignar_cama tipo equivocado: PaseTipoInvalido y SIN efectos.
# --------------------------------------------------------------------------- #
async def test_asignar_cama_tipo_equivocado_sin_efectos(session, servicio):
    internacion = await _crear_internacion(session)
    origen = await _crear_cama(
        session, tipo=TipoCama.UTI, estado=EstadoCamaGestion.OCUPADA,
        internacion_id=internacion.id, nombre="UTI-ORIGEN",
    )
    destino_uco = await _crear_cama(
        session, tipo=TipoCama.UCO, estado=EstadoCamaGestion.DISPONIBLE, nombre="UCO-1",
    )
    pase = await servicio.solicitar_pase(
        session, internacion, origen, TipoCama.UTI, RolOperativo.MEDICO  # requiere UTI
    )

    with pytest.raises(PaseTipoInvalido):
        await servicio.asignar_cama(session, pase, destino_uco, RolOperativo.ADMISION)

    await session.refresh(pase)
    await session.refresh(destino_uco)
    assert pase.estado == EstadoPase.SOLICITADO          # intacto
    assert pase.reserva_id is None
    assert destino_uco.estado_gestion == EstadoCamaGestion.DISPONIBLE  # intacta
    assert await _contar(session, Reserva) == 0


# --------------------------------------------------------------------------- #
# 4. confirmar_pase: destino OCUPADA con la internación + origen en PROCESO_DE_ALTA,
#    reserva CUMPLIDA, pase CONFIRMADO.
# --------------------------------------------------------------------------- #
async def test_confirmar_pase_ocupa_destino_y_libera_origen(session, servicio):
    internacion, origen, destino = await _origen_y_destino(session)
    pase = await servicio.solicitar_pase(session, internacion, origen, TipoCama.UTI, RolOperativo.MEDICO)
    await servicio.asignar_cama(session, pase, destino, RolOperativo.ADMISION)
    await servicio.iniciar_traslado(session, pase, RolOperativo.ENFERMERIA)

    await servicio.confirmar_pase(session, pase, RolOperativo.ENFERMERIA)

    await session.refresh(pase)
    await session.refresh(destino)
    await session.refresh(origen)
    assert pase.estado == EstadoPase.CONFIRMADO
    assert pase.confirmado_at is not None
    # destino: OCUPADA con la internación
    assert destino.estado_gestion == EstadoCamaGestion.OCUPADA
    assert destino.internacion_actual_id == internacion.id
    # origen: al camino de limpieza (NO disponible directo)
    assert origen.estado_gestion == EstadoCamaGestion.PROCESO_DE_ALTA
    # la reserva quedó CUMPLIDA
    reserva = await session.get(Reserva, pase.reserva_id)
    assert reserva.estado == EstadoReserva.CUMPLIDA


# --------------------------------------------------------------------------- #
# 5. confirmar_pase ATOMICIDAD: si la liberación de la ORIGEN falla, NADA persiste
#    (la destino NO queda ocupada; reserva y pase intactos). Rollback conjunto.
# --------------------------------------------------------------------------- #
async def test_confirmar_pase_atomicidad_rollback_conjunto(session, servicio):
    internacion, origen, destino = await _origen_y_destino(session)
    pase = await servicio.solicitar_pase(session, internacion, origen, TipoCama.UTI, RolOperativo.MEDICO)
    await servicio.asignar_cama(session, pase, destino, RolOperativo.ADMISION)
    await servicio.iniciar_traslado(session, pase, RolOperativo.ENFERMERIA)
    destino_id, origen_id, pase_id, reserva_id = (
        destino.id, origen.id, pase.id, pase.reserva_id
    )

    # Sabotaje: la origen deja de estar OCUPADA, así OCUPADA→PROCESO_DE_ALTA fallará
    # DESPUÉS de que la destino ya se ocupó (flush) dentro de confirmar_pase.
    origen.estado_gestion = EstadoCamaGestion.DISPONIBLE
    await session.commit()

    with pytest.raises(Exception):
        await servicio.confirmar_pase(session, pase, RolOperativo.ENFERMERIA)

    # Rollback conjunto: la destino NO quedó ocupada; reserva y pase intactos.
    destino_db = await session.get(CamaGestion, destino_id)
    reserva_db = await session.get(Reserva, reserva_id)
    pase_db = await session.get(PaseServicio, pase_id)
    assert destino_db.estado_gestion == EstadoCamaGestion.RESERVADA   # NO ocupada
    assert reserva_db.estado == EstadoReserva.ACTIVA                  # no cumplida
    assert pase_db.estado == EstadoPase.EN_TRASLADO                   # no confirmado
    assert await _hito_existe(session, "ATLAS_PASE_CONFIRMADO") == 0


# --------------------------------------------------------------------------- #
# 6. cancelar_pase con motivo: reserva CANCELADA, destino DISPONIBLE, el paciente
#    sigue en la origen (no se la toca).
# --------------------------------------------------------------------------- #
async def test_cancelar_pase_con_motivo(session, servicio):
    internacion, origen, destino = await _origen_y_destino(session)
    pase = await servicio.solicitar_pase(session, internacion, origen, TipoCama.UTI, RolOperativo.MEDICO)
    await servicio.asignar_cama(session, pase, destino, RolOperativo.ADMISION)

    await servicio.cancelar_pase(session, pase, "cama destino se necesitó para una urgencia", RolOperativo.ADMISION)

    await session.refresh(pase)
    await session.refresh(destino)
    await session.refresh(origen)
    assert pase.estado == EstadoPase.CANCELADO
    assert pase.cancelado_at is not None
    assert pase.motivo_cancelacion == "cama destino se necesitó para una urgencia"
    reserva = await session.get(Reserva, pase.reserva_id)
    assert reserva.estado == EstadoReserva.CANCELADA
    assert destino.estado_gestion == EstadoCamaGestion.DISPONIBLE   # liberada
    # el paciente sigue en la origen, intacta
    assert origen.estado_gestion == EstadoCamaGestion.OCUPADA
    assert origen.internacion_actual_id == internacion.id


# --------------------------------------------------------------------------- #
# 7. cancelar_pase sin motivo: error (obligatorio) y SIN efectos.
# --------------------------------------------------------------------------- #
async def test_cancelar_pase_sin_motivo_sin_efectos(session, servicio):
    internacion, origen, destino = await _origen_y_destino(session)
    pase = await servicio.solicitar_pase(session, internacion, origen, TipoCama.UTI, RolOperativo.MEDICO)
    await servicio.asignar_cama(session, pase, destino, RolOperativo.ADMISION)

    with pytest.raises(ValueError):
        await servicio.cancelar_pase(session, pase, "   ", RolOperativo.ADMISION)

    await session.refresh(pase)
    await session.refresh(destino)
    assert pase.estado == EstadoPase.CAMA_ASIGNADA          # intacto
    reserva = await session.get(Reserva, pase.reserva_id)
    assert reserva.estado == EstadoReserva.ACTIVA            # intacta
    assert destino.estado_gestion == EstadoCamaGestion.RESERVADA  # intacta


# --------------------------------------------------------------------------- #
# 8. FLUJO COMPLETO end-to-end: solicitar → asignar (ISBAR) → iniciar_traslado →
#    confirmar → destino OCUPADA + origen en limpieza.
# --------------------------------------------------------------------------- #
async def test_flujo_completo_end_to_end(session, servicio):
    internacion, origen, destino = await _origen_y_destino(session)

    pase = await servicio.solicitar_pase(session, internacion, origen, TipoCama.UTI, RolOperativo.MEDICO)
    assert pase.estado == EstadoPase.SOLICITADO

    await servicio.asignar_cama(session, pase, destino, RolOperativo.ADMISION)
    await session.refresh(pase)
    await session.refresh(destino)
    assert pase.estado == EstadoPase.CAMA_ASIGNADA
    assert destino.estado_gestion == EstadoCamaGestion.RESERVADA
    assert await _hito_existe(session, "ATLAS_PASE_ISBAR_REGISTRADO") == 1

    await servicio.iniciar_traslado(session, pase, RolOperativo.ENFERMERIA)
    await session.refresh(pase)
    assert pase.estado == EstadoPase.EN_TRASLADO

    await servicio.confirmar_pase(session, pase, RolOperativo.ENFERMERIA)
    await session.refresh(pase)
    await session.refresh(destino)
    await session.refresh(origen)
    assert pase.estado == EstadoPase.CONFIRMADO
    assert destino.estado_gestion == EstadoCamaGestion.OCUPADA
    assert destino.internacion_actual_id == internacion.id
    assert origen.estado_gestion == EstadoCamaGestion.PROCESO_DE_ALTA  # camino de limpieza


# --------------------------------------------------------------------------- #
# 9. asignar_cama ATOMICIDAD: si el commit envolvente falla (después de que
#    crear_reserva con commit=False ya flusheó reserva + cama RESERVADA), el rollback
#    único descarta TODO: ni Reserva, ni cama RESERVADA, ni pase en CAMA_ASIGNADA.
# --------------------------------------------------------------------------- #
async def test_asignar_cama_atomicidad_rollback_conjunto(session, servicio, monkeypatch):
    internacion, origen, destino = await _origen_y_destino(session)
    pase = await servicio.solicitar_pase(session, internacion, origen, TipoCama.UTI, RolOperativo.MEDICO)
    destino_id, pase_id = destino.id, pase.id

    # Forzar un fallo en el commit envolvente de asignar_cama. crear_reserva(commit=False)
    # ya habrá flusheado la reserva + cama RESERVADA (sin commitear); el rollback debe
    # deshacerlo junto con el resto.
    async def _boom():
        raise RuntimeError("fallo simulado durante el commit de asignar_cama")
    monkeypatch.setattr(session, "commit", _boom)

    with pytest.raises(RuntimeError):
        await servicio.asignar_cama(session, pase, destino, RolOperativo.ADMISION)

    monkeypatch.undo()  # restaurar commit real; las verificaciones son sólo lecturas
    # Nada persistió: ni Reserva, ni cama RESERVADA, ni pase avanzado, ni hito ISBAR.
    assert await _contar(session, Reserva) == 0
    destino_db = await session.get(CamaGestion, destino_id)
    pase_db = await session.get(PaseServicio, pase_id)
    assert destino_db.estado_gestion == EstadoCamaGestion.DISPONIBLE
    assert pase_db.estado == EstadoPase.SOLICITADO
    assert pase_db.reserva_id is None
    assert pase_db.cama_destino_id is None
    assert await _hito_existe(session, "ATLAS_PASE_ISBAR_REGISTRADO") == 0
