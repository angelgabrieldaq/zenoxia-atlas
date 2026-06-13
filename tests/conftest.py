"""Setup de la base de tests: crea atlas_test y aplica migraciones una vez por sesión.

Solo se ejecuta una vez por invocación de pytest (scope="session"). Los tests
individuales truncan tablas dentro de esa base — nunca tocan la base de dev.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()


def _test_url() -> str:
    """Devuelve la URL de la base de tests.

    Prioridad: DATABASE_URL_TEST explícita. Si no está, deriva de DATABASE_URL
    reemplazando el nombre de base por atlas_test.
    """
    explicit = os.environ.get("DATABASE_URL_TEST")
    if explicit:
        return explicit
    dev = os.environ["DATABASE_URL"]
    return dev.rsplit("/", 1)[0] + "/atlas_test"


@pytest.fixture(scope="session", autouse=True)
def _setup_test_database():
    """Crea atlas_test (si no existe) y aplica alembic upgrade head."""
    import asyncpg
    from alembic import command
    from alembic.config import Config

    test_url = _test_url()

    # asyncpg no acepta el prefijo +asyncpg — convierte al scheme estándar
    asyncpg_url = test_url.replace("postgresql+asyncpg://", "postgresql://")
    admin_url = asyncpg_url.rsplit("/", 1)[0] + "/atlas"

    async def _create_if_not_exists() -> None:
        conn = await asyncpg.connect(admin_url)
        try:
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", "atlas_test"
            )
            if not exists:
                # CREATE DATABASE no puede correr dentro de una transacción
                await conn.execute("CREATE DATABASE atlas_test")
        finally:
            await conn.close()

    asyncio.run(_create_if_not_exists())

    # Migraciones: inyecta la URL de test en DATABASE_URL para que alembic/env.py la lea
    _prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_url
    try:
        alembic_cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        command.upgrade(alembic_cfg, "head")
    finally:
        if _prev is not None:
            os.environ["DATABASE_URL"] = _prev
        else:
            os.environ.pop("DATABASE_URL", None)
