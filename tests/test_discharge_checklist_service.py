"""Tests de integración del ServicioChecklistAlta (capa 1a, §6), Postgres real.

Sub-paso 1 — pasos: instanciación según categoría (universales vs. de categoría),
idempotencia, completar un paso (con hito de auditoría), y la consulta de bloqueantes
pendientes; más la semilla del catálogo (y su idempotencia).

Sub-paso 2 — override del alta física (``dar_alta_fisica_validada``): procede sin
bloqueantes pendientes; rechaza con bloqueantes y sin override; fuerza con motivo (y deja
hito); exige el motivo al forzar; atomicidad del caso forzado (rollback conjunto); y que
los pasos NO bloqueantes pendientes no frenan el alta. Cada test queda aislado truncando
las tablas relevantes.
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

from database.enums import CategoriaInternacion, EstadoCamaGestion, TipoCama
from database.models import (
    CamaGestion,
    HitoAtlas,
    InternacionLocal,
    PacienteLocal,
    PasoAltaCatalogo,
    PasoAltaInternacion,
)
from database.seeds import PASOS_ALTA_INICIALES, seed_pasos_alta_catalogo
from domain.discharge_checklist_service import (
    AltaConPasosPendientes,
    ServicioChecklistAlta,
)
from domain.state_machine import RolOperativo

load_dotenv()
DATABASE_URL = os.getenv(
    "DATABASE_URL_TEST", "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas_test"
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


async def _crear_cama_en_proceso_alta(
    session: AsyncSession,
    internacion: InternacionLocal,
    nombre: str = "CLINICA-07",
) -> CamaGestion:
    """Cama lista para el alta física: ya en PROCESO_DE_ALTA y con la internación vinculada
    (es el estado del que parte ``dar_alta_fisica`` PROCESO_DE_ALTA → LIMPIEZA_TERMINAL)."""
    cama = CamaGestion(
        nombre=nombre,
        tipo=TipoCama.CAMA_INTERNACION,
        sector="Clínica",
        estado_gestion=EstadoCamaGestion.PROCESO_DE_ALTA,
        internacion_actual_id=internacion.id,
    )
    session.add(cama)
    await session.commit()
    return cama


async def _hitos_por_codigo(session: AsyncSession, codigo: str) -> list[HitoAtlas]:
    resultado = await session.execute(
        select(HitoAtlas).where(HitoAtlas.hito_codigo == codigo)
    )
    return list(resultado.scalars().all())


async def _completar_bloqueantes(
    session: AsyncSession, servicio: ServicioChecklistAlta, internacion: InternacionLocal
) -> None:
    """Completa TODOS los pasos bloqueantes de la internación (deja libres los no bloqueantes)."""
    for paso in await servicio.pasos_bloqueantes_pendientes(session, internacion):
        await servicio.completar_paso(session, paso, RolOperativo.MEDICO)


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


# =========================================================================== #
# Sub-paso 2: override del alta física (dar_alta_fisica_validada).
# =========================================================================== #


# --------------------------------------------------------------------------- #
# 7. SIN bloqueantes pendientes: procede, cama → LIMPIEZA_TERMINAL, sin hito de forzado.
# --------------------------------------------------------------------------- #
async def test_alta_validada_sin_bloqueantes_procede(session, servicio):
    await seed_pasos_alta_catalogo(session)
    internacion = await _crear_internacion(session, CategoriaInternacion.CLINICA)
    await servicio.instanciar_pasos(session, internacion)
    await _completar_bloqueantes(session, servicio, internacion)  # los 2 bloqueantes OK
    cama = await _crear_cama_en_proceso_alta(session, internacion)

    hito = await servicio.dar_alta_fisica_validada(
        session, cama, internacion, RolOperativo.ADMISION, actor_nombre="Admisión Central"
    )

    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.LIMPIEZA_TERMINAL
    assert cama.internacion_actual_id is None  # el alta física desvincula (B2)
    # Es la transición canónica de §11, no un alta forzada.
    assert hito.hito_codigo == "ATLAS_LIMPIEZA_INICIADA"
    assert await _hitos_por_codigo(session, "ATLAS_ALTA_FORZADA_PASOS_PENDIENTES") == []


# --------------------------------------------------------------------------- #
# 8. CON bloqueantes pendientes y forzar=False: AltaConPasosPendientes, SIN efectos.
# --------------------------------------------------------------------------- #
async def test_alta_validada_con_bloqueantes_sin_forzar_rechaza(session, servicio):
    await seed_pasos_alta_catalogo(session)
    internacion = await _crear_internacion(session, CategoriaInternacion.CLINICA)
    await servicio.instanciar_pasos(session, internacion)  # 2 bloqueantes pendientes
    cama = await _crear_cama_en_proceso_alta(session, internacion)

    with pytest.raises(AltaConPasosPendientes) as exc:
        await servicio.dar_alta_fisica_validada(
            session, cama, internacion, RolOperativo.ADMISION
        )

    # La excepción lleva los códigos bloqueantes pendientes.
    assert set(exc.value.pasos_pendientes) == {
        "EPICRISIS_FIRMADA", "MEDICACION_CONCILIADA"
    }
    # SIN efectos: la cama sigue en PROCESO_DE_ALTA, sin hito de transición ni de forzado.
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.PROCESO_DE_ALTA
    assert cama.internacion_actual_id == internacion.id
    assert await _hitos_por_codigo(session, "ATLAS_LIMPIEZA_INICIADA") == []
    assert await _hitos_por_codigo(session, "ATLAS_ALTA_FORZADA_PASOS_PENDIENTES") == []


# --------------------------------------------------------------------------- #
# 9. CON bloqueantes, forzar=True + motivo: procede Y escribe el hito de alta forzada.
# --------------------------------------------------------------------------- #
async def test_alta_validada_forzada_con_motivo_procede_y_deja_hito(session, servicio):
    await seed_pasos_alta_catalogo(session)
    internacion = await _crear_internacion(session, CategoriaInternacion.CLINICA)
    await servicio.instanciar_pasos(session, internacion)  # 2 bloqueantes pendientes
    cama = await _crear_cama_en_proceso_alta(session, internacion)

    motivo = "urgencia de cama; epicrisis se firma en la hora"
    await servicio.dar_alta_fisica_validada(
        session, cama, internacion, RolOperativo.ADMISION,
        actor_nombre="Admisión Central", forzar=True, motivo_override=motivo,
    )

    # Procede: la cama pasó a limpieza y se desvinculó la internación.
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.LIMPIEZA_TERMINAL
    assert cama.internacion_actual_id is None
    # La transición canónica ocurrió...
    assert len(await _hitos_por_codigo(session, "ATLAS_LIMPIEZA_INICIADA")) == 1
    # ...y además quedó el hito de la excepción con motivo + pasos que faltaban.
    forzados = await _hitos_por_codigo(session, "ATLAS_ALTA_FORZADA_PASOS_PENDIENTES")
    assert len(forzados) == 1
    hito = forzados[0]
    assert hito.internacion_id == internacion.id
    assert hito.cama_gestion_id == cama.id
    assert hito.metadata_evento["motivo_override"] == motivo
    assert set(hito.metadata_evento["pasos_pendientes"]) == {
        "EPICRISIS_FIRMADA", "MEDICACION_CONCILIADA"
    }


# --------------------------------------------------------------------------- #
# 10. CON bloqueantes, forzar=True pero SIN motivo: error y SIN efectos.
# --------------------------------------------------------------------------- #
async def test_alta_validada_forzada_sin_motivo_error_sin_efectos(session, servicio):
    await seed_pasos_alta_catalogo(session)
    internacion = await _crear_internacion(session, CategoriaInternacion.CLINICA)
    await servicio.instanciar_pasos(session, internacion)  # 2 bloqueantes pendientes
    cama = await _crear_cama_en_proceso_alta(session, internacion)

    with pytest.raises(ValueError):
        await servicio.dar_alta_fisica_validada(
            session, cama, internacion, RolOperativo.ADMISION,
            forzar=True, motivo_override="   ",  # vacío al normalizar
        )

    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.PROCESO_DE_ALTA  # sin cambios
    assert cama.internacion_actual_id == internacion.id
    assert await _hitos_por_codigo(session, "ATLAS_LIMPIEZA_INICIADA") == []
    assert await _hitos_por_codigo(session, "ATLAS_ALTA_FORZADA_PASOS_PENDIENTES") == []


# --------------------------------------------------------------------------- #
# 11. ATOMICIDAD del caso forzado: si el commit envolvente falla, NADA persiste
#     (ni el cambio de estado de B2 ni el hito de alta forzada).
# --------------------------------------------------------------------------- #
async def test_alta_validada_forzada_atomicidad_rollback_conjunto(
    session, servicio, monkeypatch
):
    await seed_pasos_alta_catalogo(session)
    internacion = await _crear_internacion(session, CategoriaInternacion.CLINICA)
    await servicio.instanciar_pasos(session, internacion)  # 2 bloqueantes pendientes
    cama = await _crear_cama_en_proceso_alta(session, internacion)
    # Capturar ids ANTES: el rollback del servicio expira los objetos y, en async, releer
    # un atributo expirado por acceso dispararía IO fuera del greenlet (MissingGreenlet).
    cama_id, internacion_id = cama.id, internacion.id

    # B2.dar_alta_fisica(commit=False) ya habrá flusheado el cambio de estado + su hito;
    # forzar un fallo en el commit envolvente debe deshacerlo TODO con el rollback único.
    async def _boom():
        raise RuntimeError("fallo simulado en el commit envolvente del alta forzada")
    monkeypatch.setattr(session, "commit", _boom)

    with pytest.raises(RuntimeError):
        await servicio.dar_alta_fisica_validada(
            session, cama, internacion, RolOperativo.ADMISION,
            forzar=True, motivo_override="urgencia de cama",
        )

    monkeypatch.undo()  # restaurar commit real; lo que sigue son sólo lecturas
    cama_db = await session.get(CamaGestion, cama_id)
    assert cama_db.estado_gestion == EstadoCamaGestion.PROCESO_DE_ALTA  # no cambió
    assert cama_db.internacion_actual_id == internacion_id
    assert await _hitos_por_codigo(session, "ATLAS_LIMPIEZA_INICIADA") == []
    assert await _hitos_por_codigo(session, "ATLAS_ALTA_FORZADA_PASOS_PENDIENTES") == []


# --------------------------------------------------------------------------- #
# 12. Caso límite: bloqueantes COMPLETOS pero quedan NO bloqueantes pendientes →
#     procede igual (los no bloqueantes no frenan el alta).
# --------------------------------------------------------------------------- #
async def test_alta_validada_no_bloqueantes_pendientes_no_frenan(session, servicio):
    await seed_pasos_alta_catalogo(session)
    internacion = await _crear_internacion(session, CategoriaInternacion.CLINICA)
    await servicio.instanciar_pasos(session, internacion)
    await _completar_bloqueantes(session, servicio, internacion)  # solo los bloqueantes
    cama = await _crear_cama_en_proceso_alta(session, internacion)

    # Precondición del caso: NO quedan bloqueantes, pero SÍ quedan no bloqueantes sin hacer.
    assert await servicio.pasos_bloqueantes_pendientes(session, internacion) == []
    todos = await servicio.listar_pasos(session, internacion)
    no_bloqueantes_pendientes = [
        p for p in todos if not p.era_bloqueante and not p.completado
    ]
    assert len(no_bloqueantes_pendientes) >= 1

    # Procede sin forzar y sin hito de forzado (los no bloqueantes son recordatorios).
    await servicio.dar_alta_fisica_validada(
        session, cama, internacion, RolOperativo.ADMISION
    )

    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.LIMPIEZA_TERMINAL
    assert await _hitos_por_codigo(session, "ATLAS_ALTA_FORZADA_PASOS_PENDIENTES") == []
