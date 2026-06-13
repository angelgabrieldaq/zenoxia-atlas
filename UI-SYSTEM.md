# Zenoxia Atlas — UI-SYSTEM.md

**Módulo:** Gestión de Camas  
**Versión UI:** 3.0  
**Stack:** FastAPI (backend) + Vanilla TS (frontend)

---

## 1. Tokens de diseño

Atlas consume `design-tokens.css` v3.0 desde `/frontend/design-tokens.css`.  
Ver [zenoxia-core/design/UI-SYSTEM.md] para la referencia completa de tokens.

### Paleta de estados de cama

| Estado | CSS class | Color token | Hex |
|---|---|---|---|
| Disponible | `.libre` | `--cama-libre` | `#1B8A3A` |
| Ocupada | `.ocup` | `--cama-ocup` | `#2B57D9` |
| Reservada | `.demo` | `--cama-demo` | `#7C3AED` |
| En alta | `.alta` | `--cama-alta` | `#B45309` |
| Limpieza terminal | `.limp` | `--cama-limp` | `#0369A1` |
| Bloqueada | `.bloq` | `--cama-bloq` | `#C0281F` |

---

## 2. Arquitectura de la UI

### Componentes principales

```
app.ts
├── State (Proxy reactivo)
│   ├── camas[]
│   ├── internacionesById{}
│   ├── detalle (Cama | null)
│   ├── egreso (Egreso | null)
│   └── pendingAction
│
├── renderBoard()           → Grilla de camas por sector
├── renderDetalle()         → Panel lateral (drawer)
│   ├── renderAccionesArea()
│   ├── renderEgresoPanel()
│   └── renderCrearEgresoPanel()
├── renderResumen()         → Chips de conteo por estado
└── renderToast()           → Feedback visual
```

### Patrón reactivo
El estado vive en un `Proxy`. Cada asignación a `state.X` dispara automáticamente el renderer correspondiente. No se usa ningún framework externo.

---

## 3. Flujo de egreso

```
OCUPADA → [Médico: iniciar-alta] → PROCESO_DE_ALTA
  └── Crear egreso (admisión selecciona medio)
      └── Checklist multi-rol (médico / enfermería / admisión / hotelería)
          ├── OK Administrativo (admisión)
          └── Confirmar Salida Física → LIMPIEZA_TERMINAL
              └── Checklist de limpieza (limpieza / hotelería)
                  └── Cama → DISPONIBLE
```

### Tipos de egreso
| Medio | Orden de traslado requerida |
|---|---|
| Camina | No |
| Traslado interno | No |
| Ambulancia | Sí — modal con datos de destino |
| Derivación | Sí — modal con datos de destino |
| Defunción | No |

---

## 4. Roles y permisos de UI

| Rol | Acciones visibles |
|---|---|
| ADMISION | Todas las acciones + overrides con discrepancia |
| MEDICO | Iniciar alta |
| ENFERMERIA | Ocupar reserva |
| MANTENIMIENTO | Bloquear / Desbloquear |
| LIMPIEZA | Marcar ítems de limpieza terminal |
| HOTELERIA | Supervisión de limpieza |
| OPERACIONES | Solo lectura en tablero |

---

## 5. CSS y responsive

```html
<!-- En index.html (orden obligatorio) -->
<link rel="stylesheet" href="design-tokens.css">
<link rel="stylesheet" href="responsive.css">
<link rel="stylesheet" href="styles.css">
```

- Breakpoints: 375 / 768 / 1024 / 1440 px
- `.cama-grid`: `auto-fill` con columnas de `minmax(160px, 1fr)` — se adapta sola
- Drawer: `position: fixed` lateral en desktop, `bottom-sheet` en móvil
- Formularios de acción: inputs de `height: 44px` mínimo

---

## 6. TypeScript

- `app.ts` contiene todos los tipos del dominio: `Cama`, `Internacion`, `Egreso`, `ChecklistItem`, `LimpiezaItem`, `Discrepancia`, `AppState`
- Compilar: `cd frontend && tsc`
- El archivo compilado `dist/app.js` reemplaza a `app.js`

---

## 7. Accesibilidad

- Cards de cama: `role="button"` implícito via `cursor: pointer` + `onClick`
- Toast: aria-live region (implementar en v3.1)
- Foco visible en todos los botones de acción
- Contraste de color verificado: estado `libre` (#1B8A3A sobre #fff) = 5.1:1 ✓
