"""Semillas de datos de configuración de Atlas (capa 1a).

Por ahora: el catálogo inicial de pasos de alta (``PasoAltaCatalogo``). Es configuración
por institución — un set razonable de arranque que luego el admin prende/apaga/edita.

Idempotente: se siembra por ``codigo`` (no duplica si el paso ya existe).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.enums import CategoriaInternacion
from database.models import PasoAltaCatalogo

# Catálogo de arranque. (codigo, nombre, categoria_aplica, bloqueante, orden).
# categoria_aplica=None → universal (aplica a toda internación).
PASOS_ALTA_INICIALES: list[tuple[str, str, CategoriaInternacion | None, bool, int]] = [
    ("EPICRISIS_FIRMADA", "Epicrisis firmada por el médico", None, True, 10),
    ("MEDICACION_CONCILIADA", "Medicación del alta conciliada", None, True, 20),
    ("RESUMEN_ALTA_ENTREGADO", "Resumen de alta entregado al paciente", None, False, 30),
    ("FACTURACION_CERRADA", "Facturación / liquidación cerrada", None, False, 40),
    (
        "CONTROL_POST_UTI_AGENDADO",
        "Control post-UTI agendado",
        CategoriaInternacion.CRITICA,
        False,
        50,
    ),
]


async def seed_pasos_alta_catalogo(session: AsyncSession) -> list[PasoAltaCatalogo]:
    """Inserta los pasos del catálogo que falten (por ``codigo``). Idempotente.

    Devuelve solo los pasos NUEVOS que se crearon (lista vacía si ya estaban todos).
    Un único commit al final."""
    existentes = set(
        (await session.execute(select(PasoAltaCatalogo.codigo))).scalars().all()
    )
    creados: list[PasoAltaCatalogo] = []
    for codigo, nombre, categoria, bloqueante, orden in PASOS_ALTA_INICIALES:
        if codigo in existentes:
            continue
        paso = PasoAltaCatalogo(
            codigo=codigo,
            nombre=nombre,
            categoria_aplica=categoria,
            bloqueante=bloqueante,
            activo=True,
            orden=orden,
        )
        session.add(paso)
        creados.append(paso)
    if creados:
        await session.commit()
    return creados
