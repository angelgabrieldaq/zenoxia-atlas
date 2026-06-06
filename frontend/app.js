"use strict";

const API = "http://localhost:8000";

const ROLES = [
  "ADMISION", "ENFERMERIA", "MEDICO", "HOTELERIA",
  "LIMPIEZA", "MANTENIMIENTO", "OPERACIONES",
];

const CATEGORIAS = [
  "CLINICA", "CRITICA", "QUIRURGICA_PROGRAMADA",
  "QUIRURGICA_URGENCIA", "GUARDIA_OBSERVACION", "OBSTETRICA",
];

const ESTADOS_ORDEN = [
  "DISPONIBLE", "OCUPADA", "RESERVADA",
  "PROCESO_DE_ALTA", "LIMPIEZA_TERMINAL", "BLOQUEADA",
];

const ESTADO_LABEL = {
  DISPONIBLE: "Libre", OCUPADA: "Ocupada", RESERVADA: "Reservada",
  PROCESO_DE_ALTA: "En alta", LIMPIEZA_TERMINAL: "Limpieza", BLOQUEADA: "Bloqueada",
};
const ESTADO_PLURAL = {
  DISPONIBLE: "libres", OCUPADA: "ocupadas", RESERVADA: "reservadas",
  PROCESO_DE_ALTA: "en alta", LIMPIEZA_TERMINAL: "en limpieza", BLOQUEADA: "bloqueadas",
};
const TIPO_LABEL = { CAMA_INTERNACION: "Internación", UTI: "UTI", UCO: "UCO" };

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
    { id: "ocupar", label: "Ocupar reserva", rol: "ENFERMERIA", needs: "internacion-actual" },
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

const initialState = {
  camas: [],
  internacionesById: {},
  checklistByInternacion: {},
  rol: "ADMISION",
  actorNombre: "",
  detalle: null,
  pendingAction: null,
  toast: null,
  loading: false,
};

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

const state = new Proxy({ ...initialState }, {
  set(target, key, value) {
    target[key] = value;
    (RENDERERS[key] || (() => {}))();
    return true;
  },
});

function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "dataset") Object.assign(n.dataset, v);
    else if (k === "html") n.innerHTML = v;
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

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(API + path, { headers: { "Content-Type": "application/json" }, ...opts });
  } catch (e) {
    throw new ApiError("No hay conexión con la API.", 0);
  }
  const raw = await res.text();
  let data = null;
  if (raw) { try { data = JSON.parse(raw); } catch { } }
  if (!res.ok) throw new ApiError((data && data.detail) || `Error ${res.status}`, res.status);
  return data;
}

const fmtFecha = (iso) => iso ? new Date(iso).toLocaleString("es-AR") : "—";
const internOf = (cama) => cama && cama.internacion_actual_id ? state.internacionesById[cama.internacion_actual_id] || null : null;
const cobLinea = (intern) => intern ? [intern.cobertura, intern.plan_cobertura].filter(Boolean).join(" · ") : "";
const nombrePaciente = (intern) => intern ? [intern.paciente_apellido, intern.paciente_nombre].filter(Boolean).join(", ") : "Paciente";

let toastTimer = null;
function toast(text, kind = "info") {
  const id = Date.now() + Math.random();
  state.toast = { text, kind, id };
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { if (state.toast && state.toast.id === id) state.toast = null; }, 4000);
}

async function cargarTablero() {
  state.loading = true;
  try {
    const [camas, internaciones] = await Promise.all([api("/camas"), api("/internaciones")]);
    const map = {};
    for (const i of internaciones) map[i.id] = i;
    state.internacionesById = map;
    state.camas = camas;
  } catch (e) { toast("No se pudo cargar el tablero", "err"); } finally { state.loading = false; }
}

async function abrirDetalle(camaId) {
  try {
    const detalle = await api("/camas/" + camaId);
    state.pendingAction = null;
    state.detalle = detalle;
    const intern = internOf(detalle);
    if (intern && detalle.estado_gestion === "PROCESO_DE_ALTA") await ensureChecklist(intern.id);
  } catch (e) { toast(e.message, "err"); }
}

function cerrarDetalle() { state.pendingAction = null; state.detalle = null; }

function onAccion(cama, accion) {
  if (accion.needs === "internacion" || accion.needs === "motivo") {
    state.pendingAction = { camaId: cama.id, accionId: accion.id, rol: accion.rol, needs: accion.needs };
    return;
  }
  if (accion.needs === "internacion-actual") {
    if (!cama.internacion_actual_id) return toast("La reserva no tiene internación", "err");
    ejecutar(cama.id, accion.id, { internacion_id: cama.internacion_actual_id });
    return;
  }
  ejecutar(cama.id, accion.id, {});
}

async function ejecutar(camaId, accionId, payload) {
  const body = { rol: state.rol, ...payload };
  if (state.actorNombre.trim()) body.actor_nombre = state.actorNombre.trim();
  try {
    await api(`/camas/${camaId}/${accionId}`, { method: "POST", body: JSON.stringify(body) });
    toast("Acción aplicada.", "ok");
    state.pendingAction = null;
    await cargarTablero();
    await abrirDetalle(camaId);
  } catch (e) { toast(e.message, "err"); }
}

async function loadChecklist(internacionId) {
  try { state.checklistByInternacion = { ...state.checklistByInternacion, [internacionId]: await api(`/camas/internaciones/${internacionId}/pasos`) }; } catch (e) {}
}

async function instantiateChecklist(internacionId) {
  try { await api(`/camas/internaciones/${internacionId}/pasos/instanciar`, { method: "POST" }); await loadChecklist(internacionId); } catch (e) {}
}

async function ensureChecklist(internacionId) {
  if (!state.checklistByInternacion[internacionId]) { await loadChecklist(internacionId); if (!state.checklistByInternacion[internacionId]?.length) await instantiateChecklist(internacionId); }
}

async function completarPaso(pasoId, internacionId, camaId) {
  try { await api(`/camas/pasos/${pasoId}/completar`, { method: "POST", body: JSON.stringify({ rol: state.rol, actor_nombre: state.actorNombre.trim() || undefined }) }); await loadChecklist(internacionId); await abrirDetalle(camaId); } catch (e) { toast(e.message, "err"); }
}

function internacionesLibres() {
  const ocupadas = new Set(state.camas.map((c) => c.internacion_actual_id).filter(Boolean));
  return Object.values(state.internacionesById).filter((i) => !i.finalizada_at && !ocupadas.has(i.id));
}

function renderResumen() {
  const host = document.getElementById("resumen");
  host.innerHTML = "";
  if (state.camas.length === 0) return;

  const conteo = {};
  for (const e of ESTADOS_ORDEN) conteo[e] = 0;
  for (const c of state.camas) conteo[c.estado_gestion] = (conteo[c.estado_gestion] || 0) + 1;

  const cont = el("div", { style: "display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px" });
  for (const e of ESTADOS_ORDEN) {
    const m = { DISPONIBLE: "ok", OCUPADA: "info", RESERVADA: "info", PROCESO_DE_ALTA: "alta", LIMPIEZA_TERMINAL: "warn", BLOQUEADA: "err" }[e];
    cont.append(el("span", { class: `pill p-${m}` }, `${conteo[e]} ${ESTADO_PLURAL[e]}`));
  }
  host.append(cont);
}

function renderBoard() {
  const host = document.getElementById("board");
  host.innerHTML = "";
  if (state.camas.length === 0) return;

  const sectores = new Map();
  for (const c of state.camas) {
    if (!sectores.has(c.sector)) sectores.set(c.sector, []);
    sectores.get(c.sector).push(c);
  }

  for (const [sector, camas] of sectores) {
    const grid = el("div", { class: "cama-grid" });
    for (const c of camas) {
      const intern = internOf(c);
      const estado = { DISPONIBLE: "libre", OCUPADA: "ocup", RESERVADA: "demo", PROCESO_DE_ALTA: "alta", LIMPIEZA_TERMINAL: "limp", BLOQUEADA: "bloq" }[c.estado_gestion] || "libre";
      
      const card = el("div", { class: `cama ${estado}`, onClick: () => abrirDetalle(c.id) },
        el("div", { class: "cama-top" }, el("div", { class: "cama-num" }, c.nombre), el("div", { class: "cama-state-dot", style: `background: var(--cama-${estado === 'demo' ? 'demo' : estado})` })),
        el("div", { class: `cama-status bst-${estado}`, style: "margin-bottom:6px;" }, ESTADO_LABEL[c.estado_gestion]),
        c.estado_gestion === "BLOQUEADA" ? el("div", { class: "cama-espera" }, c.motivo_bloqueo || "Bloqueada") :
        intern ? el("div", {}, el("div", { class: "cama-pac" }, nombrePaciente(intern)), el("div", { class: "cama-proc" }, cobLinea(intern))) :
        el("div", { class: c.estado_gestion === "DISPONIBLE" ? "cama-libre-lbl" : "cama-proc" }, c.estado_gestion === "DISPONIBLE" ? "Disponible" : "Esperando limpieza")
      );
      grid.append(card);
    }
    host.append(el("div", { style: "margin-bottom: 24px;" }, el("h3", { style: "font-size: 14px; margin-bottom: 12px; color: var(--txt); border-bottom: 1px solid var(--brd); padding-bottom:6px;" }, `${sector} (${camas.length})`), grid));
  }
}

function renderDetalle() {
  const host = document.getElementById("drawer-host");
  host.innerHTML = "";
  const d = state.detalle;
  if (!d) return;
  const intern = internOf(d);

  // ... (tu código del drawer y overlay sigue igual hasta el drawer-body)

  const drawerBody = el("div", { class: "drawer-body" });
  
  // Agregamos la info del paciente
  if (intern) {
    drawerBody.append(el("div", { class: "card" }, /* ... tu código existente ... */));
  }
  
  drawerBody.append(renderAccionesArea(d));
  if (intern) drawerBody.append(renderChecklist(intern, d));

  // --- AQUÍ LA CORRECCIÓN: Creamos los elementos dinámicamente ---
  const hitosCont = el("div", { class: "card" });
  hitosCont.append(el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Traza de hitos")));
  const hitosList = el("div", { class: "card-body" });
  hitosCont.append(hitosList);
  renderHitos(d.hitos || [], hitosList);
  drawerBody.append(hitosCont);

  const notasCont = el("div", { class: "card" });
  notasCont.append(el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Notas de la cama")));
  const notasList = el("div", { class: "card-body" });
  notasCont.append(notasList);
  renderNotas(d.notas || [], notasList);
  drawerBody.append(notasCont);
  // -----------------------------------------------------------

  const drawer = el("aside", { class: "drawer open" }, /* ... tu header del drawer ... */, drawerBody);
  host.append(overlay, drawer);
}

function renderAccionesArea(d) {
  const wrap = el("div", { class: "card" });
  wrap.append(el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Acciones")));
  
  const cont = el("div", { class: "card-body", style: "display:flex; flex-direction:column; gap:8px;" });
  if (state.pendingAction && state.pendingAction.camaId === d.id) {
    if (state.pendingAction.needs === "motivo") {
      const input = el("input", { class: "field-sel", id: "f-motivo", placeholder: "Ej. Mantenimiento..." });
      cont.append(el("div", { class: "field-lbl" }, "Motivo del bloqueo"), input, el("div", { class: "action-foot" }, el("button", { class: "btn-primary", onClick: () => { if(input.value) ejecutar(d.id, "bloquear", { motivo_bloqueo: input.value }); } }, "Confirmar"), el("button", { class: "btn-ghost", onClick: () => state.pendingAction = null }, "Cancelar")));
    } else {
      const libres = internacionesLibres();
      const select = el("select", { class: "field-sel" }, el("option", { value: "" }, "— elegir internación existente —"), ...libres.map(i => el("option", { value: i.id }, `${nombrePaciente(i)} - ${i.paciente_dni}`)));
      cont.append(el("div", { class: "field-lbl" }, "Seleccionar internación"), select, el("div", { class: "action-foot" }, el("button", { class: "btn-primary", onClick: () => { if(select.value) ejecutar(d.id, state.pendingAction.accionId, { internacion_id: select.value }); } }, "Confirmar"), el("button", { class: "btn-ghost", onClick: () => state.pendingAction = null }, "Cancelar")));
    }
  } else {
    const acciones = ACCIONES[d.estado_gestion] || [];
    if (!acciones.length) cont.append(el("p", { class: "ri-meta" }, "Sin acciones disponibles."));
    for (const a of acciones) cont.append(el("button", { class: a.kind === "warn" ? "btn-warn" : "btn-primary", onClick: () => onAccion(d, a) }, a.label));
  }
  wrap.append(cont);
  return wrap;
}

function renderChecklist(intern, cama) {
  const pasos = state.checklistByInternacion[intern.id] || [];
  const cont = el("div", { class: "check-group" });
  cont.append(el("div", { class: "cg-head cg-medico" }, el("div", {}, "Checklist de Alta"), el("div", {}, `${pasos.filter(p => p.completado).length}/${pasos.length}`)));
  
  const body = el("div", { class: "cg-body" });
  if (!pasos.length) body.append(el("button", { class: "btn-sm", onClick: () => instantiateChecklist(intern.id) }, "Instanciar checklist"));
  for (const p of pasos) {
    body.append(el("div", { class: "check-row" },
      el("div", { class: `check-box ${p.completado ? 'cb-done' : 'cb-pend'}` }, p.completado ? "✓" : ""),
      el("div", { class: "check-label" }, p.nombre),
      !p.completado ? el("button", { class: "btn-sm", onClick: () => completarPaso(p.id, intern.id, cama.id) }, "Completar") : null
    ));
  }
  cont.append(body);
  return cont;
}

function renderToast() {
  const host = document.getElementById("toast-host");
  host.innerHTML = "";
  if (!state.toast) return;
  const t = state.toast;
  const cls = t.kind === "err" ? "alert-box" : t.kind === "warn" ? "wait-box" : "info-box";
  host.append(el("div", { class: cls, style: "position:fixed; bottom:20px; right:20px; z-index:9999;" }, t.text));
}

document.addEventListener("DOMContentLoaded", () => {
  const sel = document.getElementById("rol-select");
  for (const r of ROLES) sel.append(el("option", { value: r }, r));
  sel.value = state.rol;
  sel.addEventListener("change", () => state.rol = sel.value);
  document.getElementById("btn-refresh").addEventListener("click", cargarTablero);
  cargarTablero();
});
// --- FUNCIONES PARA RECUPERAR LA DATA PERDIDA ---

function renderHitos(hitos, host) {
  host.innerHTML = "";
  if (!hitos || hitos.length === 0) {
    host.innerHTML = '<p class="muted">Sin hitos registrados.</p>';
    return;
  }
  const container = el("div", { class: "tl" });
  hitos.forEach(h => {
    container.append(el("div", { class: "tl-item" },
      el("div", { class: "tl-lw" }, el("div", { class: "tl-dot", style: "background:var(--p)" }), el("div", { class: "tl-vert" })),
      el("div", { class: "tl-body" },
        el("div", { class: "tl-time" }, fmtFecha(h.registrado_at)),
        el("div", { class: "tl-event" }, h.hito_codigo),
        el("div", { class: "tl-who" }, `${h.actor_rol || ''} · ${h.actor_nombre || ''}`)
      )
    ));
  });
  host.append(container);
}

function renderNotas(notas, host) {
  host.innerHTML = "";
  if (!notas || notas.length === 0) {
    host.innerHTML = '<p class="muted">Sin notas.</p>';
    return;
  }
  notas.forEach(n => {
    host.append(el("div", { class: "nota" },
      el("div", { class: "nota-txt" }, n.texto),
      el("div", { class: "nota-meta" }, `${fmtFecha(n.creada_at)} · ${n.creada_por_rol || ''}`)
    ));
  });
}
