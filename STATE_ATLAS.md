# STATE_ATLAS.md — Estado oficial del módulo Atlas
## Base de Conocimiento · Junio 2026

Fuente de verdad del estado actual del módulo Atlas dentro del ecosistema Zenoxia.
Leer antes de codear. Subordinado a `DECISIONES_ARQUITECTURA_CORE.md` (en zenoxia-core).

---

## 1. ESTADO ACTUAL

| Dimensión | Estado |
|---|---|
| **Tests** | **228 pasando** — suite completa verde (222 base + 6 nuevos: orden de traslado) |
| **Entorno** | Docker + PostgreSQL funcionando |
| **Fase** | Capa 1 (gestión operativa del día) en producción |
| **Backend** | FastAPI async + SQLAlchemy 2.0 + Alembic |
| **Migraciones** | 13 versiones aplicadas (hasta `c7a8b9d` — datos_traslado en egreso) |

### 1.1 Archivos de tests (11 módulos)

```
tests/
├── test_api_camas.py
├── test_api_egresos.py
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
> `computar_responsable`). La FSM acepta tanto `ADMISION` como `ENFERMERIA`
> en la transición `PROCESO_DE_ALTA → LIMPIEZA_TERMINAL` (deuda cerrada en commit 1ddd46a).

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
│       ├── egresos.py
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
├── alembic/            ← 11 migraciones aplicadas
├── tests/              ← 218 tests pasando (baseline verificado)
└── docs/               ← documentación de diseño
```

---

## 7. ENDPOINTS REST DEL EGRESO — Implementados

Los 8 endpoints están en `api/routers/egresos.py`, son wrappers finos sobre `ServicioEgreso`:

```
POST   /internaciones/{id}/egreso          crea egreso, inicializa checklists según medio
GET    /egresos/{id}                       egreso + computar_responsable() en vivo
PATCH  /egresos/{id}/checklist/{item_id}   marca ítem checklist
PATCH  /egresos/{id}/egreso-admin          ok administrativo (valida items legales)
PATCH  /egresos/{id}/salida-fisica         salida física → cama a LIMPIEZA_TERMINAL
PATCH  /egresos/{id}/limpieza/{item_id}    marca ítem limpieza (MantenimientoPendiente → 200)
PATCH  /egresos/{id}/discrepancia          registra discrepancia
POST   /egresos/{id}/notas                 agrega reclamo/novedad
```

La deuda de FSM (`ENFERMERIA` en salida física) quedó cerrada en commit 1ddd46a.

**Seguridad de roles en dominio (commits 4faca30 + 51b4665):**
- `marcar_item`: tres ramas —
  - `rol == item.responsable` → OK normal
  - `rol == ADMISION` + `item.responsable != 'admision'` + discrepancia `{motivo, nota}` → override permitido; persiste `Discrepancia` con `actor_rol=ADMISION` + hito `ATLAS_EGRESO_DISCREPANCIA`
  - `rol == ADMISION` sin discrepancia → `RolNoAutorizado` 403 ("requiere motivo")
  - cualquier otro rol distinto al responsable → `RolNoAutorizado` 403
- `ok_administrativo`: solo `ADMISION` (sin override)
- `marcar_item_limpieza`: mismo patrón de tres ramas (`LIMPIEZA`/`HOTELERIA` normales; `ADMISION` con discrepancia)
- Motivo nuevo en `DISCREP_MOTIVOS`: `"demora_responsable"` — para uso en overrides

**Endpoint adicional (3a):**
```
GET /internaciones/{id}/egreso-activo   discovery del egreso activo por internación
```
Más índice único parcial `uq_egreso_activo_por_internacion` en `egresos(internacion_local_id)`.

---

## 8. TRAMO CERRADO — 11 jun 2026 (commits hasta c8c29a1)

Frontend de egreso completo. Último commit pusheado: `c8c29a1`.

### 8.1 Lo entregado en este tramo

**Backend:**
- 8 endpoints de egreso + `GET /internaciones/{id}/egreso-activo` (discovery)
- Seguridad de roles en dominio: `RolNoAutorizado` → 403 para `marcar_item`, `ok_administrativo`, `marcar_item_limpieza`
- Override de ADMISION con `Discrepancia` obligatoria (`motivo` + `nota`); motivo `"demora_responsable"` en catálogo
- Fix **traza contaminada**: `GET /camas/{id}` filtra hitos por `internacion_actual_id OR NULL` (solo ve la internación vigente)
- Índice único parcial `uq_egreso_activo_por_internacion`
- 218 tests pasando (baseline 12 jun 2026; aritmética: 219 pre-fix − 2 eliminados + 1 nuevo = 218)
- 222 tests pasando (12 jun 2026; aritmética: 218 base + 4 nuevos = 222 — doble OK de limpieza, ver §9.3)
- 228 tests pasando (12 jun 2026; aritmética: 222 base + 6 nuevos = 228 — orden de traslado, ver §9.4)

**Frontend:**
- Panel de egreso reactivo: checklist, limpieza, OK admin, salida física, discrepancias, notas
- Override UI: `_pedirDiscrepancia()` con `prompt()` + validación contra `DISCREP_MOTIVOS`
- **Botón según rol**: "Marcar" visible solo si `state.rol` coincide con `item.responsable` o es ADMISION; limpieza: LIMPIEZA/HOTELERIA/ADMISION; resto ve "Pendiente: {responsable}"
- **Toast mejorado**: usa `.toast .toast--{kind}` del design system; errores 6 s; botón × para cerrar
- **Polling 15 s**: `setInterval` en DOMContentLoaded, pausa en `document.hidden`, recarga egreso si drawer abierto
- **Drawer scroll**: `height: 100vh` explícito

**Infra:**
- Bind mount `./:/app` + watchfiles hot-reload
- `node_modules/` en `.gitignore` + `git rm --cached`

### 8.2 Próximo borde — pantallas por rol

**Diseño acordado:**
- Una sola app; el rol cambia la vista (no rutas separadas).
- **ADMISION**: conserva el tablero actual (vista completa).
- **Resto de roles** (MEDICO, ENFERMERIA, LIMPIEZA, etc.): ven una lista plana de sus pendientes agrupada por cama — solo las camas donde tienen ítems sin marcar.

**Prerequisito backend:**
```
GET /egresos/pendientes?rol=X
```
Devuelve: `[{ cama_nombre, egreso_id, internacion_id, items_pendientes: [...] }]`  
(backlog #2 de `docs/MATRIZ_ROLES_Y_COLISIONES.md`)

**Pendiente decisión:**
- Orden de la lista del médico: ¿por tiempo trabado (más demorado primero), por sector, por número de ítems? Validar con dominio clínico antes de implementar.

---

## 9. CIERRE — 12 jun 2026 (commit bf64ed3)

### 9.1 Bug crítico cerrado — invariante de limpieza garantizada

**Problema:** `POST /camas/{id}/alta-fisica` ejecutaba la transición FSM directamente
sin crear `Egreso` ni checklist de limpieza → camas en `LIMPIEZA_TERMINAL` sin
trazabilidad (caso real registrado).

**Fix:**
- Endpoint `POST alta-fisica` → **410 Gone** (mensaje explicativo con el camino correcto).
- **Invariante garantizada:** el único camino HTTP a `LIMPIEZA_TERMINAL` es
  `PATCH /egresos/{egreso_id}/salida-fisica`, que crea egreso + checklist + hito.
- Seed corregido: `LIMPIEZA_TERMINAL` eliminado del patrón demo (requiere egreso real).
- 3 camas huérfanas del seed anterior (A-114, C-304, UCO-06) reparadas a `DISPONIBLE`
  con hito `ATLAS_CAMA_DISPONIBLE` de auditoría (`scripts/repair_orfanas.py`).
- Tests: 218/218 pasando; `test_alta_fisica_endpoint_obsoleto_devuelve_410` como
  contrato de no-regresión.

### 9.2 Relevamiento operativo incorporado

`docs/RELEVAMIENTO_OPERATIVO.md` — fuente primaria de dominio: hotelería (4 sub-roles),
agilización de altas (flujo real: WhatsApp + Excel + planilla → Atlas), limpieza
tercerizada (frontera contractual LIMPIEZA/HOTELERIA confirmada), circuito de
derivaciones, documentación legal formal, cadena del colchón y actor BIOINGENIERIA.

**Decisiones cerradas por el relevamiento** (ver §7 del doc):
- Doble OK limpieza: item 2 = solo HOTELERIA (frontera contractual, no de diseño).
- Orden médica → `requerido_legal=True` (pendiente confirmación final del fundador).
- Cola de hotelería filtra por sector/piso.

**Backlog que emerge del relevamiento:**

| Item | Origen | Estado |
|---|---|---|
| Guard item 2 solo-HOTELERIA + métrica espera de supervisión | §3 rel. | **Cerrado — commits 65ea24b + 0e42295 (ver §9.3)** |
| Item "orden de derivación" `requerido_legal=True` + campos destino/prestador | §4 rel. | Esperando confirmación |
| Flag prioridad de admisión en cola de limpieza | §1 rel. | Tramo pantallas por rol |
| Flag ADS + checklist de pronóstico nocturno | §2 rel. | Backlog |
| Bloqueo con motivo (reparación/cuarentena/activo) + actor BIOINGENIERIA | §6 rel. | Mini-tramo C4 |
| Circuito documental / empaquetado del egreso | §5 rel. | Doc de diseño, backlog |

### 9.3 Doble OK de limpieza con roles diferenciados — 12 jun 2026

**Fundamento:** `docs/RELEVAMIENTO_OPERATIVO.md §3` — frontera contractual confirmada:
la empresa tercerizada (LIMPIEZA) ejecuta la limpieza, la institución (HOTELERIA) la
supervisa. No es una preferencia de diseño: es una cláusula contractual.

**Cambios implementados (commits `65ea24b` + `2d9f062` + `0e42295`):**

- `ItemChecklistLimpieza` recibe campo `codigo` (String 20, NOT NULL) para identidad
  estable de los ítems, independiente del orden SQL.
- `_CATALOGO_LIMPIEZA` cambia de tuple de strings a tuple de `(codigo, label)`:
  - `EJECUCION` — "Cama limpiada según protocolo" → LIMPIEZA o HOTELERIA
  - `SUPERVISION` — "Control final — cama OK" → **solo HOTELERIA** (o ADMISION con discrepancia)
- Guard en `marcar_item_limpieza`: LIMPIEZA no puede marcar SUPERVISION → 403.
- Guard de orden: SUPERVISION no puede marcarse antes de EJECUCION done → 409.
  ADMISION con discrepancia bypasea el orden (caso de urgencia operativa).
- FSM `LIMPIEZA_TERMINAL → DISPONIBLE`: roles ampliados a `{LIMPIEZA, HOTELERIA, ADMISION}`
  (HOTELERIA es ahora quien dispara la transición final al marcar SUPERVISION).
- Migración `900995c` aplica el campo `codigo` con `server_default=''` + `alter_column`.
- Nuevo handler global: `EjecucionPendiente → 409` en `api/main.py`.

**Tests:**
- 4 tests nuevos en `test_api_egresos.py`: LIMPIEZA → 403, HOTELERIA con EJECUCION done → 200,
  HOTELERIA sin EJECUCION → 409, ADMISION con discrepancia → 200.
- `ROLES_ESPERADOS` en `test_state_machine.py` actualizado.
- Suite: **222/222 pasando**.

### 9.4 Orden de traslado legal + datos logísticos — 12 jun 2026

**Fundamento:** `docs/RELEVAMIENTO_OPERATIVO.md §4` + decisión del fundador (12 jun):
el ítem "Orden de traslado" es requerido_legal=True para ambulancia y derivacion.
El formulario ES el acto de transcribir la orden: sin datos logísticos no se puede marcar.

**Cambios implementados (commits `8c09cf8` + `8fa2a3b`):**

Backend:
- Catálogo: `("medico", "Orden de traslado emitida por el médico", True)` en `ambulancia` y `derivacion`.
- Modelo: campo `datos_traslado` JSONB nullable en `Egreso` (migración `c7a8b9d`).
- Schema: `DatosTraslado` Pydantic validado con 3 enums (`DestinoTipo`, `AccesibilidadDestino`, `InternacionDomiciliaria`).
  - `internacion_domiciliaria` tri-estado deliberado: `desconocido` es valor real del dominio, no null.
- Guard `marcar_item`: item de orden requiere `datos_traslado` → 422 si ausente.
- Guard `ok_administrativo`: ambulancia + domicilio + `internacion_domiciliaria=desconocido` → 409.
- `PATCH /egresos/{id}/datos-traslado`: edición post-marcado (admisión corrige al teléfono).
- 6 tests nuevos (222 → 228).

Frontend:
- Formulario modal al marcar el ítem de orden (8 campos, R2 hit targets 44px).
- Banner persistente en "Iniciar egreso" (reactivo al medio) y en el panel del egreso mientras la orden esté pendiente.
- `renderLimpiezaItem` diferenciado por `item.codigo`: SUPERVISION muestra "Pendiente: supervisión de hotelería" a LIMPIEZA.
- 409 de `EjecucionPendiente` llega como toast vía `ApiError.detail`.

---

## 10. DOCUMENTOS RELACIONADOS

- `docs/RELEVAMIENTO_OPERATIVO.md` — fuente primaria de dominio: flujos reales de hotelería, limpieza, derivaciones y documentación legal.
- `docs/MODELO_EGRESO_CERRADO.md` — diseño detallado del modelo de egreso (entidades, FSM, validaciones).
- `docs/DISENO_MODULO_ATLAS.md` — visión de las 3 capas del módulo.
- `docs/DISENO_TECNICO_ATLAS_CAPA1A.md` — especificación técnica Capa 1.
- `docs/MATRIZ_ROLES_Y_COLISIONES.md` — matriz de roles, responsabilidades y colisiones; insumo de pantallas por rol.
- `CLAUDE.md` — contexto operativo para Claude Code.
- `zenoxia-core/DECISIONES_ARQUITECTURA_CORE.md` — decisiones de arquitectura del ecosistema.
