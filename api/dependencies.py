"""Dependencias de FastAPI: sesión de base de datos e instancias de servicios."""

from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base
from domain.discharge_checklist_service import ServicioChecklistAlta
from domain.note_service import ServicioNotas
from domain.reservation_service import ServicioReservas
from domain.transition_service import ServicioTransiciones

# La URL se lee desde la variable de entorno configurada en .env. Si no hay override,
# usa el default del docker-compose (útil en tests que inyecten su propio engine).
import os
from dotenv import load_dotenv

load_dotenv()

_DATABASE_URL = os.environ["DATABASE_URL"]

_engine = create_async_engine(_DATABASE_URL, echo=False)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency que provee una AsyncSession por request. Cierra al salir."""
    async with _session_factory() as session:
        yield session


# Servicios stateless: una instancia global compartida (B2 no guarda estado).
_transiciones = ServicioTransiciones()
_reservas = ServicioReservas(_transiciones)
_checklist = ServicioChecklistAlta(_transiciones)
_notas = ServicioNotas()


def get_transiciones() -> ServicioTransiciones:
    return _transiciones


def get_reservas() -> ServicioReservas:
    return _reservas


def get_checklist() -> ServicioChecklistAlta:
    return _checklist


def get_notas() -> ServicioNotas:
    return _notas
