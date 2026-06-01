"""Tests de integración del ServicioTransiciones (capa 1a, paso B2).

Corren contra Postgres real (tablas creadas con `alembic upgrade head`). Cada test
queda aislado truncando las tablas antes y después (la fixture `session`). Verifican
los tres efectos de una transición (estado, hito, internacion_actual_id), el rechazo
de transiciones ilegales y roles no autorizados, y la atomicidad (rollback total).
"""

import os
import uuid

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
    TipoCama,
)
from database.models import CamaGestion, HitoAtlas, InternacionLocal, PacienteLocal
from domain.state_machine import RolOperativo, TransicionInvalida
from domain.transition_service import RolNoAutorizado, ServicioTransiciones

load_dotenv()
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas"
)

# Orden no importa: CASCADE resuelve las FK entre las 4 tablas. alembic_version queda.
_TABLAS = ("hito_atlas", "cama_gestion", "internacion_local", "paciente_local")
_TRUNCATE = f"TRUNCATE {', '.join(_TABLAS)} RESTART IDENTITY CASCADE"


@pytest_asyncio.fixture
async def session():
    """AsyncSession contra la base real, aislada por test (truncate antes y después).

    NullPool: no reusar conexiones entre los event loops (uno por test) para evitar
    'attached to a different loop'.
    """
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
    return ServicioTransiciones()


# --- helpers de armado (commitean para tener ids reales) ---

async def _crear_internacion(
    session: AsyncSession,
    dni: str = "30111222",
    categoria: CategoriaInternacion = CategoriaInternacion.CLINICA,
) -> InternacionLocal:
    paciente = PacienteLocal(dni=dni, nombre="Ana", apellido="Gómez")
    session.add(paciente)
    await session.commit()
    internacion = InternacionLocal(paciente_local_id=paciente.id, categoria=categoria)
    session.add(internacion)
    await session.commit()
    return internacion


async def _crear_cama(
    session: AsyncSession,
    estado: EstadoCamaGestion = EstadoCamaGestion.DISPONIBLE,
    internacion_id: uuid.UUID | None = None,
    nombre: str = "UTI-03",
) -> CamaGestion:
    cama = CamaGestion(
        nombre=nombre,
        tipo=TipoCama.UTI,
        sector="UTI",
        estado_gestion=estado,
        internacion_actual_id=internacion_id,
    )
    session.add(cama)
    await session.commit()
    return cama


async def _contar_hitos(session: AsyncSession) -> int:
    return (
        await session.execute(select(func.count()).select_from(HitoAtlas))
    ).scalar_one()


async def _unico_hito(session: AsyncSession) -> HitoAtlas:
    return (await session.execute(select(HitoAtlas))).scalars().one()


# --------------------------------------------------------------------------- #
# 1. Transición válida + rol correcto: los TRES efectos.
# --------------------------------------------------------------------------- #
async def test_transicion_valida_aplica_estado_hito_e_internacion(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, estado=EstadoCamaGestion.DISPONIBLE)

    hito = await servicio.ejecutar_transicion(
        session,
        cama,
        EstadoCamaGestion.OCUPADA,
        RolOperativo.ADMISION,
        actor_nombre="Admisión Central",
        internacion=internacion,
    )

    # efecto 1: estado cambió (persistido — refresh re-lee de la base)
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.OCUPADA
    # efecto 2: internacion_actual_id actualizado
    assert cama.internacion_actual_id == internacion.id
    # efecto 3: hito escrito, con el código del catálogo §11 y el rol actor
    assert await _contar_hitos(session) == 1
    assert hito.hito_codigo == "ATLAS_CAMA_OCUPADA"
    assert hito.actor_rol == "ADMISION"
    assert hito.actor_nombre == "Admisión Central"
    assert hito.sincronizado_core is False


# --------------------------------------------------------------------------- #
# 2. Transición ilegal: TransicionInvalida y SIN efectos.
# --------------------------------------------------------------------------- #
async def test_transicion_ilegal_lanza_y_no_deja_efectos(session, servicio):
    cama = await _crear_cama(session, estado=EstadoCamaGestion.DISPONIBLE)

    with pytest.raises(TransicionInvalida):
        # DISPONIBLE → PROCESO_DE_ALTA no existe en la tabla §10
        await servicio.ejecutar_transicion(
            session,
            cama,
            EstadoCamaGestion.PROCESO_DE_ALTA,
            RolOperativo.MEDICO,
        )

    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.DISPONIBLE
    assert await _contar_hitos(session) == 0


# --------------------------------------------------------------------------- #
# 3. Rol no autorizado: RolNoAutorizado y SIN efectos.
# --------------------------------------------------------------------------- #
async def test_rol_no_autorizado_lanza_y_no_deja_efectos(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, estado=EstadoCamaGestion.DISPONIBLE)

    with pytest.raises(RolNoAutorizado):
        # DISPONIBLE → OCUPADA la dispara ADMISION, no ENFERMERIA
        await servicio.ejecutar_transicion(
            session,
            cama,
            EstadoCamaGestion.OCUPADA,
            RolOperativo.ENFERMERIA,
            internacion=internacion,
        )

    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.DISPONIBLE
    assert cama.internacion_actual_id is None
    assert await _contar_hitos(session) == 0


# --------------------------------------------------------------------------- #
# 4. ocupar setea internacion_actual_id; iniciar_alta lo mantiene; (alta física
#    también lo mantiene); finalizar_limpieza lo pone en None.
# --------------------------------------------------------------------------- #
async def test_ocupar_setea_iniciar_alta_mantiene_finalizar_limpieza_libera(
    session, servicio
):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, estado=EstadoCamaGestion.DISPONIBLE)

    # ocupar: set
    await servicio.ocupar(session, cama, internacion, RolOperativo.ADMISION)
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.OCUPADA
    assert cama.internacion_actual_id == internacion.id

    # iniciar_alta: mantiene
    await servicio.iniciar_alta(session, cama, RolOperativo.MEDICO)
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.PROCESO_DE_ALTA
    assert cama.internacion_actual_id == internacion.id

    # dar_alta_fisica: mantiene (el paciente sigue vinculado hasta liberar la cama)
    await servicio.dar_alta_fisica(session, cama, RolOperativo.ADMISION)
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.LIMPIEZA_TERMINAL
    assert cama.internacion_actual_id == internacion.id

    # finalizar_limpieza: libera (None)
    await servicio.finalizar_limpieza(session, cama, RolOperativo.LIMPIEZA)
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.DISPONIBLE
    assert cama.internacion_actual_id is None


# --------------------------------------------------------------------------- #
# 5. El hito estampa internacion_id y cama_gestion_id DENTRO de metadata_evento
#    (contrato del modelo HitoAtlas), además de en las columnas FK.
# --------------------------------------------------------------------------- #
async def test_hito_estampa_ids_redundantes_en_metadata(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, estado=EstadoCamaGestion.DISPONIBLE)

    await servicio.reservar(session, cama, internacion, RolOperativo.ADMISION)

    hito = await _unico_hito(session)
    # columnas FK
    assert hito.cama_gestion_id == cama.id
    assert hito.internacion_id == internacion.id
    # ids redundantes dentro del JSONB, como str (serializable)
    assert hito.metadata_evento["cama_gestion_id"] == str(cama.id)
    assert hito.metadata_evento["internacion_id"] == str(internacion.id)
    assert hito.hito_codigo == "ATLAS_CAMA_RESERVADA"


# --------------------------------------------------------------------------- #
# 6. Atomicidad: si la persistencia falla a mitad, rollback total (estado y hito
#    quedan consistentes — como si nada hubiera pasado).
# --------------------------------------------------------------------------- #
async def test_atomicidad_rollback_total_si_falla_la_persistencia(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, estado=EstadoCamaGestion.DISPONIBLE)
    cama_id = cama.id

    # Un valor no serializable a JSON revienta al escribir el JSONB del hito,
    # después de haber mutado el estado en memoria: fuerza el fallo a mitad.
    with pytest.raises(Exception):
        await servicio.ejecutar_transicion(
            session,
            cama,
            EstadoCamaGestion.OCUPADA,
            RolOperativo.ADMISION,
            internacion=internacion,
            metadata={"no_serializable": object()},
        )

    # Tras el rollback no quedó NADA: ni el cambio de estado ni el hito.
    cama_db = await session.get(CamaGestion, cama_id)
    assert cama_db.estado_gestion == EstadoCamaGestion.DISPONIBLE
    assert cama_db.internacion_actual_id is None
    assert await _contar_hitos(session) == 0


# --------------------------------------------------------------------------- #
# 7. Reversión tardía (LIMPIEZA_TERMINAL → OCUPADA): mantiene al MISMO paciente
#    y audita ATLAS_ALTA_REVERTIDA con su metadata obligatoria.
# --------------------------------------------------------------------------- #
async def test_revertir_alta_tardia_mantiene_internacion_y_audita(session, servicio):
    internacion = await _crear_internacion(session)
    # cama con alta física ya dada: en LIMPIEZA_TERMINAL pero el paciente sigue ligado
    cama = await _crear_cama(
        session,
        estado=EstadoCamaGestion.LIMPIEZA_TERMINAL,
        internacion_id=internacion.id,
    )

    hito = await servicio.revertir_alta_tardia(
        session,
        cama,
        RolOperativo.ADMISION,
        motivo_reversion="el paciente nunca egresó físicamente",
        limpieza_ya_ejecutada=True,
    )

    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.OCUPADA
    assert cama.internacion_actual_id == internacion.id  # MANTIENE
    assert hito.hito_codigo == "ATLAS_ALTA_REVERTIDA"
    assert hito.metadata_evento["motivo_reversion"] == "el paciente nunca egresó físicamente"
    assert hito.metadata_evento["limpieza_ya_ejecutada"] is True
    assert hito.metadata_evento["internacion_id"] == str(internacion.id)


# --------------------------------------------------------------------------- #
# 8. La reversión exige motivo_reversion (obligatorio, §11) y no deja efectos.
# --------------------------------------------------------------------------- #
async def test_reversion_sin_motivo_lanza_value_error(session, servicio):
    cama = await _crear_cama(session, estado=EstadoCamaGestion.PROCESO_DE_ALTA)

    with pytest.raises(ValueError):
        await servicio.revertir_alta_temprana(
            session, cama, RolOperativo.MEDICO, motivo_reversion="  "
        )

    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.PROCESO_DE_ALTA
    assert await _contar_hitos(session) == 0
