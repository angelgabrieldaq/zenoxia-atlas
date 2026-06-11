# Defunción como medio de egreso — Proceso operativo y alcance de Atlas

**Estado:** Diseño cerrado. `defuncion` entra al MVP como quinto `medio_egreso`.
**Fecha:** 10 jun 2026
**Fuente:** Relevamiento operativo directo (institución de referencia, CABA) +
instructivos internos de inscripción de defunción y confección de certificado.

**Decisión madre:** El corte de alcance va donde la cama deja de ser el eje.
Atlas traza los hitos que bloquean o liberan la cama; NO gestiona el circuito
documental del óbito (legajos, trámite GCBA, copias).

---

## 1. El proceso real, hoy (sin trazabilidad en ningún actor)

Relevado en campo. Los intervalos marcados ⏱ son los que hoy nadie mide:

1. **Fallecimiento.** Médico/enfermería avisa a admisión para contactar a la
   familia si no está presente.
   ⏱ **Aviso a familia puede diferir del fallecimiento por HORAS.**

2. **Familia presente e informada.** Debe conciliar el documento del fallecido
   y presentarse un autorizante con su propio documento, que completa el
   troquel del certificado de defunción.

3. **El médico informa el alta en el HIS** para que la defunción exista en
   el sistema.

4. **Admisión recibe a la familia:** documentos, contactos, consulta por
   cremación (formulario aparte, opcional), consulta si habrá
   reconocimiento presencial en el retiro o si se acepta retiro solo con
   cochería. Explica trámite y demoras.

5. **Admisión envía al médico:** certificado de defunción vacío + instructivo,
   certificado de cremación (si aplica), documento original del fallecido.

6. **El médico completa los certificados.**
   ⏱ **De 30 minutos a 3 horas**, extremadamente variable, sin trazabilidad
   desde ningún actor. Mientras tanto, admisión sigue conteniendo a la familia.

7. **Admisión carga el trámite en la plataforma del gobierno** (≈3 hs según
   instructivo) y hace el cierre administrativo del episodio, incluyendo
   revisión de costos pendientes de la internación.

8. **Aviso a la familia** (puede retirar) + copia de todo a seguridad para
   habilitar el retiro.

9. **Retiro efectivo del óbito.** Se registra: cochería, nombre de quien
   retira, administrativo que entrega, personal de seguridad intervinieente.
   ⏱ **Tiempo entre habilitación y retiro:** hoy solo en libro papel.

10. **Legajo administrativo + listado** que se entrega al registro del
    gobierno con todos los certificados. ⏱ Tampoco hay trazabilidad.

---

## 2. Qué captura Atlas v1 (lo que bloquea la cama)

`defuncion` se modela **igual que `derivacion`**: checklist multi-rol que
bloquea, OK administrativo, y un prestador externo (la cochería) que retira.
Cero cambios de schema; una entrada de catálogo y un string en la cascada.

### Catálogo de items

| # | Responsable | Item | Legal |
|---|---|---|---|
| 1 | admision | Documentación del fallecido y autorizante recibida | No |
| 2 | admision | Certificados e instructivo enviados al médico | No |
| 3 | medico | Certificado de defunción completado, firmado y sellado | **Sí** |
| 4 | medico | Certificado de cremación completado (si corresponde) | No |
| 5 | admision | Inscripción en registro civil cargada | **Sí** |
| 6 | admision | Cierre administrativo del episodio (costos pendientes) | No |
| 7 | admision | Documentación entregada a seguridad — retiro habilitado | No |

**Convención No Aplica:** si no hay cremación, el item 4 se marca `done` de
inmediato (autor + hito con metadata `{"no_aplica": true}`). Sin cambio de schema.

### Cascada de responsable

Nivel 3 (post-OK-admin, sin salida física):
`medio_egreso in ('ambulancia', 'derivacion', 'defuncion')` → `prestador_externo`,
tarea para defunción: **"Retiro del óbito por cochería"**.

### Hito de salida física

`ATLAS_SALIDA_FISICA` con `metadata_evento`:

```json
{"cocheria": "...", "quien_retira": "...",
 "administrativo_entrega": "...", "seguridad": "..."}
```

Es la versión digital del libro de retiro de óbitos, sellada e inmutable.

---

## 3. Métricas que emergen gratis de los hitos

Cada item marcado sella un `HitoAtlas` con timestamp. Sin código adicional:

- **Demora del médico** = hito item 2 → hito item 3. El intervalo de 30 min a
  3 hs, medido por primera vez. Mientras está pendiente, el tablero muestra
  `Responsable: MÉDICO` — presión por visibilidad.
- **Demora de cochería** = hito item 7 → hito salida física. Por cochería
  (metadata), permite ranking de eficiencia de prestadores.
- **Demora familia/documentación** = creación del egreso → hito item 1.
- **Bloqueo total de cama por óbito** = `trabado_desde` → salida física.
  Hoy invisible; probablemente de los peores casos del hospital.

**Encuadre de venta:** Atlas **detecta anomalías y patrones, no prueba
irregularidades**. Una concentración estadística de una cochería en un turno
dispara revisión humana — puede ser cercanía, convenio o algo peor; el valor
es que la pregunta hoy ni siquiera puede formularse por falta de dato.
Venderlo como **transparencia y métricas de prestadores externos**.

---

## 4. Qué queda FUERA de Atlas (y dónde vive)

| Pieza | Por qué afuera | Dónde vive a futuro |
|---|---|---|
| Legajo físico de óbito (3 juegos de copias) | No bloquea la cama; obligación documental | Feature "circuito documental" o adaptador HIS |
| Mecánica del trámite en plataforma GCBA | Sistema de terceros; Atlas registra el hito, no opera la carga | Ídem; eventual RPA/integración |
| Listado al registro del gobierno | Post-liberación de cama | Ídem |
| Reconocimiento presencial del cadáver | Detalle del circuito de retiro | Metadata del hito si se necesita |
| Gestión de contactos de la familia | CRM-like, fuera de dominio | HIS / módulo futuro |

**Regla:** Atlas **registra que estos pasos ocurrieron** (hitos), no gestiona su
contenido. Si mañana se construye el circuito documental, nace sobre los
hitos que Atlas ya siembra desde el día uno.
