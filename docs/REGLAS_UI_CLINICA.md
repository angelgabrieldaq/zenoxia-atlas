# Atlas/Zenoxia — Reglas de UI Clínica (destilado accionable)

**Estado:** Reglas adoptadas. Fuente: informe deepsearch de factores humanos
(11 jun 2026), filtrado con el mismo criterio que ESTRATEGIA_MERCADO: los
principios son doctrina HCI/NHS estándar y se adoptan; las cifras y estudios
citados (Michigan/emojis, Penda Health, "Veritas") NO se usan en pitch sin
sourcear la fuente primaria.
**Alcance:** Atlas hoy; Kairos hereda las secciones marcadas [QX].

---

## 1. Reglas que entran a Atlas YA (baratas, alto valor)

### R1 — Números tabulares
`font-variant-numeric: tabular-nums` en TODO dato numérico en grilla o
columna: minutos de trabado, contadores done/total, timestamps de hitos,
y a futuro cualquier dosis/vital. Razón: la fluctuación de ancho entre
dígitos rompe el escaneo vertical y puede inducir error de lectura de
magnitud. Costo: una línea de CSS sobre las clases de datos.

### R2 — Hit targets ≥ 44×44 px
Enfermería y limpieza van a operar Atlas desde tablet/celular, con guantes.
Todo botón accionable: mínimo 44×44 px de caja de colisión con 8 px de
respiro. Acciones de consecuencia (OK Administrativo, Confirmar Salida
Física, override con discrepancia): 48×48 px + 16 px de respiro — el error
de dedo en estas no se deshace gratis. Auditar los `btn-sm` actuales: si
el padding los deja bajo 44 px de alto táctil, agrandar el área tocable
(el ícono visual puede seguir chico; la caja no).

### R3 — Nunca íconos huérfanos
Regla permanente: ningún control accionable representado solo por un
símbolo. Siempre texto, o texto+ícono. Atlas hoy cumple (botones con
texto) — esta regla existe para que no se rompa cuando entre iconografía.

### R4 — Taxonomía de alertas en tres niveles
Ya construida a medias; se formaliza:
- **Toast** (lo que hoy tenemos): SOLO informativo/confirmación. Nunca
  para algo que requiera decisión.
- **Banner inline**: para advertencias en contexto que el usuario debe
  ver pero no bloquean (ej. "liberación bloqueada por mantenimiento" —
  hoy es toast efímero y por eso limpieza no se entera: DEBE ser banner
  persistente en el panel. Confirma el hallazgo C4 de la matriz).
- **Modal con justificación estructurada**: solo para acciones críticas
  irreversibles. El override de ADMISION ya sigue este espíritu (motivo
  obligatorio de un menú + nota) — cuando se reemplace el prompt() por
  UI propia, mantener: opciones de un toque, nada de texto libre
  obligatorio.
- Anti-patrón prohibido: hard stops sin salida. Todo bloqueo debe tener
  un camino con justificación auditada (nuestro patrón discrepancia).

### R5 — Sin emojis Unicode en la UI
Render inconsistente entre dispositivos + se corrupción en exportación.
Los glifos actuales (✓, ⚠, ×) son aceptables en MVP por ser símbolos
tipográficos simples, pero quedan registrados como deuda: migrar a SVG
inline cuando entre la iconografía. PROHIBIDO introducir emojis nuevos.

### R6 — Microinteracciones calmas
Nada de parpadeo, strobe ni animación agresiva para estados de alarma.
El estado DEMORADO (>120 min) se comunica con color sostenido y texto,
como ya está. Si algún día pulsa, que sea lento (ease-in-out, frecuencia
tipo respiración en reposo).

---

## 2. Validaciones de lo ya construido (el informe nos da la razón)

- **El drawer ES el patrón slide-over** que el informe prescribe contra
  el "abismo de los 20 clics": panel lateral sin abandonar el tablero. ✓
- **Navegación plana**: Atlas tiene una pantalla + drawer; cero anidación. ✓
- **Revelación progresiva**: tarjeta de cama resume, drawer profundiza. ✓
- **Soft-stop con justificación**: el override con discrepancia es
  exactamente la "gobernanza del override" que recomienda. ✓

## 3. Para después (no MVP, no abrir ahora)

- **Iconografía SVG propia**: NO hace falta para "subir de nivel". El
  propio informe demuestra que texto-solo supera a ícono-solo; texto+ícono
  es lo óptimo pero es pulido, no nivel. Cuando se haga: outline=inactivo,
  solid=seleccionado, stroke 2px constante, biblioteca SVG en repo (estilo
  NHS/Terra), jamás font de emojis.
- **[QX] Modo oscuro mesópico**: relevante para Kairos (quirófano con luz
  baja), no para Atlas (piso con luz normal). Cuando se haga: fondo
  #121212–#1C1C1C, texto #E0E0E0 — nunca #000/#FFF puros (halación).
- **[QX] Swimlanes con conflictos superpuestos**: patrón para el
  scheduling de quirófanos de Kairos.
- **Sparklines** junto a valores: para cuando Atlas muestre series
  (ej. ocupación histórica) o llegue ICU.
- **XAI/HITL**: no hay IA en Atlas. Se archiva el principio: toda
  sugerencia algorítmica futura desglosa sus variables y captura el
  rechazo del clínico.

## 4. Qué NO usar del informe de origen

- Cifras y estudios sin fuente primaria verificada (218M de notas de
  Michigan, tasas de override 49–96%, "Penda Health", "Veritas"). Pueden
  ser reales; no se citan en material comercial hasta sourcearlos.
- La prosa grandilocuente. Las reglas de arriba son el 100% del valor
  accionable.
