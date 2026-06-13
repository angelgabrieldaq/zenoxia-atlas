"use strict";

const API = "http://localhost:8000";

const ROLES = [
  "ADMISION", "ENFERMERIA", "MEDICO", "HOTELERIA",
  "LIMPIEZA", "MANTENIMIENTO",
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

// Estado de secciones colapsables del drawer — preservado entre re-renders del polling.
// Se limpia al abrir una cama distinta (ver abrirDetalle).
const _drawerSectionsOpen = {};

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
  if (state.detalle?.id !== camaId) {
    for (const k of Object.keys(_drawerSectionsOpen)) delete _drawerSectionsOpen[k];
  }
  try {
    const detalle = await api("/camas/" + camaId);
    state.pendingAction = null;
    state.egreso = null;
    state.detalle = detalle;
    const intern = internOf(detalle);
    if (intern && ["PROCESO_DE_ALTA", "LIMPIEZA_TERMINAL"].includes(detalle.estado_gestion)) {
      try { state.egreso = await api(`/internaciones/${intern.id}/egreso-activo`); }
      catch (e) { if (e.status !== 404) toast(e.message, "err"); }
    }
  } catch (e) { toast(e.message, "err"); }
}

function cerrarDetalle() { state.pendingAction = null; state.detalle = null; state.egreso = null; }

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

const _LABEL_ORDEN_TRASLADO = "Orden de traslado emitida por el médico";
const _MEDIOS_CON_ORDEN = new Set(["ambulancia", "derivacion"]);

// Muestra el formulario de datos logísticos de traslado como modal.
// Retorna Promise<object|null> — null si el usuario canceló.
function _pedirDatosTraslado(datosPrevios = {}) {
  return new Promise((resolve) => {
    const overlay = el("div", { class: "overlay open", style: "z-index:300;" });
    const modal = el("div", {
      class: "card",
      style: "position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:301;width:min(480px,92vw);max-height:85vh;overflow-y:auto;",
    });

    modal.append(el("div", { class: "card-head" },
      el("div", { class: "card-title" }, "Datos de traslado — Orden médica"),
    ));
    const body = el("div", { class: "card-body", style: "display:flex;flex-direction:column;gap:12px;" });

    const _sel = (id, opts, val) => {
      const s = el("select", { class: "field-sel", id, style: "height:44px;" },
        ...opts.map(([v, lbl]) => el("option", { value: v }, lbl)),
      );
      if (val) s.value = val;
      return s;
    };
    const _chk = (id, lbl, val) => {
      const inp = document.createElement("input");
      inp.type = "checkbox"; inp.id = id; inp.checked = !!val;
      inp.style.cssText = "width:20px;height:20px;cursor:pointer;";
      return el("label", { style: "display:flex;align-items:center;gap:8px;cursor:pointer;height:44px;" }, inp, lbl);
    };
    const _txt = (id, val) => {
      const t = document.createElement("input");
      t.type = "text"; t.id = id; t.className = "field-sel"; t.value = val || "";
      t.style.height = "44px";
      return t;
    };
    const _lbl = (txt) => el("div", { class: "field-lbl" }, txt);

    const selDestino = _sel("f-destino", [
      ["domicilio","Domicilio"],["sanatorio","Sanatorio"],
      ["tercer_nivel","Tercer nivel"],["geriatrico","Geriátrico"],
      ["psiquiatrico","Psiquiátrico"],["otro","Otro"],
    ], datosPrevios.destino_tipo);
    const txtDireccion = _txt("f-direccion", datosPrevios.destino_direccion);
    const txtPrestador = _txt("f-prestador", datosPrevios.prestador);
    const chkMedico    = _chk("f-medico", "Médico a bordo", datosPrevios.medico_a_bordo);
    const chkAcomp     = _chk("f-acomp", "Acompañante", datosPrevios.acompanante);
    const chkOxigeno   = _chk("f-oxig", "Oxígeno", datosPrevios.oxigeno);
    const selAccesib   = _sel("f-accesib", [
      ["planta_baja","Planta baja"],["escaleras","Escaleras"],
      ["ascensor","Ascensor"],["no_aplica","No aplica"],
    ], datosPrevios.accesibilidad_destino);
    const selIntDom    = _sel("f-intdom", [
      ["desconocido","Desconocido (confirmar antes del cierre)"],
      ["si","Sí — requiere internación domiciliaria"],
      ["no","No"],
    ], datosPrevios.internacion_domiciliaria || "desconocido");

    body.append(
      _lbl("Destino"), selDestino,
      _lbl("Dirección destino *"), txtDireccion,
      _lbl("Prestador / Empresa"), txtPrestador,
      el("div", { style: "display:flex;flex-direction:column;gap:4px;" },
        _lbl("Requerimientos"), chkMedico, chkAcomp, chkOxigeno,
      ),
      _lbl("Accesibilidad destino"), selAccesib,
      _lbl("Internación domiciliaria"), selIntDom,
    );

    const foot = el("div", { class: "action-foot" },
      el("button", { class: "btn-primary", style: "height:44px;", onClick: () => {
        const dir = txtDireccion.value.trim();
        const pre = txtPrestador.value.trim();
        if (!dir || !pre) { toast("Dirección y prestador son obligatorios.", "err"); return; }
        cleanup();
        resolve({
          destino_tipo: selDestino.value,
          destino_direccion: dir,
          prestador: pre,
          medico_a_bordo: chkMedico.querySelector("input").checked,
          acompanante: chkAcomp.querySelector("input").checked,
          oxigeno: chkOxigeno.querySelector("input").checked,
          accesibilidad_destino: selAccesib.value,
          internacion_domiciliaria: selIntDom.value,
        });
      }}, "Confirmar orden"),
      el("button", { class: "btn-ghost", style: "height:44px;", onClick: () => { cleanup(); resolve(null); } }, "Cancelar"),
    );
    body.append(foot);
    modal.append(body);

    const cleanup = () => { overlay.remove(); modal.remove(); };
    overlay.addEventListener("click", () => { cleanup(); resolve(null); });
    document.body.append(overlay, modal);
  });
}

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

async function marcarItemEgreso(egresoId, itemId, internacionId, responsableItem, itemLabel, egreso) {
  const body = { rol: state.rol };
  if (state.actorNombre.trim()) body.actor_nombre = state.actorNombre.trim();
  if (state.rol === "ADMISION" && responsableItem !== "admision") {
    const disc = _pedirDiscrepancia();
    if (disc === null) return;
    body.discrepancia = disc;
  }
  // Ítem de orden de traslado: requiere datos logísticos (medio ambulancia/derivacion)
  if (itemLabel === _LABEL_ORDEN_TRASLADO && _MEDIOS_CON_ORDEN.has(egreso?.medio_egreso)) {
    const datos = await _pedirDatosTraslado(egreso?.datos_traslado || {});
    if (datos === null) return;              // usuario canceló
    body.datos_traslado = datos;
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
  const estado = egreso.estado;
  const resp = egreso.responsable_actual;
  const esMio = !!(resp && resp.rol && resp.rol.toUpperCase() === state.rol);
  const defaultOpen = esMio;
  const abierto = "egreso" in _drawerSectionsOpen ? _drawerSectionsOpen.egreso : defaultOpen;

  const badgeColor = { info: "var(--p)", bloqueado: "var(--s-warn)", egreso_admin: "var(--s-ok)", liberado: "var(--s-ok)" }[estado] || "var(--p)";
  const estadoLabel = { info: "En proceso", bloqueado: "Bloqueado", egreso_admin: "Admin OK", liberado: "Liberado" }[estado] || estado;
  const miTurnoBadgeKind = egreso.minutos_trabado > 120 ? "err" : "warn";

  const details = el("details", { class: "card", open: abierto });
  details.addEventListener("toggle", () => { _drawerSectionsOpen.egreso = details.open; });
  details.append(el("summary", { class: "card-head" },
    el("div", { class: "card-title" }, "Egreso"),
    el("div", { class: "ds-right" },
      el("span", { style: `background:${badgeColor}20; color:${badgeColor}; padding:2px 8px; border-radius:var(--r-xs); font-size:var(--text-2xs);` }, estadoLabel),
      esMio ? el("span", { class: `ds-badge ds-badge--${miTurnoBadgeKind}` }, "Mi turno") : null,
      el("span", { class: "ds-chevron", "aria-hidden": "true" }),
    ),
  ));

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

  // Banner orden de traslado pendiente (ambulancia/derivacion)
  if (_MEDIOS_CON_ORDEN.has(egreso.medio_egreso)) {
    const ordenPendiente = egreso.items_checklist?.some(
      i => i.label === _LABEL_ORDEN_TRASLADO && !i.done
    );
    if (ordenPendiente) {
      body.append(el("div", { class: "banner-warn" },
        "Este egreso requiere orden de traslado del médico: datos del paciente, destino y dirección, " +
        "requerimientos (oxígeno, médico a bordo, acompañante) e internación domiciliaria si va a domicilio."
      ));
    }
  }

  // Checklist de egreso (estados pre-salida-fisica)
  if (["info", "bloqueado", "egreso_admin"].includes(estado) && egreso.items_checklist?.length) {
    const rolNorm = state.rol.toLowerCase();
    const isAdmision = state.rol === "ADMISION";

    const renderCheckItem = (item) => el("div", { class: "check-row" },
      el("div", { class: `check-box ${item.done ? "cb-done" : "cb-pend"}` }, item.done ? "✓" : ""),
      el("div", { class: "check-label" },
        item.label,
        item.requerido_legal ? el("span", { style: "color:var(--err); font-size:11px; margin-left:4px;" }, "legal") : null,
      ),
      !item.done
        ? (rolNorm === item.responsable || isAdmision
            ? el("button", { class: "btn-sm tap", onClick: () => marcarItemEgreso(egreso.id, item.id, intern.id, item.responsable, item.label, egreso) }, "Marcar")
            : el("span", { class: "ri-meta" }, `Pendiente: ${item.responsable}`))
        : el("span", { class: "ri-meta" }, item.autor || ""),
    );

    const grp = el("div", { class: "check-group" });
    const done = egreso.items_checklist.filter(i => i.done).length;
    grp.append(el("div", { class: "cg-head cg-medico" },
      el("div", {}, "Checklist de egreso"),
      el("div", { class: "num" }, `${done}/${egreso.items_checklist.length}`),
    ));
    const gbody = el("div", { class: "cg-body" });

    if (isAdmision) {
      for (const item of egreso.items_checklist) gbody.append(renderCheckItem(item));
    } else {
      const myItems = egreso.items_checklist.filter(i => i.responsable === rolNorm);
      const otherItems = egreso.items_checklist.filter(i => i.responsable !== rolNorm);
      for (const item of myItems) gbody.append(renderCheckItem(item));
      if (otherItems.length) {
        const otherPending = otherItems.filter(i => !i.done);
        const byRole = otherPending.reduce((acc, i) => { acc[i.responsable] = (acc[i.responsable] || 0) + 1; return acc; }, {});
        const breakdown = Object.entries(byRole).map(([r, n]) => `${r} ${n}`).join(", ");
        const summaryText = otherPending.length
          ? `▸ ${otherPending.length} pendientes de otros roles${breakdown ? ` (${breakdown})` : ""}`
          : `▸ ${otherItems.length} ítems de otros roles (todos completos)`;
        const details = el("details", { class: "check-otros" });
        details.append(el("summary", { class: "check-otros-summary" }, summaryText));
        for (const item of otherItems) details.append(renderCheckItem(item));
        gbody.append(details);
      }
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
    // EJECUCION: LIMPIEZA, HOTELERIA, ADMISION pueden marcar
    // SUPERVISION: solo HOTELERIA, ADMISION; LIMPIEZA ve texto informativo
    const renderLimpiezaItem = (item) => {
      let accion;
      if (!item.done) {
        const esSupervision = item.codigo === "SUPERVISION";
        const puedeMarcar = esSupervision
          ? ["HOTELERIA", "ADMISION"].includes(state.rol)
          : ["LIMPIEZA", "HOTELERIA", "ADMISION"].includes(state.rol);
        if (puedeMarcar) {
          accion = el("button", { class: "btn-sm tap", onClick: () => marcarItemLimpieza(egreso.id, item.id, cama.id) }, "Marcar");
        } else if (esSupervision && state.rol === "LIMPIEZA") {
          accion = el("span", { class: "ri-meta" }, "Pendiente: supervisión de hotelería");
        } else {
          accion = el("span", { class: "ri-meta" }, "Pendiente: limpieza");
        }
      } else {
        accion = el("span", { class: "ri-meta" }, item.autor || "");
      }
      return el("div", { class: "check-row" },
        el("div", { class: `check-box ${item.done ? "cb-done" : "cb-pend"}` }, item.done ? "✓" : ""),
        el("div", { class: "check-label" }, item.label),
        accion,
      );
    };

    const grp = el("div", { class: "check-group" });
    const done = egreso.limpieza_checklist.filter(i => i.done).length;
    grp.append(el("div", { class: "cg-head cg-medico" },
      el("div", {}, "Limpieza terminal"),
      el("div", { class: "num" }, `${done}/${egreso.limpieza_checklist.length}`),
    ));
    const gbody = el("div", { class: "cg-body" });

    const puedeLimpieza = ["LIMPIEZA", "HOTELERIA", "ADMISION"].includes(state.rol);
    if (puedeLimpieza) {
      for (const item of egreso.limpieza_checklist) gbody.append(renderLimpiezaItem(item));
    } else {
      const pending = egreso.limpieza_checklist.filter(i => !i.done).length;
      const summaryText = pending
        ? `▸ ${pending} pendientes de limpieza`
        : `▸ ${egreso.limpieza_checklist.length} ítems de limpieza (todos completos)`;
      const details = el("details", { class: "check-otros" });
      details.append(el("summary", { class: "check-otros-summary" }, summaryText));
      for (const item of egreso.limpieza_checklist) details.append(renderLimpiezaItem(item));
      gbody.append(details);
    }

    grp.append(gbody);
    body.append(grp);

    if (egreso.mantenimiento_requerido && egreso.limpieza_checklist.every(i => i.done)) {
      body.append(el("div", { class: "banner-warn" }, "Liberación bloqueada: mantenimiento pendiente"));
    }
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

  details.append(body);
  return details;
}

function renderCrearEgresoPanel(intern, cama) {
  const abierto = "egreso" in _drawerSectionsOpen ? _drawerSectionsOpen.egreso : state.rol === "ADMISION";
  const details = el("details", { class: "card", open: abierto });
  details.addEventListener("toggle", () => { _drawerSectionsOpen.egreso = details.open; });
  details.append(el("summary", { class: "card-head" },
    el("div", { class: "card-title" }, "Iniciar egreso"),
    el("div", { class: "ds-right" }, el("span", { class: "ds-chevron", "aria-hidden": "true" })),
  ));

  const body = el("div", { class: "card-body", style: "display:flex; flex-direction:column; gap:8px;" });
  const select = el("select", { class: "field-sel" },
    ...MEDIOS_EGRESO.map(m => el("option", { value: m.value }, m.label)),
  );

  const bannerOrden = el("div", { class: "banner-warn", style: "display:none;" },
    "Este egreso requiere orden de traslado del médico: datos del paciente, destino y dirección, " +
    "requerimientos (oxígeno, médico a bordo, acompañante) e internación domiciliaria si va a domicilio."
  );

  const actualizarBanner = () => {
    bannerOrden.style.display = _MEDIOS_CON_ORDEN.has(select.value) ? "" : "none";
  };
  select.addEventListener("change", actualizarBanner);
  actualizarBanner();

  body.append(
    el("div", { class: "field-lbl" }, "Medio de egreso"),
    select,
    bannerOrden,
    el("div", { class: "action-foot" },
      el("button", { class: "btn-primary", onClick: () => crearEgreso(intern.id, cama.id, select.value) }, "Crear egreso"),
    ),
  );
  details.append(body);
  return details;
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
  const _prevScroll = host.querySelector(".drawer-body")?.scrollTop ?? 0;
  host.innerHTML = "";
  const d = state.detalle;
  if (!d) return;
  const intern = internOf(d);

  // --- AQUÍ ESTABA EL ERROR: Declaramos el overlay de nuevo ---
  const overlay = el("div", { class: "overlay open", onClick: cerrarDetalle });

  const drawerBody = el("div", { class: "drawer-body" });
  
  
  drawerBody.append(renderAccionesArea(d));

  if (intern && ["PROCESO_DE_ALTA", "LIMPIEZA_TERMINAL"].includes(d.estado_gestion)) {
    if (state.egreso) drawerBody.append(renderEgresoPanel(state.egreso, intern, d));
    else if (d.estado_gestion === "PROCESO_DE_ALTA") drawerBody.append(renderCrearEgresoPanel(intern, d));
  }

  // Pintamos hitos — siempre colapsado (es consulta)
  const hitosOpen = "hitos" in _drawerSectionsOpen ? _drawerSectionsOpen.hitos : false;
  const hitosCont = el("details", { class: "card", open: hitosOpen });
  hitosCont.addEventListener("toggle", () => { _drawerSectionsOpen.hitos = hitosCont.open; });
  hitosCont.append(el("summary", { class: "card-head" },
    el("div", { class: "card-title" }, "Traza de hitos"),
    el("div", { class: "ds-right" }, el("span", { class: "ds-chevron", "aria-hidden": "true" })),
  ));
  const hitosList = el("div", { class: "card-body" });
  renderHitos(d.hitos || [], hitosList);
  hitosCont.append(hitosList);
  drawerBody.append(hitosCont);

  // Pintamos notas — abierto si hay alguna de tipo reclamo
  const notas = d.notas || [];
  const tieneReclamo = notas.some(n => n.tipo === "reclamo");
  const notasOpen = "notas" in _drawerSectionsOpen ? _drawerSectionsOpen.notas : tieneReclamo;
  const notasCont = el("details", { class: "card", open: notasOpen });
  notasCont.addEventListener("toggle", () => { _drawerSectionsOpen.notas = notasCont.open; });
  notasCont.append(el("summary", { class: "card-head" },
    el("div", { class: "card-title" }, "Notas de la cama"),
    el("div", { class: "ds-right" },
      tieneReclamo ? el("span", { class: "ds-badge ds-badge--warn" }, "Reclamo") : null,
      el("span", { class: "ds-chevron", "aria-hidden": "true" }),
    ),
  ));
  const notasList = el("div", { class: "card-body" });
  renderNotas(notas, notasList);
  notasCont.append(notasList);
  drawerBody.append(notasCont);

  // Creamos el drawer usando el drawerBody que construimos
  const drawer = el("aside", { class: "drawer open" }, 
    el("div", { class: "drawer-head" },
      el("div", { class: "dh-av", style: "background:var(--p-light); color:var(--p-dark);" }, "🛏️"),
      el("div", { class: "dh-info" },
        el("div", { class: "dh-name" }, d.nombre),
        el("div", { class: "dh-sub" }, `${ESTADO_LABEL[d.estado_gestion]} · ${TIPO_LABEL[d.tipo] || d.tipo}`),
        ...(intern ? [
          el("div", { class: "dh-paciente" }, nombrePaciente(intern)),
          el("div", { class: "dh-cob" }, `DNI ${intern.paciente_dni || "—"} · ${intern.cobertura || "—"}`)
        ] : [])
      ),
      el("button", { class: "drawer-close", onClick: cerrarDetalle }, "×")
    ),
    drawerBody
  );
  
  host.append(overlay, drawer);
  if (_prevScroll) drawerBody.scrollTop = _prevScroll;
}

function renderAccionesArea(d) {
  const pendingActive = !!(state.pendingAction && state.pendingAction.camaId === d.id);
  const acciones = ACCIONES[d.estado_gestion] || [];
  const hayParaRol = pendingActive || acciones.some(a => a.rol === state.rol);
  const abierto = "acciones" in _drawerSectionsOpen ? _drawerSectionsOpen.acciones : hayParaRol;

  const details = el("details", { class: "card", open: abierto });
  details.addEventListener("toggle", () => { _drawerSectionsOpen.acciones = details.open; });
  details.append(el("summary", { class: "card-head" },
    el("div", { class: "card-title" }, "Acciones"),
    el("div", { class: "ds-right" }, el("span", { class: "ds-chevron", "aria-hidden": "true" })),
  ));

  const cont = el("div", { class: "card-body", style: "display:flex; flex-direction:column; gap:8px;" });
  if (pendingActive) {
    if (state.pendingAction.needs === "motivo") {
      const input = el("input", { class: "field-sel", id: "f-motivo", placeholder: "Ej. Mantenimiento..." });
      cont.append(el("div", { class: "field-lbl" }, "Motivo del bloqueo"), input, el("div", { class: "action-foot" }, el("button", { class: "btn-primary", onClick: () => { if(input.value) ejecutar(d.id, "bloquear", { motivo_bloqueo: input.value }); } }, "Confirmar"), el("button", { class: "btn-ghost", onClick: () => state.pendingAction = null }, "Cancelar")));
    } else {
      const libres = internacionesLibres();
      const select = el("select", { class: "field-sel" }, el("option", { value: "" }, "— elegir internación existente —"), ...libres.map(i => el("option", { value: i.id }, `${nombrePaciente(i)} - ${i.paciente_dni}`)));
      cont.append(el("div", { class: "field-lbl" }, "Seleccionar internación"), select, el("div", { class: "action-foot" }, el("button", { class: "btn-primary", onClick: () => { if(select.value) ejecutar(d.id, state.pendingAction.accionId, { internacion_id: select.value }); } }, "Confirmar"), el("button", { class: "btn-ghost", onClick: () => state.pendingAction = null }, "Cancelar")));
    }
  } else {
    if (!acciones.length) cont.append(el("p", { class: "ri-meta" }, "Sin acciones disponibles."));
    for (const a of acciones) cont.append(el("button", { class: a.kind === "warn" ? "btn-warn" : "btn-primary", onClick: () => onAccion(d, a) }, a.label));
  }
  details.append(cont);
  return details;
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
// Helpers de renderizado del drawer — hitos y notas de la cama

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
