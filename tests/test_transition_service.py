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
from domain.transition_service import (
    ReversionSinInternacion,
    RolNoAutorizado,
    ServicioTransiciones,
)

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
# 4. ocupar setea internacion_actual_id; iniciar_alta lo mantiene; el ALTA FÍSICA
#    libera (None); finalizar_limpieza sigue en None.
# --------------------------------------------------------------------------- #
async def test_ocupar_setea_iniciar_alta_mantiene_alta_fisica_libera(
    session, servicio
):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, estado=EstadoCamaGestion.DISPONIBLE)

    # ocupar: set
    await servicio.ocupar(session, cama, internacion, RolOperativo.ADMISION)
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.OCUPADA
    assert cama.internacion_actual_id == internacion.id

    # iniciar_alta: mantiene (el paciente sigue mientras corre la cadena de pre-alta)
    await servicio.iniciar_alta(session, cama, RolOperativo.MEDICO)
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.PROCESO_DE_ALTA
    assert cama.internacion_actual_id == internacion.id

    # dar_alta_fisica: LIBERA (el alta física saca al paciente; la cama queda sin él)
    await servicio.dar_alta_fisica(session, cama, RolOperativo.ADMISION)
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.LIMPIEZA_TERMINAL
    assert cama.internacion_actual_id is None

    # finalizar_limpieza: sigue sin paciente
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
# 7. OCUPADA → BLOQUEADA ahora LIBERA: bloquear una cama ocupada desvincula al
#    paciente (queda None); el hito conserva la traza de a quién se desplazó.
# --------------------------------------------------------------------------- #
async def test_bloquear_cama_ocupada_libera_internacion(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(
        session,
        estado=EstadoCamaGestion.OCUPADA,
        internacion_id=internacion.id,
    )

    hito = await servicio.bloquear(
        session, cama, RolOperativo.MANTENIMIENTO, motivo="caño roto"
    )

    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.BLOQUEADA
    assert cama.internacion_actual_id is None                       # LIBERA
    assert cama.motivo_bloqueo == "caño roto"
    assert hito.hito_codigo == "ATLAS_CAMA_BLOQUEADA"
    # el hito guarda al paciente desplazado (columna + metadata)
    assert hito.internacion_id == internacion.id
    assert hito.metadata_evento["internacion_id"] == str(internacion.id)


# --------------------------------------------------------------------------- #
# 8. Reversión tardía CON internación: re-vincula la internación pasada.
# --------------------------------------------------------------------------- #
async def test_revertir_alta_tardia_con_internacion_revincula(session, servicio):
    internacion = await _crear_internacion(session)
    # cama en limpieza SIN paciente (estado consistente tras el alta física)
    cama = await _crear_cama(session, estado=EstadoCamaGestion.LIMPIEZA_TERMINAL)
    assert cama.internacion_actual_id is None

    hito = await servicio.revertir_alta_tardia(
        session,
        cama,
        RolOperativo.ADMISION,
        motivo_reversion="el paciente nunca egresó",
        internacion=internacion,
        limpieza_ya_ejecutada=True,
    )

    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.OCUPADA
    assert cama.internacion_actual_id == internacion.id             # RE-ASIGNA
    assert hito.hito_codigo == "ATLAS_ALTA_REVERTIDA"
    assert hito.metadata_evento["motivo_reversion"] == "el paciente nunca egresó"
    assert hito.metadata_evento["limpieza_ya_ejecutada"] is True
    assert hito.metadata_evento["internacion_id"] == str(internacion.id)


# --------------------------------------------------------------------------- #
# 9. Flujo completo: ocupar → iniciar_alta → dar_alta_fisica (libera) →
#    revertir_alta_tardia() SIN parámetro recupera al paciente del hito de alta y
#    re-vincula. La cama vuelve a OCUPADA con la internación ORIGINAL.
# --------------------------------------------------------------------------- #
async def test_flujo_completo_reversion_tardia_recupera_del_hito(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, estado=EstadoCamaGestion.DISPONIBLE)

    await servicio.ocupar(session, cama, internacion, RolOperativo.ADMISION)
    await servicio.iniciar_alta(session, cama, RolOperativo.MEDICO)
    await servicio.dar_alta_fisica(session, cama, RolOperativo.ADMISION)
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.LIMPIEZA_TERMINAL
    assert cama.internacion_actual_id is None  # el alta física lo desvinculó

    # revertir SIN pasar internación → se recupera del hito de alta física
    hito = await servicio.revertir_alta_tardia(
        session, cama, RolOperativo.ADMISION, motivo_reversion="volvió el paciente"
    )

    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.OCUPADA
    assert cama.internacion_actual_id == internacion.id  # MISMO paciente, recuperado
    assert hito.hito_codigo == "ATLAS_ALTA_REVERTIDA"
    assert hito.metadata_evento["internacion_id"] == str(internacion.id)
    assert hito.metadata_evento["limpieza_ya_ejecutada"] is False


# --------------------------------------------------------------------------- #
# 10. Reversión tardía SIN internación y SIN hito de alta previo: error claro.
# --------------------------------------------------------------------------- #
async def test_revertir_alta_tardia_sin_internacion_ni_hito_lanza(session, servicio):
    # cama en limpieza pero sin ningún hito de alta del cual recuperar al paciente
    cama = await _crear_cama(session, estado=EstadoCamaGestion.LIMPIEZA_TERMINAL)

    with pytest.raises(ReversionSinInternacion):
        await servicio.revertir_alta_tardia(
            session, cama, RolOperativo.ADMISION, motivo_reversion="sin paciente"
        )

    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.LIMPIEZA_TERMINAL
    assert cama.internacion_actual_id is None
    assert await _contar_hitos(session) == 0


# --------------------------------------------------------------------------- #
# 11. La reversión exige motivo_reversion (obligatorio, §11) y no deja efectos.
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


# --------------------------------------------------------------------------- #
# 12. commit=False: aplica y deja los cambios VISIBLES en la sesión (vía flush),
#     pero NO commitea — un rollback posterior los descarta por completo.
# --------------------------------------------------------------------------- #
async def test_commit_false_visible_pero_rollback_descarta(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, estado=EstadoCamaGestion.DISPONIBLE)
    cama_id = cama.id

    hito = await servicio.ejecutar_transicion(
        session, cama, EstadoCamaGestion.OCUPADA, RolOperativo.ADMISION,
        internacion=internacion, commit=False,
    )

    # Visible dentro de la transacción (flush): estado, vínculo y hito.
    assert hito.hito_codigo == "ATLAS_CAMA_OCUPADA"
    assert cama.estado_gestion == EstadoCamaGestion.OCUPADA
    assert cama.internacion_actual_id == internacion.id
    assert await _contar_hitos(session) == 1  # flusheado, visible en la transacción

    # Pero NO commiteado: un rollback lo descarta todo.
    await session.rollback()
    cama_db = await session.get(CamaGestion, cama_id)
    assert cama_db.estado_gestion == EstadoCamaGestion.DISPONIBLE
    assert cama_db.internacion_actual_id is None
    assert await _contar_hitos(session) == 0


# --------------------------------------------------------------------------- #
# 13. Orquestación: dos transiciones con commit=False encadenadas y un ÚNICO commit
#     externo → ambas persisten atómicamente (el caso de uso del refactor).
# --------------------------------------------------------------------------- #
async def test_commit_false_orquesta_dos_transiciones_atomicas(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama(session, estado=EstadoCamaGestion.DISPONIBLE)
    cama_id = cama.id

    # El "orquestador" encadena dos transiciones sin commitear cada una; la segunda
    # ve el estado de la primera (flusheado en la sesión).
    await servicio.ocupar(session, cama, internacion, RolOperativo.ADMISION, commit=False)
    await servicio.iniciar_alta(session, cama, RolOperativo.MEDICO, commit=False)

    # Recién acá cierra la transacción, una sola vez.
    await session.commit()

    cama_db = await session.get(CamaGestion, cama_id)
    assert cama_db.estado_gestion == EstadoCamaGestion.PROCESO_DE_ALTA
    assert cama_db.internacion_actual_id == internacion.id
    assert await _contar_hitos(session) == 2  # los dos hitos, en la misma transacción
