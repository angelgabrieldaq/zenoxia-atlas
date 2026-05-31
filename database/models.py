import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from database.enums import CategoriaInternacion, TipoComodidad


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
