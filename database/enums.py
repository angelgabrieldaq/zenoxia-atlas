import enum


class CategoriaInternacion(str, enum.Enum):
    """Tipo clínico que motiva la internación; orienta la asignación de cama."""

    QUIRURGICA_PROGRAMADA = "QUIRURGICA_PROGRAMADA"
    QUIRURGICA_URGENCIA = "QUIRURGICA_URGENCIA"
    CLINICA = "CLINICA"
    GUARDIA_OBSERVACION = "GUARDIA_OBSERVACION"
    CRITICA = "CRITICA"
    OBSTETRICA = "OBSTETRICA"


class EstadoCamaGestion(str, enum.Enum):
    """Máquina de estados de gestión de Atlas (sobre el estado_cache del core)."""

    DISPONIBLE = "DISPONIBLE"
    RESERVADA = "RESERVADA"
    OCUPADA = "OCUPADA"
    PROCESO_DE_ALTA = "PROCESO_DE_ALTA"
    LIMPIEZA_TERMINAL = "LIMPIEZA_TERMINAL"
    BLOQUEADA = "BLOQUEADA"


class TipoComodidad(str, enum.Enum):
    """Categoría de comodidad de la cama (preferencia del paciente / cobertura)."""

    SIN_PREFERENCIA = "SIN_PREFERENCIA"
    COMPARTIDA = "COMPARTIDA"
    INDIVIDUAL = "INDIVIDUAL"
    SUITE = "SUITE"


class TipoCama(str, enum.Enum):
    """Tipo físico-asistencial de la cama (mapea a LocationResource del core)."""

    CAMA_INTERNACION = "CAMA_INTERNACION"
    UTI = "UTI"
    UCO = "UCO"


class MotivoReserva(str, enum.Enum):
    """Razón operativa por la que una cama queda reservada."""

    QUIRURGICA = "QUIRURGICA"
    PASE_INTERNO = "PASE_INTERNO"
    INGRESO_PROGRAMADO = "INGRESO_PROGRAMADO"


class EstadoReserva(str, enum.Enum):
    """Ciclo de vida de una reserva de cama."""

    ACTIVA = "ACTIVA"
    CUMPLIDA = "CUMPLIDA"
    CANCELADA = "CANCELADA"
    VENCIDA = "VENCIDA"


class EstadoPase(str, enum.Enum):
    """Ciclo de vida de un pase entre servicios/niveles (eslabón de enroque)."""

    SOLICITADO = "SOLICITADO"
    CAMA_ASIGNADA = "CAMA_ASIGNADA"
    EN_TRASLADO = "EN_TRASLADO"
    CONFIRMADO = "CONFIRMADO"
    CANCELADO = "CANCELADO"
