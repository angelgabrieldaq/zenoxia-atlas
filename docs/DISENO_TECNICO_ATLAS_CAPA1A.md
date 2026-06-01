# Atlas · Diseño Técnico — Capa 1a (modelo federado)

_Plano de modelos previo al código · SQLAlchemy 2.0 async_

Baja el diseño conceptual de Atlas a modelos concretos, bajo el modelo federado
del ecosistema: Atlas funciona 100% solo, con su propia base y representación
local; el core es punto de sincronización cuando coexisten, no dependencia de
arranque.

Es el plano que guía a Claude Code; **NO es el código final**. Subordinado a
`docs/DISENO_MODULO_ATLAS.md`, `VISION_ECOSISTEMA_ZENOXIA.md` (modelo federado) y
la convención de roles.

**Alcance capa 1a:** representación local + CamaGestion + máquina de estados +
Reserva + pases + hitos + capa de sincronización (interfaz, sin conector real
todavía). **NO incluye:** motor de sugerencias (1b), proyección (capa 2),
aprendizaje (nivel B), conector real al core/HIS (Fase 3).

---

## El problema que Atlas resuelve (por qué es autónomo)

En las instituciones, el HIS suele ser un sistema cerrado y la fuente de verdad
operativa. Las soluciones de gestión de camas existentes suelen ser visores
acoplados al HIS: leen su información y la muestran, pero no pueden hacer más de
lo que el sistema cerrado permite. Por eso son estáticas — espejos de un sistema
al que no le pueden pedir más.

Atlas se diseña al revés, y esa es su razón de ser:

- **Atlas es autónomo (modelo federado):** tiene su propia base y lógica, funciona
  sin depender del HIS. No es un espejo; es una herramienta que opera al lado del
  sistema cerrado sin pedirle permiso.
- **Donde el HIS sea integrable, un conector OPCIONAL sincroniza (Fase 3).** Donde
  no lo sea —el caso común, porque los HIS son cerrados— Atlas funciona igual con
  su captura propia.
- **La dirección de la sincronización es:** Atlas es fuente de verdad del estado de
  la cama; el HIS lo recibe, no al revés.

Esto es el problema real observado en la operación: el personal trabaja contra un
HIS cerrado que limita lo que pueden hacer. Atlas existe para dar la inteligencia
y la flexibilidad que el sistema cerrado no permite.

## Requisito de producto: visibilidad antes de habilitar

Antes de habilitar una cama (pasarla a DISPONIBLE), Admisión debe poder ver hace
cuánto se limpió y hace cuánto terminó el mantenimiento (si lo hubo), para decidir
con información en vez de a ciegas.

Este requisito **NO necesita campos nuevos**: se resuelve leyendo los HitoAtlas de
la cama (cada transición de estado deja su timestamp). La última transición
`LIMPIEZA_TERMINAL → DISPONIBLE` da la hora de limpieza; la última
`BLOQUEADA → DISPONIBLE` da el fin de mantenimiento. Es una CONSULTA sobre el
historial de hitos, a implementar en la capa de servicio/UI (no en la capa 1a). Se
documenta acá para garantizar que los hitos capturen los timestamps necesarios.

## Contexto: por qué Atlas es autónomo (validación desde la práctica)

El problema real observado en terreno: en muchas instituciones, el HIS es la
fuente de verdad y está cerrado — no se le puede hablar ni extender. Las
herramientas de visualización que conviven con él son espejos pasivos: leen del
HIS y muestran trazabilidad, pero no operan (no ejecutan traslados ni cambian
estados). Quedan limitadas a lo que el HIS permite ver, por eso son estáticas.

Atlas se diseña como lo opuesto a ese espejo: autónomo (base propia, lógica
propia, funciona sin pedirle permiso al HIS). El estado de la cama se EDITA en
Atlas y, cuando exista conector, IMPACTA hacia afuera — Atlas es fuente de verdad
operativa del estado de cama, no un reflejo del HIS. Esto es la razón de ser del
modelo federado: el HIS cerrado es justamente por qué la sincronización es opcional
(Fase 3) y nunca un requisito de arranque. Atlas trabaja al lado del HIS, no por
debajo de él.

## Principios que gobiernan este plano

1. **Atlas es autónomo (modelo federado).** Base de datos propia. Representación
   local de lo que necesita (cama, internación). Funciona sin core y sin HIS.
2. **El core es sincronización opcional, no dependencia.** NO hay FK físicas a
   tablas del core. Donde el core exista, una capa de sincronización mantiene en
   acuerdo la representación local de Atlas con la entidad canónica del core.
3. **Cero dato clínico fino en Atlas.** Atlas guarda categoría operativa de
   internación (gruesa, no clínica), nunca diagnóstico. El diagnóstico fino se
   cruza del core/HIS cuando exista (salida 3). Esto mantiene a Atlas fuera del
   terreno regulatorio de datos de salud.
4. **El estado de gestión es la fuente de verdad de Atlas sobre la cama.** Si hay
   core, se sincroniza con el `estado_cache` del LocationResource; si no, vive solo.
5. **Todo evento relevante escribe un hito** en el log de auditoría de Atlas
   (append-only, propio; se sincroniza con HitoTiempo del core cuando coexisten).
6. **Roles escritos con la convención `sistema:CODIGO`** (ver convención de roles).
7. **Las transiciones excepcionales se permiten pero se marcan.** La operación real
   tiene caminos raros (revertir un alta, bloquear cama ocupada). El sistema no se
   traba; los permite y los registra como excepción con su hito. Así no se pierde
   integridad del censo NI el rastro de lo que pasó.
8. **Acoplamiento de sincronización encapsulado.** Todo lo que toca el core vive en
   una capa de sincronización aislada (un módulo `sync/`), no disperso por la
   lógica. Atlas no asume en ningún lado que el core está presente.

---

## 1. Enum: `CategoriaInternacion`

Categoría OPERATIVA gruesa del porqué de la internación (no clínica). Reemplaza al
"diagnóstico" para los insights, sin guardar dato de salud sensible.

| Código | Significado |
|--------|-------------|
| `QUIRURGICA_PROGRAMADA` | Cirugía agendada |
| `QUIRURGICA_URGENCIA` | Cirugía no programada |
| `CLINICA` | Internación clínica, no quirúrgica |
| `GUARDIA_OBSERVACION` | Viene de guardia, en observación |
| `CRITICA` | UTI/UCO por estado, independiente de la causa |
| `OBSTETRICA` | Parto/cesárea (categoría disponible; ruteo obstétrico fuera de v1) |

## 2. Enum: `EstadoCamaGestion`

Los 5 estados de gestión + mapeo al `estado_cache` del core (para cuando se
sincroniza).

| EstadoCamaGestion (Atlas) | estado_cache (core, al sincronizar) | Significado |
|---------------------------|-------------------------------------|-------------|
| `DISPONIBLE` | LIBRE | Validada post-limpieza, lista para asignar |
| `RESERVADA` | LIBRE (no asignable) | Apartada para una internación que aún no llegó |
| `OCUPADA` | OCUPADO | Paciente físicamente ingresado |
| `PROCESO_DE_ALTA` | OCUPADO (sigue ocupando físicamente) | Tiene alta médica pero aún no egresó: corre la cadena de pasos pendientes (epicrisis, facturación, retirar vía, camillero, etc.). La cama se VA a liberar pero todavía no |
| `LIMPIEZA_TERMINAL` | LIMPIEZA | Bloqueo sanitario post-egreso |
| `BLOQUEADA` | FUERA_DE_SERVICIO | Mantenimiento / reparación |

### Enum auxiliar: `TipoComodidad`

Nivel de habitación, como dato de gestión INTERNA del hospital (no preferencia del
paciente). En la cama = lo que ofrece; en la internación = el nivel que la
cobertura habilita. Atlas cruza ambos para asignar correctamente, puertas adentro.

| Código | Significado |
|--------|-------------|
| `SIN_PREFERENCIA` | Sin restricción de nivel |
| `COMPARTIDA` | Habitación compartida |
| `INDIVIDUAL` | Habitación individual |
| `SUITE` | Suite / categoría superior |

## 3. Modelo: `PacienteLocal` (representación local)

Identificación mínima del paciente, local a Atlas. Cuando hay core, se sincroniza
con Patient (por DNI, identificador de negocio). Sin core, vive solo.

| Campo | Tipo | Reglas |
|-------|------|--------|
| `id` | UUID | PK default uuid4 |
| `dni` | String(8) | not null, index. Identificador de negocio; clave de sincronización con Patient del core |
| `nombre` | String(100) | not null |
| `apellido` | String(100) | not null |
| `core_patient_id` | UUID | nullable. El id del Patient en el core, si se sincronizó. NULL si Atlas corre solo |
| `nhc_externo` | String(50) | nullable. Número de Historia Clínica del HIS de la institución. Identificador de referencia para sincronizar con un HIS real (que suele identificar por NHC, no por DNI). NULL si no hay HIS |
| `creado_at` | DateTime(tz) | server_default now |

**Nota:** Atlas NO guarda fecha de nacimiento, sexo, ni nada clínico. Solo lo
mínimo para identificar a quién está en la cama. El resto vive en el core/HIS.
Sincronización: con el core de Zenoxia por DNI (identificador de negocio); con un
HIS externo por NHC (que es como el HIS suele identificar). Ambos opcionales: si no
hay con qué sincronizar, Atlas funciona con su identificación local.

## 4. Modelo: `InternacionLocal` (representación local)

La internación como la ve Atlas: quién está, desde cuándo, de qué tipo (categoría
operativa). Es el equivalente local del Episodio del core, reducido a lo que camas
necesita.

| Campo | Tipo | Reglas |
|-------|------|--------|
| `id` | UUID | PK default uuid4 |
| `paciente_local_id` | UUID | FK → paciente_local.id not null, index |
| `categoria` | Enum(CategoriaInternacion) | not null, index. El "por qué" operativo |
| `servicio_codigo` | String(10) | nullable. Especialidad (CLIN/CIRU/...); se sincroniza con MedicalService del core |
| `comodidad_requerida` | Enum(TipoComodidad) | nullable. Nivel de habitación que la COBERTURA del paciente habilita. Es una restricción INTERNA de asignación que usa el hospital (Admisión) para ubicar al paciente, NO una preferencia ni un derecho que el paciente elige o ve. Atlas no asigna por debajo del nivel habilitado. Derivado de la cobertura, pero Atlas NO guarda la aseguradora — solo la consecuencia operativa. El "de qué aseguradora viene" se cruza del HIS |
| `core_episodio_id` | UUID | nullable. El id del Episodio del core, si se sincronizó. NULL si corre solo |
| `iniciada_at` | DateTime(tz) | server_default now, index |
| `finalizada_at` | DateTime(tz) | nullable. Cierre de la internación (para permanencia/métricas) |

**Permanencia** = `finalizada_at - iniciada_at` (se calcula, no se guarda). Base
del aprendizaje futuro (nivel B): permanencia por categoria + servicio + tipo de
cama.

## 5. Modelo: `CamaGestion`

La entidad central. Una cama gestionada por Atlas. Local; sincroniza con
LocationResource del core cuando existe.

| Campo | Tipo | Reglas |
|-------|------|--------|
| `id` | UUID | PK default uuid4 |
| `nombre` | String(100) | not null. Identificación de la cama (ej. "UTI-03") |
| `tipo` | Enum(TipoCama) | not null. CAMA_INTERNACION / UTI / UCO (espejo local del tipo del core) |
| `comodidad` | Enum(TipoComodidad) | nullable. Qué comodidad ofrece esta cama (individual/compartida/suite). Se cruza con comodidad_requerida de la internación para validar la asignación |
| `sector` | String(50) | not null |
| `estado_gestion` | Enum(EstadoCamaGestion) | not null, default DISPONIBLE, index |
| `internacion_actual_id` | UUID | FK → internacion_local.id nullable, ondelete SET NULL. Quién ocupa/reservó |
| `motivo_bloqueo` | String(200) | nullable. Solo si BLOQUEADA |
| `core_location_id` | UUID | nullable. El id del LocationResource del core, si se sincronizó. NULL si corre solo |
| `actualizado_at` | DateTime(tz) | server_default now, onupdate now |
| `creado_at` | DateTime(tz) | server_default now |

**Regla de sincronización (cuando hay core):** al cambiar `estado_gestion`, la capa
sync actualiza el `estado_cache` del LocationResource del core según §2, en la
misma operación lógica. Sin core, solo cambia el estado local.

## 6. Modelos: `PasoAltaCatalogo` y `PasoAltaInternacion` (checklist configurable)

Capturan la cadena de pre-alta (que las soluciones acopladas al HIS resuelven con
subestados fijos), pero de forma CONFIGURABLE por institución — cada hospital
activa solo los pasos que usa. Esto es lo que hace a Atlas más flexible/vendible
que un set hardcodeado.

### `PasoAltaCatalogo` — los pasos que esta institución usa (configuración)

| Campo | Tipo | Reglas |
|-------|------|--------|
| `id` | UUID | PK default uuid4 |
| `codigo` | String(40) | not null, unique. Ej. ALTA_MEDICA, EPICRISIS, FACTURACION, RETIRAR_VIA, DISPENSACION, CAMILLERO, ALTA_FISICA |
| `nombre` | String(100) | not null. Etiqueta legible |
| `orden` | Integer | not null. Secuencia sugerida del paso en la cadena |
| `activo` | Boolean | not null, default True. La institución prende/apaga pasos sin tocar código |

**Pasos de referencia** (NO hardcodeados — son filas configurables): p/ alta
médica, p/ epicrisis, p/ liquidación, p/ facturación, p/ alta administrativa, p/
retirar vía, p/ dispensación, p/ medicamentos dispensados, p/ camillero, p/ alta
física.

### `PasoAltaInternacion` — el estado de cada paso para una internación concreta

| Campo | Tipo | Reglas |
|-------|------|--------|
| `id` | UUID | PK default uuid4 |
| `internacion_id` | UUID | FK → internacion_local.id not null, index, ondelete CASCADE |
| `paso_codigo` | String(40) | not null. Refiere a PasoAltaCatalogo.codigo |
| `completado` | Boolean | not null, default False |
| `completado_por_rol` | String(40) | nullable. Convención sistema:CODIGO |
| `completado_at` | DateTime(tz) | nullable |

**Uso:** al entrar a PROCESO_DE_ALTA, se instancian los pasos activos del catálogo
para esa internación. Ver qué pasos faltan = ver POR QUÉ la cama no se libera
todavía. La transición `PROCESO_DE_ALTA → LIMPIEZA_TERMINAL` se habilita cuando los
pasos requeridos están completos (en particular el de alta física).

## 7. Modelo: `Reserva`

Cama apartada para una internación que aún no llegó. Sostiene la validación
quirúrgica cruzada (interna a Atlas en 1a; Kairos la consulta vía sync en Fase 3).

| Campo | Tipo | Reglas |
|-------|------|--------|
| `id` | UUID | PK default uuid4 |
| `cama_gestion_id` | UUID | FK → cama_gestion.id not null, index, ondelete CASCADE |
| `internacion_id` | UUID | FK → internacion_local.id not null, index |
| `motivo` | Enum(MotivoReserva) | not null. QUIRURGICA / PASE_INTERNO / INGRESO_PROGRAMADO |
| `estado` | Enum(EstadoReserva) | not null, default ACTIVA. ACTIVA / CUMPLIDA / CANCELADA / VENCIDA |
| `requerimiento_nivel` | Enum(TipoCama) | nullable. Nivel requerido (UTI/UCO) para validación quirúrgica |
| `valida_hasta` | DateTime(tz) | nullable. Vencimiento (ej. previsión nocturna) |
| `creada_por_rol` | String(40) | not null. Convención sistema:CODIGO |
| `creada_at` | DateTime(tz) | server_default now |
| `resuelta_at` | DateTime(tz) | nullable |

**Regla (validación quirúrgica cruzada):** cirugía con destino UTI/UCO requiere
Reserva ACTIVA con `motivo=QUIRURGICA` y `requerimiento_nivel` coincidente ANTES de
confirmarse.

## 8. Modelo: `PaseServicio`

Pase de un paciente entre servicios/áreas (UCI↔IG). Registra el ISBAR como HECHO,
no contenido (el contenido vive en HCE / dominio clínico).

| Campo | Tipo | Reglas |
|-------|------|--------|
| `id` | UUID | PK default uuid4 |
| `internacion_id` | UUID | FK → internacion_local.id not null, index |
| `cama_origen_id` | UUID | FK → cama_gestion.id nullable, ondelete SET NULL |
| `cama_destino_id` | UUID | FK → cama_gestion.id nullable, ondelete SET NULL |
| `estado` | Enum(EstadoPase) | not null, default SOLICITADO. SOLICITADO / CAMA_ASIGNADA / EN_TRASLADO / CONFIRMADO / CANCELADO |
| `isbar_realizado` | Boolean | not null, default False. Solo el HECHO de que ocurrió |
| `isbar_at` | DateTime(tz) | nullable |
| `solicitado_por_rol` | String(40) | not null |
| `confirmado_por_rol` | String(40) | nullable |
| `solicitado_at` | DateTime(tz) | server_default now |
| `confirmado_at` | DateTime(tz) | nullable |

**Regla de oro de sincronización:** `estado=CONFIRMADO` y `confirmado_at` solo se
escriben cuando el paciente está FÍSICAMENTE en la cama destino. Ni antes, ni con
demora.

## 9. Modelo: `HitoAtlas` (auditoría local)

Log append-only propio de Atlas. Espejo local del HitoTiempo del core; se
sincroniza cuando coexisten. Atlas no depende del core para auditar.

| Campo | Tipo | Reglas |
|-------|------|--------|
| `id` | UUID | PK default uuid4 |
| `internacion_id` | UUID | FK → internacion_local.id nullable, index |
| `cama_gestion_id` | UUID | FK → cama_gestion.id nullable, index |
| `hito_codigo` | String(100) | not null, index. Ver §10 |
| `actor_rol` | String(40) | nullable. Convención sistema:CODIGO |
| `actor_nombre` | String(100) | nullable |
| `metadata_evento` | JSONB | nullable. Payload del evento (motivo_reversion, etc.). NUNCA dato clínico |
| `registrado_at` | DateTime(tz) | server_default now, index |
| `sincronizado_core` | Boolean | default False. Si ya se replicó al HitoTiempo del core |

**APPEND-ONLY:** nunca se actualiza ni borra (salvo marcar `sincronizado_core`).
Toda corrección es un hito compensatorio.

## 9b. Modelo: `NotaCama` (notas libres de comunicación)

Notas tipo post-it asociadas a una cama/internación, para comunicación del equipo
("avisar a familia", "revisar aire", etc.). SEPARADAS del checklist de pre-alta
(§6): el checklist es lo que formalmente traba la liberación de la cama; las notas
son recordatorios sueltos del equipo. No confundir ni mezclar.

| Campo | Tipo | Reglas |
|-------|------|--------|
| `id` | UUID | PK default uuid4 |
| `cama_gestion_id` | UUID | FK → cama_gestion.id not null, index, ondelete CASCADE |
| `internacion_id` | UUID | FK → internacion_local.id nullable, index. La nota puede ser de la cama o de la internación actual |
| `texto` | String(500) | not null. Contenido libre. NUNCA dato clínico sensible |
| `autor_rol` | String(40) | nullable. Convención sistema:CODIGO |
| `autor_nombre` | String(100) | nullable |
| `resuelta` | Boolean | not null, default False. Una nota se marca resuelta cuando ya no aplica |
| `creada_at` | DateTime(tz) | server_default now, index |

**Regla:** las notas NO son auditoría (no van a HitoAtlas) — son comunicación
efímera. Se pueden editar/resolver/borrar, a diferencia de los hitos que son
append-only. Límite: texto libre, sin dato clínico. Es comunicación operativa, no
historia clínica.

## 10. Máquina de estados de la cama — transiciones

| Desde | Hacia | Disparador (rol + acción) | Hito |
|-------|-------|---------------------------|------|
| DISPONIBLE | RESERVADA | ADMISION crea Reserva | CAMA_RESERVADA |
| DISPONIBLE | OCUPADA | ADMISION/ENFERMERIA confirma ingreso directo | CAMA_OCUPADA |
| RESERVADA | OCUPADA | ENFERMERIA confirma ingreso físico del reservado | CAMA_OCUPADA |
| RESERVADA | DISPONIBLE | ADMISION cancela/vence reserva | RESERVA_LIBERADA |
| OCUPADA | PROCESO_DE_ALTA | MEDICO carga alta médica — arranca la cadena de pasos | PROCESO_ALTA_INICIADO |
| PROCESO_DE_ALTA | LIMPIEZA_TERMINAL | ADMISION da el alta física (completa los pasos) — esto libera la cama y dispara trabajo a limpieza/hotelería | LIMPIEZA_INICIADA |
| LIMPIEZA_TERMINAL | DISPONIBLE | LIMPIEZA finaliza + supervisor aprueba | CAMA_DISPONIBLE |
| DISPONIBLE | BLOQUEADA | MANTENIMIENTO bloquea (motivo obligatorio) | CAMA_BLOQUEADA |
| BLOQUEADA | DISPONIBLE | MANTENIMIENTO desbloquea + validación Operaciones | CAMA_DESBLOQUEADA |
| OCUPADA | BLOQUEADA (excepción) | MANTENIMIENTO urgente — mover paciente primero | CAMA_BLOQUEADA |
| PROCESO_DE_ALTA | OCUPADA (excepción) | reversión de alta — el paciente nunca egresó | ALTA_REVERTIDA |
| LIMPIEZA_TERMINAL | OCUPADA (excepción) | reversión de alta tardía — ADMISION deshace el alta física, el paciente vuelve | ALTA_REVERTIDA |

### Transición excepcional: reversión de alta

Se disparó el alta (`OCUPADA → LIMPIEZA_TERMINAL`) pero el paciente nunca egresó
físicamente y hay que deshacerla.

- La cama vuelve a OCUPADA; `internacion_actual_id` NO cambia (mismo paciente).
- Hito `ATLAS_ALTA_REVERTIDA` con metadata: `motivo_reversion` (texto/categoría,
  OBLIGATORIO) y `limpieza_ya_ejecutada` (bool — true solo si la limpieza efectiva
  ya se hizo antes de revertir = trabajo desperdiciado real).
- Atlas revierte SU reflejo del egreso (la cama), no el acto clínico de alta (ese
  lo deshace quien lo dio, en su sistema).

**Reglas duras:** no se ocupa una cama en LIMPIEZA/BLOQUEADA por flujo normal (solo
por las excepciones marcadas); LIMPIEZA→DISPONIBLE exige aprobación de supervisor;
BLOQUEADA→DISPONIBLE exige validación de Operaciones; toda transición escribe hito.

### Dos altas distintas (clave del flujo de egreso)

- **Alta médica:** la informa el MÉDICO. Es la decisión clínica de que el paciente
  puede irse. NO libera la cama por sí sola (arranca PROCESO_DE_ALTA).
- **Alta física/administrativa:** la da ADMISIÓN. Confirma que el paciente
  efectivamente egresó. ES la que libera la cama y dispara la limpieza
  (`PROCESO_DE_ALTA → LIMPIEZA_TERMINAL`).
- Por eso el disparador de LIMPIEZA_TERMINAL es ADMISION (no Hotelería): Hotelería
  y Limpieza EJECUTAN el trabajo, pero el evento que cambia el estado es el alta
  física.
- **Reversión TEMPRANA** (`PROCESO_DE_ALTA → OCUPADA`): la dispara MEDICO (deshace
  su alta médica, aún no hubo alta física).
- **Reversión TARDÍA** (`LIMPIEZA_TERMINAL → OCUPADA`): la dispara ADMISION (deshace
  el alta física que ya había dado).

### Mantenimiento: carril puntual, no rutinario

Limpieza es el recambio normal y obligatorio: SIEMPRE libera la cama. Mantenimiento
es un caso puntual (no rutinario): solo entra cuando hay un problema, y solo libera
la cama si él mismo la bloqueó para ese fin (`BLOQUEADA → DISPONIBLE`). NO se modela
un estado de "doble OK simultáneo" porque limpieza y mantenimiento no son pasos
paralelos del recambio normal: son carriles distintos.

### Requisito de consulta a soportar (UI/servicio, no modelo)

Antes de habilitar una cama (`LIMPIEZA_TERMINAL/BLOQUEADA → DISPONIBLE`), ADMISIÓN
debe poder ver hace cuánto se limpió y hace cuánto terminó el mantenimiento (si
hubo). Este dato NO requiere campos nuevos: sale de leer los HitoAtlas de la cama
(cada transición deja su `registrado_at`). Es una consulta/visualización de la capa
de servicio+UI, no de la máquina de estados. Se anota acá para no perderlo; se
implementa más adelante.

## 11. Catálogo de hitos (`HitoAtlas.hito_codigo`)

Prefijo `ATLAS_`. `producto_origen = "Atlas"` al sincronizar con el core.

| hito_codigo | Cuándo |
|-------------|--------|
| `ATLAS_CAMA_RESERVADA` | Se crea una reserva |
| `ATLAS_RESERVA_LIBERADA` | Reserva cancelada o vencida |
| `ATLAS_CAMA_OCUPADA` | Paciente ingresa físicamente |
| `ATLAS_PROCESO_ALTA_INICIADO` | Se carga el alta médica; arranca la cadena de pasos de pre-alta |
| `ATLAS_PASO_ALTA_COMPLETADO` | Se completa un paso del checklist de alta. metadata: paso_codigo |
| `ATLAS_PASE_SOLICITADO` | Se solicita un pase entre servicios |
| `ATLAS_PASE_ISBAR_REGISTRADO` | Se registra que el ISBAR ocurrió (hecho, no contenido) |
| `ATLAS_PASE_CONFIRMADO` | Recepción física confirmada (regla de oro) |
| `ATLAS_LIMPIEZA_INICIADA` | Egreso físico → bloqueo sanitario |
| `ATLAS_CAMA_DISPONIBLE` | Limpieza aprobada, cama al pool |
| `ATLAS_CAMA_BLOQUEADA` | Bloqueo por mantenimiento |
| `ATLAS_CAMA_DESBLOQUEADA` | Cama vuelve de mantenimiento |
| `ATLAS_ALTA_REVERTIDA` | Reversión de alta. metadata: motivo_reversion (oblig.), limpieza_ya_ejecutada (bool) |

## 12. Capa de sincronización (interfaz, sin conector real en 1a)

El acoplamiento al core vive AISLADO acá (módulo `sync/`). En capa 1a se define
solo la INTERFAZ; el conector real al core es Fase 3.

**Responsabilidad:** cuando el core existe, mantener en acuerdo:

- `PacienteLocal ↔ Patient` (por DNI) — llena `core_patient_id`.
- `InternacionLocal ↔ Episodio` — llena `core_episodio_id`; cruza diagnóstico fino
  para insights sin guardarlo en Atlas.
- `CamaGestion ↔ LocationResource` — llena `core_location_id`; sincroniza
  `estado_cache`.
- `HitoAtlas → HitoTiempo` — replica los hitos; marca `sincronizado_core`.

**Regla:** la lógica de negocio de Atlas NUNCA llama directo al core. Siempre pasa
por esta capa. Si el core no está, la capa es un no-op y Atlas sigue funcionando.

## 13. Qué NO entra en capa 1a (límites explícitos)

- Motor de sugerencias de enroque → **capa 1b**.
- Proyección de altas / candidatos a mover → **capa 2**.
- Aprendizaje de permanencia → **nivel B (capa 3)**; solo se deja el dato crudo
  (categoria + servicio + permanencia + tipo de cama).
- Conector real al core/HIS → **Fase 3** (en 1a solo la interfaz de sync).
- Ruteo de maternidad → fuera de v1.
- Servicios al paciente / dietas / cobro → módulo futuro.
- Tabla catálogo de roles → string libre con convención.

## 14. Orden de construcción sugerido (dentro de 1a)

1. Enums (`CategoriaInternacion`, `EstadoCamaGestion`, `TipoCama`, `TipoComodidad`,
   y los de Reserva/Pase).
2. Representación local: `PacienteLocal` + `InternacionLocal` (con
   `comodidad_requerida`).
3. `CamaGestion` (con comodidad) + `HitoAtlas` + máquina de estados + hitos.
4. Checklist de alta: `PasoAltaCatalogo` + `PasoAltaInternacion` + estado
   PROCESO_DE_ALTA.
5. Reserva + validación quirúrgica cruzada + validación de comodidad (no asignar
   por debajo del derecho).
6. `PaseServicio` + regla de oro + hito ISBAR.
7. `NotaCama` (notas libres, entidad simple aparte).
8. Interfaz de la capa sync (sin conector real): definir los métodos no-op.

> Cada paso: rama git, probar, revisión humana, commit. No avanzar sin verificar.
