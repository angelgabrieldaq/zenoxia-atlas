"""Tests de integración del ServicioEgreso (capa de servicio, plano de estado).

Postgres real, tablas aisladas por test (truncate antes/después). Cada test
verifica:

* el cambio de estado / timestamp visible,
* el hito sellado (código + metadata),
* la atomicidad por operación.

Camino feliz punta a punta cubierto para 'camina' y 'defuncion', con assert
del hito en cada paso. Reversión hookea con el Egreso (estado='revertido').
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy import select, text
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
    TipoReversion,
)
from database.models import (
    CamaGestion,
    Discrepancia,
    Egreso,
    HitoAtlas,
    InternacionLocal,
    ItemChecklistEgreso,
    ItemChecklistLimpieza,
    NotaEgreso,
    PacienteLocal,
)
from domain.discharge_catalog import CATALOGO_CHECKLIST_EGRESO, DISCREP_MOTIVOS
from domain.egreso_service import (
    ChecklistLegalIncompleto,
    EgresoActivoYaExiste,
    ItemYaMarcado,
    MantenimientoPendiente,
    MedioEgresoDesconocido,
    MotivoDiscrepanciaInvalido,
    SalidaFisicaSinOkAdmin,
    ServicioEgreso,
)
from domain.state_machine import RolOperativo
from domain.transition_service import ServicioTransiciones

load_dotenv()
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas"
)

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
    return ServicioEgreso()


# ────────────────────────────────────────────────────────────────────────── #
# Helpers de armado
# ────────────────────────────────────────────────────────────────────────── #


async def _crear_internacion(
    session: AsyncSession,
    dni: str = "30111222",
) -> InternacionLocal:
    paciente = PacienteLocal(dni=dni, nombre="Ana", apellido="Gómez")
    session.add(paciente)
    await session.commit()
    internacion = InternacionLocal(
        paciente_local_id=paciente.id,
        categoria=CategoriaInternacion.CLINICA,
    )
    session.add(internacion)
    await session.commit()
    return internacion


async def _crear_cama_ocupada(
    session: AsyncSession,
    internacion: InternacionLocal,
    nombre: str = "H-01",
) -> CamaGestion:
    """Armada directamente en PROCESO_DE_ALTA: lo que el ciclo del egreso
    asume de entrada (el alta médica ya disparó la transición previa)."""
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
    rows = await session.execute(
        select(HitoAtlas).where(HitoAtlas.hito_codigo == codigo)
    )
    return list(rows.scalars().all())


async def _ultimo_hito(session: AsyncSession, codigo: str) -> HitoAtlas:
    hitos = await _hitos_por_codigo(session, codigo)
    assert hitos, f"no se encontró hito {codigo}"
    return sorted(hitos, key=lambda h: h.registrado_at)[-1]


async def _marcar_todos_los_items(
    session: AsyncSession,
    servicio: ServicioEgreso,
    egreso_id: uuid.UUID,
) -> None:
    """Marca cada item del checklist con el rol que le corresponde por
    catálogo (los items_checklist no exponen RolOperativo, así que mapeamos
    el string responsable a un rol válido para el hito)."""
    rol_por_responsable = {
        "medico": RolOperativo.MEDICO,
        "enfermeria": RolOperativo.ENFERMERIA,
        "admision": RolOperativo.ADMISION,
    }
    items = (await session.execute(
        select(ItemChecklistEgreso).where(
            ItemChecklistEgreso.egreso_id == egreso_id
        )
    )).scalars().all()
    for item in items:
        await servicio.marcar_item(
            session, egreso_id, item.id,
            rol=rol_por_responsable[item.responsable],
        )


# ────────────────────────────────────────────────────────────────────────── #
# crear_egreso
# ────────────────────────────────────────────────────────────────────────── #


async def test_crear_egreso_medio_inexistente_lanza(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)
    with pytest.raises(MedioEgresoDesconocido):
        await servicio.crear_egreso(
            session, internacion, cama, "teletransporte",
            RolOperativo.MEDICO,
        )


async def test_crear_egreso_camina_instancia_catalogo_y_hito(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)

    egreso = await servicio.crear_egreso(
        session, internacion, cama, "camina",
        RolOperativo.MEDICO, actor_nombre="Dr. Pérez",
    )

    assert egreso.estado == "info"
    assert egreso.medio_egreso == "camina"
    assert egreso.cama_gestion_id == cama.id

    items = (await session.execute(
        select(ItemChecklistEgreso).where(
            ItemChecklistEgreso.egreso_id == egreso.id
        )
    )).scalars().all()
    assert len(items) == len(CATALOGO_CHECKLIST_EGRESO["camina"])
    labels = {it.label for it in items}
    assert "Epicrisis firmada" in labels  # canónico del catálogo

    hito = await _ultimo_hito(session, "ATLAS_EGRESO_INICIADO")
    assert hito.metadata_evento["egreso_id"] == str(egreso.id)
    assert hito.metadata_evento["medio_egreso"] == "camina"


async def test_crear_egreso_con_activo_existente_lanza(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)
    await servicio.crear_egreso(
        session, internacion, cama, "camina", RolOperativo.MEDICO,
    )
    with pytest.raises(EgresoActivoYaExiste):
        await servicio.crear_egreso(
            session, internacion, cama, "ambulancia", RolOperativo.MEDICO,
        )


# ────────────────────────────────────────────────────────────────────────── #
# marcar_item
# ────────────────────────────────────────────────────────────────────────── #


async def test_marcar_item_marca_done_y_sella_hito(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)
    egreso = await servicio.crear_egreso(
        session, internacion, cama, "camina", RolOperativo.MEDICO,
    )
    item = (await session.execute(
        select(ItemChecklistEgreso).where(
            ItemChecklistEgreso.egreso_id == egreso.id
        ).limit(1)
    )).scalar_one()

    actualizado = await servicio.marcar_item(
        session, egreso.id, item.id, RolOperativo.MEDICO,
        actor_nombre="Dr. Pérez",
    )

    assert actualizado.done is True
    assert actualizado.hora_marcado is not None
    assert actualizado.autor == "Dr. Pérez"
    hito = await _ultimo_hito(session, "ATLAS_CHECKLIST_ITEM_MARCADO")
    assert hito.metadata_evento["item_id"] == str(item.id)
    assert hito.metadata_evento["label"] == item.label


async def test_marcar_item_ya_done_lanza(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)
    egreso = await servicio.crear_egreso(
        session, internacion, cama, "camina", RolOperativo.MEDICO,
    )
    item = (await session.execute(
        select(ItemChecklistEgreso).where(
            ItemChecklistEgreso.egreso_id == egreso.id
        ).limit(1)
    )).scalar_one()
    await servicio.marcar_item(session, egreso.id, item.id, RolOperativo.MEDICO)

    with pytest.raises(ItemYaMarcado):
        await servicio.marcar_item(session, egreso.id, item.id, RolOperativo.MEDICO)


async def test_marcar_item_metadata_no_aplica_va_al_hito(session, servicio):
    """Convención 'No Aplica': metadata extra se preserva en el hito."""
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)
    egreso = await servicio.crear_egreso(
        session, internacion, cama, "defuncion", RolOperativo.MEDICO,
    )
    # El item de cremación es el caso canónico de "no aplica".
    item = (await session.execute(
        select(ItemChecklistEgreso).where(
            ItemChecklistEgreso.egreso_id == egreso.id,
            ItemChecklistEgreso.label.like("Certificado de cremación%"),
        )
    )).scalar_one()
    await servicio.marcar_item(
        session, egreso.id, item.id, RolOperativo.MEDICO,
        metadata={"no_aplica": True},
    )
    hito = await _ultimo_hito(session, "ATLAS_CHECKLIST_ITEM_MARCADO")
    assert hito.metadata_evento["no_aplica"] is True


# ────────────────────────────────────────────────────────────────────────── #
# ok_administrativo
# ────────────────────────────────────────────────────────────────────────── #


async def test_ok_admin_sin_legales_completos_lanza_con_lista(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)
    egreso = await servicio.crear_egreso(
        session, internacion, cama, "camina", RolOperativo.MEDICO,
    )
    with pytest.raises(ChecklistLegalIncompleto) as exc:
        await servicio.ok_administrativo(session, egreso.id, RolOperativo.ADMISION)
    assert "Epicrisis firmada" in exc.value.items_pendientes


async def test_ok_admin_con_legales_completos_pasa_a_egreso_admin(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)
    egreso = await servicio.crear_egreso(
        session, internacion, cama, "camina", RolOperativo.MEDICO,
    )
    # Marcamos solo los legales (suficiente para el guard).
    items = (await session.execute(
        select(ItemChecklistEgreso).where(
            ItemChecklistEgreso.egreso_id == egreso.id,
            ItemChecklistEgreso.requerido_legal.is_(True),
        )
    )).scalars().all()
    for it in items:
        await servicio.marcar_item(session, egreso.id, it.id, RolOperativo.MEDICO)

    egreso = await servicio.ok_administrativo(
        session, egreso.id, RolOperativo.ADMISION, actor_nombre="María Admisión",
    )
    assert egreso.estado == "egreso_admin"
    assert egreso.egreso_admin_at is not None
    hito = await _ultimo_hito(session, "ATLAS_EGRESO_ADMIN")
    assert hito.metadata_evento["egreso_id"] == str(egreso.id)


# ────────────────────────────────────────────────────────────────────────── #
# confirmar_salida_fisica
# ────────────────────────────────────────────────────────────────────────── #


async def test_salida_fisica_sin_ok_admin_lanza(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)
    egreso = await servicio.crear_egreso(
        session, internacion, cama, "camina", RolOperativo.MEDICO,
    )
    with pytest.raises(SalidaFisicaSinOkAdmin):
        await servicio.confirmar_salida_fisica(
            session, egreso.id, RolOperativo.ENFERMERIA,
        )


# ────────────────────────────────────────────────────────────────────────── #
# Camino feliz e2e: 'camina'
# ────────────────────────────────────────────────────────────────────────── #


async def test_e2e_camina_crea_marca_admin_salida_limpieza_libera(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)

    egreso = await servicio.crear_egreso(
        session, internacion, cama, "camina", RolOperativo.MEDICO,
    )
    assert await _hitos_por_codigo(session, "ATLAS_EGRESO_INICIADO")

    await _marcar_todos_los_items(session, servicio, egreso.id)
    assert len(await _hitos_por_codigo(session, "ATLAS_CHECKLIST_ITEM_MARCADO")) == \
        len(CATALOGO_CHECKLIST_EGRESO["camina"])

    await servicio.ok_administrativo(session, egreso.id, RolOperativo.ADMISION)
    assert await _hitos_por_codigo(session, "ATLAS_EGRESO_ADMIN")

    await servicio.confirmar_salida_fisica(
        session, egreso.id, RolOperativo.ENFERMERIA,
    )
    assert await _hitos_por_codigo(session, "ATLAS_SALIDA_FISICA")
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.LIMPIEZA_TERMINAL

    items_limpieza = (await session.execute(
        select(ItemChecklistLimpieza).where(
            ItemChecklistLimpieza.egreso_id == egreso.id
        )
    )).scalars().all()
    assert len(items_limpieza) == 2
    for it in items_limpieza:
        await servicio.marcar_item_limpieza(
            session, egreso.id, it.id, RolOperativo.LIMPIEZA,
        )

    await session.refresh(egreso)
    await session.refresh(cama)
    assert egreso.estado == "liberado"
    assert cama.estado_gestion == EstadoCamaGestion.DISPONIBLE
    assert await _hitos_por_codigo(session, "ATLAS_CAMA_LIBERADA")


# ────────────────────────────────────────────────────────────────────────── #
# Camino feliz e2e: 'defuncion' con metadata de cochería
# ────────────────────────────────────────────────────────────────────────── #


async def test_e2e_defuncion_con_metadata_cocheria(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)

    egreso = await servicio.crear_egreso(
        session, internacion, cama, "defuncion", RolOperativo.MEDICO,
    )

    # Algunos items pueden marcarse como "no aplica" (cremación canónico).
    items = (await session.execute(
        select(ItemChecklistEgreso).where(
            ItemChecklistEgreso.egreso_id == egreso.id
        )
    )).scalars().all()
    rol_por_responsable = {
        "medico": RolOperativo.MEDICO,
        "enfermeria": RolOperativo.ENFERMERIA,
        "admision": RolOperativo.ADMISION,
    }
    for it in items:
        meta = {"no_aplica": True} if "cremación" in it.label.lower() else None
        await servicio.marcar_item(
            session, egreso.id, it.id,
            rol_por_responsable[it.responsable],
            metadata=meta,
        )

    await servicio.ok_administrativo(session, egreso.id, RolOperativo.ADMISION)
    metadata_cocheria = {
        "cocheria": "Cochería del Sur",
        "quien_retira": "Juan Pérez",
        "administrativo_entrega": "María Admisión",
        "seguridad": "Carlos S.",
    }
    await servicio.confirmar_salida_fisica(
        session, egreso.id, RolOperativo.ENFERMERIA,
        metadata=metadata_cocheria,
    )
    hito = await _ultimo_hito(session, "ATLAS_SALIDA_FISICA")
    for k, v in metadata_cocheria.items():
        assert hito.metadata_evento[k] == v
    await session.refresh(cama)
    assert cama.estado_gestion == EstadoCamaGestion.LIMPIEZA_TERMINAL

    items_limpieza = (await session.execute(
        select(ItemChecklistLimpieza).where(
            ItemChecklistLimpieza.egreso_id == egreso.id
        )
    )).scalars().all()
    for it in items_limpieza:
        await servicio.marcar_item_limpieza(
            session, egreso.id, it.id, RolOperativo.LIMPIEZA,
        )
    await session.refresh(egreso)
    await session.refresh(cama)
    assert egreso.estado == "liberado"
    assert cama.estado_gestion == EstadoCamaGestion.DISPONIBLE


# ────────────────────────────────────────────────────────────────────────── #
# Liberación bloqueada por mantenimiento_requerido
# ────────────────────────────────────────────────────────────────────────── #


async def test_liberacion_con_mantenimiento_requerido_lanza_y_no_libera(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)

    egreso = await servicio.crear_egreso(
        session, internacion, cama, "camina", RolOperativo.MEDICO,
    )
    egreso.mantenimiento_requerido = True
    await session.commit()

    await _marcar_todos_los_items(session, servicio, egreso.id)
    await servicio.ok_administrativo(session, egreso.id, RolOperativo.ADMISION)
    await servicio.confirmar_salida_fisica(
        session, egreso.id, RolOperativo.ENFERMERIA,
    )

    items_limpieza = (await session.execute(
        select(ItemChecklistLimpieza).where(
            ItemChecklistLimpieza.egreso_id == egreso.id
        )
    )).scalars().all()
    # Primer item se marca sin problema.
    await servicio.marcar_item_limpieza(
        session, egreso.id, items_limpieza[0].id, RolOperativo.LIMPIEZA,
    )
    # Al cerrar el último item, el guard de mantenimiento lanza.
    with pytest.raises(MantenimientoPendiente):
        await servicio.marcar_item_limpieza(
            session, egreso.id, items_limpieza[1].id, RolOperativo.LIMPIEZA,
        )
    # Rollback: la cama queda en LIMPIEZA_TERMINAL y el egreso NO liberado.
    await session.refresh(egreso)
    await session.refresh(cama)
    assert egreso.estado != "liberado"
    assert cama.estado_gestion == EstadoCamaGestion.LIMPIEZA_TERMINAL


# ────────────────────────────────────────────────────────────────────────── #
# Discrepancia / Nota
# ────────────────────────────────────────────────────────────────────────── #


async def test_registrar_discrepancia_motivo_invalido_lanza(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)
    egreso = await servicio.crear_egreso(
        session, internacion, cama, "camina", RolOperativo.MEDICO,
    )
    with pytest.raises(MotivoDiscrepanciaInvalido):
        await servicio.registrar_discrepancia(
            session, egreso.id, "motivo_inventado", "n/a",
            RolOperativo.ADMISION, actor_nombre="A",
        )


async def test_registrar_discrepancia_feliz(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)
    egreso = await servicio.crear_egreso(
        session, internacion, cama, "ambulancia", RolOperativo.MEDICO,
    )
    disc = await servicio.registrar_discrepancia(
        session, egreso.id, "ambulancia_demorada", "espera +45 min",
        RolOperativo.ADMISION, actor_nombre="María",
    )
    assert disc.motivo == "ambulancia_demorada"
    hito = await _ultimo_hito(session, "ATLAS_EGRESO_DISCREPANCIA")
    assert hito.metadata_evento["motivo"] == "ambulancia_demorada"


async def test_agregar_nota_feliz(session, servicio):
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)
    egreso = await servicio.crear_egreso(
        session, internacion, cama, "ambulancia", RolOperativo.MEDICO,
    )
    nota = await servicio.agregar_nota(
        session, egreso.id, "reclamo", "Familia avisó retraso",
        RolOperativo.ADMISION, actor_nombre="María",
    )
    assert nota.tipo == "reclamo"
    hito = await _ultimo_hito(session, "ATLAS_EGRESO_NOTA")
    assert hito.metadata_evento["tipo"] == "reclamo"


# ────────────────────────────────────────────────────────────────────────── #
# Reconciliación con la reversión (PR #19)
# ────────────────────────────────────────────────────────────────────────── #


async def test_reversion_temprana_marca_egreso_como_revertido(session, servicio):
    """Reversión PROCESO_DE_ALTA → OCUPADA: el egreso activo de la cama pasa
    a 'revertido' (terminal único). El hito de reversión incluye el id en
    metadata para correlación."""
    internacion = await _crear_internacion(session)
    cama = await _crear_cama_ocupada(session, internacion)
    egreso = await servicio.crear_egreso(
        session, internacion, cama, "camina", RolOperativo.MEDICO,
    )

    transiciones = ServicioTransiciones()
    await transiciones.revertir_alta_temprana(
        session, cama, RolOperativo.MEDICO,
        motivo_reversion="se cargó el alta por error",
        tipo=TipoReversion.ALTA_INFORMADA_POR_ERROR,
    )

    await session.refresh(egreso)
    await session.refresh(cama)
    assert egreso.estado == "revertido"
    assert cama.estado_gestion == EstadoCamaGestion.OCUPADA

    hito = await _ultimo_hito(session, "ATLAS_ALTA_REVERTIDA_POR_ERROR")
    assert hito.metadata_evento["egreso_revertido_id"] == str(egreso.id)
