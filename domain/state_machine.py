"""Máquina de estados de gestión de cama — validación pura.

Tabla §10 del diseño técnico de la capa 1a. NO accede a la base, NO escribe nada,
SIN side effects: solo decide si una transición es legal según la tabla y devuelve
los metadatos (rol disparador, si es excepción).

La autorización por rol es una capa aparte: acá solo se DECLARA quién puede disparar
cada transición. Verificar que el usuario actual tenga ese rol es responsabilidad del
servicio que invoque esta validación.
"""

import enum
from dataclasses import dataclass

from database.enums import EstadoCamaGestion


class RolOperativo(str, enum.Enum):
    """Roles operativos propios de Atlas que disparan transiciones de cama."""

    ADMISION = "ADMISION"
    ENFERMERIA = "ENFERMERIA"
    MEDICO = "MEDICO"
    HOTELERIA = "HOTELERIA"
    LIMPIEZA = "LIMPIEZA"
    MANTENIMIENTO = "MANTENIMIENTO"
    OPERACIONES = "OPERACIONES"


@dataclass(frozen=True)
class Transicion:
    """Una transición legal de la tabla §10."""

    origen: EstadoCamaGestion
    destino: EstadoCamaGestion
    roles: frozenset[RolOperativo]
    es_excepcion: bool = False
    nota: str = ""


class TransicionInvalida(Exception):
    """Se intentó una transición que no está contemplada en la tabla §10."""


TRANSICIONES: tuple[Transicion, ...] = (
    # --- Operación normal ---
    Transicion(
        origen=EstadoCamaGestion.DISPONIBLE,
        destino=EstadoCamaGestion.RESERVADA,
        roles=frozenset({RolOperativo.ADMISION}),
    ),
    Transicion(
        origen=EstadoCamaGestion.DISPONIBLE,
        destino=EstadoCamaGestion.OCUPADA,
        roles=frozenset({RolOperativo.ADMISION}),
        nota="ingreso directo a cama (sin reserva previa) lo gestiona Admisión",
    ),
    Transicion(
        origen=EstadoCamaGestion.RESERVADA,
        destino=EstadoCamaGestion.OCUPADA,
        roles=frozenset({RolOperativo.ENFERMERIA}),
    ),
    Transicion(
        origen=EstadoCamaGestion.RESERVADA,
        destino=EstadoCamaGestion.DISPONIBLE,
        roles=frozenset({RolOperativo.ADMISION}),
    ),
    Transicion(
        origen=EstadoCamaGestion.OCUPADA,
        destino=EstadoCamaGestion.PROCESO_DE_ALTA,
        roles=frozenset({RolOperativo.MEDICO}),
    ),
    Transicion(
        origen=EstadoCamaGestion.PROCESO_DE_ALTA,
        destino=EstadoCamaGestion.LIMPIEZA_TERMINAL,
        roles=frozenset({RolOperativo.ADMISION, RolOperativo.ENFERMERIA}),
        nota="salida física: Enfermería confirma la partida del paciente (cascada nivel 3); Admisión también autorizado",
    ),
    Transicion(
        origen=EstadoCamaGestion.LIMPIEZA_TERMINAL,
        destino=EstadoCamaGestion.DISPONIBLE,
        roles=frozenset({RolOperativo.LIMPIEZA, RolOperativo.HOTELERIA, RolOperativo.ADMISION}),
        nota="LIMPIEZA ejecuta ítem 1; HOTELERIA ejecuta ítem 2 (supervisor institucional); ADMISION con override",
    ),
    Transicion(
        origen=EstadoCamaGestion.DISPONIBLE,
        destino=EstadoCamaGestion.BLOQUEADA,
        roles=frozenset({RolOperativo.MANTENIMIENTO}),
    ),
    Transicion(
        origen=EstadoCamaGestion.BLOQUEADA,
        destino=EstadoCamaGestion.DISPONIBLE,
        roles=frozenset({RolOperativo.MANTENIMIENTO}),
        nota="requiere validación de Operaciones",
    ),
    # --- Excepciones ---
    Transicion(
        origen=EstadoCamaGestion.OCUPADA,
        destino=EstadoCamaGestion.BLOQUEADA,
        roles=frozenset({RolOperativo.MANTENIMIENTO}),
        es_excepcion=True,
        nota="mantenimiento urgente con paciente en cama",
    ),
    Transicion(
        origen=EstadoCamaGestion.PROCESO_DE_ALTA,
        destino=EstadoCamaGestion.OCUPADA,
        roles=frozenset({RolOperativo.MEDICO}),
        es_excepcion=True,
        nota="reversión temprana: todavía no hubo alta física, solo médica",
    ),
    Transicion(
        origen=EstadoCamaGestion.LIMPIEZA_TERMINAL,
        destino=EstadoCamaGestion.OCUPADA,
        roles=frozenset({RolOperativo.ADMISION}),
        es_excepcion=True,
        nota="reversión de alta tardía: deshacer el alta física es competencia de Admisión",
    ),
)


_INDEX: dict[tuple[EstadoCamaGestion, EstadoCamaGestion], Transicion] = {
    (t.origen, t.destino): t for t in TRANSICIONES
}


def puede_transicionar(
    origen: EstadoCamaGestion, destino: EstadoCamaGestion
) -> bool:
    """¿La transición origen → destino está declarada en la tabla §10?"""
    return (origen, destino) in _INDEX


def validar_transicion(
    origen: EstadoCamaGestion, destino: EstadoCamaGestion
) -> Transicion:
    """Devuelve la Transicion si es legal. Lanza TransicionInvalida si no lo es."""
    transicion = _INDEX.get((origen, destino))
    if transicion is None:
        raise TransicionInvalida(
            f"Transición ilegal: {origen.value} → {destino.value}"
        )
    return transicion
