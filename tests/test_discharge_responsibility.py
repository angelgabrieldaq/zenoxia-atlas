"""Tests de ``computar_responsable`` — función pura, objetos en memoria, sin DB.

Cubre los 5 niveles de la cascada del §2.1 del MODELO_EGRESO_CERRADO.md.
Los objetos se arman con ``SimpleNamespace`` para no acoplar el test al ORM:
la función es duck-typed sobre los atributos del Egreso y sus checklists.
"""

from types import SimpleNamespace

from domain.discharge_responsibility import Responsable, computar_responsable


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _item(responsable: str, label: str, done: bool = False) -> SimpleNamespace:
    return SimpleNamespace(responsable=responsable, label=label, done=done)


def _limpieza(label: str, done: bool = False) -> SimpleNamespace:
    return SimpleNamespace(label=label, done=done)


def _egreso(
    estado: str = "info",
    medio_egreso: str = "camina",
    egreso_admin_at: str | None = None,
    salida_fisica_at: str | None = None,
    items_checklist: list | None = None,
    limpieza_checklist: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        estado=estado,
        medio_egreso=medio_egreso,
        egreso_admin_at=egreso_admin_at,
        salida_fisica_at=salida_fisica_at,
        items_checklist=items_checklist if items_checklist is not None else [],
        limpieza_checklist=limpieza_checklist if limpieza_checklist is not None else [],
    )


# ------------------------------------------------------------------ #
# Nivel 1: estados terminales del Egreso
# ------------------------------------------------------------------ #

def test_estado_liberado_devuelve_none():
    assert computar_responsable(_egreso(estado="liberado")) is None


def test_estado_revertido_devuelve_none():
    assert computar_responsable(_egreso(estado="revertido")) is None


# ------------------------------------------------------------------ #
# Nivel 2: salida física ya pasó → hotelería sobre limpieza
# ------------------------------------------------------------------ #

def test_salida_fisica_con_limpieza_pendiente_devuelve_hoteleria_primer_item():
    e = _egreso(
        salida_fisica_at="2026-06-10T14:00",
        limpieza_checklist=[
            _limpieza("Cama limpiada según protocolo", done=True),
            _limpieza("Control final — cama OK", done=False),
            _limpieza("Auditoría supervisor", done=False),
        ],
    )
    assert computar_responsable(e) == Responsable(
        "hoteleria", "Control final — cama OK"
    )


def test_salida_fisica_limpieza_completa_devuelve_none():
    """Todos los items de limpieza done = cama lista; no hay responsable acá
    (el guard de mantenimiento, si aplica, se evalúa arriba en el servicio)."""
    e = _egreso(
        salida_fisica_at="2026-06-10T14:00",
        limpieza_checklist=[
            _limpieza("Cama limpiada", done=True),
            _limpieza("Control final", done=True),
        ],
    )
    assert computar_responsable(e) is None


# ------------------------------------------------------------------ #
# Nivel 3: OK administrativo sin salida física
# ------------------------------------------------------------------ #

def test_admin_con_ambulancia_devuelve_prestador_externo():
    e = _egreso(
        medio_egreso="ambulancia",
        egreso_admin_at="2026-06-10T13:00",
    )
    assert computar_responsable(e) == Responsable(
        "prestador_externo", "Confirmar llegada de ambulancia/traslado"
    )


def test_admin_con_derivacion_devuelve_prestador_externo():
    e = _egreso(
        medio_egreso="derivacion",
        egreso_admin_at="2026-06-10T13:00",
    )
    r = computar_responsable(e)
    assert r is not None
    assert r.rol == "prestador_externo"


def test_admin_con_camina_devuelve_admision_confirmar_salida():
    e = _egreso(
        medio_egreso="camina",
        egreso_admin_at="2026-06-10T13:00",
    )
    assert computar_responsable(e) == Responsable(
        "admision", "Confirmar salida física del paciente"
    )


def test_admin_con_traslado_interno_devuelve_admision_confirmar_salida():
    """traslado_interno NO está en la lista de prestador externo → admisión."""
    e = _egreso(
        medio_egreso="traslado_interno",
        egreso_admin_at="2026-06-10T13:00",
    )
    r = computar_responsable(e)
    assert r is not None
    assert r.rol == "admision"


# ------------------------------------------------------------------ #
# Nivel 4: checklist pendiente, prioridad medico > enfermeria > admision
# ------------------------------------------------------------------ #

def test_checklist_prioriza_medico_sobre_enfermeria_y_admision():
    e = _egreso(items_checklist=[
        _item("admision", "Documentos", done=False),
        _item("enfermeria", "Apto", done=False),
        _item("medico", "Resumen de epicrisis", done=False),
    ])
    assert computar_responsable(e) == Responsable(
        "medico", "Resumen de epicrisis"
    )


def test_checklist_prioriza_enfermeria_sobre_admision_cuando_medico_completo():
    e = _egreso(items_checklist=[
        _item("medico", "epicrisis", done=True),
        _item("admision", "Documentos", done=False),
        _item("enfermeria", "Apto", done=False),
    ])
    assert computar_responsable(e) == Responsable("enfermeria", "Apto")


def test_checklist_devuelve_primer_item_pendiente_del_rol_ganador():
    """Cuando el rol prioritario tiene varios pendientes, gana el primero en la lista."""
    e = _egreso(items_checklist=[
        _item("medico", "primero", done=True),
        _item("medico", "segundo", done=False),
        _item("medico", "tercero", done=False),
    ])
    assert computar_responsable(e) == Responsable("medico", "segundo")


def test_checklist_omite_items_done_y_cae_al_proximo_rol():
    e = _egreso(items_checklist=[
        _item("medico", "epicrisis", done=True),
        _item("enfermeria", "apto", done=True),
        _item("admision", "documentos", done=False),
    ])
    assert computar_responsable(e) == Responsable("admision", "documentos")


# ------------------------------------------------------------------ #
# Nivel 5: checklist completo, sin OK administrativo
# ------------------------------------------------------------------ #

def test_checklist_completo_sin_admin_devuelve_admision_para_ok_final():
    e = _egreso(items_checklist=[
        _item("medico", "x", done=True),
        _item("enfermeria", "y", done=True),
        _item("admision", "z", done=True),
    ])
    assert computar_responsable(e) == Responsable(
        "admision", "Dar OK administrativo final"
    )


def test_egreso_sin_items_devuelve_admision_para_ok_final():
    """Sin items pendientes ni hechos: nada bloquea, admisión cierra."""
    assert computar_responsable(_egreso()) == Responsable(
        "admision", "Dar OK administrativo final"
    )


# ------------------------------------------------------------------ #
# Garantías de pureza
# ------------------------------------------------------------------ #

def test_responsable_es_inmutable():
    """@dataclass(frozen=True): no se puede asignar atributos al Responsable."""
    import dataclasses

    import pytest

    r = Responsable("medico", "x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.rol = "admision"  # type: ignore[misc]
