"""Catálogo de items de checklist por medio de egreso + motivos de discrepancia.

Constantes en código, no tabla de DB. Razones (§3 del plan operativo):

* MVP: evita una migración + endpoint + admin UI para mover algo que cambia
  muy de a poco (una vez por institución, no por egreso).
* Validación en capa de servicio (igual que el catálogo de transiciones de
  ``state_machine.py``), no en SQL.
* Cuando una institución necesite parametrizarlo, se extrae a una tabla
  análoga a ``PasoAltaCatalogo`` sin romper contrato (la fila de DB
  ``ItemChecklistEgreso`` ya tiene la forma esperada).

Cada entrada del catálogo es una tupla ``(responsable, label, requerido_legal)``:

* ``responsable``: 'medico' | 'enfermeria' | 'admision'. Lo usa
  ``computar_responsable`` (nivel 4 de la cascada).
* ``label``: texto humano del item.
* ``requerido_legal``: True si bloquea el OK administrativo
  (validación crítica §6.2 del modelo de egreso). Garantía sobre el catálogo
  (ver tests): todo medio tiene ≥1 item legal de responsable médico, de modo
  que el guard de ``ok_administrativo`` no es vacuo.

## Convención "No Aplica"

Algunos items son condicionales por contexto (ej. "Certificado de cremación"
solo aplica si el destino del cuerpo es cremación). El catálogo NO ramifica
por sub-medio: se incluye el item con ``requerido_legal=False`` y, cuando no
aplica, se lo marca ``done`` de inmediato pasando metadata
``{"no_aplica": True}`` a ``marcar_item``. El hito de auditoría conserva la
marca, así Atlas distingue "hecho" de "no aplicaba" sin inventar estados.

Esta convención también es la salida para items del checklist que se
omiten por excepción legítima (paciente que rechaza una intervención
previa al alta, por ejemplo): la decisión queda registrada en el hito, no
silenciada por un done liso.
"""

from __future__ import annotations


# (responsable, label, requerido_legal)
CATALOGO_CHECKLIST_EGRESO: dict[str, list[tuple[str, str, bool]]] = {
    "camina": [
        ("medico",     "Epicrisis firmada",                                       True),
        ("medico",     "Indicaciones y conciliación de medicación al alta",       True),
        ("enfermeria", "Retiro de vías/dispositivos — apto",                      False),
        ("admision",   "Verificación administrativa y cobertura",                 False),
    ],
    "ambulancia": [
        ("medico",     "Epicrisis firmada",                                       True),
        ("medico",     "Indicaciones y conciliación de medicación al alta",       True),
        ("medico",     "Resumen clínico para traslado",                           True),
        ("medico",     "Orden de traslado emitida por el médico",                 True),
        ("enfermeria", "Apto de enfermería para traslado",                        False),
        ("admision",   "Ambulancia solicitada al prestador",                      False),
        ("admision",   "Verificación administrativa y cobertura",                 False),
    ],
    "derivacion": [
        ("medico",     "Epicrisis firmada",                                       True),
        ("medico",     "Estudios e imágenes adjuntos",                            True),
        ("medico",     "Resumen clínico para institución destino",                True),
        ("medico",     "Orden de traslado emitida por el médico",                 True),
        ("enfermeria", "Apto de enfermería para traslado",                        False),
        ("admision",   "Cama en destino confirmada",                              False),
        ("admision",   "Traslado coordinado",                                     False),
    ],
    "traslado_interno": [
        ("medico",     "Pase de servicio con indicaciones",                       True),
        ("enfermeria", "Apto y entrega de paciente",                              False),
        ("admision",   "Cama destino confirmada",                                 False),
    ],
    "defuncion": [
        ("admision",   "Documentación del fallecido y autorizante recibida",      False),
        ("admision",   "Certificados e instructivo enviados al médico",           False),
        ("medico",     "Certificado de defunción completado, firmado y sellado",  True),
        ("medico",     "Certificado de cremación completado (si corresponde)",    False),
        ("admision",   "Inscripción de defunción en registro civil cargada",      True),
        ("admision",   "Cierre administrativo del episodio (costos pendientes)",  False),
        ("admision",   "Documentación entregada a seguridad — retiro habilitado", False),
    ],
}


# Motivos predefinidos para registrar discrepancias durante el egreso (§5 del
# modelo). 'otro' es válvula de escape (la nota libre acompaña en la entidad
# ``Discrepancia``).
DISCREP_MOTIVOS: tuple[str, ...] = (
    "ambulancia_demorada",
    "familiar_ausente",
    "documentacion_incompleta",
    "cama_destino_no_disponible",
    "paciente_se_niega",
    "demora_responsable",   # override de ADMISION: el responsable del item se demoró
    "otro",
)
