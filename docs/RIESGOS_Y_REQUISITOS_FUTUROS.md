# Zenoxia — Riesgos y Requisitos Futuros
## Documento estratégico · NO es backlog inmediato

Captura riesgos estructurales y requisitos que el ecosistema VA a enfrentar, con su
fase del roadmap asignada. El objetivo es doble: (1) no perder análisis valioso, y
(2) NO construir nada de esto antes de tiempo — la disciplina contra la "Mansión MVP"
(producto sobredimensionado que nunca llega al cliente).

Regla rectora: cada ítem acá está documentado, NO en construcción. Se activa cuando
su fase llega. Construir esto antes de tener un módulo funcionando es el error que
hunde proyectos de healthtech.

---

# Vector 1 — Red hostil / Offline-first

**Riesgo:** los hospitales son entornos de red hostiles (salas blindadas, subsuelos,
congestión). Un módulo que dependa de conexión continua falla en la operación real.
La "consistencia eventual" entre módulos puede crear asimetrías de información
clínicamente peligrosas.

**Requisito futuro:**
- Los módulos deben funcionar en modo local (offline-first) y sincronizar asíncrono
  al recuperar conexión.
- `HitoTiempo` (y `HitoAtlas`) debe preservar el timestamp del MOMENTO REAL del evento
  (cuándo el usuario lo hizo), distinto del momento de sincronización. Desdoblar:
  `ocurrido_at` (en el dispositivo) vs `sincronizado_at` (en el core).
- Considerar flag de sincronización diferida (si el delta supera un umbral) para que
  la UI advierta que un dato llegó tarde.
- Resolución de conflictos sin sobrescritura silenciosa de datos clínicos.

**Fase:** el desdoblamiento de timestamps puede entrar temprano (es barato y no
estorba). El offline-first completo y la resolución de conflictos son de la fase de
integración / despliegue real (Fase 3+). NO en capa 1a.

---

# Vector 2 — Interoperabilidad defensiva (HIS cerrados)

**Riesgo:** los HIS heredados son monolitos cerrados; sus proveedores tienen
incentivo comercial en que módulos externos fracasen. Asumir cooperación FHIR dócil
es ingenuo.

**Requisito futuro:**
- Capacidades de extracción asimétricas que no dependan del permiso del proveedor:
  intercepción de tráfico MLLP (HL7 v2), raspado de impresión/pantalla con OCR.
- Una zona de cuarentena para ingerir datos sucios/desestructurados del HIS sin
  contaminar el modelo limpio, que después se traduce a FHIR.
- Anti-duplicación: hash del payload crudo (ej. SHA-256) con unicidad, porque los
  HIS legacy reenvían mensajes duplicados ante fallos de red.

**Fase:** Fase 3 (integración con sistemas externos). El principio federado YA nos
protege acá: Atlas funciona sin HIS, así que esto es enriquecimiento opcional, no
bloqueo. NO en capa 1a.

---

# Vector 3 — Anti-boicot / fricción operativa

**Riesgo:** el personal agotado boicotea sistemas que les suman trabajo. El
"secuestro de camas" (retrasar el alta para no recibir pacientes) es un mecanismo de
defensa real. Si Atlas depende de carga manual fiel, mostrará datos ficticios.

**Requisito futuro:**
- Minimizar carga manual: captura pasiva donde se pueda.
- Dar a Cordis/Atlas visibilidad en tiempo real de ingresos/egresos para DETECTAR
  patrones anómalos (ej. liberación súbita de muchas altas = secuestro).
- El rol de "control de camas" (que ya tenemos como ADMISION orquestando) es la
  figura que interviene; el sistema le da los tableros, no reemplaza su autoridad.

**Fase:** transversal al diseño. Ya está incorporado en el norte de Atlas ("facilitar
el trabajo cognitivo", proyección de capacidad). La detección algorítmica de patrones
es capa 1b/2. NO agregar nada al modelo de capa 1a por esto.

---

# Vector 4 — Médico-legal / MDR Regla 11

**Riesgo:** software que informa decisiones diagnósticas/terapéuticas puede
clasificarse como Dispositivo Médico (MDR Regla 11: Clase IIa o superior). Un registro
inmutable (HitoTiempo) es un arma de doble filo: prueba forense que puede usarse
CONTRA el sistema si hay desincronización y daño.

**Requisito futuro:**
- Mantener "humano en el bucle" (human-in-the-loop) en toda sugerencia algorítmica:
  flags `es_sugerencia_algoritmica` y `aprobado_por_humano`, más `motivo_modificacion`
  cuando un humano corrige a la máquina (escudo legal + dato de reentrenamiento).
- Decisión regulatoria explícita sobre la clasificación de cada módulo ANTES de
  comercializar sugerencias clínicas.

**Fase:** crítico para la capa 1b (motor de sugerencias) y cualquier cosa predictiva.
NO aplica a la capa 1a, que solo registra estado de camas (no sugiere nada clínico).
Cuando se diseñe la capa 1b, este vector es requisito de entrada, no opcional.

---

# Vector 5 — Comercial / caballo de Troya

**Riesgo:** ciclo de venta B2B hospitalario de 8-18 meses, comité de compras
heterogéneo, sesgo de costo hundido del "monolito ya pagado". Vender el ecosistema
completo de entrada es inviable.

**Estrategia (ya adoptada):**
- Entrar por UN módulo en un nicho de dolor agudo (Cordis en guardia, o Atlas en
  camas), con presupuesto discrecional de un jefe de área, sin pelear con el comité
  ni con el HIS central.
- Land-and-expand: el módulo alivia dolor real → el personal lo evangeliza → se
  justifica el siguiente módulo → eventualmente se fuerza la apertura del HIS.
- Esto VALIDA la decisión federada (módulo vendible solo) y el principio "no sumar
  sin necesidad" (el módulo debe aliviar dolor, no agregar burocracia).

**Fase:** estrategia de negocio, transversal. No es código. Refuerza por qué el
roadmap construye módulos autónomos y por capas.

---

# Síntesis: qué significa esto para HOY

- **Nada de esto entra en la capa 1a.** La capa 1a registra estado de camas. Punto.
- **Lo único que vale considerar temprano** (barato, no estorba): el desdoblamiento
  de timestamps en los hitos (ocurrido_at vs sincronizado_at) — Vector 1. Se puede
  evaluar al diseñar HitoAtlas, sin comprometerse aún.
- **Todo lo demás** se activa en su fase: interoperabilidad defensiva y offline-first
  en Fase 3; médico-legal/MDR en capa 1b; anti-boicot y comercial son transversales
  ya incorporados al norte del proyecto.
- **El riesgo mayor no es técnico, es de foco:** construir cualquiera de estos vectores
  antes de tener un módulo funcionando es la "Mansión MVP" que hunde el proyecto.
