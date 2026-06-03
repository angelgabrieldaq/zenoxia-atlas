"""Semillas de datos de Atlas (capa 1a).

Dos seeds:

1. ``seed_pasos_alta_catalogo``: el catálogo inicial de pasos de alta
   (``PasoAltaCatalogo``). Configuración por institución — un set razonable de arranque
   que luego el admin prende/apaga/edita. Idempotente por ``codigo``.

2. ``seed_hospital_demo``: datos SINTÉTICOS para poblar el tablero de camas y poder verlo
   con vida. Todo es 100% inventado y obviamente ficticio (códigos genéricos, pacientes
   "Demo/Ejemplo/Muestra", DNIs falsos secuenciales): NO hay ninguna referencia a personas,
   hospitales ni instituciones reales. Idempotente por ``nombre`` de cama. Es un seed de
   ESTADO INICIAL: setea estados y vínculos directo (no hace replay de transiciones), pero
   el resultado es coherente (ninguna OCUPADA sin paciente, ninguna RESERVADA sin reserva).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.enums import (
    CategoriaInternacion,
    EstadoCamaGestion,
    EstadoReserva,
    MotivoReserva,
    TipoCama,
    TipoComodidad,
)
from database.models import (
    CamaGestion,
    InternacionLocal,
    PacienteLocal,
    PasoAltaCatalogo,
    Reserva,
)

# Catálogo de arranque. (codigo, nombre, categoria_aplica, bloqueante, orden).
# categoria_aplica=None → universal (aplica a toda internación).
PASOS_ALTA_INICIALES: list[tuple[str, str, CategoriaInternacion | None, bool, int]] = [
    ("EPICRISIS_FIRMADA", "Epicrisis firmada por el médico", None, True, 10),
    ("MEDICACION_CONCILIADA", "Medicación del alta conciliada", None, True, 20),
    ("RESUMEN_ALTA_ENTREGADO", "Resumen de alta entregado al paciente", None, False, 30),
    ("FACTURACION_CERRADA", "Facturación / liquidación cerrada", None, False, 40),
    (
        "CONTROL_POST_UTI_AGENDADO",
        "Control post-UTI agendado",
        CategoriaInternacion.CRITICA,
        False,
        50,
    ),
]


async def seed_pasos_alta_catalogo(session: AsyncSession) -> list[PasoAltaCatalogo]:
    """Inserta los pasos del catálogo que falten (por ``codigo``). Idempotente.

    Devuelve solo los pasos NUEVOS que se crearon (lista vacía si ya estaban todos).
    Un único commit al final."""
    existentes = set(
        (await session.execute(select(PasoAltaCatalogo.codigo))).scalars().all()
    )
    creados: list[PasoAltaCatalogo] = []
    for codigo, nombre, categoria, bloqueante, orden in PASOS_ALTA_INICIALES:
        if codigo in existentes:
            continue
        paso = PasoAltaCatalogo(
            codigo=codigo,
            nombre=nombre,
            categoria_aplica=categoria,
            bloqueante=bloqueante,
            activo=True,
            orden=orden,
        )
        session.add(paso)
        creados.append(paso)
    if creados:
        await session.commit()
    return creados


# --------------------------------------------------------------------------- #
# Seed sintético del hospital demo (datos ficticios para el tablero)
# --------------------------------------------------------------------------- #

# Sectores genéricos del demo: (sector, prefijo, tipo de cama, cantidad).
# Códigos neutros A-1xx / B-2xx / C-3xx / UTI-0x / UCO-0x. Nada institucional.
_SECTORES_DEMO: list[tuple[str, str, TipoCama, int]] = [
    ("Internacion General - Piso 1", "A-1", TipoCama.CAMA_INTERNACION, 15),
    ("Internacion General - Piso 2", "B-2", TipoCama.CAMA_INTERNACION, 15),
    ("Cuidados Intermedios", "C-3", TipoCama.CAMA_INTERNACION, 10),
    ("UTI", "UTI-", TipoCama.UTI, 8),
    ("Unidad Coronaria", "UCO-", TipoCama.UCO, 7),
]

# Patrón de estados que se cicla sobre las camas (índice global). Pesado hacia
# DISPONIBLE/OCUPADA, con presencia garantizada de los demás (como un hospital real).
_PATRON_ESTADOS: tuple[EstadoCamaGestion, ...] = (
    EstadoCamaGestion.DISPONIBLE,
    EstadoCamaGestion.OCUPADA,
    EstadoCamaGestion.DISPONIBLE,
    EstadoCamaGestion.OCUPADA,
    EstadoCamaGestion.DISPONIBLE,
    EstadoCamaGestion.OCUPADA,
    EstadoCamaGestion.DISPONIBLE,
    EstadoCamaGestion.RESERVADA,
    EstadoCamaGestion.DISPONIBLE,
    EstadoCamaGestion.OCUPADA,
    EstadoCamaGestion.PROCESO_DE_ALTA,
    EstadoCamaGestion.DISPONIBLE,
    EstadoCamaGestion.OCUPADA,
    EstadoCamaGestion.LIMPIEZA_TERMINAL,
    EstadoCamaGestion.DISPONIBLE,
    EstadoCamaGestion.OCUPADA,
    EstadoCamaGestion.DISPONIBLE,
    EstadoCamaGestion.BLOQUEADA,
    EstadoCamaGestion.OCUPADA,
    EstadoCamaGestion.DISPONIBLE,
)

# Piezas para armar nombres CLARAMENTE ficticios. Combinadas con un contador dan
# "Ana Ejemplo", "Juan Muestra", "Paciente Demo", etc. Nadie real.
_NOMBRES_DEMO = (
    "Paciente", "Ana", "Juan", "Sofia", "Carlos",
    "Lucia", "Pedro", "Marta", "Diego", "Elena",
)
_APELLIDOS_DEMO = ("Demo", "Ejemplo", "Muestra", "Ficticio", "Prueba", "Sintetico")

# Comodidades a ciclar (restricción interna de asignación, no preferencia del paciente).
_COMODIDADES = (
    TipoComodidad.SIN_PREFERENCIA,
    TipoComodidad.COMPARTIDA,
    TipoComodidad.INDIVIDUAL,
)

# Datos de cobertura ficticios (Atlas muestra, no interpreta).
_COBERTURAS_DEMO = (
    "Obra Social Ejemplo A",
    "Prepaga Demo B",
    "Cobertura Publica C",
    "Mutual Ficticia D",
)
_PLANES_DEMO = ("Plan 100", "Plan 210", "Plan Basico", "Plan Integral")
_NOTAS_COBERTURA_DEMO = (
    "Cubre habitacion individual - demo",
    "Requiere autorizacion previa - demo",
    None,
    None,
    None,
)

# Estados que llevan un paciente vinculado en la cama (internacion_actual_id no nulo).
_ESTADOS_CON_INTERNACION = frozenset(
    {
        EstadoCamaGestion.OCUPADA,
        EstadoCamaGestion.PROCESO_DE_ALTA,
        EstadoCamaGestion.RESERVADA,
    }
)

# Tablas que el modo --reset vacía (datos operativos del demo). El catálogo de pasos de
# alta (configuración) NO se toca.
_TABLAS_RESET = (
    "hito_atlas",
    "nota_cama",
    "reserva",
    "pase_servicio",
    "paso_alta_internacion",
    "internacion_local",
    "paciente_local",
    "cama_gestion",
)


def _categoria_y_servicio(tipo: TipoCama) -> tuple[CategoriaInternacion, str]:
    """Categoría operativa + código de servicio coherentes con el tipo de cama."""
    if tipo is TipoCama.UTI:
        return CategoriaInternacion.CRITICA, "UTI"
    if tipo is TipoCama.UCO:
        return CategoriaInternacion.CRITICA, "UCO"
    return CategoriaInternacion.CLINICA, "CLIN"


def _generar_plano_camas() -> list[tuple[str, str, TipoCama, EstadoCamaGestion, int]]:
    """Plano determinístico del hospital demo.

    Devuelve (nombre, sector, tipo, estado, indice_global) por cama. El estado sale de
    ciclar ``_PATRON_ESTADOS`` con el índice global, así la distribución es estable y
    cubre todos los EstadoCamaGestion."""
    plano: list[tuple[str, str, TipoCama, EstadoCamaGestion, int]] = []
    indice = 0
    for sector, prefijo, tipo, cantidad in _SECTORES_DEMO:
        for n in range(1, cantidad + 1):
            # A-1 + 01 → "A-101"; UTI- + 01 → "UTI-01".
            nombre = f"{prefijo}{n:02d}"
            estado = _PATRON_ESTADOS[indice % len(_PATRON_ESTADOS)]
            plano.append((nombre, sector, tipo, estado, indice))
            indice += 1
    return plano


async def _reset_demo(session: AsyncSession) -> None:
    """Vacía los datos operativos (modo limpiar y resembrar). NO toca el catálogo de pasos."""
    from sqlalchemy import text

    await session.execute(
        text(f"TRUNCATE {', '.join(_TABLAS_RESET)} RESTART IDENTITY CASCADE")
    )
    await session.commit()


async def seed_hospital_demo(
    session: AsyncSession, *, reset: bool = False
) -> dict[str, int]:
    """Siembra ~55 camas sintéticas con datos coherentes para el tablero.

    Idempotente: salta las camas cuyo ``nombre`` ya existe (no duplica). Con ``reset=True``
    primero vacía los datos operativos y resiembra desde cero.

    Por cada cama crea, según su estado, los datos asociados que el modelo exige:

    * OCUPADA / PROCESO_DE_ALTA → un ``PacienteLocal`` + ``InternacionLocal`` ligada por
      ``internacion_actual_id`` (el paciente está/sigue en la cama);
    * RESERVADA → paciente + internación + ``Reserva`` ACTIVA del tipo de la cama
      (``internacion_actual_id`` apuntando a esa internación, como deja la transición real);
    * LIMPIEZA_TERMINAL → sin internación (el paciente ya egresó);
    * BLOQUEADA → ``motivo_bloqueo`` ficticio, sin paciente;
    * DISPONIBLE → cama limpia, sin nada.

    Devuelve un conteo {estado: cantidad de camas creadas} para verificación.
    """
    if reset:
        await _reset_demo(session)

    # Idempotencia: no recrear camas ya presentes (por nombre).
    plano = _generar_plano_camas()
    nombres = [p[0] for p in plano]
    existentes = set(
        (
            await session.execute(
                select(CamaGestion.nombre).where(CamaGestion.nombre.in_(nombres))
            )
        ).scalars().all()
    )

    creadas_por_estado: dict[str, int] = {}
    dni_seq = 30000001  # DNIs falsos obvios y secuenciales.
    paciente_seq = 0

    for nombre, sector, tipo, estado, indice in plano:
        if nombre in existentes:
            continue

        cama = CamaGestion(
            nombre=nombre,
            tipo=tipo,
            sector=sector,
            comodidad=_COMODIDADES[indice % len(_COMODIDADES)],
            estado_gestion=estado,
        )

        if estado is EstadoCamaGestion.BLOQUEADA:
            cama.motivo_bloqueo = "Mantenimiento de equipo - demo"

        # Datos asociados según el estado (coherencia del censo).
        if estado in _ESTADOS_CON_INTERNACION:
            nombre_pac = _NOMBRES_DEMO[paciente_seq % len(_NOMBRES_DEMO)]
            apellido_pac = _APELLIDOS_DEMO[paciente_seq % len(_APELLIDOS_DEMO)]
            categoria, servicio = _categoria_y_servicio(tipo)

            paciente = PacienteLocal(
                dni=str(dni_seq),
                nombre=f"{nombre_pac} {paciente_seq + 1:02d}",
                apellido=apellido_pac,
                nhc_externo=f"DEMO-NHC-{paciente_seq + 1:04d}",
            )
            session.add(paciente)
            await session.flush()  # asigna paciente.id

            internacion = InternacionLocal(
                paciente_local_id=paciente.id,
                categoria=categoria,
                comodidad_requerida=_COMODIDADES[indice % len(_COMODIDADES)],
                servicio_codigo=servicio,
                cobertura=_COBERTURAS_DEMO[paciente_seq % len(_COBERTURAS_DEMO)],
                plan_cobertura=_PLANES_DEMO[paciente_seq % len(_PLANES_DEMO)],
                numero_socio=f"SOC-DEMO-{paciente_seq + 1:05d}",
                nota_cobertura=_NOTAS_COBERTURA_DEMO[paciente_seq % len(_NOTAS_COBERTURA_DEMO)],
            )
            session.add(internacion)
            await session.flush()  # asigna internacion.id

            cama.internacion_actual_id = internacion.id
            session.add(cama)
            await session.flush()  # asigna cama.id (para la Reserva)

            if estado is EstadoCamaGestion.RESERVADA:
                session.add(
                    Reserva(
                        cama_gestion_id=cama.id,
                        internacion_id=internacion.id,
                        motivo=MotivoReserva.INGRESO_PROGRAMADO,
                        estado=EstadoReserva.ACTIVA,
                        tipo_cama_requerido=tipo,
                    )
                )

            dni_seq += 1
            paciente_seq += 1
        else:
            # DISPONIBLE / LIMPIEZA_TERMINAL / BLOQUEADA: sin internación ligada.
            session.add(cama)

        creadas_por_estado[estado.value] = creadas_por_estado.get(estado.value, 0) + 1

    await session.commit()
    return creadas_por_estado
