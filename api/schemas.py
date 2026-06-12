"""Schemas Pydantic (DTOs) de la API REST de Atlas. Contrato público de los endpoints.

Separados de los modelos ORM: los endpoints nunca devuelven objetos SQLAlchemy directos.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from database.enums import (
    CategoriaInternacion,
    EstadoCamaGestion,
    TipoCama,
    TipoComodidad,
    TipoReversion,
)
from domain.state_machine import RolOperativo


# ------------------------------------------------------------------ #
# Camas
# ------------------------------------------------------------------ #

class CamaOut(BaseModel):
    """Vista resumida de una cama para el tablero."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nombre: str
    tipo: TipoCama
    sector: str
    comodidad: TipoComodidad | None
    estado_gestion: EstadoCamaGestion
    internacion_actual_id: uuid.UUID | None
    motivo_bloqueo: str | None
    actualizado_at: datetime


class HitoOut(BaseModel):
    """Hito de auditoría para el detalle de cama."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hito_codigo: str
    actor_rol: str | None
    actor_nombre: str | None
    internacion_id: uuid.UUID | None
    registrado_at: datetime
    metadata_evento: dict | None


class NotaCamaOut(BaseModel):
    """Nota operativa activa sobre una cama."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    texto: str
    creada_por_rol: str | None
    creada_por_nombre: str | None
    creada_at: datetime
    modificada_at: datetime | None


class CamaDetalleOut(CamaOut):
    """Vista detallada de cama: cama + hitos recientes + notas activas."""

    hitos: list[HitoOut] = Field(default_factory=list)
    notas: list[NotaCamaOut] = Field(default_factory=list)


# ------------------------------------------------------------------ #
# Internaciones
# ------------------------------------------------------------------ #

class InternacionOut(BaseModel):
    """Vista de una internación local con datos mínimos del paciente."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    categoria: CategoriaInternacion
    comodidad_requerida: TipoComodidad | None
    servicio_codigo: str | None
    cobertura: str | None
    plan_cobertura: str | None
    numero_socio: str | None
    nota_cobertura: str | None
    iniciada_at: datetime
    finalizada_at: datetime | None
    paciente_local_id: uuid.UUID
    # Campos del paciente desnormalizados en la respuesta (evita un join al cliente).
    paciente_dni: str | None = None
    paciente_nombre: str | None = None
    paciente_apellido: str | None = None


class InternacionCreate(BaseModel):
    """Body para crear una nueva internación. Busca o crea el PacienteLocal por DNI."""

    dni: str = Field(..., min_length=1, max_length=8)
    nombre: str = Field(..., min_length=1, max_length=100)
    apellido: str = Field(..., min_length=1, max_length=100)
    categoria: CategoriaInternacion
    comodidad_requerida: TipoComodidad | None = None
    servicio_codigo: str | None = Field(None, max_length=10)
    cobertura: str | None = Field(None, max_length=100)
    plan_cobertura: str | None = Field(None, max_length=60)
    numero_socio: str | None = Field(None, max_length=60)
    nota_cobertura: str | None = None


# ------------------------------------------------------------------ #
# Bodies de acciones semánticas sobre camas
# ------------------------------------------------------------------ #

class _RolBase(BaseModel):
    rol: RolOperativo
    actor_nombre: str | None = None


class OcuparBody(_RolBase):
    internacion_id: uuid.UUID


class ReservarBody(_RolBase):
    internacion_id: uuid.UUID
    tipo_cama_requerido: TipoCama


class IniciarAltaBody(_RolBase):
    pass


class AltaFisicaBody(_RolBase):
    forzar: bool = False
    motivo_override: str | None = None


class FinalizarLimpiezaBody(_RolBase):
    pass


class BloquearBody(_RolBase):
    motivo_bloqueo: str = Field(..., min_length=1, max_length=200)


class DesbloquearBody(_RolBase):
    pass


class CancelarReservaBody(_RolBase):
    motivo_cancelacion: str = Field(..., min_length=1, max_length=200)


class RevertirAltaBody(_RolBase):
    tipo_reversion: TipoReversion
    motivo_reversion: str = Field(..., min_length=1, max_length=200)


class PasoAltaInternacionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    internacion_id: uuid.UUID
    paso_catalogo_id: uuid.UUID
    codigo: str | None = None
    nombre: str | None = None
    era_bloqueante: bool
    completado: bool
    completado_por_rol: str | None = None
    completado_por_nombre: str | None = None
    completado_at: datetime | None = None
    creada_at: datetime


class CompletarPasoBody(_RolBase):
    pass


class NotaCamaCreate(_RolBase):
    texto: str = Field(..., min_length=1, max_length=500)


# ------------------------------------------------------------------ #
# Egresos
# ------------------------------------------------------------------ #

class ItemChecklistEgresoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    responsable: str
    requerido_legal: bool
    done: bool
    hora_marcado: datetime | None
    autor: str | None


class ItemChecklistLimpiezaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    codigo: str
    label: str
    done: bool
    hora_marcado: datetime | None
    autor: str | None


class DiscrepanciaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    motivo: str
    nota: str | None
    autor: str
    hora: datetime


class NotaEgresoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tipo: str
    texto: str
    autor: str
    hora: datetime


class EgresoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    internacion_local_id: uuid.UUID
    cama_gestion_id: uuid.UUID
    estado: str
    medio_egreso: str
    mantenimiento_requerido: bool
    created_at: datetime
    trabado_desde: datetime | None
    egreso_admin_at: datetime | None
    salida_fisica_at: datetime | None


class EgresoDetalleOut(EgresoOut):
    items_checklist: list[ItemChecklistEgresoOut] = Field(default_factory=list)
    discrepancias: list[DiscrepanciaOut] = Field(default_factory=list)
    notas: list[NotaEgresoOut] = Field(default_factory=list)
    limpieza_checklist: list[ItemChecklistLimpiezaOut] = Field(default_factory=list)
    responsable_actual: dict | None = None
    minutos_trabado: float | None = None


class MarcarLimpiezaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    codigo: str
    label: str
    done: bool
    hora_marcado: datetime | None
    autor: str | None
    liberacion_bloqueada: str | None = None


class CrearEgresoBody(_RolBase):
    medio_egreso: str


class _DiscrepanciaOverride(BaseModel):
    """Discrepancia inline para el override de ADMISION en checklist/limpieza."""
    motivo: str
    nota: str | None = None


class MarcarItemChecklistBody(_RolBase):
    no_aplica: bool = False
    discrepancia: _DiscrepanciaOverride | None = None


class OkAdministrativoBody(_RolBase):
    pass


class ConfirmarSalidaFisicaBody(_RolBase):
    metadata: dict | None = None


class MarcarItemLimpiezaBody(_RolBase):
    discrepancia: _DiscrepanciaOverride | None = None


class RegistrarDiscrepanciaBody(_RolBase):
    motivo: str
    nota: str | None = None


class AgregarNotaEgresoBody(_RolBase):
    tipo: str
    texto: str


# ------------------------------------------------------------------ #
# Respuesta de error estándar
# ------------------------------------------------------------------ #

class ErrorOut(BaseModel):
    detail: str
