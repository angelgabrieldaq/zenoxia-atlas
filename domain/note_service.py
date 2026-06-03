"""Servicio CRUD de NotaCama (capa 1a, §9b): notas libres de comunicación sobre una cama.

Editable y auditable: el texto se puede modificar (registrando quién y cuándo), y el
borrado es lógico (activa=False), nunca DELETE físico. Sin orquestación multi-entidad:
cada operación es un commit propio.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import CamaGestion, NotaCama


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class ServicioNotas:
    """CRUD de NotaCama: crear, editar (texto + modificada_por), desactivar, listar activas."""

    async def crear_nota(
        self,
        session: AsyncSession,
        cama: CamaGestion,
        texto: str,
        creada_por_rol: str | None = None,
        creada_por_nombre: str | None = None,
    ) -> NotaCama:
        """Crea una NotaCama activa sobre la cama. Commit propio."""
        nota = NotaCama(
            cama_gestion_id=cama.id,
            texto=texto,
            creada_por_rol=creada_por_rol,
            creada_por_nombre=creada_por_nombre,
        )
        session.add(nota)
        await session.commit()
        return nota

    async def editar_nota(
        self,
        session: AsyncSession,
        nota: NotaCama,
        nuevo_texto: str,
        modificada_por_rol: str | None = None,
        modificada_por_nombre: str | None = None,
    ) -> NotaCama:
        """Actualiza el texto y estampa modificada_por/modificada_at. Commit propio."""
        nota.texto = nuevo_texto
        nota.modificada_por_rol = modificada_por_rol
        nota.modificada_por_nombre = modificada_por_nombre
        nota.modificada_at = _ahora()
        await session.commit()
        return nota

    async def desactivar_nota(
        self,
        session: AsyncSession,
        nota: NotaCama,
    ) -> NotaCama:
        """Borrado lógico: activa=False. El registro persiste en la base. Commit propio."""
        nota.activa = False
        await session.commit()
        return nota

    async def listar_notas_activas(
        self,
        session: AsyncSession,
        cama: CamaGestion,
    ) -> list[NotaCama]:
        """Devuelve las notas activas de la cama, ordenadas de más antigua a más nueva."""
        resultado = await session.execute(
            select(NotaCama)
            .where(NotaCama.cama_gestion_id == cama.id, NotaCama.activa.is_(True))
            .order_by(NotaCama.creada_at)
        )
        return list(resultado.scalars().all())
