# Modelo de Egreso — Cerrado

**Estado:** Prototipo validado. Listo para descender a entidades SQLAlchemy.  
**Fecha:** 5 jun 2026  
**Contexto:** Los 4 huecos transversales están integrados en el prototipo (`frontend/index.html`). El modelo no revela más descubrimientos.

---

## Los 4 Transversales (integrados)

### 1. **Responsable Actual (Computado)**

**Problema:** Cuando un egreso está trabado, no hay claridad sobre de quién es la "pelota".  
Cada rol se ve a sí mismo como listo e impulsa la culpa al otro.

**Solución en el prototipo:**
- Se computa en tiempo real cuál es el siguiente responsable, derivado del estado de los checklists.
- Se muestra prominentemente: `Esperando: MÉDICO (estudios)`, `Esperando: PRESTADOR EXTERNO (ambulancia)`, etc.
- No es un campo que alguien ingresa: emerge del modelo.

**Casos:**
- Cama de derivación con estudios pendientes → `Responsable: MÉDICO`
- Ambulancia ya solicitada, sin confirmación → `Responsable: PRESTADOR EXTERNO`
- Todo médico OK, pero sin apto de enfermería → `Responsable: ENFERMERÍA`
- Todo listo → `Responsable: ADMISIÓN (OK final)`

**En el backend:**
- Función `computar_responsable(egreso)` que retorna `{rol, tarea}` o `None` si no hay bloqueo.
- Se ejecuta al leer el egreso, no se almacena (es derivado).

---

### 2. **Egreso Administrativo ≠ Liberación Física**

**Problema:** Hoy colapsamos dos eventos:  
_OK final de admisión_ → _cama pasa a limpieza_.

Pero el paciente puede tener el OK administrativo y estar físicamente en la cama esperando que lo busquen (o una ambulancia que no llegó).

**Solución en el prototipo:**
- Dos eventos separados:
  1. **Egreso administrativo** (`egreso_admin_hora`): Admisión da OK final. Egreso cierra formalmente.
  2. **Salida física confirmada** (`salida_fisica`): Admisión confirma que el paciente abandonó la habitación.
  
- Nuevo estado de cama: `alta_admin` (egreso cerrado, pero cama ocupada).
- Cuando se confirma la salida física → `limp` (limpieza).

**Timeline:**
```
08:30 → OK administrativo del egreso
    Estado cama: alta_admin
    Paciente sigue en la habitación, esperando ambulancia

14:00 → Salida física confirmada
    Estado cama: limp
    Cama va a limpieza
```

**En el backend:**
- Campo `egreso_admin_hora` en entidad `Egreso`.
- Campo `salida_fisica_hora` en entidad `Egreso`.
- Evento separado: `egreso.paciente_abandono_habitacion()`.
- Máquina de estados de cama: `ALTA_ADMIN` (nuevo estado).

---

### 3. **Tiempo Trabado + Escalado**

**Problema:** Un egreso trabado durante 20 minutos se ve igual que uno trabado 4 horas.  
No hay reloj visible, ni escala a jefe de servicio.

**Solución en el prototipo:**
- Campo `trabado_desde` (hora en que el egreso bloqueó la cama).
- Cada vez que se renderiza, se computa `minutos_trabado = ahora - trabado_desde`.
- Si `minutos_trabado > 120` (2hs):
  - La tarjeta de cama se marca como `DEMORADO`.
  - Color rojo en la UI.
  - Se muestra: "⚠ Desde hace 145 minutos".
- Señal implícita de escalado (el jefe de servicio que mira el tablero ve todas las camas rojas).

**En el backend:**
- Campo `trabado_desde` en entidad `Egreso` (se setea cuando pasa a `estado='bloqueado'`).
- Lógica: si `egreso.trabado_desde < now() - 2hs` → mostrar como demorado.
- Futuro: endpoint que retorne egresos demorados para escalar a jefe.

---

### 4. **Liberación de Cama: Doble OK (Limpieza + Mantenimiento)**

**Problema:** La sesión pasada quedó pendiente:  
¿Cómo vuelve una cama a `DISPONIBLE` después de un egreso?

La realidad:
- Limpieza: la enfermería/hotelería limpia la cama.
- Mantenimiento: si hay daño/reparación, la comisión de mantenimiento revisa.
- Ambas son OK independientes y pueden ocurrir en cualquier orden.

**Solución en el prototipo:**
- Cuando se confirma la salida física, aparece checklist: `limpieza`.
- Dos items:
  1. Cama limpiada según protocolo (ej. Hotelería)
  2. Control final → cama OK (ej. Jefe de hotelería)
- Solo cuando ambos están ✓ → cama vuelve a `DISPONIBLE`.
- Es consistente con el patrón de checklists multi-rol de todo el egreso.

**Modelo:**
```
egreso.salida_fisica_hora = "14:05" → cama pasa a "limp"
  ├─ limp[0]: Cama limpiada según protocolo → done @ 14:15
  ├─ limp[1]: Control final — cama OK → done @ 14:20
  └─ Cuando ambos done → cama vuelve a DISPONIBLE
```

**En el backend:**
- Entidad `ItemChecklistLimpieza` (similar a `ItemChecklistEgreso`).
- Se crea cuando `egreso.salida_fisica_hora` se setea.
- Cama vuelve a `DISPONIBLE` solo si `limpieza.all(done=True)` Y (si aplica) `mantenimiento.all(done=True)`.

---

## Entidades que el modelo implica

### Nuevas o modificadas en `database/models.py`:

```python
# ─── EGRESO ───
class Egreso(Base):
    __tablename__ = 'egresos'
    
    internacion_local_id: int  # FK
    estado: str  # 'info', 'bloqueado', 'egreso_admin', 'liberado'
    medio_egreso: str  # 'camina', 'ambulancia', 'derivacion', 'traslado_interno'
    
    # Timestamps de eventos clave
    trabado_desde: datetime | None  # Cuando pasó a 'bloqueado'
    egreso_admin_hora: datetime | None  # OK administrativo
    salida_fisica_hora: datetime | None  # Paciente abandonó habitación
    
    # Relaciones
    items_checklist: list[ItemChecklistEgreso]
    discrepancias: list[Discrepancia]
    notas: list[NotaEgreso]
    limpieza_checklist: list[ItemChecklistLimpieza]

# ─── ITEM CHECKLIST EGRESO ───
class ItemChecklistEgreso(Base):
    __tablename__ = 'item_checklist_egreso'
    
    egreso_id: int  # FK
    responsable: str  # 'medico', 'enfermeria', 'admision'
    label: str
    requerido_legal: bool
    done: bool
    hora_marcado: datetime | None
    autor: str | None

# ─── DISCREPANCIA ───
class Discrepancia(Base):
    __tablename__ = 'discrepancias'
    
    egreso_id: int  # FK
    motivo: str  # Enum de DISCREP_MOTIVOS
    nota: str | None
    autor: str
    hora: datetime

# ─── NOTA EGRESO (reclamos/novedades) ───
class NotaEgreso(Base):
    __tablename__ = 'nota_egreso'
    
    egreso_id: int  # FK
    tipo: str  # 'reclamo', 'novedad'
    texto: str
    autor: str
    hora: datetime

# ─── ITEM CHECKLIST LIMPIEZA ───
class ItemChecklistLimpieza(Base):
    __tablename__ = 'item_checklist_limpieza'
    
    egreso_id: int  # FK
    label: str
    done: bool
    hora_marcado: datetime | None
    autor: str | None
```

### Máquina de estados de CAMA:

```
LIBRE
  ↓ (paciente admitido)
OCUPADA
  ↓ (médico da alta)
ALTA (estado antiguo del prototipo actual)
  ↓ (admisión da OK final)
ALTA_ADMIN ← NUEVO
  ↓ (paciente abandona habitación)
LIMPIEZA
  ↓ (todos OK de limpieza/mant)
DISPONIBLE
```

---

## Migraciones Alembic necesarias

1. `egresos` (nueva tabla)
2. `item_checklist_egreso` (nueva tabla)
3. `discrepancias` (nueva tabla)
4. `nota_egreso` (nueva tabla)
5. `item_checklist_limpieza` (nueva tabla)
6. `cama_gestion`: agregar transiciones a `ALTA_ADMIN`, cambios de máquina de estados

---

## Endpoints API (mínimo viable)

```
POST   /internaciones/{internacion_id}/egreso
       → crea egreso, inicializa checklists según medio

GET    /egreso/{egreso_id}
       → retorna egreso + computarResponsable() en tiempo real

PATCH  /egreso/{egreso_id}/checklist/{item_id}
       → marca un item como done

PATCH  /egreso/{egreso_id}/egreso_admin
       → setea egreso_admin_hora → estado pasa a ALTA_ADMIN

PATCH  /egreso/{egreso_id}/salida_fisica
       → setea salida_fisica_hora → estado cama pasa a LIMPIEZA
       → crea checklist_limpieza

PATCH  /egreso/{egreso_id}/discrepancia
       → registra discrepancia

POST   /egreso/{egreso_id}/nota
       → agrega reclamo/novedad
```

---

## Validaciones críticas

1. **Responsable actual no puede cambiar sin acción:** Si el responsable es MÉDICO (falta un item legal), no se puede avanzar hasta que MÉDICO lo marque.

2. **OK administrativo requiere documentación legal completa:** Antes de setear `egreso_admin_hora`, validar que todos los items `legal=True` del medio específico están `done=True`.

3. **Salida física solo después de OK administrativo:** No se puede llamar `salida_fisica` si `egreso_admin_hora` es `None`.

4. **Limpieza vuelve a DISPONIBLE solo con doble OK:** Cama permanece en LIMPIEZA hasta que todos los items de limpieza + mantenimiento sean `done=True`.

---

## Resumen: Modelo Cerrado

| Transversal | Integrado | Validado | Listo para backend |
|---|---|---|---|
| Responsable actual (computado) | ✓ | ✓ | ✓ |
| Egreso admin ≠ liberación física | ✓ | ✓ | ✓ |
| Tiempo trabado + escalado | ✓ | ✓ | ✓ |
| Doble OK limpieza+mant | ✓ | ✓ | ✓ |

**Próximo paso:** Descender a entidades en SQLAlchemy + migraciones Alembic.
