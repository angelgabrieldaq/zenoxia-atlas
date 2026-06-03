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
    pass


class FinalizarLimpiezaBody(_RolBase):
    pass


class BloquearBody(_RolBase):
    motivo_bloqueo: str = Field(..., min_length=1, max_length=200)


class DesbloquearBody(_RolBase):
    pass


# ------------------------------------------------------------------ #
# Respuesta de error estándar
# ------------------------------------------------------------------ #

class ErrorOut(BaseModel):
    detail: str
