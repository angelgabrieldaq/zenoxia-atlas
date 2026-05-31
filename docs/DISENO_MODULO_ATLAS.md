# Zenoxia · Módulo Atlas — Documento de Diseño
## Fase 2 del roadmap · diseño previo a código

Diseña el módulo Atlas (gestión de camas) del ecosistema Zenoxia. Captura la visión
completa (las tres capas); la construcción es incremental (capa 1 → 2 → 3).
Subordinado a `docs/VISION_ECOSISTEMA_ZENOXIA.md` y al principio de oro.

---

# 1. Qué es Atlas

El módulo que organiza la ocupación del sanatorio. No es un censo más: es la
herramienta donde la capacidad que se va a liberar se vuelve visible, para que
Admisión y los coordinadores **organicen el día en vez de improvisarlo** —
incluido el enroque cuando se está full.

Resuelve la fricción que ningún otro módulo resuelve solo: la cama es el recurso
que guardia (Cordis) y quirófano (Kairos) se disputan. Por eso Atlas es la prueba
viva de la tesis del ecosistema.

## El problema real (no el del manual)
El sistema ordenado "hay cama → se reserva → llega el paciente" describe un mundo
que no existe cuando hay presión. En la realidad, **cuando están full ingresan
igual** y la cama se fabrica sobre la marcha: alta temprana, bajar un paciente de
intensiva a intermedia, de intermedia a piso — el **enroque**. Hoy se improvisa
porque la información que lo haría planificable (altas que el médico ya marcó,
"estos se van mañana") existe pero está dispersa: parte en sistema, parte en
mensajería informal, parte en la cabeza del coordinador.

**Atlas no predice nada mágico. Junta en un solo lugar lo que los médicos ya saben
y hoy se pierde.**

---

# 2. Principio de arquitectura: Atlas funciona sin HIS

Atlas es un producto para vender a **distintas instituciones**, no para un solo
sanatorio. Cada institución tiene un HIS (sistema de información hospitalaria)
distinto: cerrado, propio, viejo, o inexistente. De esto se deriva una regla dura:

> **Atlas no puede depender de ningún HIS para funcionar.**
> Si Atlas necesitara integrarse a un HIS para ser útil, no sería vendible: cada
> venta moriría en una integración distinta contra un sistema cerrado.

## Consecuencias de diseño
- **El dato de "alta programada / candidato a mover" se captura EN Atlas**, en su
  propia interfaz, como *intención operativa*. No requiere fuente externa. El médico
  o el coordinador lo marca en Atlas, y con eso la capa 2 (proyección) ya funciona.
- **Donde exista un HIS integrable, un conector OPCIONAL** puede pre-cargar o
  sincronizar ese dato hacia Atlas. Es un *plus*, nunca un requisito. Atlas funciona
  igual sin él.
- La fuente de verdad clínica del **acto de alta** (el paciente egresó) sigue siendo
  del HIS donde lo haya. Atlas no lo reemplaza ni lo dispara.

## Distinción crítica: intención vs. acto
Marcar "este paciente se va mañana" (intención operativa, planificación) NO es lo
mismo que dar el alta clínica (acto médico con consecuencias en la HCE). Confundirlos
es peligroso en la operación real. Por lo tanto:

> **La proyección de Atlas ("se planea que se vaya") NUNCA debe poder disparar el
> acto clínico ("se fue").** Son dos hechos distintos, en dos sistemas distintos, y
> el diseño debe mantenerlos separados de forma estricta.

Esto encaja con el principio de oro: Atlas registra la *intención operativa* (que es
suya, del dominio de gestión de camas); el *acto clínico* es del dominio clínico /
del HIS. Atlas nunca origina ni ejecuta el acto clínico.

## Atlas vs. soluciones existentes (ej. Haily)
Existen soluciones de gestión de camas que registran estados (limpieza,
mantenimiento, ocupación) y muestran algunos insights. Son **estáticas**: sirven
para *mirar* el censo, no para *organizar* el día. La diferencia de Atlas es la
capa 2 (proyección): convierte el censo en organización. "Esta cama se libera hoy a
las 14h (alta programada), con eso resolvés la cirugía de las 16h sin enroque" —
eso es organizar, no mirar.

**Regla de producto:** no sumar cosas sin necesidad. Cada feature se gana su lugar
resolviendo el problema real (organizar la ocupación) o no entra. Atlas es pocas
cosas bien hechas, no un tablero lleno de extras.

---

# 3. Las tres capas

El módulo es una sola cosa que registra bien y muestra el mismo dato con tres
lentes. Las tres se construyen sobre el mismo registro de eventos.

## Capa 1 — Organizar el día (el corazón)
La función operativa diaria. Admisión y los coordinadores arman la ocupación
cruzando lo que se libera contra lo que entra (cirugías programadas que necesitan
cama, ingresos de guardia que requieren internación). Acá viven:
- El estado y la reserva de camas.
- El **enroque**: cadena de movimientos que abre una cama donde hace falta.
- La **validación quirúrgica cruzada** (no se confirma cirugía con destino UTI/UCO
  sin cama coordinada).
- Los pases entre servicios (UCI ↔ Internación General).
Es lo que se usa cada mañana y cada vez que están full.

## Capa 2 — Proyección (lo que alimenta a la capa 1)
Para organizar el día hay que ver lo que viene. Esta capa hace visible la
**capacidad que se va a liberar**: altas tempranas marcadas por el médico,
pacientes señalados como "candidato a mover / próximo a alta", capacidad proyectada
por sector. Sin esta capa, la capa 1 vuelve a ser improvisación. Es la que hace
que el módulo valga la pena.

**Dato clave:** la señal la emite el médico (marca el candidato/alta en el dominio
clínico). Atlas la LEE; no la inventa. Ver principio de oro abajo.

## Capa 3 — Análisis (el producto secundario)
Todo lo que pasa por las capas 1 y 2 queda registrado en el log append-only del
core. De ahí salen las métricas institucionales, sin capturar nada extra:
- % Ocupación de camas
- Giro cama
- Intervalo de giro / sustitución
- Promedio de permanencia
- % Pacientes derivados a UCI desde IG (%PD)
- % Retorno a área crítica (%PD_AC)
No es para el día a día: es para que la dirección entienda cómo respira el
sanatorio en el tiempo. Las fórmulas son lógica de Atlas; el dato crudo es del core.

---

# 4. Qué consume del core y qué es propio de Atlas

Aplicación directa del principio de oro.

## Del core (ya existe, no se toca)
- `Patient` — identidad del paciente a internar.
- `LocationResource` — la cama es un LocationResource (tipos CAMA_INTERNACION /
  UTI / UCO ya existen). Su `estado_cache` (LIBRE/OCUPADO/LIMPIEZA/FUERA_DE_SERVICIO)
  es el estado físico-operativo genérico.
- `Episodio` — la internación es un Episodio (estado genérico EstadoEncounter).
- `Traslado` — cada movimiento físico (incluido cada eslabón de un enroque) es un
  Traslado. La cadena del enroque se modela como Traslados encadenados.
- `HitoTiempo` — cada evento de cama queda acá (append-only). Fuente de la capa 3.

## Propio de Atlas (vive en el módulo, NO en el core)
- La **máquina de estados de gestión de cama** (sección 4) — más rica que
  `estado_cache`. "Reservada" es de Atlas, no del core.
- La lógica del **enroque** (cadena propuesta → aprobada → ejecutada por pasos).
- Las **reglas de validación quirúrgica cruzada**.
- Las **fórmulas de las métricas** (capa 3).
- Los **roles operativos** propios (Admisión/gestión de camas, Hotelería,
  Coordinación de Enfermería, Central de Traslados).

## El dato clínico de proyección — dónde vive (DECISIÓN DE ORO)
La marca "candidato a alta / próximo a mover / estable para bajar de nivel" es
información CLÍNICA: la emite el médico. Por el principio de oro NO vive en Atlas.
Opciones a decidir (sección 6): vive en el Episodio del core como un campo/hito
genérico que cualquier módulo puede leer, o vive en el dominio clínico de cada
módulo. Atlas siempre la consume, nunca la origina.

---

# 5. Estados de la cama — reconciliación con el core

El core tiene `EstadoCacheRecurso`: LIBRE / OCUPADO / LIMPIEZA / FUERA_DE_SERVICIO.
El sanatorio necesita cinco estados de gestión. NO son lo mismo: el del core es
físico-operativo genérico (lo comparten todos los módulos), el de Atlas agrega la
semántica de gestión.

| Estado de gestión (Atlas) | Mapea a estado_cache (core) | Qué agrega Atlas |
|---|---|---|
| Disponible | LIBRE | Validada por auditoría de calidad post-limpieza |
| Reservada | LIBRE (físicamente) / no asignable | **Asignada a un paciente que aún no llegó** — no existe en el core |
| Ocupada | OCUPADO | Paciente físicamente ingresado y en HCE |
| En Limpieza Terminal | LIMPIEZA | Bloqueo sanitario post-egreso |
| Bloqueada / Mantenimiento | FUERA_DE_SERVICIO | Justificación y validación técnica para volver |

**Decisión de diseño (sección 6):** Atlas NO redefine `estado_cache`. Lleva su
propia máquina de estados de gestión ENCIMA del LocationResource del core, y la
mantiene sincronizada con `estado_cache`. El estado "Reservada" —el que no existe
en el core y donde vive la lógica de Atlas— es la razón por la que el módulo
necesita su propia capa de estado y no le alcanza con el del core.

Nota: "Reservada" es el estado donde se sostiene la **validación quirúrgica
cruzada** — la cama de UTI/UCO queda reservada al confirmar la cirugía y no es
elegible para otra asignación.

---

# 6. El enroque — modelado

El enroque es una **cadena de traslados** que abre una cama donde hace falta
moviendo pacientes en cascada (bajar C a piso libera intermedia para B, cuya cama
libera intensiva para el que entra).

Principios de diseño, derivados de cómo funciona de verdad:
1. **El sistema NO decide el enroque.** Lo coordina y registra. La decisión de a
   quién mover es médica (coordinador + médicos de servicio); Admisión orquesta.
2. **El sistema muestra los candidatos** (capa 2): qué pacientes están marcados
   como estables / próximos a alta, para que la persona decida con la info a la
   vista en vez de a ciegas.
3. **Una cadena puede quedar a medio ejecutar.** Se mueve el primero, el segundo
   movimiento se cae (paciente se descompensó, familia se opuso, coordinador dijo
   que no). El modelo debe tolerar cadenas parciales sin romper el censo. Esto
   ABLANDA la "regla de oro" rígida del documento institucional, que asume que
   todo pase se completa.
4. **Cada eslabón es un Traslado del core**, con su confirmación física propia
   (regla de oro de sincronización: el pase se confirma cuando el paciente está
   físicamente en destino, ni antes ni con demora).

Modelo tentativo: una entidad `Enroque` (propia de Atlas) que agrupa N Traslados
ordenados, con estado propuesto / en ejecución / completado / parcial / cancelado.
A detallar en el diseño técnico tras aprobar este documento.

---

# 7. Decisiones abiertas (a resolver antes de codear)

1. **Estado de Atlas: ¿tabla propia o extensión de LocationResource?**
   Recomendación: tabla propia del módulo (`CamaGestion` o similar) con FK al
   LocationResource del core, que sincroniza `estado_cache`. Mantiene el core limpio.

2. **El dato de proyección clínica (candidato a alta/mover): ¿dónde vive?**
   → **RESUELTA** (ver sección 2). Se captura EN Atlas como *intención operativa*,
   independiente de cualquier HIS. Donde haya un HIS integrable, un conector opcional
   puede sincronizarlo. La proyección nunca dispara el acto clínico de alta.

3. **Roles nuevos del ecosistema.** Atlas introduce Admisión/gestión de camas,
   Hotelería, Coordinación de Enfermería, Central de Traslados. ¿Se definen como
   roles propios de Atlas o se suman al catálogo de roles del ecosistema?

4. **ISBAR en los pases.** El pase enfermería↔enfermería exige metodología ISBAR.
   ¿Se modela como campos estructurados del Traslado/pase, o como un HitoTiempo con
   payload, o queda como requisito de proceso fuera del dato?

5. **Maternidad (enrutamiento diurno/nocturno).** El PDF define reglas de ruteo
   obstétrico por horario. ¿Entra en Atlas v1 o es lógica de un eventual módulo
   obstétrico (relación con Gia, hoy en revisión)? Probable: fuera de v1.

---

# 8. Orden de construcción (capas)

- **Capa 1** primero: estados de cama, reserva, validación quirúrgica cruzada,
  pases UCI↔IG, enroque básico. El módulo usable cada día.
- **Capa 2** después: proyección — consumir las marcas clínicas de alta/candidato,
  mostrar capacidad que se va a liberar.
- **Capa 3** al final: métricas, leídas del HitoTiempo append-only. Producto
  secundario sin captura extra de datos.

Cada capa: diseño técnico → rama git → probar → revisión humana → commit. Mismo
método que la depuración del core.
