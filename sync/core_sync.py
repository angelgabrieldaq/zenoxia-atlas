"""Capa de sincronización con el core de Zenoxia — CONTRATO (capa 1a, §12).

REGLA DE ORO
------------
La lógica de negocio de Atlas NUNCA llama al core directamente. Todo el acoplamiento al
core (Patient, Episodio, LocationResource, HitoTiempo) vive AISLADO en este módulo
``sync/``, detrás de este contrato. Los servicios de dominio dependen de la abstracción
``CoreSync``, no de una implementación concreta (inversión de dependencias): reciben una
instancia que cumpla el contrato y la usan sin saber si hay core detrás.

Si el core no está presente (Atlas corriendo solo, modelo federado), se inyecta la
implementación no-op (``NoOpCoreSync``) y Atlas funciona igual: "sincronizar" no hace nada
y no falla. En Fase 3 se agrega ``RealCoreSync(CoreSync)`` con el conector real, con la
MISMA interfaz, sin tocar una sola línea de la lógica de negocio.

Dirección de la sincronización: Atlas es la fuente de verdad del estado de gestión de la
cama; al core se le REPLICA (estado_cache, HitoTiempo) y del core se LEE la identidad
canónica (Patient/Episodio/LocationResource) para llenar los ids de enlace locales
(core_patient_id, core_episodio_id, core_location_id). Atlas nunca origina dato clínico.

En capa 1a SOLO existe la interfaz; no hay conector real (es Fase 3).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Solo para los type hints: el contrato es interfaz pura y no necesita los modelos en
    # runtime (no hay implementación que los use). Mantiene la capa desacoplada del ORM.
    from database.models import CamaGestion, HitoAtlas, InternacionLocal, PacienteLocal


class CoreSync(ABC):
    """Contrato de la capa de sincronización con el core (§12).

    Una implementación mantiene "en acuerdo" la representación local de Atlas con la entidad
    canónica del core, cuando el core coexiste. Atlas depende de ESTA abstracción; la
    implementación concreta (no-op hoy, real en Fase 3) se inyecta desde afuera.

    Todos los métodos son async (en Fase 3 hacen IO contra el core/HIS) e idempotentes por
    diseño: volver a sincronizar una entidad ya enlazada no debe duplicar nada. Cada método
    devuelve la entidad recibida (enriquecida en Fase 3, sin cambios en el no-op).
    """

    @abstractmethod
    async def sincronizar_paciente(self, paciente_local: PacienteLocal) -> PacienteLocal:
        """Concilia el ``PacienteLocal`` con el ``Patient`` del core por DNI (clave de negocio).

        Fase 3: busca/crea el Patient en el core por DNI y llena ``core_patient_id`` en el
        PacienteLocal (lo deja enlazado). No trae dato clínico: solo el id canónico.
        Hoy (no-op): devuelve el paciente sin cambios (``core_patient_id`` queda como estaba).
        """
        ...

    @abstractmethod
    async def sincronizar_internacion(
        self, internacion_local: InternacionLocal
    ) -> InternacionLocal:
        """Concilia la ``InternacionLocal`` con el ``Episodio`` del core.

        Fase 3: enlaza/crea el Episodio y llena ``core_episodio_id``; cruza el diagnóstico
        fino del core para los insights SIN guardarlo en Atlas (cero dato clínico local).
        Hoy (no-op): devuelve la internación sin cambios.
        """
        ...

    @abstractmethod
    async def sincronizar_cama(self, cama_gestion: CamaGestion) -> CamaGestion:
        """Concilia la ``CamaGestion`` con el ``LocationResource`` del core.

        Fase 3: enlaza/crea el LocationResource y llena ``core_location_id``; replica el
        estado mapeando ``estado_gestion`` → ``estado_cache`` del core (Atlas es la fuente
        de verdad del estado de la cama). Hoy (no-op): devuelve la cama sin cambios.
        """
        ...

    @abstractmethod
    async def replicar_hito(self, hito_atlas: HitoAtlas) -> HitoAtlas:
        """Replica un ``HitoAtlas`` (append-only) al ``HitoTiempo`` del core.

        Fase 3: crea el HitoTiempo equivalente (``producto_origen="Atlas"``) y marca
        ``hito_atlas.sincronizado_core = True`` cuando la réplica quedó confirmada.
        Hoy (no-op): devuelve el hito sin cambios (``sincronizado_core`` queda en False).
        """
        ...
