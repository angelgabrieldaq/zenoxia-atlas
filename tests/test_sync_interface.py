"""Tests de la interfaz de sincronización con el core (capa 1a, §12). SIN base de datos.

Verifican el CONTRATO, no IO: que ``NoOpCoreSync`` cumple el ABC ``CoreSync``, que cada
método no-op no falla y devuelve la entidad TAL CUAL la recibió (pass-through, sin mutar ni
enlazar), y que el contrato abstracto no se puede instanciar (ni una subclase incompleta).
Se pasan sentinelas (``object()``) en lugar de modelos: el no-op no toca atributos, así el
test no necesita ni ORM ni Postgres.
"""

import inspect

import pytest

from sync import CoreSync, NoOpCoreSync

# Los 4 métodos del contrato según §12 (Capa de sincronización).
METODOS_CONTRATO = (
    "sincronizar_paciente",
    "sincronizar_internacion",
    "sincronizar_cama",
    "replicar_hito",
)


def test_noop_es_instancia_del_contrato():
    """NoOpCoreSync satisface el contrato: es subclase e instancia de CoreSync (ABC)."""
    assert issubclass(NoOpCoreSync, CoreSync)
    assert isinstance(NoOpCoreSync(), CoreSync)


def test_contrato_abstracto_no_se_instancia():
    """CoreSync es ABC con métodos abstractos: instanciarlo directo es TypeError."""
    with pytest.raises(TypeError):
        CoreSync()  # type: ignore[abstract]


def test_subclase_incompleta_no_se_instancia():
    """Una implementación que no cubre TODOS los métodos del contrato no se instancia."""

    class SyncParcial(CoreSync):
        async def sincronizar_paciente(self, paciente_local):
            return paciente_local

        # faltan sincronizar_internacion, sincronizar_cama y replicar_hito a propósito.

    with pytest.raises(TypeError):
        SyncParcial()  # type: ignore[abstract]


def test_contrato_declara_los_cuatro_metodos_async():
    """El contrato declara los 4 métodos de §12 y todos son corutinas (async)."""
    for nombre in METODOS_CONTRATO:
        assert hasattr(CoreSync, nombre), f"falta el método del contrato: {nombre}"
        assert inspect.iscoroutinefunction(
            getattr(CoreSync, nombre)
        ), f"{nombre} debería ser async"


async def test_noop_sincronizar_paciente_passthrough():
    """No-op: devuelve EXACTAMENTE el paciente recibido, sin error ni mutación."""
    sync = NoOpCoreSync()
    paciente = object()  # sentinela: el no-op no toca atributos
    assert await sync.sincronizar_paciente(paciente) is paciente


async def test_noop_sincronizar_internacion_passthrough():
    """No-op: devuelve EXACTAMENTE la internación recibida."""
    sync = NoOpCoreSync()
    internacion = object()
    assert await sync.sincronizar_internacion(internacion) is internacion


async def test_noop_sincronizar_cama_passthrough():
    """No-op: devuelve EXACTAMENTE la cama recibida."""
    sync = NoOpCoreSync()
    cama = object()
    assert await sync.sincronizar_cama(cama) is cama


async def test_noop_replicar_hito_passthrough():
    """No-op: devuelve EXACTAMENTE el hito recibido (no marca sincronizado_core)."""
    sync = NoOpCoreSync()
    hito = object()
    assert await sync.replicar_hito(hito) is hito
