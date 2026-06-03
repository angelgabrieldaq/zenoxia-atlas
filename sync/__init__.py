"""Capa de sincronización con el core de Zenoxia (§12).

El acoplamiento al core vive AISLADO acá: un contrato abstracto (``CoreSync``) + la
implementación no-op (``NoOpCoreSync``) que usa Atlas en capa 1a (modelo federado, sin
core). La lógica de negocio depende del contrato; en Fase 3 se inyecta un ``RealCoreSync``
con la misma interfaz, sin tocarla.
"""

from sync.core_sync import CoreSync
from sync.noop_sync import NoOpCoreSync

__all__ = ["CoreSync", "NoOpCoreSync"]
