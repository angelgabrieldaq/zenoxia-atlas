"""Repara camas seeded directamente en LIMPIEZA_TERMINAL sin egreso ni hitos.

A-114, C-304, UCO-06: artefactos del seed que seteó estado_gestion=LIMPIEZA_TERMINAL
sin pasar por FSM. Solución: moverlas a DISPONIBLE con un hito de auditoría que deja
trazabilidad del fix. No hay internacion ni egreso que rescatar — sólo corrección de dato.
"""
import asyncio
import uuid
from datetime import datetime, timezone

from database.session import AsyncSessionLocal
from database.models import CamaGestion, HitoAtlas, Egreso
from database.enums import EstadoCamaGestion
from sqlalchemy import select

ORFANAS = ["A-114", "C-304", "UCO-06"]


async def main():
    async with AsyncSessionLocal() as s:
        for nombre in ORFANAS:
            cama = (await s.execute(
                select(CamaGestion).where(CamaGestion.nombre == nombre)
            )).scalar_one_or_none()
            if not cama:
                print(f"  {nombre}: NO ENCONTRADA — skip")
                continue

            egreso = (await s.execute(
                select(Egreso).where(Egreso.cama_gestion_id == cama.id)
            )).scalar_one_or_none()
            if egreso:
                print(f"  {nombre}: TIENE EGRESO — no es un artefacto de seed, skip")
                continue

            if cama.estado_gestion != EstadoCamaGestion.LIMPIEZA_TERMINAL:
                print(f"  {nombre}: estado={cama.estado_gestion.value} — no es LIMPIEZA_TERMINAL, skip")
                continue

            cama.estado_gestion = EstadoCamaGestion.DISPONIBLE

            hito = HitoAtlas(
                id=uuid.uuid4(),
                cama_gestion_id=cama.id,
                internacion_id=None,
                hito_codigo="ATLAS_CAMA_DISPONIBLE",
                actor_rol="SISTEMA",
                actor_nombre="repair_orfanas.py",
                registrado_at=datetime.now(timezone.utc),
                metadata_evento={
                    "motivo": "Corrección: cama seeded en LIMPIEZA_TERMINAL sin egreso (artefacto de seed). Movida a DISPONIBLE."
                },
            )
            s.add(hito)
            print(f"  {nombre}: LIMPIEZA_TERMINAL → DISPONIBLE (hito de auditoría creado)")

        await s.commit()
        print("\nListo.")


asyncio.run(main())
