import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from database.enums import (
    CategoriaInternacion,
    EstadoCamaGestion,
    EstadoPase,
    EstadoReserva,
    MotivoReserva,
    TipoCama,
    TipoComodidad,
)


class Base(AsyncAttrs, DeclarativeBase):
    pass


class PacienteLocal(Base):
    """Representación local mínima del paciente. NO guarda dato clínico
    (sexo, fecha de nacimiento, diagnósticos). Se vincula al core vía core_patient_id."""

    __tablename__ = "paciente_local"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dni: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    apellido: Mapped[str] = mapped_column(String(100), nullable=False)
    core_patient_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    nhc_externo: Mapped[str | None] = mapped_column(String(50), nullable=True)
    creado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    internaciones: Mapped[list["InternacionLocal"]] = relationship(
        back_populates="paciente_local"
    )


class InternacionLocal(Base):
    """Episodio de internación gestionado por Atlas. Si existe el Episodio del core,
    se enlaza vía core_episodio_id. La categoría orienta la asignación de cama."""

    __tablename__ = "internacion_local"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    paciente_local_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paciente_local.id"),
        nullable=False,
        index=True,
    )
    categoria: Mapped[CategoriaInternacion] = mapped_column(
        Enum(CategoriaInternacion, name="categoria_internacion"),
        nullable=False,
        index=True,
    )
    comodidad_requerida: Mapped[TipoComodidad | None] = mapped_column(
        Enum(TipoComodidad, name="tipo_comodidad"),
        nullable=True,
    )
    servicio_codigo: Mapped[str | None] = mapped_column(String(10), nullable=True)
    core_episodio_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    iniciada_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    finalizada_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    paciente_local: Mapped["PacienteLocal"] = relationship(
        back_populates="internaciones"
    )


class CamaGestion(Base):
    """Cama física desde la perspectiva de gestión de Atlas. Lleva la máquina de
    estados propia (incluye RESERVADA, que el core no tiene). Si existe el
    LocationResource del core, se enlaza vía core_location_id."""

    __tablename__ = "cama_gestion"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    tipo: Mapped[TipoCama] = mapped_column(
        Enum(TipoCama, name="tipo_cama"), nullable=False, index=True
    )
    comodidad: Mapped[TipoComodidad | None] = mapped_column(
        Enum(TipoComodidad, name="tipo_comodidad"), nullable=True
    )
    sector: Mapped[str] = mapped_column(String(50), nullable=False)
    estado_gestion: Mapped[EstadoCamaGestion] = mapped_column(
        Enum(EstadoCamaGestion, name="estado_cama_gestion"),
        nullable=False,
        default=EstadoCamaGestion.DISPONIBLE,
        index=True,
    )
    internacion_actual_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("internacion_local.id", ondelete="SET NULL"),
        nullable=True,
    )
    motivo_bloqueo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    core_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    actualizado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    creado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HitoAtlas(Base):
    """Registro de auditoría append-only de eventos de gestión de cama.
    actor_rol y hito_codigo son String libre (no enum) para mantener independencia
    del core y permitir evolución sin migraciones de tipo.
    metadata_evento nunca almacena dato clínico.

    Contrato del servicio que crea hitos (B2): DEBE estampar internacion_id y
    cama_gestion_id también dentro de metadata_evento (id redundante), para que
    el hito sea autocontenido y viaje completo a la sincronización con el core
    (Fase 3), independiente de la FK."""

    __tablename__ = "hito_atlas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    internacion_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("internacion_local.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    cama_gestion_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cama_gestion.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    hito_codigo: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    actor_rol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor_nombre: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metadata_evento: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    registrado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    sincronizado_core: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


class Reserva(Base):
    """Cama apartada para una internación que aún no llegó (§7). Sostiene la validación
    quirúrgica cruzada (tipo de cama requerido vs. tipo de la cama).

    Lleva el ciclo de vida de la reserva en sí (ACTIVA → CUMPLIDA / CANCELADA). El estado
    de la CAMA no se toca acá: lo cambia ServicioTransiciones (B2), única fuente de verdad
    del estado_gestion. VENCIDA queda definido en el enum pero no se usa en 1a (vencer es
    decisión humana / capa 2)."""

    __tablename__ = "reserva"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cama_gestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cama_gestion.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    internacion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("internacion_local.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    motivo: Mapped[MotivoReserva] = mapped_column(
        Enum(MotivoReserva, name="motivo_reserva"), nullable=False
    )
    estado: Mapped[EstadoReserva] = mapped_column(
        Enum(EstadoReserva, name="estado_reserva"),
        nullable=False,
        default=EstadoReserva.ACTIVA,
        index=True,
    )
    # Reusa el enum tipo_cama ya creado por la migración de cama_gestion (la migración
    # de Reserva lo referencia con create_type=False; acá el modelo lo nombra igual).
    tipo_cama_requerido: Mapped[TipoCama] = mapped_column(
        Enum(TipoCama, name="tipo_cama"), nullable=False
    )
    motivo_cancelacion: Mapped[str | None] = mapped_column(String(200), nullable=True)
    creada_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    resuelta_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PaseServicio(Base):
    """Pase de un paciente entre camas/niveles (§8): el eslabón del enroque. Registra el
    ISBAR como HECHO (que ocurrió), nunca su contenido clínico.

    Orquestado por ServicioPases, que apoya la RESERVA de la cama destino y la LIBERACIÓN
    de la origen. El estado de cada cama lo cambia SIEMPRE ServicioTransiciones (B2);
    acá vive el ciclo de vida del pase (SOLICITADO → CAMA_ASIGNADA → EN_TRASLADO →
    CONFIRMADO, o → CANCELADO)."""

    __tablename__ = "pase_servicio"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    internacion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("internacion_local.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    cama_origen_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cama_gestion.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # null hasta que se asigna la cama destino (CAMA_ASIGNADA).
    cama_destino_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cama_gestion.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # la reserva que aparta la cama destino (desde CAMA_ASIGNADA).
    reserva_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reserva.id", ondelete="RESTRICT"),
        nullable=True,
    )
    estado: Mapped[EstadoPase] = mapped_column(
        Enum(EstadoPase, name="estado_pase"),
        nullable=False,
        default=EstadoPase.SOLICITADO,
        index=True,
    )
    # Reusa el enum tipo_cama ya creado (la migración lo referencia con create_type=False).
    tipo_cama_destino: Mapped[TipoCama] = mapped_column(
        Enum(TipoCama, name="tipo_cama"), nullable=False
    )
    motivo_cancelacion: Mapped[str | None] = mapped_column(String(200), nullable=True)
    solicitado_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    confirmado_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelado_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
