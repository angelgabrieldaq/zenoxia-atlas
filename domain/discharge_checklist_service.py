"""Servicio del checklist de alta (capa 1a, §6) — sub-paso 1: pasos y su estado.

Cuando una internación entra en proceso de alta, se instancian los pasos del catálogo
que apliquen (universales + los de su categoría). Cada paso se completa de forma
auditable (quién, cuándo, + HitoAtlas). ``pasos_bloqueantes_pendientes`` queda listo
como consulta para el sub-paso 2 (override del alta física), pero acá NO se conecta a la
transición de alta: este sub-paso sólo modela y opera los pasos.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    HitoAtlas,
    InternacionLocal,
    PasoAltaCatalogo,
    PasoAltaInternacion,
)
from domain.state_machine import RolOperativo

# Hito de auditoría al completar un paso del checklist (catálogo §11, string libre).
_HITO_PASO_COMPLETADO = "ATLAS_PASO_ALTA_COMPLETADO"


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class ServicioChecklistAlta:
    """Instancia y opera los pasos de alta de una internación, con trazabilidad por hito."""

    async def instanciar_pasos(
        self,
        session: AsyncSession,
        internacion: InternacionLocal,
    ) -> list[PasoAltaInternacion]:
        """Instancia, para la internación, los pasos del catálogo ACTIVOS que apliquen:
        universales (``categoria_aplica IS NULL``) + los que matcheen ``internacion.categoria``.

        Copia ``era_bloqueante`` desde ``bloqueante`` del catálogo (snapshot). Idempotente:
        NO re-crea pasos ya instanciados para esta internación (matchea por catálogo).
        Devuelve sólo los pasos NUEVOS creados. Un único commit."""
        # Catálogo aplicable: activo + (universal o de la categoría de la internación).
        aplicables = (
            await session.execute(
                select(PasoAltaCatalogo).where(
                    PasoAltaCatalogo.activo.is_(True),
                    (
                        PasoAltaCatalogo.categoria_aplica.is_(None)
                        | (PasoAltaCatalogo.categoria_aplica == internacion.categoria)
                    ),
                )
            )
        ).scalars().all()

        # Ya instanciados para esta internación (idempotencia por catálogo).
        ya_instanciados = set(
            (
                await session.execute(
                    select(PasoAltaInternacion.paso_catalogo_id).where(
                        PasoAltaInternacion.internacion_id == internacion.id
                    )
                )
            ).scalars().all()
        )

        creados: list[PasoAltaInternacion] = []
        for paso_cat in aplicables:
            if paso_cat.id in ya_instanciados:
                continue
            paso = PasoAltaInternacion(
                internacion_id=internacion.id,
                paso_catalogo_id=paso_cat.id,
                era_bloqueante=paso_cat.bloqueante,
                completado=False,
            )
            session.add(paso)
            creados.append(paso)
        if creados:
            await session.commit()
        return creados

    async def completar_paso(
        self,
        session: AsyncSession,
        paso_internacion: PasoAltaInternacion,
        rol: RolOperativo,
        actor_nombre: str | None = None,
    ) -> PasoAltaInternacion:
        """Marca el paso completado (quién + cuándo) y escribe un HitoAtlas de auditoría
        (``ATLAS_PASO_ALTA_COMPLETADO``, con metadata de qué paso). Un único commit."""
        paso_internacion.completado = True
        paso_internacion.completado_por_rol = rol.value
        paso_internacion.completado_por_nombre = actor_nombre
        paso_internacion.completado_at = _ahora()

        # Para la trazabilidad del hito: el código legible del paso (del catálogo).
        paso_cat = await session.get(PasoAltaCatalogo, paso_internacion.paso_catalogo_id)
        # Hito autocontenido: internacion_id redundante en metadata (str, JSONB), + qué paso.
        hito = HitoAtlas(
            internacion_id=paso_internacion.internacion_id,
            cama_gestion_id=None,  # el paso de checklist no es sobre una cama puntual
            hito_codigo=_HITO_PASO_COMPLETADO,
            actor_rol=rol.value,
            actor_nombre=actor_nombre,
            metadata_evento={
                "internacion_id": str(paso_internacion.internacion_id),
                "cama_gestion_id": None,
                "paso_internacion_id": str(paso_internacion.id),
                "paso_catalogo_id": str(paso_internacion.paso_catalogo_id),
                "paso_codigo": paso_cat.codigo if paso_cat is not None else None,
                "era_bloqueante": paso_internacion.era_bloqueante,
            },
            sincronizado_core=False,  # §12: sync real es Fase 3
        )
        session.add(hito)
        await session.commit()
        return paso_internacion

    async def listar_pasos(
        self,
        session: AsyncSession,
        internacion: InternacionLocal,
    ) -> list[PasoAltaInternacion]:
        """Devuelve los pasos de la internación con su estado, ordenados por creación."""
        resultado = await session.execute(
            select(PasoAltaInternacion)
            .where(PasoAltaInternacion.internacion_id == internacion.id)
            .order_by(PasoAltaInternacion.creada_at)
        )
        return list(resultado.scalars().all())

    async def pasos_bloqueantes_pendientes(
        self,
        session: AsyncSession,
        internacion: InternacionLocal,
    ) -> list[PasoAltaInternacion]:
        """Pasos ``era_bloqueante=True`` aún NO completados de la internación.

        Consulta lista para el sub-paso 2 (override del alta física); acá NO se conecta
        todavía a la transición de alta."""
        resultado = await session.execute(
            select(PasoAltaInternacion)
            .where(
                PasoAltaInternacion.internacion_id == internacion.id,
                PasoAltaInternacion.era_bloqueante.is_(True),
                PasoAltaInternacion.completado.is_(False),
            )
            .order_by(PasoAltaInternacion.creada_at)
        )
        return list(resultado.scalars().all())
