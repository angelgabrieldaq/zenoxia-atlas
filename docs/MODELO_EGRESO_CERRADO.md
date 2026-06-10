# Modelo de Egreso — Cerrado (unificado)

**Estado:** Prototipo validado (4 transversales integrados en `frontend/index.html`).
Esta versión reconcilia el diseño del prototipo con la arquitectura federada del
ecosistema y los principios de interoperabilidad FHIR. Listo para descender a
entidades SQLAlchemy + Alembic.

**Reemplaza:** la versión previa de este mismo archivo y el borrador
`DECISIONES_MODELO_EGRESO.md` (no se sube como archivo aparte).

**Subordinado a:** `VISION_ECOSISTEMA_ZENOXIA.md` (modelo federado + principio de oro).

---

## 0. Principio de modelado: dos planos (estado vs. auditoría)

FHIR separa lo que nosotros separamos:

- **Estado vigente** (mutable, "lo que es ahora") → en Atlas: `cama_gestion.estado_gestion`
  y la entidad de proceso `Egreso`.
- **Registro de eventos** (inmutable, append-only, "lo que pasó") → `HitoTiempo` (core).

El egreso en curso es **estado**, no auditoría. Cada sub-evento que ocurre de verdad
sella un `HitoTiempo` real *en el momento real* (con su actor y su hora). **Nunca un
placeholder**: un timestamp vacío en una tabla inmutable afirma que algo pasó cuando
no pasó. Por eso los timestamps que "todavía no ocurrieron" viven como `NULL` honesto
en el plano de estado (`Egreso` / items de checklist), no como filas de auditoría.

---

## 1. Por qué Atlas existe (y el HIS no alcanza)

El HIS monolítico colapsa dos eventos en uno: marca el traslado/alta y al instante la
cama vieja "ya está" en limpieza y el paciente "ya está" en la nueva, aunque
físicamente siga en la habitación. El HIS modela el episodio administrativo; comprime
el tiempo físico a cero. Atlas modela ese intervalo. El HIS sirve como **ancla/
disparador** (un `HitoTiempo` `EGRESO_INFORMADO_POR_HIS`, `producto_origen='HIS'`, con
el único timestamp que dio), no como fuente de los tiempos operativos —esos los genera
Atlas—. (Absorción de PDF del HIS: aparcado.)

---

## 2. Los 4 transversales (integrados)

### 2.1 Responsable actual (computado, no almacenado)

Cuando un egreso se traba, no hay claridad sobre de quién es la pelota; cada rol se ve
listo y empuja la culpa al otro.

- Se computa en tiempo real el siguiente responsable, derivado del estado de los
  checklists: `Esperando: MÉDICO (estudios)`, `PRESTADOR EXTERNO (ambulancia)`,
  `ENFERMERÍA (apto)`, `ADMISIÓN (OK final)`.
- **Emerge del modelo, no se ingresa.** Backend: `computar_responsable(egreso) →
  {rol, tarea} | None`, ejecutado al leer el egreso. No se persiste.

### 2.2 Egreso administrativo ≠ liberación física (DOS eventos)

El paciente puede tener OK administrativo y seguir físicamente en la cama (esperando
ambulancia que no llegó).

- **Egreso administrativo** (`egreso_admin_at`): admisión da OK final; el egreso cierra
  formalmente. **La cama NO cambia de estado todavía** (sigue en `PROCESO_DE_ALTA`,
  paciente presente). El que cambia es `Egreso.estado` → `egreso_admin`.
- **Salida física** (`salida_fisica_at`): admisión confirma que el paciente abandonó la
  habitación. **Recién acá** la cama transiciona `PROCESO_DE_ALTA → LIMPIEZA_TERMINAL`.
- **No se crea un estado de cama `ALTA_ADMIN`** (ver §4): el "limbo admin-OK / paciente-
  presente" es un dato del *proceso*, no un estado de la *cama*. La card del mapa ya lee
  el `Egreso` (lo necesita para "Esperando: …"), así que distingue el limbo sin estado
  nuevo en la máquina de la cama.

### 2.3 Tiempo trabado + escalado

Un egreso trabado 20 min se ve igual que uno de 4 hs; no hay reloj ni señal a jefatura.

- `trabado_desde` (timestamp): se setea cuando el egreso bloquea la cama.
- Al renderizar: `minutos_trabado = ahora − trabado_desde` (computado, no almacenado).
- Sobre umbral configurable (default 120 min): card marcada `DEMORADO`, rojo, "⚠ Desde
  hace N minutos". Señal de escalado: la jefatura ve el tablero lleno de rojo.
- Futuro: endpoint que liste egresos demorados.

### 2.4 Liberación de cama: doble OK (limpieza + mantenimiento)

La cama vuelve a `DISPONIBLE` solo con doble OK independiente:

- **Limpieza**: checklist multi-ítem (protocolo), cada ítem con autor + hora propios.
- **Mantenimiento**: condicional (`mantenimiento_requerido`). **Reutiliza el mecanismo
  ya existente de Atlas** (`BLOQUEADA` + rol `MANTENIMIENTO` que bloquea/desbloquea por
  reparación) en lugar de inventar un checklist paralelo. Verificar en código si
  `BLOQUEADA` cubre el caso; si lo cubre, no se modela un segundo checklist.
- Gate a `DISPONIBLE`: `limpieza.all(done)` **Y** (si `mantenimiento_requerido`) cama no
  `BLOQUEADA` por mantenimiento. Pueden ocurrir en cualquier orden.

---

## 3. Fronteras federadas (FKs — corregido vs. borrador previo)

`Egreso` es **Atlas-específico**: vive en el repo de Atlas y se ata a la representación
**local**, no al core (el core se entera por sincronización, no por FK dura).

- `internacion_local_id` → **FK dura a `internacion_local.id`** (qué internación disparó
  el recambio = referencia/trigger). El link al `Episodio` del core ya lo tiene
  `internacion_local` vía `core_episodio_id`; `Egreso` no lo duplica.
- `cama_gestion_id` → **FK dura a `cama_gestion.id`** (la cama que el proceso gobierna =
  sujeto del ciclo). Justificación: habilita la invariante "un egreso activo por cama"
  como índice único parcial a nivel DB. El link al `LocationResource` del core lo tiene
  `cama_gestion` vía `core_location_id` (UUID sin FK); `Egreso` no apunta al core.
- Gobierna **`estado_gestion`** (`EstadoCamaGestion`), no el `estado_cache` del core.

---

## 4. Interacción de máquinas de estado — Opción A (sin máquina paralela)

Hay **dos** máquinas, correctamente separadas:

- `cama_gestion.estado_gestion` (existe): `DISPONIBLE / RESERVADA / OCUPADA /
  PROCESO_DE_ALTA / LIMPIEZA_TERMINAL / BLOQUEADA`.
- `Egreso.estado` (nueva): FSM del *proceso* de egreso.

**El `Egreso` gobierna las transiciones de la cama; no las duplica.** Mapeo:

| Evento del proceso | `Egreso.estado` | Transición de cama | ¿Toca state_machine.py? |
|---|---|---|---|
| Se crea el egreso (alta médica) | `info` | `OCUPADA → PROCESO_DE_ALTA` | No (ya existe) |
| Se traba (falta alguien) | `bloqueado` (+ `trabado_desde`) | queda en `PROCESO_DE_ALTA` | No |
| OK administrativo | `egreso_admin` (+ `egreso_admin_at`) | **queda en `PROCESO_DE_ALTA`** | No |
| Salida física | — (+ `salida_fisica_at`) | `PROCESO_DE_ALTA → LIMPIEZA_TERMINAL` | No (ya existe) |
| Doble OK limpieza/mant | `liberado` | `LIMPIEZA_TERMINAL → DISPONIBLE` | Solo el **guard** de mant |

**Resultado: cero transiciones nuevas en la máquina de la cama.** `PROCESO_DE_ALTA` ya
cubre el limbo administrativo; lo distingue `Egreso.estado`. El único cambio posible en
`state_machine.py` es agregar el **guard** de `mantenimiento_requerido` sobre la
transición ya existente `LIMPIEZA_TERMINAL → DISPONIBLE` (extensión, no transición
nueva). Esto descarta el `ALTA_ADMIN` del prototipo original: habría forzado dos
transiciones nuevas en la máquina de la cama para representar un dato que es del proceso.

---

## 5. Entidades (5 tablas — `Egreso` es el núcleo)

`Egreso` es el corazón; las otras cuatro son satélites que cuelgan de él. Convención de
nombres de columna temporal: sufijo `_at` (consistente con el core: `created_at`,
`registrado_at`). **Verificar `api/schemas.py` ya commiteado**: si el contrato con el
frontend usa `_hora`, mapear en la capa Pydantic, no renombrar a ciegas.

```python
# ─── EGRESO (núcleo / plano de estado del proceso) ───
class Egreso(Base):
    __tablename__ = 'egresos'
    id
    internacion_local_id   # FK → internacion_local.id  (trigger / referencia)
    cama_gestion_id        # FK → cama_gestion.id        (sujeto del ciclo)
    estado: str            # FSM proceso: info | bloqueado | egreso_admin | liberado
    medio_egreso: str      # camina | ambulancia | derivacion | traslado_interno
    mantenimiento_requerido: bool = False
    # Anclas de eventos (NULL honesto = todavía no pasó)
    created_at: datetime           # apertura del proceso (no nulo)
    trabado_desde: datetime | None # ancla del reloj de demora
    egreso_admin_at: datetime | None
    salida_fisica_at: datetime | None
    # NO hay limpieza_*_at ni disponible_at acá:
    #   - los tiempos de limpieza/mant viven en los items (hora_marcado + autor)
    #   - disponible queda en el HitoTiempo ATLAS_CAMA_DISPONIBLE (auditoría)
    items_checklist: list[ItemChecklistEgreso]
    discrepancias:   list[Discrepancia]
    notas:           list[NotaEgreso]
    limpieza_checklist: list[ItemChecklistLimpieza]

# ─── ITEM CHECKLIST EGRESO (médico / enfermería / admisión) ───
class ItemChecklistEgreso(Base):
    __tablename__ = 'item_checklist_egreso'
    id; egreso_id  # FK
    responsable: str       # medico | enfermeria | admision
    label: str
    requerido_legal: bool
    done: bool = False
    hora_marcado: datetime | None   # NULL hasta que ocurre de verdad
    autor: str | None

# ─── DISCREPANCIA (checklist médico no es bloqueo duro → evento de auditoría) ───
class Discrepancia(Base):
    __tablename__ = 'discrepancias'
    id; egreso_id  # FK
    motivo: str            # enum DISCREP_MOTIVOS (predefinidos)
    nota: str | None       # texto libre
    autor: str
    hora: datetime

# ─── NOTA EGRESO (reclamos / novedades — ambulancia, derivación) ───
class NotaEgreso(Base):
    __tablename__ = 'nota_egreso'
    id; egreso_id  # FK
    tipo: str              # reclamo | novedad
    texto: str
    autor: str
    hora: datetime

# ─── ITEM CHECKLIST LIMPIEZA ───
class ItemChecklistLimpieza(Base):
    __tablename__ = 'item_checklist_limpieza'
    id; egreso_id  # FK
    label: str
    done: bool = False
    hora_marcado: datetime | None
    autor: str | None
```

> Sellado de auditoría (capa de servicio, NO en esta etapa de entidades): cada vez que
> un ítem pasa a `done` o la cama transiciona, atómicamente se setea el estado y se
> escribe un `HitoTiempo` real. Los items con `done=False, hora_marcado=NULL` son el
> "esqueleto honesto" del ciclo: nulls que significan "todavía no", no auditoría falsa.

---

## 6. Validaciones críticas

1. **Responsable no avanza sin acción:** si el responsable es MÉDICO (falta ítem legal),
   no se avanza hasta que MÉDICO lo marque.
2. **OK admin requiere documentación legal completa:** antes de setear `egreso_admin_at`,
   todos los items `requerido_legal=True` del medio específico deben estar `done`.
3. **Salida física solo tras OK admin:** no se puede setear `salida_fisica_at` si
   `egreso_admin_at IS NULL`.
4. **Doble OK para DISPONIBLE:** la cama permanece en `LIMPIEZA_TERMINAL` hasta
   `limpieza.all(done)` Y (si `mantenimiento_requerido`) mantenimiento OK.
5. **Un egreso activo por cama:** índice único parcial sobre `cama_gestion_id` donde
   `estado != 'liberado'`. (Garantizado por el dominio: internación/terapia/UCO/UTI no
   reasignan cama sin pasar por limpieza; la "cama caliente" por urgencia es Cordis.)

---

## 7. Migraciones Alembic

1. `egresos` (nueva)
2. `item_checklist_egreso` (nueva)
3. `discrepancias` (nueva)
4. `nota_egreso` (nueva)
5. `item_checklist_limpieza` (nueva)
6. `state_machine.py`: **solo** el guard de `mantenimiento_requerido` en
   `LIMPIEZA_TERMINAL → DISPONIBLE`. **NO** se agrega estado `ALTA_ADMIN` ni transiciones
   nuevas a `cama_gestion`.

---

## 8. Endpoints API (mínimo viable)

```
POST   /internaciones/{id}/egreso          crea egreso, inicializa checklists según medio
GET    /egreso/{id}                         egreso + computar_responsable() en vivo
PATCH  /egreso/{id}/checklist/{item_id}     marca ítem done
PATCH  /egreso/{id}/egreso_admin            setea egreso_admin_at (valida items legales)
PATCH  /egreso/{id}/salida_fisica           setea salida_fisica_at → cama a LIMPIEZA_TERMINAL → crea checklist limpieza
PATCH  /egreso/{id}/discrepancia            registra discrepancia
POST   /egreso/{id}/nota                     agrega reclamo/novedad
```

---

## 9. Aparcado (no construir ahora)

- **Mapeo FHIR:** `Egreso` ↔ FHIR `Task` (proceso in-progress → completed); `HitoTiempo`
  ↔ `Provenance`. No construir; solo no cerrar la puerta.
- **Handoff Cordis (Escenario 5):** egreso de guardia a piso. Punto de encuentro = la
  cama en el core. Fase de integración.
- **Absorción de PDF del HIS** y envío por mail de documentos de egreso.
