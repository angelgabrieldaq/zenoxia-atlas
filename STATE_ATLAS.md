# STATE_ATLAS.md — Estado oficial del módulo Atlas
## Base de Conocimiento · Junio 2026

Fuente de verdad del estado actual del módulo Atlas dentro del ecosistema Zenoxia.
Leer antes de codear. Subordinado a `DECISIONES_ARQUITECTURA_CORE.md` (en zenoxia-core).

---

## 1. ESTADO ACTUAL

| Dimensión | Estado |
|---|---|
| **Tests** | **193 pasando** — suite completa verde |
| **Entorno** | Docker + PostgreSQL funcionando |
| **Fase** | Capa 1 (gestión operativa del día) en producción |
| **Backend** | FastAPI async + SQLAlchemy 2.0 + Alembic |
| **Migraciones** | 10 versiones aplicadas (hasta `e8546bf` — egreso y satélites) |

### 1.1 Archivos de tests (11 módulos)

```
tests/
├── test_api_camas.py
├── test_discharge_catalog.py
├── test_discharge_checklist_service.py
├── test_discharge_responsibility.py
├── test_egreso_service.py
├── test_note_service.py
├── test_pass_service.py
├── test_reservation_service.py
├── test_state_machine.py
├── test_sync_interface.py
└── test_transition_service.py
```

---

## 2. CLASES PRINCIPALES

### 2.1 CamaGestion (plano mutable)

La entidad central del módulo. Representa el estado operativo actual de una cama.

```python
class CamaGestion(Base):
    __tablename__ = 'cama_gestion'

    id                  # UUID PK
    core_location_id    # UUID — referencia al LocationResource del core (SIN FK cruzada)
    estado_gestion      # EstadoCamaGestion (máquina de estados)
    # ... campos operativos
```

**Estados válidos de `EstadoCamaGestion`:**

```
DISPONIBLE
    │ OCUPAR
    ▼
OCUPADA
    │ INICIAR_ALTA
    ▼
PROCESO_DE_ALTA
    │ CONFIRMAR_SALIDA_FISICA
    ▼
LIMPIEZA_TERMINAL  ──► (guard: mantenimiento_requerido)
    │ COMPLETAR_LIMPIEZA + DISPONIBLE_MANTENIMIENTO
    ▼
DISPONIBLE

DISPONIBLE ──► RESERVADA ──► OCUPADA  (ruta de reserva)
CUALQUIER_ESTADO ──► BLOQUEADA ──► estado_previo  (mantenimiento)
```

### 2.2 Egreso (plano mutable — núcleo del proceso de egreso)

```python
class Egreso(Base):
    __tablename__ = 'egresos'

    id
    internacion_local_id    # FK dura → internacion_local.id (mismo módulo)
    cama_gestion_id         # FK dura → cama_gestion.id (mismo módulo)
    estado                  # FSM: info | bloqueado | egreso_admin | liberado | revertido
    medio_egreso            # camina | ambulancia | derivacion | traslado_interno | defuncion
    mantenimiento_requerido # bool
    created_at
    trabado_desde           # NULL hasta que se traba; ancla del reloj de demora
    egreso_admin_at         # NULL hasta que admisión da OK
    salida_fisica_at        # NULL hasta que el paciente sale físicamente
    items_checklist         # list[ItemChecklistEgreso]
    discrepancias           # list[Discrepancia]
    notas                   # list[NotaEgreso]
    limpieza_checklist      # list[ItemChecklistLimpieza]
```

### 2.3 HitoAtlas (plano inmutable — append-only)

Registro de auditoría inmutable de cada evento real en el módulo Atlas.
**Nunca se modifica ni elimina un hito ya escrito.**

Ejemplos de hitos: `ATLAS_CAMA_DISPONIBLE`, `ATLAS_EGRESO_ABIERTO`,
`ATLAS_SALIDA_FISICA`, `ATLAS_LIMPIEZA_COMPLETADA`.

Cada hito registra: código de evento, actor, rol, timestamp real, cama afectada.

---

## 3. LÓGICA DE EGRESOS — REGLAS DE ORO

### 3.1 Separación estricta: alta administrativa ≠ salida física

Son **DOS eventos distintos** con efectos distintos sobre la cama:

| Evento | Columna en Egreso | Efecto sobre la cama |
|---|---|---|
| **Alta administrativa** (`egreso_admin`) | `egreso_admin_at` | La cama **permanece** en `PROCESO_DE_ALTA`. El paciente sigue físicamente presente. |
| **Salida física** | `salida_fisica_at` | **Recién aquí** la cama transiciona `PROCESO_DE_ALTA → LIMPIEZA_TERMINAL`. |

**Por qué:** el paciente puede tener OK administrativo y seguir en la cama esperando
una ambulancia que no llegó. La cama no puede declararse libre hasta que el paciente
se vaya físicamente. Atlas modela ese intervalo que el HIS comprime a cero.

> **Quién confirma la salida física:** ENFERMERÍA (nivel 3 de la cascada de
> `computar_responsable`). La transición FSM se invoca internamente con rol
> `ADMISION` fijo — deuda declarada: aceptar `ENFERMERIA` en la FSM, pendiente.

### 3.2 Regla del responsable computado

El responsable actual del egreso **no se almacena**: se computa en tiempo real a
partir del estado de los checklists:

```python
computar_responsable(egreso) → {rol, tarea} | None
# Posibles resultados:
# "Esperando: MÉDICO (estudios pendientes)"
# "Esperando: PRESTADOR EXTERNO (ambulancia)"
# "Esperando: ENFERMERÍA (apto de traslado)"
# "Esperando: ADMISIÓN (OK final)"
# None → egreso desbloqueado
```

Este valor emerge del modelo; no se persiste.

### 3.3 Reloj de demora y escalado

- `trabado_desde` (timestamp): se setea cuando el egreso bloquea la cama.
- `minutos_trabado = ahora − trabado_desde` (computado al leer, no almacenado).
- Umbral configurable (default: 120 min): card marcada `DEMORADO` en rojo con
  "⚠ Desde hace N minutos". La jefatura ve el tablero como señal de escalado.

### 3.4 Regla de limpieza terminal → DISPONIBLE (guardia de mantenimiento)

La cama transiciona a `DISPONIBLE` **solo** con doble OK independiente:

1. **Limpieza**: todos los ítems del checklist (`limpieza_checklist.all(done)`)
   con autor + hora propios por ítem.
2. **Mantenimiento** (condicional): si `mantenimiento_requerido = True`, la cama no
   puede pasar a `DISPONIBLE` mientras esté `BLOQUEADA` por el rol `MANTENIMIENTO`.
   El mecanismo reutiza el bloqueo existente de Atlas — no se crea un checklist paralelo.

> **El guard de `mantenimiento_requerido` vive en la capa de servicio** (`MantenimientoPendiente`
> se lanza al marcar el último ítem de limpieza si la flag está activa), no en `state_machine.py`.
> La FSM no tiene transiciones nuevas (Opción A del modelo).

> **El único actor que puede liberar el bloqueo de mantenimiento es el rol `MANTENIMIENTO`
> (guardia de mantenimiento).** Limpieza y mantenimiento pueden completarse en cualquier orden.

Gate lógico:
```
cama → DISPONIBLE  ssi:
    limpieza_checklist.all(done)
    AND (mantenimiento_requerido == False OR cama NO está BLOQUEADA)
```

### 3.5 Invariante de unicidad

Un único egreso activo por cama en todo momento.
Índice único parcial **positivo** sobre `cama_gestion_id`
donde `estado IN ('info', 'bloqueado', 'egreso_admin')`.
`liberado` y `revertido` quedan fuera del índice, liberando el slot para el ciclo siguiente.

---

## 4. FRONTERAS FEDERADAS

Atlas respeta el contrato de arquitectura del core:

- **NO existen FKs cruzadas** hacia la DB del core.
- Referencias al core se materialzan como UUIDs:
  - `cama_gestion.core_location_id` → UUID del `LocationResource` del core.
  - `internacion_local.core_episodio_id` → UUID del `Episodio` del core.
- La sincronización de datos del core hacia Atlas se realiza por la interfaz
  `sync/core_sync.py` (o su stub `noop_sync.py` en entorno sin core).

---

## 5. VALIDACIONES CRÍTICAS

1. **Sin OK admin sin documentación legal completa:** todos los ítems
   `requerido_legal=True` del medio específico deben estar `done` antes de
   setear `egreso_admin_at`.
2. **Salida física solo tras OK admin:** `salida_fisica_at` requiere que
   `egreso_admin_at IS NOT NULL`.
3. **Doble OK para DISPONIBLE:** ver §3.4.
4. **Timestamps reales, nunca placeholders:** los ítems con `done=False,
   hora_marcado=NULL` son el "esqueleto honesto"; no se crean hitos de auditoría
   para eventos que aún no ocurrieron.

---

## 6. ESTRUCTURA DEL PROYECTO

```
zenoxia-atlas/
├── api/
│   ├── main.py
│   ├── schemas.py
│   ├── dependencies.py
│   └── routers/
│       ├── camas.py
│       └── internaciones.py
├── database/
│   ├── models.py       ← entidades SQLAlchemy
│   ├── enums.py        ← EstadoCamaGestion y otros enums
│   ├── session.py
│   └── seeds.py
├── domain/
│   ├── state_machine.py          ← FSM de cama_gestion
│   ├── egreso_service.py         ← ciclo de vida del egreso
│   ├── discharge_checklist_service.py
│   ├── discharge_responsibility.py  ← computar_responsable()
│   ├── discharge_catalog.py      ← catálogo de ítems por medio_egreso
│   ├── transition_service.py
│   ├── reservation_service.py
│   ├── pass_service.py
│   └── note_service.py
├── sync/
│   ├── core_sync.py    ← interfaz de sincronización con zenoxia-core
│   └── noop_sync.py    ← stub para tests sin core
├── alembic/            ← 10 migraciones aplicadas
├── tests/              ← 193 tests pasando
└── docs/               ← documentación de diseño
```

---

## 7. PRÓXIMO PASO — Endpoints REST del egreso

`ServicioEgreso` ya existe y está completo. El trabajo pendiente son los **7 endpoints**
que lo exponen como API (wrappers sobre el servicio):

```
POST   /internaciones/{id}/egreso          crea egreso, inicializa checklists según medio
GET    /egreso/{id}                         egreso + computar_responsable() en vivo
PATCH  /egreso/{id}/checklist/{item_id}     marca ítem done
PATCH  /egreso/{id}/egreso_admin            setea egreso_admin_at (valida items legales)
PATCH  /egreso/{id}/salida_fisica           setea salida_fisica_at → cama a LIMPIEZA_TERMINAL
PATCH  /egreso/{id}/discrepancia            registra discrepancia
POST   /egreso/{id}/nota                    agrega reclamo/novedad
```

**Follow-up FSM:** aceptar `ENFERMERIA` (además de `ADMISION`) en la transición
`PROCESO_DE_ALTA → LIMPIEZA_TERMINAL` en `state_machine.py` (deuda declarada).

---

## 8. DOCUMENTOS RELACIONADOS

- `docs/MODELO_EGRESO_CERRADO.md` — diseño detallado del modelo de egreso (entidades, FSM, validaciones).
- `docs/DISENO_MODULO_ATLAS.md` — visión de las 3 capas del módulo.
- `docs/DISENO_TECNICO_ATLAS_CAPA1A.md` — especificación técnica Capa 1.
- `CLAUDE.md` — contexto operativo para Claude Code.
- `zenoxia-core/DECISIONES_ARQUITECTURA_CORE.md` — decisiones de arquitectura del ecosistema.
