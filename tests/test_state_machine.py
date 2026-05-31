import pytest

from database.enums import EstadoCamaGestion
from domain.state_machine import (
    TRANSICIONES,
    RolOperativo,
    Transicion,
    TransicionInvalida,
    puede_transicionar,
    validar_transicion,
)


TRANSICIONES_LEGALES_NORMALES = [
    (EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.RESERVADA),
    (EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.OCUPADA),
    (EstadoCamaGestion.RESERVADA, EstadoCamaGestion.OCUPADA),
    (EstadoCamaGestion.RESERVADA, EstadoCamaGestion.DISPONIBLE),
    (EstadoCamaGestion.OCUPADA, EstadoCamaGestion.PROCESO_DE_ALTA),
    (EstadoCamaGestion.PROCESO_DE_ALTA, EstadoCamaGestion.LIMPIEZA_TERMINAL),
    (EstadoCamaGestion.LIMPIEZA_TERMINAL, EstadoCamaGestion.DISPONIBLE),
    (EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.BLOQUEADA),
    (EstadoCamaGestion.BLOQUEADA, EstadoCamaGestion.DISPONIBLE),
]

TRANSICIONES_EXCEPCION = [
    (EstadoCamaGestion.OCUPADA, EstadoCamaGestion.BLOQUEADA),
    (EstadoCamaGestion.PROCESO_DE_ALTA, EstadoCamaGestion.OCUPADA),
    (EstadoCamaGestion.LIMPIEZA_TERMINAL, EstadoCamaGestion.OCUPADA),
]

TRANSICIONES_ILEGALES = [
    # Las que el plano técnico nombra como contraejemplos
    (EstadoCamaGestion.LIMPIEZA_TERMINAL, EstadoCamaGestion.RESERVADA),
    (EstadoCamaGestion.BLOQUEADA, EstadoCamaGestion.OCUPADA),
    (EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.PROCESO_DE_ALTA),
    # Otras combinaciones que NO están en la tabla
    (EstadoCamaGestion.DISPONIBLE, EstadoCamaGestion.LIMPIEZA_TERMINAL),
    (EstadoCamaGestion.OCUPADA, EstadoCamaGestion.DISPONIBLE),  # tiene que pasar por proceso_de_alta
    (EstadoCamaGestion.RESERVADA, EstadoCamaGestion.BLOQUEADA),
    (EstadoCamaGestion.BLOQUEADA, EstadoCamaGestion.RESERVADA),
    (EstadoCamaGestion.PROCESO_DE_ALTA, EstadoCamaGestion.DISPONIBLE),
]


@pytest.mark.parametrize(
    "origen,destino", TRANSICIONES_LEGALES_NORMALES + TRANSICIONES_EXCEPCION
)
def test_transicion_legal_puede_transicionar_true(origen, destino):
    assert puede_transicionar(origen, destino) is True


@pytest.mark.parametrize(
    "origen,destino", TRANSICIONES_LEGALES_NORMALES + TRANSICIONES_EXCEPCION
)
def test_transicion_legal_validar_no_lanza(origen, destino):
    transicion = validar_transicion(origen, destino)
    assert isinstance(transicion, Transicion)
    assert transicion.origen == origen
    assert transicion.destino == destino


@pytest.mark.parametrize("origen,destino", TRANSICIONES_ILEGALES)
def test_transicion_ilegal_puede_transicionar_false(origen, destino):
    assert puede_transicionar(origen, destino) is False


@pytest.mark.parametrize("origen,destino", TRANSICIONES_ILEGALES)
def test_transicion_ilegal_validar_lanza(origen, destino):
    with pytest.raises(TransicionInvalida) as excinfo:
        validar_transicion(origen, destino)
    assert origen.value in str(excinfo.value)
    assert destino.value in str(excinfo.value)


@pytest.mark.parametrize("origen,destino", TRANSICIONES_EXCEPCION)
def test_transiciones_de_excepcion_estan_marcadas(origen, destino):
    transicion = validar_transicion(origen, destino)
    assert transicion.es_excepcion is True


@pytest.mark.parametrize("origen,destino", TRANSICIONES_LEGALES_NORMALES)
def test_transiciones_normales_no_estan_marcadas_como_excepcion(origen, destino):
    transicion = validar_transicion(origen, destino)
    assert transicion.es_excepcion is False


def test_tabla_no_tiene_pares_duplicados():
    pares = [(t.origen, t.destino) for t in TRANSICIONES]
    assert len(pares) == len(set(pares))


def test_tabla_tiene_12_transiciones():
    assert len(TRANSICIONES) == 12


def test_cada_transicion_tiene_al_menos_un_rol():
    for t in TRANSICIONES:
        assert len(t.roles) >= 1
        assert all(isinstance(r, RolOperativo) for r in t.roles)


def test_no_existe_transicion_a_si_mismo():
    for t in TRANSICIONES:
        assert t.origen != t.destino
