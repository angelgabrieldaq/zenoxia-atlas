"""Script ejecutable para sembrar el hospital demo sintético en la base de Atlas.

Uso (desde la raíz del repo, con la base migrada en alembic head):

    python -m scripts.seed_demo            # siembra (idempotente: no duplica)
    python -m scripts.seed_demo --reset    # limpia datos operativos y resiembra

Lee la conexión de ``DATABASE_URL`` (del .env). Datos 100% ficticios — ver
``database/seeds.py::seed_hospital_demo``.
"""

from __future__ import annotations

import argparse
import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from database.models import CamaGestion
from database.seeds import seed_hospital_demo


async def _run(reset: bool) -> None:
    load_dotenv()
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        creadas = await seed_hospital_demo(session, reset=reset)

        # Verificación: conteo de camas por estado en la base.
        filas = (
            await session.execute(
                select(CamaGestion.estado_gestion, func.count())
                .group_by(CamaGestion.estado_gestion)
                .order_by(CamaGestion.estado_gestion)
            )
        ).all()

    await engine.dispose()

    if creadas:
        print(f"Camas creadas en esta corrida: {sum(creadas.values())}")
        for estado, n in sorted(creadas.items()):
            print(f"  {estado:<20} {n}")
    else:
        print("No se creó nada nuevo (el demo ya estaba sembrado).")

    print("\nCenso actual de camas por estado:")
    for estado, n in filas:
        print(f"  {estado.value:<20} {n}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Siembra el hospital demo sintético.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Vacía los datos operativos (camas, internaciones, pacientes...) y resiembra.",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.reset))


if __name__ == "__main__":
    main()
