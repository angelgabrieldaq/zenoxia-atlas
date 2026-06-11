"""Tests de invariantes del catálogo de checklist de egreso.

No prueban contenido literal (eso es decisión institucional, cambia con la
operación). Prueban garantías estructurales que el resto del servicio supone."""

from __future__ import annotations

import pytest

from domain.discharge_catalog import CATALOGO_CHECKLIST_EGRESO, DISCREP_MOTIVOS

_MEDIOS_ESPERADOS = {"camina", "ambulancia", "derivacion", "traslado_interno", "defuncion"}
_ROLES_VALIDOS = {"medico", "enfermeria", "admision"}


def test_catalogo_cubre_los_cinco_medios():
    assert set(CATALOGO_CHECKLIST_EGRESO.keys()) == _MEDIOS_ESPERADOS


@pytest.mark.parametrize("medio", sorted(_MEDIOS_ESPERADOS))
def test_cada_medio_tiene_al_menos_un_item_legal_de_medico(medio: str):
    """Garantía que sostiene el guard de ok_administrativo: para todo medio,
    existe al menos un item del médico con requerido_legal=True. Si esto se
    rompe (catálogo editado), el guard se vuelve vacuo y dejaría pasar OK
    administrativos sin epicrisis ni certificado."""
    items = CATALOGO_CHECKLIST_EGRESO[medio]
    legales_medicos = [
        (resp, label, legal)
        for resp, label, legal in items
        if resp == "medico" and legal is True
    ]
    assert len(legales_medicos) >= 1, (
        f"Medio '{medio}' no tiene items legales del médico; el guard de OK "
        f"administrativo quedaría vacuo."
    )


@pytest.mark.parametrize("medio", sorted(_MEDIOS_ESPERADOS))
def test_items_tienen_estructura_esperada(medio: str):
    """Cada entry: tupla (str, str, bool) con responsable en el set válido."""
    for i, entry in enumerate(CATALOGO_CHECKLIST_EGRESO[medio]):
        assert len(entry) == 3, f"{medio}[{i}] no es una tripla"
        responsable, label, legal = entry
        assert responsable in _ROLES_VALIDOS, (
            f"{medio}[{i}] responsable '{responsable}' fuera de {_ROLES_VALIDOS}"
        )
        assert isinstance(label, str) and label.strip(), f"{medio}[{i}] label vacío"
        assert isinstance(legal, bool), f"{medio}[{i}] requerido_legal no es bool"


def test_discrep_motivos_incluye_otro():
    """'otro' es la válvula de escape obligatoria (con nota libre)."""
    assert "otro" in DISCREP_MOTIVOS


def test_discrep_motivos_sin_duplicados():
    assert len(DISCREP_MOTIVOS) == len(set(DISCREP_MOTIVOS))
