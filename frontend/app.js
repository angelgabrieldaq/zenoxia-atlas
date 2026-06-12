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

const MEDIOS_EGRESO = [
  { value: "camina",           label: "Camina" },
  { value: "ambulancia",       label: "Ambulancia" },
  { value: "derivacion",       label: "Derivación" },
  { value: "traslado_interno", label: "Traslado interno" },
  { value: "defuncion",        label: "Defunción" },
];

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
  PROCESO_DE_ALTA:   [],
  LIMPIEZA_TERMINAL: [],
  BLOQUEADA: [
    { id: "desbloquear", label: "Desbloquear", rol: "MANTENIMIENTO" },
  ],
};

const initialState = {
  camas: [],
  internacionesById: {},
  rol: "ADMISION",
  actorNombre: "",
  detalle: null,
  pendingAction: null,
  toast: null,
  loading: false,
  egreso: null,
};

const RENDERERS = {
  camas:            () => { renderResumen(); renderBoard(); },
  internacionesById:() => { renderResumen(); renderBoard(); renderDetalle(); },
  rol:              () => { renderDetalle(); },
  actorNombre:      () => {},
  detalle:          () => { renderDetalle(); },
  pendingAction:    () => { renderDetalle(); },
  toast:            () => { renderToast(); },
  loading:          () => { renderResumen(); },
  egreso:           () => { renderDetalle(); },
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
  const ms = kind === "err" ? 6000 : 4000;
  toastTimer = setTimeout(() => { if (state.toast && state.toast.id === id) state.toast = null; }, ms);
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
    state.egreso = null;
    const intern = internOf(detalle);
    if (intern && ["PROCESO_DE_ALTA", "LIMPIEZA_TERMINAL"].includes(detalle.estado_gestion)) {
      try { state.egreso = await api(`/internaciones/${intern.id}/egreso-activo`); }
      catch (e) { if (e.status !== 404) toast(e.message, "err"); }
    }
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

async function crearEgreso(internacionId, camaId, medioEgreso) {
  const body = { medio_egreso: medioEgreso, rol: state.rol };
  if (state.actorNombre.trim()) body.actor_nombre = state.actorNombre.trim();
  try {
    await api(`/internaciones/${internacionId}/egreso`, { method: "POST", body: JSON.stringify(body) });
    toast("Egreso creado.", "ok");
    state.egreso = await api(`/internaciones/${internacionId}/egreso-activo`);
  } catch (e) { toast(e.message, "err"); }
}

async function recargarEgreso(internacionId) {
  try { state.egreso = await api(`/internaciones/${internacionId}/egreso-activo`); }
  catch (e) { if (e.status !== 404) toast(e.message, "err"); else state.egreso = null; }
}

const _DISCREP_MOTIVOS = [
  "ambulancia_demorada",
  "familiar_ausente",
  "documentacion_incompleta",
  "cama_destino_no_disponible",
  "paciente_se_niega",
  "demora_responsable",
  "otro",
];

function _pedirDiscrepancia() {
  const lista = _DISCREP_MOTIVOS.join(", ");
  const motivo = prompt(`Override ADMISION — motivo obligatorio:\n${lista}`);
  if (motivo === null) return null;           // canceló
  const m = motivo.trim();
  if (!_DISCREP_MOTIVOS.includes(m)) {
    toast(`Motivo inválido. Usá uno de: ${lista}`, "err");
    return null;
  }
  const nota = prompt("Nota adicional (opcional — Enter para omitir):") ?? "";
  return { motivo: m, nota: nota.trim() || null };
}

async function marcarItemEgreso(egresoId, itemId, internacionId, responsableItem) {
  const body = { rol: state.rol };
  if (state.actorNombre.trim()) body.actor_nombre = state.actorNombre.trim();
  if (state.rol === "ADMISION" && responsableItem !== "admision") {
    const disc = _pedirDiscrepancia();
    if (disc === null) return;               // usuario canceló
    body.discrepancia = disc;
  }
  try {
    await api(`/egresos/${egresoId}/checklist/${itemId}`, { method: "PATCH", body: JSON.stringify(body) });
    await recargarEgreso(internacionId);
  } catch (e) { toast(e.message, "err"); }
}

async function darOkAdmin(egresoId, internacionId) {
  const body = { rol: state.rol };
  if (state.actorNombre.trim()) body.actor_nombre = state.actorNombre.trim();
  try {
    await api(`/egresos/${egresoId}/egreso-admin`, { method: "PATCH", body: JSON.stringify(body) });
    toast("OK administrativo registrado.", "ok");
    await recargarEgreso(internacionId);
  } catch (e) { toast(e.message, "err"); }
}

async function confirmarSalidaFisica(egresoId, camaId) {
  const body = { rol: state.rol };
  if (state.actorNombre.trim()) body.actor_nombre = state.actorNombre.trim();
  try {
    await api(`/egresos/${egresoId}/salida-fisica`, { method: "PATCH", body: JSON.stringify(body) });
    toast("Salida física confirmada.", "ok");
    await cargarTablero();
    await abrirDetalle(camaId);
  } catch (e) { toast(e.message, "err"); }
}

async function marcarItemLimpieza(egresoId, itemId, camaId) {
  const body = { rol: state.rol };
  if (state.actorNombre.trim()) body.actor_nombre = state.actorNombre.trim();
  if (state.rol === "ADMISION") {
    const disc = _pedirDiscrepancia();
    if (disc === null) return;               // usuario canceló
    body.discrepancia = disc;
  }
  try {
    const r = await api(`/egresos/${egresoId}/limpieza/${itemId}`, { method: "PATCH", body: JSON.stringify(body) });
    if (r.liberacion_bloqueada === "mantenimiento_pendiente") toast("Limpieza OK. Pendiente: mantenimiento.", "warn");
    await cargarTablero();
    await abrirDetalle(camaId);
  } catch (e) { toast(e.message, "err"); }
}

function internacionesLibres() {
  const ocupadas = new Set(state.camas.map((c) => c.internacion_actual_id).filter(Boolean));
  return Object.values(state.internacionesById).filter((i) => !i.finalizada_at && !ocupadas.has(i.id));
}

function renderEgresoPanel(egreso, intern, cama) {
  const wrap = el("div", { class: "card" });
  const estado = egreso.estado;

  // Header con estado y responsable actual
  const badgeColor = { info: "var(--p)", bloqueado: "var(--warn)", egreso_admin: "var(--ok)", liberado: "var(--ok)" }[estado] || "var(--p)";
  const estadoLabel = { info: "En proceso", bloqueado: "Bloqueado", egreso_admin: "Admin OK", liberado: "Liberado" }[estado] || estado;
  const head = el("div", { class: "card-head" },
    el("div", { class: "card-title" }, "Egreso"),
    el("span", { style: `background:${badgeColor}20; color:${badgeColor}; padding:2px 8px; border-radius:4px; font-size:12px;` }, estadoLabel),
  );
  wrap.append(head);

  const body = el("div", { class: "card-body", style: "display:flex; flex-direction:column; gap:12px;" });

  // Responsable actual
  if (egreso.responsable_actual) {
    const { rol, tarea } = egreso.responsable_actual;
    const isDelayed = egreso.minutos_trabado > 120;
    body.append(el("div", { class: isDelayed ? "alert-box" : "wait-box", style: "margin:0;" },
      `Esperando: ${rol.toUpperCase()} — ${tarea}`,
      isDelayed ? el("span", { class: "num" }, ` ⚠ Hace ${Math.round(egreso.minutos_trabado)} min`) : "",
    ));
  }

  // Medio de egreso
  body.append(el("div", { style: "font-size:13px; color:var(--txt-2);" },
    el("strong", {}, "Medio: "), egreso.medio_egreso,
  ));

  // Checklist de egreso (estados pre-salida-fisica)
  if (["info", "bloqueado", "egreso_admin"].includes(estado) && egreso.items_checklist?.length) {
    const grp = el("div", { class: "check-group" });
    const done = egreso.items_checklist.filter(i => i.done).length;
    grp.append(el("div", { class: "cg-head cg-medico" },
      el("div", {}, "Checklist de egreso"),
      el("div", { class: "num" }, `${done}/${egreso.items_checklist.length}`),
    ));
    const gbody = el("div", { class: "cg-body" });
    for (const item of egreso.items_checklist) {
      gbody.append(el("div", { class: "check-row" },
        el("div", { class: `check-box ${item.done ? "cb-done" : "cb-pend"}` }, item.done ? "✓" : ""),
        el("div", { class: "check-label" },
          item.label,
          item.requerido_legal ? el("span", { style: "color:var(--err); font-size:11px; margin-left:4px;" }, "legal") : null,
        ),
        !item.done
          ? (state.rol.toLowerCase() === item.responsable || state.rol === "ADMISION"
              ? el("button", { class: "btn-sm tap", onClick: () => marcarItemEgreso(egreso.id, item.id, intern.id, item.responsable) }, "Marcar")
              : el("span", { class: "ri-meta" }, `Pendiente: ${item.responsable}`))
          : el("span", { class: "ri-meta" }, item.autor || ""),
      ));
    }
    grp.append(gbody);
    body.append(grp);

    // Botones de acción según estado
    const todosDone = egreso.items_checklist.every(i => i.done);
    if (estado === "info" || estado === "bloqueado") {
      if (todosDone) {
        body.append(el("button", { class: "btn-primary", onClick: () => darOkAdmin(egreso.id, intern.id) }, "OK Administrativo"));
      }
    } else if (estado === "egreso_admin" && !egreso.salida_fisica_at) {
      body.append(el("button", { class: "btn-primary", onClick: () => confirmarSalidaFisica(egreso.id, cama.id) }, "Confirmar Salida Física"));
    }
  } else if (estado === "egreso_admin" && !egreso.salida_fisica_at) {
    body.append(el("button", { class: "btn-primary", onClick: () => confirmarSalidaFisica(egreso.id, cama.id) }, "Confirmar Salida Física"));
  }

  // Checklist de limpieza (cama en LIMPIEZA_TERMINAL)
  if (cama.estado_gestion === "LIMPIEZA_TERMINAL" && egreso.limpieza_checklist?.length) {
    const grp = el("div", { class: "check-group" });
    const done = egreso.limpieza_checklist.filter(i => i.done).length;
    grp.append(el("div", { class: "cg-head cg-medico" },
      el("div", {}, "Limpieza terminal"),
      el("div", { class: "num" }, `${done}/${egreso.limpieza_checklist.length}`),
    ));
    const gbody = el("div", { class: "cg-body" });
    for (const item of egreso.limpieza_checklist) {
      gbody.append(el("div", { class: "check-row" },
        el("div", { class: `check-box ${item.done ? "cb-done" : "cb-pend"}` }, item.done ? "✓" : ""),
        el("div", { class: "check-label" }, item.label),
        !item.done
          ? (["LIMPIEZA", "HOTELERIA", "ADMISION"].includes(state.rol)
              ? el("button", { class: "btn-sm tap", onClick: () => marcarItemLimpieza(egreso.id, item.id, cama.id) }, "Marcar")
              : el("span", { class: "ri-meta" }, "Pendiente: limpieza"))
          : el("span", { class: "ri-meta" }, item.autor || ""),
      ));
    }
    grp.append(gbody);
    body.append(grp);
  }

  // Discrepancias
  if (egreso.discrepancias?.length) {
    const grp = el("div", {});
    grp.append(el("div", { class: "field-lbl", style: "margin-bottom:4px;" }, "Discrepancias"));
    for (const d of egreso.discrepancias) {
      grp.append(el("div", { class: "nota" },
        el("div", { class: "nota-txt" }, `${d.motivo}: ${d.nota || ""}`),
        el("div", { class: "nota-meta" }, `${fmtFecha(d.hora)} · ${d.autor}`),
      ));
    }
    body.append(grp);
  }

  // Notas de egreso
  if (egreso.notas?.length) {
    const grp = el("div", {});
    grp.append(el("div", { class: "field-lbl", style: "margin-bottom:4px;" }, "Notas de egreso"));
    for (const n of egreso.notas) {
      grp.append(el("div", { class: "nota" },
        el("div", { class: "nota-txt" }, `[${n.tipo}] ${n.texto}`),
        el("div", { class: "nota-meta" }, `${fmtFecha(n.hora)} · ${n.autor}`),
      ));
    }
    body.append(grp);
  }

  wrap.append(body);
  return wrap;
}

function renderCrearEgresoPanel(intern, cama) {
  const wrap = el("div", { class: "card" });
  wrap.append(el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Iniciar egreso")));
  const body = el("div", { class: "card-body", style: "display:flex; flex-direction:column; gap:8px;" });
  const select = el("select", { class: "field-sel" },
    ...MEDIOS_EGRESO.map(m => el("option", { value: m.value }, m.label)),
  );
  body.append(
    el("div", { class: "field-lbl" }, "Medio de egreso"),
    select,
    el("div", { class: "action-foot" },
      el("button", { class: "btn-primary", onClick: () => crearEgreso(intern.id, cama.id, select.value) }, "Crear egreso"),
    ),
  );
  wrap.append(body);
  return wrap;
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

  // --- AQUÍ ESTABA EL ERROR: Declaramos el overlay de nuevo ---
  const overlay = el("div", { class: "overlay open", onClick: cerrarDetalle });

  const drawerBody = el("div", { class: "drawer-body" });
  
  // Agregamos la info del paciente
  if (intern) {
    drawerBody.append(el("div", { class: "card" }, 
      el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Paciente y cobertura")), 
      el("div", { class: "card-body", style: "display:flex; flex-direction:column; gap:8px;" }, 
        el("div", {}, el("strong", {}, "Paciente: "), nombrePaciente(intern)), 
        el("div", {}, el("strong", {}, "DNI: "), intern.paciente_dni || "—"), 
        el("div", {}, el("strong", {}, "Cobertura: "), intern.cobertura || "—")
      )
    ));
  }
  
  drawerBody.append(renderAccionesArea(d));

  if (intern && ["PROCESO_DE_ALTA", "LIMPIEZA_TERMINAL"].includes(d.estado_gestion)) {
    if (state.egreso) drawerBody.append(renderEgresoPanel(state.egreso, intern, d));
    else if (d.estado_gestion === "PROCESO_DE_ALTA") drawerBody.append(renderCrearEgresoPanel(intern, d));
  }

  // Pintamos hitos
  const hitosCont = el("div", { class: "card" });
  hitosCont.append(el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Traza de hitos")));
  const hitosList = el("div", { class: "card-body" });
  hitosCont.append(hitosList);
  renderHitos(d.hitos || [], hitosList);
  drawerBody.append(hitosCont);

  // Pintamos notas
  const notasCont = el("div", { class: "card" });
  notasCont.append(el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Notas de la cama")));
  const notasList = el("div", { class: "card-body" });
  notasCont.append(notasList);
  renderNotas(d.notas || [], notasList);
  drawerBody.append(notasCont);

  // Creamos el drawer usando el drawerBody que construimos
  const drawer = el("aside", { class: "drawer open" }, 
    el("div", { class: "drawer-head" },
      el("div", { class: "dh-av", style: "background:var(--p-light); color:var(--p-dark);" }, "🛏️"),
      el("div", { class: "dh-info" }, el("div", { class: "dh-name" }, d.nombre), el("div", { class: "dh-sub" }, `${ESTADO_LABEL[d.estado_gestion]} · ${TIPO_LABEL[d.tipo] || d.tipo}`)),
      el("button", { class: "drawer-close", onClick: cerrarDetalle }, "×")
    ),
    drawerBody
  );
  
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

function renderToast() {
  const host = document.getElementById("toast-host");
  host.innerHTML = "";
  if (!state.toast) return;
  const t = state.toast;
  host.append(el("div", { class: `toast toast--${t.kind}` },
    el("span", { class: "toast-text" }, t.text),
    el("button", { class: "toast-close", onClick: () => { state.toast = null; } }, "×"),
  ));
}

document.addEventListener("DOMContentLoaded", () => {
  const sel = document.getElementById("rol-select");
  for (const r of ROLES) sel.append(el("option", { value: r }, r));
  sel.value = state.rol;
  sel.addEventListener("change", () => state.rol = sel.value);
  document.getElementById("btn-refresh").addEventListener("click", cargarTablero);
  cargarTablero();

  setInterval(async () => {
    if (document.hidden) return;
    await cargarTablero();
    if (state.detalle && !state.pendingAction) {
      const intern = internOf(state.detalle);
      if (intern && state.egreso) await recargarEgreso(intern.id);
    }
  }, 15000);
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
