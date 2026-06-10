"""Responsable actual del egreso — derivado del estado de los checklists.

Función pura: cero queries, cero side effects. Recibe el ``Egreso`` ya cargado con
sus checklists (`items_checklist` y `limpieza_checklist`) como listas en memoria y
devuelve quién es el siguiente responsable de mover la pelota.

Subordinada al §2.1 del MODELO_EGRESO_CERRADO.md: el responsable **emerge** del
modelo y NO se persiste. Se computa al leer el egreso.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Responsable:
    """Quién tiene la pelota y qué tarea concreta lo destraba.

    ``rol`` es String libre (no Enum) por consistencia con el resto de Atlas
    (``hito_codigo``, ``actor_rol``): permite evolucionar sin migrar tipos.
    Valores actuales: 'medico' | 'enfermeria' | 'admision' | 'prestador_externo'
    | 'hoteleria'."""

    rol: str
    tarea: str


# Estados del Egreso que cierran el proceso: no hay responsable porque no hay
# pelota. ``revertido`` queda reservado para cuando la reversión de alta marque
# el egreso correspondiente (ver §5.5 del STATE_ATLAS).
_ESTADOS_TERMINALES = frozenset({"liberado", "revertido"})

# Orden de prioridad en el nivel 4 de la cascada. Médico primero porque su
# checklist suele incluir la documentación legal de la que dependen los otros
# roles; admisión cierra al final.
_PRIORIDAD_CHECKLIST = ("medico", "enfermeria", "admision")

# Medios de egreso que delegan el último tramo a un tercero. Si el medio es
# uno de estos y ya hay OK administrativo, la pelota la tiene el prestador
# externo hasta que llegue (la salida física la confirma admisión cuando ocurre).
_MEDIOS_CON_PRESTADOR_EXTERNO = frozenset({"ambulancia", "derivacion"})


def computar_responsable(egreso) -> Responsable | None:
    """Cascada del §2.1: devuelve el PRIMER match.

    1. Egreso en estado terminal → no hay responsable.
    2. Salida física confirmada → hotelería sobre el primer ítem de limpieza
       pendiente. Si todos los ítems están hechos, no hay responsable
       (la cama está a un paso de DISPONIBLE; el guard de mantenimiento lo
       cierra arriba).
    3. OK administrativo dado pero sin salida física → prestador externo si el
       medio es ambulancia/derivación, admisión si el paciente se va caminando.
    4. Checklist de egreso con pendientes → primer rol por prioridad
       (médico → enfermería → admisión) sobre su primer ítem pendiente.
    5. Checklist completo y sin OK administrativo → admisión para dar el OK
       final (cubre el caso "no hay ítems" también: nada bloquea, admisión
       cierra).
    """
    if egreso.estado in _ESTADOS_TERMINALES:
        return None

    if egreso.salida_fisica_at is not None:
        for item in egreso.limpieza_checklist:
            if not item.done:
                return Responsable("hoteleria", item.label)
        return None

    if egreso.egreso_admin_at is not None:
        if egreso.medio_egreso in _MEDIOS_CON_PRESTADOR_EXTERNO:
            return Responsable(
                "prestador_externo",
                "Confirmar llegada de ambulancia/traslado",
            )
        return Responsable("admision", "Confirmar salida física del paciente")

    for rol in _PRIORIDAD_CHECKLIST:
        for item in egreso.items_checklist:
            if item.responsable == rol and not item.done:
                return Responsable(rol, item.label)

    return Responsable("admision", "Dar OK administrativo final")
