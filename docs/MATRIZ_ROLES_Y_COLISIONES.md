# Atlas — Matriz de Roles Operativos y Colisiones

**Estado:** Diseño para discusión. Insumo del tramo "pantallas de un botón".
**Fecha:** 11 jun 2026
**Fuente:** Convención de roles del ecosistema, catálogos de egreso, cascada de
`computar_responsable`, proceso de óbito, y evidencia empírica del HAR de la
prueba manual (un rol frente a items ajenos, sin saber qué puede tocar).

---

## 1. Propósito

Simular la jornada de cada rol operativo alrededor de la cama para responder
tres preguntas antes de diseñar pantallas:

1. ¿Qué hace cada rol en Atlas y qué pantalla mínima necesita?
2. ¿Dónde se pisan los roles entre sí (colisiones del día a día)?
3. ¿Qué resuelve ya el modelo y qué huecos quedan?

Regla de lectura: **✓ = el modelo actual lo resuelve** · **⚠ = hueco o deuda**.

---

## 2. Matriz por rol

### MEDICO
| Dimensión | Detalle |
|---|---|
| Jornada (vs. cama) | Pasa visita, decide altas. Su participación en el egreso es puntual: epicrisis, indicaciones, resumen de traslado, certificado de defunción. Después desaparece del circuito — y es el cuello de botella más medido (30 min–3 hs en óbito). |
| Acciones en Atlas | Iniciar alta. Marcar sus items de checklist. Nada más. |
| Pantalla mínima | **Lista "mis pendientes"**: items `responsable=medico` de todos los egresos activos, agrupados por cama, ordenados por tiempo trabado descendente. Un botón por item. Cero tablero. |
| Fricción a evitar | Que tenga que abrir cama por cama para encontrar qué le falta. Si la pantalla no es más rápida que ignorar el sistema, lo va a ignorar. |

### ENFERMERIA
| Dimensión | Detalle |
|---|---|
| Jornada | Está físicamente en el piso todo el turno. Es quien VE al paciente irse. Retira vías/dispositivos, da el apto de traslado, confirma salida física. |
| Acciones en Atlas | Marcar sus items. **Confirmar salida física** (FSM ya la acepta junto a ADMISION ✓). |
| Pantalla mínima | Lista "mis pendientes" + botón destacado de salida física en camas `ALTA_ADMIN`. Es el rol que más cerca está del evento físico real. |
| Fricción a evitar | Confirmar salida es un segundo de trabajo real; si en el sistema toma más de dos toques, lo van a registrar tarde o nunca — y muere la métrica de bloqueo de cama. |

### ADMISION
| Dimensión | Detalle |
|---|---|
| Jornada | Orquesta. Rinde cuentas económicas de la cama. Persigue al médico, contiene a la familia (óbito), llama a la ambulancia, da el OK final, y cuando el circuito se traba **pasa por encima** dejando constancia. |
| Acciones en Atlas | Todo: crear egreso, sus items, OK administrativo, salida física, override con discrepancia ✓, registrar discrepancias y notas. |
| Pantalla mínima | **El tablero rico actual.** Es el único rol que necesita la foto completa + el reloj de demorados. |
| Fricción a evitar | Ninguna nueva: el tablero ya es suyo. |

### HOTELERIA / LIMPIEZA
| Dimensión | Detalle |
|---|---|
| Jornada | Reciben la cama después de la salida física. Limpieza terminal + control final. Hoy no se parten como roles (convención: Hotelería predomina; se reevalúa con el módulo de Servicios al Paciente). |
| Acciones en Atlas | Marcar los 2 items de limpieza (ambos roles válidos ✓). |
| Pantalla mínima | Lista de camas en `LIMPIEZA_TERMINAL` con sus items. Es la pantalla de un botón más pura de todas. |
| Fricción a evitar | ⚠ Cuando marcan todo y la cama NO se libera por mantenimiento pendiente, hoy reciben un toast y nada más. Necesitan ver el **porqué** persistente en su pantalla, o van a re-marcar y reportar "el sistema no anda". |

### MANTENIMIENTO
| Dimensión | Detalle |
|---|---|
| Jornada | Reactivo: entra cuando hay daño. Bloquea/desbloquea camas, resuelve lo que frena la liberación. |
| Acciones en Atlas | Desbloquear cama. Resolver `mantenimiento_requerido`. |
| Pantalla mínima | Lista de camas `BLOQUEADA` + camas en limpieza con mantenimiento pendiente. |
| Fricción a evitar | ⚠ Hoy no hay forma visible de saber QUÉ hay que reparar — el bloqueo existe pero el motivo/detalle del trabajo no se modela. Ver colisión C4. |

### CAMILLERO
| Dimensión | Detalle |
|---|---|
| Jornada | Mueve pacientes. En el egreso aparece en `traslado_interno` (paciente que cambia de cama dentro del sanatorio). |
| Acciones en Atlas | ⚠ **Ninguna hoy.** El traslado es dominio del core/Cordis (`Traslado`, `EstadoTraslado`). Atlas registra que el medio es traslado interno pero no orquesta al camillero. |
| Decisión de frontera | Atlas NO construye pantalla de camillero. Cuando coexista con Cordis, el traslado interno del egreso dispara una solicitud de `Traslado` vía core. Hasta entonces, el hito de salida física basta. **No inventar logística de camilleros dentro de Atlas** — violaría el principio de oro. |

### COORD_ENFERMERIA / Jefatura
| Dimensión | Detalle |
|---|---|
| Jornada | No ejecuta items: mira el conjunto y escala. |
| Pantalla mínima | El tablero en modo lectura ya le sirve (las camas rojas >120 min SON el escalado implícito, por diseño). Vista propia de "demorados" = futuro, no MVP. |

---

## 3. Colisiones (el día a día donde los roles se pisan)

### C1 — Admisión apura al médico ✓ (resuelto este tramo)
**Escenario:** alta decidida de palabra, epicrisis sin firmar, la cama trabada,
la familia esperando. Admisión necesita destrabar YA y que quede constancia de
quién demoró.
**Modelo:** `Esperando: MÉDICO` visible + reloj de trabado + override de
ADMISION con discrepancia obligatoria (`demora_responsable`) + hito con
`actor_rol=ADMISION` real. La presión por visibilidad y la trazabilidad del
porqué quedan selladas. **Es la colisión mejor resuelta del sistema.**

### C2 — Médico dio alta, enfermería no dio apto ✓
**Escenario:** alta médica firmada, pero el paciente tiene vía colocada. Médico
considera que "ya terminó"; la cama sigue trabada.
**Modelo:** la cascada de `computar_responsable` mueve la pelota a ENFERMERIA
automáticamente. Nadie discute de quién es: lo dice el tablero.

### C3 — Todo OK, la ambulancia no llega ✓ / ⚠
**Escenario:** egreso administrativo cerrado, paciente vestido en la cama,
ambulancia (o cochería en defunción) demorada horas.
**Modelo:** `ALTA_ADMIN` separa egreso admin de liberación física ✓; el
responsable computado pasa a `prestador_externo` ✓; la demora del prestador es
medible por hitos ✓.
**⚠ Hueco menor:** `prestador_externo` no es un rol que opere Atlas — quien
registra la llegada es ENFERMERIA/ADMISION. Correcto por diseño, pero la UI
debe dejar claro que "Esperando: PRESTADOR EXTERNO" **no es un botón de nadie**:
es información. Si un usuario busca qué tocar, no debe encontrar nada.

### C4 — Limpieza terminó, mantenimiento pendiente ⚠
**Escenario:** limpieza marca sus 2 items, la cama no se libera
(`MantenimientoPendiente`). Limpieza ya se fue a otra habitación; mantenimiento
no sabe que tiene trabajo; admisión ve la cama eternamente en limpieza.
**Modelo:** el guard de doble OK existe ✓, pero:
- ⚠ no hay registro de QUÉ hay que reparar (motivo/descripción del trabajo);
- ⚠ no hay pantalla donde mantenimiento descubra su cola de trabajo;
- ⚠ limpieza no ve el estado "bloqueado por mantenimiento" de forma persistente.
**Es la colisión peor resuelta.** Candidata a mini-tramo propio:
`mantenimiento_requerido` necesita al menos un texto de motivo y aparecer en
las listas filtradas por rol.

### C5 — Cama liberada vs. cama prometida (reserva) ⚠
**Escenario:** la cama entra a limpieza ya prometida a un paciente (de guardia
o post-op). Limpieza termina → `DISPONIBLE` → otro admisionista la ocupa con
otro paciente. Choque de dos admisiones, o de Atlas contra Kairos/Cordis.
**Modelo:** existe la entidad `Reserva` (migración `4dd9e83bb465`), pero el
flujo de egreso **no consulta reservas al liberar**: la transición
`LIMPIEZA_TERMINAL → DISPONIBLE` ignora si hay una promesa previa.
**⚠ Verificar y decidir:** la liberación debería desembocar en `RESERVADA` si
hay reserva activa. Esta colisión es LA tesis del ecosistema (la cama disputada
por guardia y quirófano) — no se resuelve hoy, pero el diseño de la liberación
no debe cerrarle la puerta.

### C6 — Dos usuarios marcan el mismo item ✓ / ⚠
**Escenario:** dos terminales abiertas (o admisión y el responsable a la vez)
marcan el mismo item con segundos de diferencia.
**Modelo:** `ItemYaMarcado` hace el conflicto explícito ✓ — el segundo recibe
error, la auditoría registra UN autor.
**⚠ UX:** el segundo usuario debe ver "ya lo marcó X a las HH:MM", no un error
genérico. Con el polling de 15s la ventana de carrera se achica pero no
desaparece.

### C7 — Mismo rol, distinta persona ⚠ (deuda conocida, post-MVP)
**Escenario:** tres médicos usan el selector MEDICO. La trazabilidad personal
depende de `actor_nombre` voluntario.
**Modelo:** suficiente para el MVP (el rol-actor es el vocabulario de la
auditoría); insuficiente para atribución individual. Se resuelve con auth/RBAC,
ya declarado post-MVP. **No abrir ahora.**

---

## 4. Backlog que emerge (ordenado por dolor)

| # | Item | Colisión | Tamaño |
|---|---|---|---|
| 1 | Pantallas de un botón por rol (MEDICO, ENFERMERIA, LIMPIEZA, MANTENIMIENTO) | todas | tramo actual |
| 2 | Endpoint `GET /egresos/pendientes?rol=X` — items pendientes del rol en todos los egresos activos (evita iterar camas desde el frontend) | habilita #1 | chico |
| 3 | Motivo/descripción en `mantenimiento_requerido` + cola visible para MANTENIMIENTO | C4 | chico |
| 4 | Estado "bloqueado por mantenimiento" persistente en la vista de limpieza | C4 | chico |
| 5 | Liberación consulta reservas: `LIMPIEZA_TERMINAL → RESERVADA` si hay reserva activa | C5 | medio — decisión de diseño primero |
| 6 | Mensaje rico en `ItemYaMarcado` (quién y cuándo) | C6 | trivial |
| 7 | Auth / atribución individual | C7 | post-MVP, no abrir |

---

## 5. Implicancia directa para las pantallas de un botón

La matriz converge en un diseño simple:

- **Una sola app, una sola URL.** El selector de rol ya existe; lo que cambia
  por rol es la **vista por defecto**, no la aplicación. Rutas separadas por
  rol serían sobreingeniería sin auth que las respalde.
- **ADMISION** → tablero rico (lo de hoy, intacto).
- **Todo otro rol** → lista plana de SUS pendientes, agrupada por cama,
  ordenada por `minutos_trabado` desc. Cada fila: cama + paciente + item +
  un botón. Tap → marcado → la fila se tilda y queda (lección del CSS bug).
- **El dato que falta es #2 del backlog:** sin `GET /egresos/pendientes?rol=X`,
  el frontend tendría que traer todas las camas y filtrar — funciona con 10
  camas, no con 200. El endpoint es un JOIN simple sobre items no-done de
  egresos activos.
- Orden de construcción propuesto: **#2 (backend + tests) → #1 (frontend) →
  #6 (trivial, mismo commit) → #3/#4 (mini-tramo mantenimiento) → #5 (diseño
  primero, conversación aparte porque toca la tesis del ecosistema).**
