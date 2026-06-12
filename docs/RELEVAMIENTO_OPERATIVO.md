# Atlas — Relevamiento Operativo (institución de referencia, CABA)

**Estado:** Fuente primaria de dominio.
**Fecha:** 12 jun 2026
**Fuente:** Relevamiento de campo por conversación directa con referentes de
la operación (hotelería, limpieza, admisión), mapeando los flujos de trabajo
tal como ocurren en el día a día. Es proceso contado por quien lo ejecuta —
máximo nivel de certeza del proyecto.

---

## 1. Hotelería: estructura real del área

Del mapeo surge que el área se organiza en cuatro sub-roles, con asignación
**territorial por pisos y rotación semanal**:

| Sub-rol | Territorio | Función núcleo |
|---|---|---|
| Internación general | Pisos altos de internación | Entrega habitaciones a Admisión en tiempo y forma; chequea orden, limpieza, insumos y mantenimiento; verifica limpieza de habitaciones libres y diaria de ocupadas |
| Áreas cerradas | Pisos quirúrgicos/críticos (QX, UTI, UCO, NEO, CO) | Chequea limpieza de altas de quirófanos y boxes ambulatorios; coordina limpiezas profundas; supervisa a la mañana los arreglos de mantenimiento que se programan de noche |
| Áreas comunes | PB a subsuelos, consultorios | Supervisión de espacios públicos |
| **Agilización de altas** | Todas las habitaciones (exc. áreas críticas) | **El middleware humano del egreso** — ver §2 |

Hallazgos transversales del mapeo:
- Hotelería trabaja en conjunto con Admisión **dando prioridad a las
  habitaciones que Admisión pide** → feature: flag de prioridad de admisión
  sobre la cola de supervisión.
- Hotelería recibe y deriva quejas/reclamos de pacientes y cliente interno
  → consistente con `NotaEgreso` tipo reclamo.
- **"Limpieza terminal"** es el término que usa el piso para la limpieza a
  fondo que sigue a cada alta → el estado `LIMPIEZA_TERMINAL` de Atlas habla
  el idioma de la operación sin traducción.

## 2. Agilización de altas: el proceso que Atlas digitaliza

Mapeando la jornada de la hotelera de agilización, su flujo real es:
1. Monitorea horarios de alta por **WhatsApp, mail, sistema y planilla Excel**
   en paralelo.
2. Recorre pisos informando altas probables a las mucamas.
3. **Verifica que el paciente tenga la epicrisis** (tiene que poder verla);
   si no la tiene, **persigue al médico de piso y hace seguimiento** — el
   "Esperando: MÉDICO" ejecutado a pie.
4. Si el paciente espera medicación → presiona a Enfermería para que Farmacia
   priorice.
5. Al egreso controla la habitación junto al paciente (olvidos), lo acompaña,
   y **avisa el egreso físico a un grupo de WhatsApp** de coordinación.
6. **Anota el horario de OK en una planilla papel cuando le llega por mail.**

**Circuito ADS (altas del día siguiente):** a las 19hs circula por mail el
listado de altas probables; la hotelera visita al paciente con un guion fijo
y releva: ¿acompañante confirmado? ¿taxi/remis? ¿espera ambulancia? ¿necesita
silla de ruedas? → **eso es `medio_egreso` + `EquipoTraslado` recolectados la
noche anterior.** Feature de backlog: flag ADS + mini-checklist de pronóstico
que precarga el egreso del día siguiente.

**Lectura de mercado:** el competidor de Atlas en esta operación no es otro
software — es WhatsApp + mail + Excel + planilla papel. Cada aviso informal
que aparece en el mapeo tiene su hito equivalente ya modelado en Atlas.

## 3. Limpieza: tercerizada, con control contractual formal

- La limpieza la presta una **empresa tercerizada**; Hotelería/Operaciones la
  **audita**: control de presentismo diario contra listado (a las 7 y 15hs),
  capacitaciones, observación directa diaria, y **desvíos leves/graves que
  derivan en penalidades económicas sobre la factura mensual**.
- Tipos de limpieza que distingue la operación: normal (diaria), **terminal**
  (al alta de cada paciente, y programada periódicamente por sector), y
  concreta/eventual (p.ej. cama que va a quirófano, derrames, post-obra).
- **Implicancia mayor para Atlas:** los hitos de limpieza (quién, qué cama,
  cuándo, cuánto tardó) son **evidencia objetiva para la auditoría del
  contrato tercerizado** — hoy esa auditoría corre por observación directa y
  planillas. Ángulo de venta directo a Operaciones/Hotelería.
- El mapeo confirma la matriz de roles del doble OK: **ejecuta la tercerizada
  (LIMPIEZA), controla la institución (HOTELERIA)**. El item 2 del checklist
  es la frontera contractual entre empresa e institución.

## 4. Derivaciones: el circuito completo aguas arriba del egreso

Del mapeo surgen tres disparadores de derivación (dimensión estadificable):
1. **Decisión médica** (la atención excede la capacidad del sanatorio)
2. **Solicitud del paciente/familiar** (debe gestionarlo con su financiador)
3. **Decisión de Admisión/Auditoría** (sin cobertura / sin convenio; sin
   financiador → la derivación se canaliza por el servicio público de
   emergencias)

Secuencia operativa relevada (decisión médica):
- Médico + Auditoría Médica evalúan con el **Financiador** la gestión
  administrativa → se informa a la familia → **el médico envía a Admisión la
  orden médica con los requerimientos para poder solicitar la ambulancia** →
  secretaria de piso/admisión solicita el traslado → médico confecciona los
  documentos del alta → el día del egreso, la llegada de la ambulancia se
  avisa **por grupo de WhatsApp** → tras el egreso físico se cierra el alta
  en la HCE.
- **La orden médica es prerequisito operativo del pedido de ambulancia**: sin
  orden no hay solicitud de traslado. Respalda marcarla como
  `requerido_legal=True` en el catálogo de derivación.
- La operación mide % de derivaciones concretadas sobre solicitadas → métrica
  que Atlas puede computar si registra la solicitud.
- Las "derivaciones no factibles" escalan a Auditoría/Dirección Médica —
  fuera de alcance de Atlas v1; el egreso recién nace cuando la derivación
  está aceptada.

## 5. Documentación legal: el circuito documental existe y es formal

- Existe un rol institucional de **analista de documentación legal** que,
  según el mapeo: recolecta la documentación física por servicio contra
  listado del sistema, **verifica completitud (datos, firma y sello)**,
  observa desvíos, **da 72hs al servicio para rectificar faltantes**, archiva
  en sobres/cajas precintadas con listado digital+físico, y gestiona archivo
  externo (recupero en ≤72hs).
- La operación mide el % de documentación con desvíos sobre lo retirado.
- Entre la documentación legal por servicio aparecen: consentimientos de
  internación, altas voluntarias, rechazos terapéuticos, y **el libro de
  registro de retiro de óbitos** (lo entrega seguridad al agotar fojas) →
  confirma que el hito `ATLAS_SALIDA_FISICA` con metadata de retiro es la
  versión digital de un registro legal que ya existe en papel.
- **Implicancia:** el "empaquetado documental del egreso" propuesto
  (validación humana + seguimiento de copias entregadas) no es una feature
  inventada: es la digitalización de este circuito real. El patrón "documento
  faltante → responsable + reloj de 72hs" es exactamente el patrón
  responsable-computado + tiempo-trabado de Atlas aplicado a documentos.
  Base para `docs/CIRCUITO_DOCUMENTAL.md` (backlog).

## 6. Activos y mantenimiento: la cadena del colchón

- La evaluación del colchón ocurre **durante la limpieza terminal**: quien
  limpia evalúa → fluidos/daño irreparable → descarte como residuo
  patogénico; funda dañada → reposición por **bioingeniería**; deformación
  con reclamos reiterados de enfermería → recambio consensuado.
- Existe **cuarentena de 72hs** (colchón enfilmado, cartel con fecha) por
  contacto con escabiosis → una cama puede quedar fuera de servicio con
  motivo y plazo definidos.
- Cadena de aviso real mapeada: limpieza → coordinador → hotelería →
  bioingeniería (decide) → seguridad e higiene (retiro). Aparece un actor
  nuevo: **BIOINGENIERIA** (no está en la convención de roles del ecosistema
  — candidato a agregarse cuando se modele el bloqueo con motivo).
- **Implicancia para C4 (backlog #3):** el bloqueo de cama necesita motivo
  estructurado (reparación / cuarentena / recambio de activo) y, en
  cuarentena, un plazo. Los arreglos se programan de noche y se supervisan a
  la mañana → el modelo de mantenimiento debe registrar programado vs
  resuelto.

## 7. Decisiones que este relevamiento cierra

1. **Doble OK de limpieza:** item 1 = LIMPIEZA (tercerizada ejecuta),
   item 2 = **solo HOTELERIA** (institución controla). Frontera contractual,
   no preferencia de diseño.
2. **Orden de derivación → `requerido_legal=True`** (prerequisito del pedido
   de ambulancia según el flujo relevado). Pendiente de confirmación final
   del fundador.
3. **Cola de hotelería filtra por sector/piso** (asignación territorial).
4. **Flag "prioridad de admisión"** sobre camas en limpieza (práctica
   relevada de la operación).
5. La metadata del hito de salida física con datos de retiro replica un
   **registro legal que ya existe en papel** (libro de retiro de óbitos).

## 8. Backlog actualizado que emerge

| Item | Origen | Estado |
|---|---|---|
| Guard item 2 solo-HOTELERIA + métrica espera de supervisión | §3 | Próximo mini-commit (tras el bug) |
| Item "orden de derivación" legal=True + campos destino/prestador | §4 | Esperando confirmación |
| Flag prioridad de admisión en cola de limpieza | §1 | Tramo pantallas por rol |
| Flag ADS + checklist de pronóstico nocturno | §2 | Backlog |
| Bloqueo con motivo (reparación/cuarentena/activo) + actor BIOINGENIERIA | §6 | Mini-tramo C4 |
| Circuito documental / empaquetado del egreso | §5 | Doc de diseño, backlog |
| Venta: hitos como evidencia de auditoría de tercerizados | §3 | Material comercial |
