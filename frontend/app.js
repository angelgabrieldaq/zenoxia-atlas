/*
 * Atlas — Tablero de camas. Lógica del front (vanilla JS, sin framework, sin build).
 *
 * - Consume la API REST de Atlas por HTTP (capa fina: toda la lógica de negocio vive
 *   en el backend). Este front sólo pinta estado y dispara acciones semánticas.
 * - Estado reactivo con un Proxy de ES6: al reasignar una clave del store, se dispara
 *   un re-render LOCALIZADO (sólo la región afectada), sin librerías.
 *
 * CORS: la API permite los orígenes http://localhost:5173 y http://localhost:3000.
 *       Serví este front en uno de esos puertos (ver README al final del prompt / chat).
 */

"use strict";

// ───────────────────────── Config ─────────────────────────

const API = "http://localhost:8000";

// Roles operativos (espejo de domain.state_machine.RolOperativo).
const ROLES = [
  "ADMISION", "ENFERMERIA", "MEDICO", "HOTELERIA",
  "LIMPIEZA", "MANTENIMIENTO", "OPERACIONES",
];

// Categorías de internación (espejo de database.enums.CategoriaInternacion).
const CATEGORIAS = [
  "CLINICA", "CRITICA", "QUIRURGICA_PROGRAMADA",
  "QUIRURGICA_URGENCIA", "GUARDIA_OBSERVACION", "OBSTETRICA",
];

// Orden estable de estados para el resumen.
const ESTADOS_ORDEN = [
  "DISPONIBLE", "OCUPADA", "RESERVADA",
  "PROCESO_DE_ALTA", "LIMPIEZA_TERMINAL", "BLOQUEADA",
];

// Etiquetas humanas.
const ESTADO_LABEL = {
  DISPONIBLE: "Libre", OCUPADA: "Ocupada", RESERVADA: "Reservada",
  PROCESO_DE_ALTA: "En alta", LIMPIEZA_TERMINAL: "Limpieza", BLOQUEADA: "Bloqueada",
};
const ESTADO_PLURAL = {
  DISPONIBLE: "libres", OCUPADA: "ocupadas", RESERVADA: "reservadas",
  PROCESO_DE_ALTA: "en alta", LIMPIEZA_TERMINAL: "en limpieza", BLOQUEADA: "bloqueadas",
};
const TIPO_LABEL = { CAMA_INTERNACION: "Internación", UTI: "UTI", UCO: "UCO" };
const COMODIDAD_LABEL = {
  SIN_PREFERENCIA: "Sin preferencia", COMPARTIDA: "Compartida",
  INDIVIDUAL: "Individual", SUITE: "Suite",
};

/*
 * Acciones disponibles POR ESTADO. Sólo se listan las que TIENEN endpoint semántico en
 * la API del MVP (ocupar, reservar, iniciar-alta, alta-fisica, finalizar-limpieza,
 * bloquear, desbloquear). `rol` es el rol que la máquina de estados exige para esa
 * transición (se muestra como pista; igual se envía el rol seleccionado arriba).
 *
 * NOTA: "Cancelar reserva" (RESERVADA → DISPONIBLE) NO está acá: la API del MVP no
 * expone un endpoint para esa transición, así que no se inventa un botón sin respaldo.
 */
const ACCIONES = {
  DISPONIBLE: [
    { id: "ocupar",   label: "Ocupar",   rol: "ADMISION",      needs: "internacion" },
    { id: "reservar", label: "Reservar", rol: "ADMISION",      needs: "internacion" },
    { id: "bloquear", label: "Bloquear", rol: "MANTENIMIENTO", needs: "motivo", kind: "warn" },
  ],
  OCUPADA: [
    { id: "iniciar-alta", label: "Iniciar alta", rol: "MEDICO" },
    { id: "bloquear",     label: "Bloquear",     rol: "MANTENIMIENTO", needs: "motivo", kind: "warn" },
  ],
  RESERVADA: [
    { id: "ocupar", label: "Ocupar (cumplir reserva)", rol: "ENFERMERIA", needs: "internacion-actual" },
  ],
  PROCESO_DE_ALTA: [
    { id: "alta-fisica", label: "Alta física", rol: "ADMISION" },
  ],
  LIMPIEZA_TERMINAL: [
    { id: "finalizar-limpieza", label: "Finalizar limpieza", rol: "LIMPIEZA" },
  ],
  BLOQUEADA: [
    { id: "desbloquear", label: "Desbloquear", rol: "MANTENIMIENTO" },
  ],
};

// Íconos inline (outline, currentColor — respeta la sección 13 de los tokens).
const ICON = {
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
};

// ───────────────────────── Estado reactivo (Proxy) ─────────────────────────

const initialState = {
  camas: [],               // GET /camas  (CamaOut[])
  internacionesById: {},   // GET /internaciones, indexadas por id
  checklistByInternacion: {}, // checklist cargado por internación
  rol: "ADMISION",
  actorNombre: "",
  detalle: null,           // GET /camas/{id}  (CamaDetalleOut) o null
  pendingAction: null,     // { camaId, accionId, rol, needs } cuando una acción pide datos
  toast: null,             // { text, kind, id } | null
  loading: false,
};

// Mapa clave-cambiada → renders localizados que dispara.
const RENDERERS = {
  camas:            () => { renderResumen(); renderBoard(); },
  internacionesById:() => { renderResumen(); renderBoard(); renderDetalle(); },
  checklistByInternacion: () => { renderDetalle(); },
  rol:              () => { renderDetalle(); },
  actorNombre:      () => {},
  detalle:          () => { renderDetalle(); },
  pendingAction:    () => { renderDetalle(); },
  toast:            () => { renderToast(); },
  loading:          () => { renderResumen(); },
};

// El Proxy intercepta las asignaciones de primer nivel. Para que dispare, SIEMPRE se
// reasigna la clave entera (ej. state.camas = [...]), no se muta en su lugar.
const state = new Proxy({ ...initialState }, {
  set(target, key, value) {
    target[key] = value;
    (RENDERERS[key] || (() => {}))();
    return true;
  },
});

// ───────────────────────── Utilidades ─────────────────────────

/** Mini-hyperscript: crea nodos sin innerHTML (textContent → seguro ante XSS). */
function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "dataset") Object.assign(n.dataset, v);
    else if (k === "html") n.innerHTML = v; // sólo para SVG de confianza (ICON)
    else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2).toLowerCase(), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return n;
}

class ApiError extends Error {
  constructor(detail, status) { super(detail); this.status = status; }
}

/** fetch + manejo uniforme de errores: si !ok, lanza ApiError con el {detail} de la API. */
async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(API + path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
  } catch (e) {
    throw new ApiError("No hay conexión con la API (¿está corriendo en " + API + "?).", 0);
  }
  const raw = await res.text();
  let data = null;
  if (raw) { try { data = JSON.parse(raw); } catch { /* respuesta no-JSON */ } }
  if (!res.ok) {
    const detail = (data && data.detail) || `Error ${res.status}`;
    throw new ApiError(detail, res.status);
  }
  return data;
}

const fmtFecha = (iso) => {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("es-AR", {
      day: "2-digit", month: "2-digit", year: "2-digit",
      hour: "2-digit", minute: "2-digit",
    }).format(new Date(iso));
  } catch { return iso; }
};

const internOf = (cama) =>
  cama && cama.internacion_actual_id ? state.internacionesById[cama.internacion_actual_id] || null : null;

const cobLinea = (intern) => {
  if (!intern) return "";
  const partes = [intern.cobertura, intern.plan_cobertura].filter(Boolean);
  return partes.join(" · ");
};

const nombrePaciente = (intern) => {
  if (!intern) return "Paciente";
  return [intern.paciente_apellido, intern.paciente_nombre].filter(Boolean).join(", ") || "Paciente";
};

let toastTimer = null;
function toast(text, kind = "info") {
  const id = Date.now() + Math.random();
  state.toast = { text, kind, id };
  if (toastTimer) clearTimeout(toastTimer);
  const ms = kind === "err" ? 7000 : 3500;
  toastTimer = setTimeout(() => {
    if (state.toast && state.toast.id === id) state.toast = null;
  }, ms);
}

// ───────────────────────── Carga de datos ─────────────────────────

async function cargarTablero() {
  state.loading = true;
  try {
    const [camas, internaciones] = await Promise.all([api("/camas"), api("/internaciones")]);
    const map = {};
    for (const i of internaciones) map[i.id] = i;
    state.internacionesById = map;
    state.camas = camas;
  } catch (e) {
    toast("No se pudo cargar el tablero: " + e.message, "err");
  } finally {
    state.loading = false;
  }
}

async function abrirDetalle(camaId, trigger) {
  if (trigger) lastTrigger = trigger;
  try {
    const detalle = await api("/camas/" + camaId);
    state.pendingAction = null;
    state.detalle = detalle;
    const intern = internOf(detalle);
    if (intern && detalle.estado_gestion === "PROCESO_DE_ALTA") {
      await ensureChecklist(intern.id);
    }
  } catch (e) {
    toast(e.message, "err");
  }
}

function cerrarDetalle() {
  state.pendingAction = null;
  state.detalle = null;
  if (lastTrigger && document.contains(lastTrigger)) lastTrigger.focus();
}

// ───────────────────────── Acciones ─────────────────────────

function onAccion(cama, accion) {
  if (accion.needs === "internacion" || accion.needs === "motivo") {
    state.pendingAction = { camaId: cama.id, accionId: accion.id, rol: accion.rol, needs: accion.needs };
    return;
  }
  if (accion.needs === "internacion-actual") {
    if (!cama.internacion_actual_id) { toast("La reserva no tiene internación asociada.", "err"); return; }
    ejecutar(cama.id, accion.id, { internacion_id: cama.internacion_actual_id });
    return;
  }
  ejecutar(cama.id, accion.id, {});
}

/** POST a la acción semántica con el rol seleccionado. Refresca tablero + detalle. */
async function ejecutar(camaId, accionId, payload) {
  const body = { rol: state.rol, ...payload };
  if (state.actorNombre.trim()) body.actor_nombre = state.actorNombre.trim();
  try {
    await api(`/camas/${camaId}/${accionId}`, { method: "POST", body: JSON.stringify(body) });
    toast("Acción aplicada correctamente.", "ok");
    state.pendingAction = null;
    await cargarTablero();
    await abrirDetalle(camaId);
  } catch (e) {
    if (accionId === "alta-fisica" && e.status === 409 && e.message.includes("pasos_pendientes")) {
      toast("Alta física bloqueada por pasos pendientes. Completá el checklist o usa override en el backend.", "warn");
    } else {
      toast(e.message, "err");
    }
  }
}

async function loadChecklist(internacionId) {
  try {
    const pasos = await api(`/camas/internaciones/${internacionId}/pasos`);
    state.checklistByInternacion = { ...state.checklistByInternacion, [internacionId]: pasos };
  } catch (e) {
    toast("No se pudo cargar el checklist: " + e.message, "err");
  }
}

async function instantiateChecklist(internacionId) {
  try {
    await api(`/camas/internaciones/${internacionId}/pasos/instanciar`, { method: "POST" });
    await loadChecklist(internacionId);
    toast("Checklist instanciado.", "ok");
  } catch (e) {
    toast("No se pudo instanciar el checklist: " + e.message, "err");
  }
}

async function ensureChecklist(internacionId) {
  const current = state.checklistByInternacion[internacionId];
  if (current !== undefined) return;
  await loadChecklist(internacionId);
  if (!state.checklistByInternacion[internacionId]?.length) {
    await instantiateChecklist(internacionId);
  }
}

async function completarPaso(pasoId, internacionId, camaId) {
  try {
    await api(`/camas/pasos/${pasoId}/completar`, {
      method: "POST",
      body: JSON.stringify({ rol: state.rol, actor_nombre: state.actorNombre.trim() || undefined }),
    });
    toast("Paso completado.", "ok");
    await loadChecklist(internacionId);
    await abrirDetalle(camaId);
  } catch (e) {
    toast(e.message, "err");
  }
}

async function crearNotaCama(camaId, texto) {
  if (!texto.trim()) { toast("La nota no puede quedar vacía.", "warn"); return; }
  try {
    await api(`/camas/${camaId}/notas`, {
      method: "POST",
      body: JSON.stringify({ texto: texto.trim(), rol: state.rol, actor_nombre: state.actorNombre.trim() || undefined }),
    });
    toast("Nota guardada.", "ok");
    await abrirDetalle(camaId);
  } catch (e) {
    toast(e.message, "err");
  }
}

/** Internaciones candidatas para ocupar/reservar: abiertas y NO asignadas hoy a una cama. */
function internacionesLibres() {
  const ocupadas = new Set(state.camas.map((c) => c.internacion_actual_id).filter(Boolean));
  return Object.values(state.internacionesById)
    .filter((i) => !i.finalizada_at && !ocupadas.has(i.id))
    .sort((a, b) => nombrePaciente(a).localeCompare(nombrePaciente(b)));
}

// ───────────────────────── Render: resumen ─────────────────────────

function renderResumen() {
  const host = document.getElementById("resumen");
  host.innerHTML = "";

  if (state.loading && state.camas.length === 0) {
    host.append(el("span", { class: "muted" }, "Cargando tablero…"));
    return;
  }

  const conteo = {};
  for (const e of ESTADOS_ORDEN) conteo[e] = 0;
  for (const c of state.camas) conteo[c.estado_gestion] = (conteo[c.estado_gestion] || 0) + 1;

  for (const e of ESTADOS_ORDEN) {
    const chip = el("span", { class: "chip", dataset: { estado: e } },
      el("span", { class: "chip-dot" }),
      el("span", { class: "chip-n" }, conteo[e]),
      el("span", {}, ESTADO_PLURAL[e]),
    );
    // Inyecta la tripleta de color del estado vía variables locales.
    chip.style.cssText = colorVarsFor(e);
    host.append(chip);
  }
  host.append(el("span", { class: "chip-total" }, `${state.camas.length} camas`));
}

// Devuelve un string de custom-properties (--c/--c-l/--c-m) para un estado, leyendo
// SIEMPRE de los tokens de cama (nada hardcodeado).
function colorVarsFor(estado) {
  const m = {
    DISPONIBLE: "libre", OCUPADA: "ocup", RESERVADA: "lista",
    PROCESO_DE_ALTA: "alta", LIMPIEZA_TERMINAL: "limp", BLOQUEADA: "bloq",
  }[estado];
  return `--c:var(--cama-${m});--c-l:var(--cama-${m}-l);--c-m:var(--cama-${m}-m);`;
}

// ───────────────────────── Render: tablero ─────────────────────────

function renderBoard() {
  const host = document.getElementById("board");
  host.innerHTML = "";

  if (state.camas.length === 0 && !state.loading) {
    host.append(el("p", { class: "muted" }, "No hay camas cargadas. Corré el seed: python -m scripts.seed_demo --reset"));
    return;
  }

  // Agrupar por sector preservando el orden (la API ya devuelve ordenado por sector, nombre).
  const sectores = new Map();
  for (const c of state.camas) {
    if (!sectores.has(c.sector)) sectores.set(c.sector, []);
    sectores.get(c.sector).push(c);
  }

  for (const [sector, camas] of sectores) {
    const grid = el("div", { class: "grid", role: "list" });
    for (const c of camas) grid.append(renderCama(c));
    host.append(el("section", { class: "sector" },
      el("div", { class: "sector-head" },
        el("h2", { class: "sector-title" }, sector),
        el("span", { class: "sector-count" }, `${camas.length} camas`),
      ),
      grid,
    ));
  }
}

function renderCama(c) {
  const intern = internOf(c);
  const card = el("button", {
    class: "cama",
    type: "button",
    role: "listitem",
    dataset: { estado: c.estado_gestion },
    "aria-label": `Cama ${c.nombre}, ${ESTADO_LABEL[c.estado_gestion]}. Ver detalle.`,
    onClick: (ev) => abrirDetalle(c.id, ev.currentTarget),
  },
    el("div", { class: "cama-top" },
      el("span", { class: "cama-codigo" }, c.nombre),
      el("span", { class: "cama-tipo" }, TIPO_LABEL[c.tipo] || c.tipo),
    ),
    el("span", { class: "badge" }, ESTADO_LABEL[c.estado_gestion]),
    renderCamaBody(c, intern),
  );
  return card;
}

function renderCamaBody(c, intern) {
  const body = el("div", { class: "cama-body" });
  if (c.estado_gestion === "BLOQUEADA") {
    body.append(el("span", { class: "cama-motivo" }, c.motivo_bloqueo || "Bloqueada"));
  } else if (intern) {
    body.append(el("span", { class: "cama-pac" }, nombrePaciente(intern)));
    const cob = cobLinea(intern);
    if (cob) body.append(el("span", { class: "cama-cob" }, cob));
  } else if (c.estado_gestion === "DISPONIBLE") {
    body.append(el("span", { class: "cama-empty" }, "Disponible"));
  } else if (c.estado_gestion === "LIMPIEZA_TERMINAL") {
    body.append(el("span", { class: "cama-empty" }, "Esperando limpieza"));
  }
  return body;
}

// ───────────────────────── Render: detalle (drawer) ─────────────────────────

let lastTrigger = null;

function renderDetalle() {
  const host = document.getElementById("drawer-host");
  host.innerHTML = "";

  const d = state.detalle;
  if (!d) { document.body.classList.remove("drawer-open"); return; }

  const intern = internOf(d);

  const overlay = el("div", { class: "overlay", onClick: cerrarDetalle });

  const drawer = el("aside", {
    class: "drawer",
    dataset: { estado: d.estado_gestion },
    role: "dialog",
    "aria-modal": "true",
    "aria-labelledby": "drawer-title",
    onKeydown: (ev) => trapFocus(ev, drawer),
  },
    el("div", { class: "drawer-head" },
      el("div", {},
        el("h2", { id: "drawer-title", class: "codigo", tabindex: "-1" }, d.nombre),
        el("div", { class: "meta" },
          `${ESTADO_LABEL[d.estado_gestion]} · ${TIPO_LABEL[d.tipo] || d.tipo} · ${d.sector}` +
          (d.comodidad ? ` · ${COMODIDAD_LABEL[d.comodidad] || d.comodidad}` : "")),
      ),
      el("button", { class: "icon-btn", type: "button", "aria-label": "Cerrar detalle", onClick: cerrarDetalle },
        el("span", { class: "ki ki-md", html: ICON.close, "aria-hidden": "true" })),
    ),
    el("div", { class: "drawer-body" },
      d.estado_gestion === "BLOQUEADA" ? renderBloqueoInfo(d) : null,
      intern ? renderPacienteInfo(intern) : null,
      renderAccionesArea(d),
      intern ? renderChecklist(intern, d) : null,
      renderHitos(d.hitos || []),
      renderNotas(d.notas || []),
      renderNotaForm(d),
    ),
  );

  host.append(overlay, drawer);
  document.body.classList.add("drawer-open");

  // Foco al título al abrir (gestión de foco accesible).
  const title = drawer.querySelector("#drawer-title");
  if (title) title.focus();
}

function renderBloqueoInfo(d) {
  return el("div", {},
    el("div", { class: "section-label" }, "Bloqueo"),
    el("div", { class: "cobertura-card" }, el("span", {}, d.motivo_bloqueo || "Sin motivo registrado.")),
  );
}

function renderPacienteInfo(intern) {
  const dl = el("dl", { class: "dl" });
  const row = (dt, dd) => { dl.append(el("dt", {}, dt), el("dd", {}, dd || "—")); };
  row("Paciente", nombrePaciente(intern));
  row("DNI", intern.paciente_dni);
  row("Cobertura", intern.cobertura);
  row("Plan", intern.plan_cobertura);
  row("N° socio", intern.numero_socio);
  if (intern.nota_cobertura) row("Nota", intern.nota_cobertura);
  return el("div", {},
    el("div", { class: "section-label" }, "Paciente y cobertura"),
    el("div", { class: "cobertura-card" }, dl),
  );
}

function renderAccionesArea(d) {
  const wrap = el("div", {},
    el("div", { class: "section-label" }, "Acciones"),
  );

  const pa = state.pendingAction;
  if (pa && pa.camaId === d.id) {
    wrap.append(pa.needs === "motivo" ? formMotivo(d, pa) : formInternacion(d, pa));
    return wrap;
  }

  const acciones = ACCIONES[d.estado_gestion] || [];
  if (acciones.length === 0) {
    wrap.append(el("p", { class: "muted" }, "No hay acciones disponibles para este estado."));
    return wrap;
  }

  const cont = el("div", { class: "acciones" });
  for (const a of acciones) {
    const mismatch = a.rol !== state.rol;
    cont.append(el("button", {
      class: "btn " + (a.kind === "warn" ? "btn-warn" : "btn-primary"),
      type: "button",
      onClick: () => onAccion(d, a),
    },
      el("span", {}, a.label),
      el("span", {
        class: "rol-hint" + (mismatch ? " rol-hint--warn" : ""),
        title: mismatch ? `El rol actual es ${state.rol}; esta acción la dispara ${a.rol}.` : "",
      }, "rol: " + a.rol),
    ));
  }
  wrap.append(cont);
  return wrap;
}

// Mini-form: bloquear (motivo obligatorio).
function formMotivo(d, pa) {
  const input = el("input", { id: "f-motivo", type: "text", maxlength: "200",
    placeholder: "Ej.: Mantenimiento de equipo", "aria-required": "true" });
  return el("div", { class: "form" },
    el("h3", {}, "Bloquear cama " + d.nombre),
    el("div", { class: "field" },
      el("label", { for: "f-motivo" }, "Motivo del bloqueo *"), input),
    formActions(() => {
      const motivo = input.value.trim();
      if (!motivo) { toast("El motivo del bloqueo es obligatorio.", "warn"); input.focus(); return; }
      ejecutar(d.id, "bloquear", { motivo_bloqueo: motivo });
    }),
  );
}

// Mini-form: ocupar / reservar (elegir internación existente o crear una nueva).
function formInternacion(d, pa) {
  const esReserva = pa.accionId === "reservar";
  const titulo = (esReserva ? "Reservar" : "Ocupar") + " cama " + d.nombre;

  const libres = internacionesLibres();
  const select = el("select", { id: "f-intern" },
    el("option", { value: "" }, "— elegir internación existente —"),
    ...libres.map((i) => el("option", { value: i.id },
      `${nombrePaciente(i)} — DNI ${i.paciente_dni || "?"}${i.cobertura ? " · " + i.cobertura : ""}`)),
  );

  // Mini-form de creación.
  const fDni = el("input", { id: "f-dni", type: "text", maxlength: "8", inputmode: "numeric", autocomplete: "off" });
  const fNom = el("input", { id: "f-nom", type: "text", maxlength: "100", autocomplete: "off" });
  const fApe = el("input", { id: "f-ape", type: "text", maxlength: "100", autocomplete: "off" });
  const fCat = el("select", { id: "f-cat" }, ...CATEGORIAS.map((c) => el("option", { value: c }, c)));
  const fCob = el("input", { id: "f-cob", type: "text", maxlength: "100", autocomplete: "off" });
  const fPlan = el("input", { id: "f-plan", type: "text", maxlength: "60", autocomplete: "off" });
  const fSoc = el("input", { id: "f-soc", type: "text", maxlength: "60", autocomplete: "off" });

  const onConfirm = async () => {
    let internId = select.value;
    if (!internId) {
      const dni = fDni.value.trim(), nombre = fNom.value.trim(), apellido = fApe.value.trim();
      if (!dni || !nombre || !apellido) {
        toast("Elegí una internación existente, o completá DNI, nombre y apellido para crear una.", "warn");
        return;
      }
      try {
        const nueva = await api("/internaciones", {
          method: "POST",
          body: JSON.stringify({
            dni, nombre, apellido, categoria: fCat.value,
            cobertura: fCob.value.trim() || null,
            plan_cobertura: fPlan.value.trim() || null,
            numero_socio: fSoc.value.trim() || null,
          }),
        });
        internId = nueva.id;
      } catch (e) { toast(e.message, "err"); return; }
    }
    const payload = { internacion_id: internId };
    if (esReserva) payload.tipo_cama_requerido = d.tipo; // la reserva exige el tipo de la cama
    ejecutar(d.id, pa.accionId, payload);
  };

  return el("div", { class: "form" },
    el("h3", {}, titulo),
    el("div", { class: "field" },
      el("label", { for: "f-intern" }, "Internación"), select),
    el("div", { class: "form-divider" }, "o crear una nueva"),
    el("fieldset", {},
      el("legend", {}, "Nueva internación"),
      el("div", { class: "form-row" },
        el("div", { class: "field" }, el("label", { for: "f-dni" }, "DNI"), fDni),
        el("div", { class: "field" }, el("label", { for: "f-cat" }, "Categoría"), fCat),
      ),
      el("div", { class: "form-row" },
        el("div", { class: "field" }, el("label", { for: "f-nom" }, "Nombre"), fNom),
        el("div", { class: "field" }, el("label", { for: "f-ape" }, "Apellido"), fApe),
      ),
      el("div", { class: "field" }, el("label", { for: "f-cob" }, "Cobertura (opcional)"), fCob),
      el("div", { class: "form-row" },
        el("div", { class: "field" }, el("label", { for: "f-plan" }, "Plan (opcional)"), fPlan),
        el("div", { class: "field" }, el("label", { for: "f-soc" }, "N° socio (opcional)"), fSoc),
      ),
    ),
    formActions(onConfirm, esReserva ? "Reservar" : "Ocupar"),
  );
}

function formActions(onConfirm, okLabel = "Confirmar") {
  return el("div", { class: "form-actions" },
    el("button", { class: "btn btn-primary", type: "button", onClick: onConfirm }, okLabel),
    el("button", { class: "btn", type: "button", onClick: () => { state.pendingAction = null; } }, "Cancelar"),
  );
}

function renderHitos(hitos) {
  const cont = el("div", { class: "hitos" });
  if (hitos.length === 0) {
    cont.append(el("p", { class: "muted" }, "Sin movimientos registrados."));
  } else {
    for (const h of hitos) {
      const quien = [h.actor_rol, h.actor_nombre].filter(Boolean).join(" · ");
      cont.append(el("div", { class: "hito" },
        el("div", {},
          el("div", { class: "hito-cod" }, h.hito_codigo),
          el("div", { class: "hito-meta" }, `${fmtFecha(h.registrado_at)}${quien ? " — " + quien : ""}`),
        ),
      ));
    }
  }
  return el("div", {}, el("div", { class: "section-label" }, "Traza de hitos"), cont);
}

function renderNotas(notas) {
  const cont = el("div", { class: "notas" });
  if (notas.length === 0) {
    cont.append(el("p", { class: "muted" }, "Sin notas."));
  } else {
    for (const n of notas) {
      const quien = [n.creada_por_rol, n.creada_por_nombre].filter(Boolean).join(" · ");
      cont.append(el("div", { class: "nota" },
        el("div", { class: "nota-txt" }, n.texto),
        el("div", { class: "nota-meta" }, `${fmtFecha(n.creada_at)}${quien ? " — " + quien : ""}`),
      ));
    }
  }
  return el("div", {}, el("div", { class: "section-label" }, "Notas de la cama"), cont);
}

function renderChecklist(intern, cama) {
  const pasos = state.checklistByInternacion[intern.id] || [];
  const cont = el("div", { class: "checklist" });

  if (pasos.length === 0) {
    return el("div", {},
      el("div", { class: "section-label" }, "Checklist de pre-alta"),
      el("p", { class: "muted" }, "Aún no se ha instanciado un checklist para esta internación."),
      el("button", { class: "btn btn-primary", type: "button", onClick: () => instantiateChecklist(intern.id) }, "Instanciar checklist"),
    );
  }

  for (const paso of pasos) {
    cont.append(el("div", { class: "check-row" },
      el("div", { class: "check-box " + (paso.completado ? "cb-done" : "cb-pend") }, paso.completado ? "✓" : ""),
      el("div", { class: "check-label" + (paso.completado ? " done" : "") },
        paso.codigo ? `${paso.codigo}: ` : "", paso.nombre || "Paso sin descripción",
        paso.era_bloqueante ? el("span", { class: "legal-tag" }, "Bloqueante") : null,
      ),
      !paso.completado ? el("button", { class: "btn btn-sm", type: "button", onClick: () => completarPaso(paso.id, intern.id, cama.id) }, "Completar") : null,
    ));
  }

  const pendientes = pasos.filter((p) => !p.completado).length;
  const hint = pendientes > 0
    ? `Hay ${pendientes} paso(s) pendiente(s) antes de la alta física.`
    : "Todos los pasos del checklist están completos.";

  return el("div", {},
    el("div", { class: "section-label" }, "Checklist de pre-alta"),
    el("div", { class: "cg-locked" }, hint),
    cont,
  );
}

function renderNotaForm(cama) {
  const textarea = el("textarea", { class: "note-ta", id: "nota-texto", placeholder: "Registrar una nota operativa sobre la cama..." });
  const submit = el("button", { class: "btn btn-green", type: "button", onClick: () => crearNotaCama(cama.id, textarea.value) }, "Guardar nota");
  return el("div", {},
    el("div", { class: "section-label" }, "Nueva nota operativa"),
    textarea,
    el("div", { class: "action-foot" }, submit),
  );
}

// Trampa de foco básica dentro del drawer (accesibilidad de diálogo modal).
function trapFocus(ev, drawer) {
  if (ev.key === "Escape") { ev.preventDefault(); cerrarDetalle(); return; }
  if (ev.key !== "Tab") return;
  const focusables = drawer.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
  if (focusables.length === 0) return;
  const first = focusables[0], last = focusables[focusables.length - 1];
  if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
  else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
}

// ───────────────────────── Render: toast ─────────────────────────

function renderToast() {
  const host = document.getElementById("toast-host");
  host.innerHTML = "";
  const t = state.toast;
  if (!t) return;
  const node = el("div", {
    class: "toast toast--" + t.kind,
    role: t.kind === "err" ? "alert" : "status",
    "aria-live": t.kind === "err" ? "assertive" : "polite",
  },
    el("span", { class: "toast-text" }, t.text),
    el("button", { class: "toast-close", type: "button", "aria-label": "Cerrar aviso",
      onClick: () => { state.toast = null; } }, "×"),
  );
  host.append(node);
}

// ───────────────────────── Arranque ─────────────────────────

function montarControles() {
  const sel = document.getElementById("rol-select");
  for (const r of ROLES) sel.append(el("option", { value: r }, r));
  sel.value = state.rol;
  sel.addEventListener("change", () => { state.rol = sel.value; });

  const actor = document.getElementById("actor-nombre");
  actor.addEventListener("input", () => { state.actorNombre = actor.value; });

  document.getElementById("btn-refresh").addEventListener("click", cargarTablero);
}

document.addEventListener("DOMContentLoaded", () => {
  montarControles();
  cargarTablero();
});
