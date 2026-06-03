"""Implementación NO-OP de la capa de sincronización (capa 1a, §12).

``NoOpCoreSync`` cumple el contrato ``CoreSync`` sin hacer nada: es la implementación que
usa Atlas mientras el core no está presente (modelo federado). Permite que el código de
negocio "sincronice" sin condicionales ni errores: cada método es un no-op que devuelve la
entidad TAL CUAL la recibió (no llena ids de enlace, no marca ``sincronizado_core``).

En Fase 3 se agrega ``RealCoreSync(CoreSync)`` con el conector real al core/HIS, con la
MISMA interfaz; pasar de una a otra es cambiar QUÉ instancia se inyecta, sin tocar la
lógica de negocio.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sync.core_sync import CoreSync

if TYPE_CHECKING:
    from database.models import CamaGestion, HitoAtlas, InternacionLocal, PacienteLocal

logger = logging.getLogger(__name__)


class NoOpCoreSync(CoreSync):
    """No-op: cumple ``CoreSync`` sin tocar el core. Devuelve cada entidad sin cambios.

    Loguea a nivel DEBUG que se invocó (sirve para verificar que la lógica pasa por la capa
    de sync y no llama al core directo), pero no hace IO ni muta nada.
    """

    async def sincronizar_paciente(self, paciente_local: PacienteLocal) -> PacienteLocal:
        """No-op: devuelve el paciente sin enlazar (``core_patient_id`` intacto)."""
        logger.debug("NoOpCoreSync.sincronizar_paciente: no-op (sin core)")
        return paciente_local

    async def sincronizar_internacion(
        self, internacion_local: InternacionLocal
    ) -> InternacionLocal:
        """No-op: devuelve la internación sin enlazar (``core_episodio_id`` intacto)."""
        logger.debug("NoOpCoreSync.sincronizar_internacion: no-op (sin core)")
        return internacion_local

    async def sincronizar_cama(self, cama_gestion: CamaGestion) -> CamaGestion:
        """No-op: devuelve la cama sin enlazar (``core_location_id`` / estado intactos)."""
        logger.debug("NoOpCoreSync.sincronizar_cama: no-op (sin core)")
        return cama_gestion

    async def replicar_hito(self, hito_atlas: HitoAtlas) -> HitoAtlas:
        """No-op: devuelve el hito sin replicar (``sincronizado_core`` queda en False)."""
        logger.debug("NoOpCoreSync.replicar_hito: no-op (sin core)")
        return hito_atlas
