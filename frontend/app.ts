"use strict";

const API = "http://localhost:8000";

// ─── Domain types ────────────────────────────────────────────────────────────

type Rol =
  | "ADMISION"
  | "ENFERMERIA"
  | "MEDICO"
  | "HOTELERIA"
  | "LIMPIEZA"
  | "MANTENIMIENTO"
  | "OPERACIONES";

type Categoria =
  | "CLINICA"
  | "CRITICA"
  | "QUIRURGICA_PROGRAMADA"
  | "QUIRURGICA_URGENCIA"
  | "GUARDIA_OBSERVACION"
  | "OBSTETRICA";

type EstadoGestion =
  | "DISPONIBLE"
  | "OCUPADA"
  | "RESERVADA"
  | "PROCESO_DE_ALTA"
  | "LIMPIEZA_TERMINAL"
  | "BLOQUEADA";

type TipoCama = "CAMA_INTERNACION" | "UTI" | "UCO";

type MedioEgresoValue =
  | "camina"
  | "ambulancia"
  | "derivacion"
  | "traslado_interno"
  | "defuncion";

type EgresoEstado = "info" | "bloqueado" | "egreso_admin" | "liberado";

type AccionNeeds = "internacion" | "internacion-actual" | "motivo";

type DiscrepanciaMotivo =
  | "ambulancia_demorada"
  | "familiar_ausente"
  | "documentacion_incompleta"
  | "cama_destino_no_disponible"
  | "paciente_se_niega"
  | "demora_responsable"
  | "otro";

// ─── API models ──────────────────────────────────────────────────────────────

interface Internacion {
  id: number;
  paciente_nombre: string;
  paciente_apellido: string;
  paciente_dni: string;
  cobertura: string | null;
  plan_cobertura: string | null;
  finalizada_at: string | null;
  internacion_actual_id?: number;
}

interface Hito {
  registrado_at: string;
  hito_codigo: string;
  actor_rol: string | null;
  actor_nombre: string | null;
}

interface NotaCama {
  texto: string;
  creada_at: string;
  creada_por_rol: string | null;
}

interface Cama {
  id: number;
  nombre: string;
  sector: string;
  tipo: TipoCama;
  estado_gestion: EstadoGestion;
  internacion_actual_id: number | null;
  motivo_bloqueo?: string | null;
  hitos?: Hito[];
  notas?: NotaCama[];
}

interface ChecklistItem {
  id: number;
  label: string;
  responsable: string;
  done: boolean;
  autor?: string;
  requerido_legal?: boolean;
}

interface LimpiezaItem {
  id: number;
  label: string;
  codigo: string;
  done: boolean;
  autor?: string;
}

interface DatosTraslado {
  destino_tipo: string;
  destino_direccion: string;
  prestador: string;
  medico_a_bordo: boolean;
  acompanante: boolean;
  oxigeno: boolean;
  accesibilidad_destino: string;
  internacion_domiciliaria: string;
}

interface Discrepancia {
  motivo: string;
  nota: string | null;
  hora: string;
  autor: string;
}

interface NotaEgreso {
  tipo: string;
  texto: string;
  hora: string;
  autor: string;
}

interface ResponsableActual {
  rol: string;
  tarea: string;
}

interface Egreso {
  id: number;
  estado: EgresoEstado;
  medio_egreso: MedioEgresoValue;
  salida_fisica_at: string | null;
  minutos_trabado: number;
  responsable_actual: ResponsableActual | null;
  items_checklist?: ChecklistItem[];
  limpieza_checklist?: LimpiezaItem[];
  discrepancias?: Discrepancia[];
  notas?: NotaEgreso[];
  datos_traslado?: DatosTraslado | null;
  mantenimiento_requerido?: boolean;
}

// ─── UI types ────────────────────────────────────────────────────────────────

interface MedioEgresoOption {
  value: MedioEgresoValue;
  label: string;
}

interface Accion {
  id: string;
  label: string;
  rol: Rol;
  needs?: AccionNeeds;
  kind?: "warn";
}

interface PendingAction {
  camaId: number;
  accionId: string;
  rol: Rol;
  needs: AccionNeeds;
}

interface ToastState {
  text: string;
  kind: "info" | "ok" | "err" | "warn";
  id: number;
}

interface AppState {
  camas: Cama[];
  internacionesById: Record<number, Internacion>;
  rol: Rol;
  actorNombre: string;
  detalle: Cama | null;
  pendingAction: PendingAction | null;
  toast: ToastState | null;
  loading: boolean;
  egreso: Egreso | null;
}

// ─── Constants ───────────────────────────────────────────────────────────────

const ROLES: Rol[] = [
  "ADMISION", "ENFERMERIA", "MEDICO", "HOTELERIA",
  "LIMPIEZA", "MANTENIMIENTO", "OPERACIONES",
];

const CATEGORIAS: Categoria[] = [
  "CLINICA", "CRITICA", "QUIRURGICA_PROGRAMADA",
  "QUIRURGICA_URGENCIA", "GUARDIA_OBSERVACION", "OBSTETRICA",
];

const ESTADOS_ORDEN: EstadoGestion[] = [
  "DISPONIBLE", "OCUPADA", "RESERVADA",
  "PROCESO_DE_ALTA", "LIMPIEZA_TERMINAL", "BLOQUEADA",
];

const ESTADO_LABEL: Record<EstadoGestion, string> = {
  DISPONIBLE:        "Libre",
  OCUPADA:           "Ocupada",
  RESERVADA:         "Reservada",
  PROCESO_DE_ALTA:   "En alta",
  LIMPIEZA_TERMINAL: "Limpieza",
  BLOQUEADA:         "Bloqueada",
};

const ESTADO_PLURAL: Record<EstadoGestion, string> = {
  DISPONIBLE:        "libres",
  OCUPADA:           "ocupadas",
  RESERVADA:         "reservadas",
  PROCESO_DE_ALTA:   "en alta",
  LIMPIEZA_TERMINAL: "en limpieza",
  BLOQUEADA:         "bloqueadas",
};

const TIPO_LABEL: Record<TipoCama, string> = {
  CAMA_INTERNACION: "Internación",
  UTI:              "UTI",
  UCO:              "UCO",
};

const MEDIOS_EGRESO: MedioEgresoOption[] = [
  { value: "camina",           label: "Camina" },
  { value: "ambulancia",       label: "Ambulancia" },
  { value: "derivacion",       label: "Derivación" },
  { value: "traslado_interno", label: "Traslado interno" },
  { value: "defuncion",        label: "Defunción" },
];

const ACCIONES: Partial<Record<EstadoGestion, Accion[]>> = {
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

// ─── State ───────────────────────────────────────────────────────────────────

const initialState: AppState = {
  camas:             [],
  internacionesById: {},
  rol:               "ADMISION",
  actorNombre:       "",
  detalle:           null,
  pendingAction:     null,
  toast:             null,
  loading:           false,
  egreso:            null,
};

type StateKey = keyof AppState;
type Renderers = Record<StateKey, () => void>;

const RENDERERS: Renderers = {
  camas:             () => { renderResumen(); renderBoard(); },
  internacionesById: () => { renderResumen(); renderBoard(); renderDetalle(); },
  rol:               () => { renderDetalle(); },
  actorNombre:       () => {},
  detalle:           () => { renderDetalle(); },
  pendingAction:     () => { renderDetalle(); },
  toast:             () => { renderToast(); },
  loading:           () => { renderResumen(); },
  egreso:            () => { renderDetalle(); },
};

const state = new Proxy({ ...initialState } as AppState, {
  set<K extends StateKey>(target: AppState, key: K, value: AppState[K]): boolean {
    target[key] = value;
    (RENDERERS[key] || (() => {}))();
    return true;
  },
});

// ─── DOM helpers ─────────────────────────────────────────────────────────────

type ElProps = Record<string, unknown>;

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: ElProps = {},
  ...kids: Array<Node | string | null | undefined | false>
): HTMLElementTagNameMap[K] {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class")   n.className = v as string;
    else if (k === "dataset") Object.assign(n.dataset, v as Record<string, string>);
    else if (k === "html")    n.innerHTML = v as string;
    else if (k.startsWith("on") && typeof v === "function")
      n.addEventListener(k.slice(2).toLowerCase(), v as EventListener);
    else n.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append((kid as Node).nodeType ? (kid as Node) : document.createTextNode(String(kid)));
  }
  return n;
}

// ─── API ─────────────────────────────────────────────────────────────────────

class ApiError extends Error {
  constructor(detail: string, public readonly status: number) {
    super(detail);
    this.name = "ApiError";
  }
}

async function api<T = unknown>(path: string, opts: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(API + path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
  } catch {
    throw new ApiError("No hay conexión con la API.", 0);
  }
  const raw = await res.text();
  let data: T | null = null;
  if (raw) { try { data = JSON.parse(raw) as T; } catch { /* noop */ } }
  if (!res.ok) throw new ApiError((data as { detail?: string } | null)?.detail ?? `Error ${res.status}`, res.status);
  return data as T;
}

// ─── Formatters ──────────────────────────────────────────────────────────────

const fmtFecha = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleString("es-AR") : "—";

const internOf = (cama: Cama): Internacion | null =>
  cama?.internacion_actual_id != null
    ? state.internacionesById[cama.internacion_actual_id] ?? null
    : null;

const cobLinea = (intern: Internacion | null): string =>
  intern ? [intern.cobertura, intern.plan_cobertura].filter(Boolean).join(" · ") : "";

const nombrePaciente = (intern: Internacion | null): string =>
  intern
    ? [intern.paciente_apellido, intern.paciente_nombre].filter(Boolean).join(", ")
    : "Paciente";

// ─── Toast ───────────────────────────────────────────────────────────────────

let toastTimer: ReturnType<typeof setTimeout> | null = null;

function toast(text: string, kind: ToastState["kind"] = "info"): void {
  const id = Date.now() + Math.random();
  state.toast = { text, kind, id };
  if (toastTimer) clearTimeout(toastTimer);
  const ms = kind === "err" ? 6000 : 4000;
  toastTimer = setTimeout(() => {
    if (state.toast?.id === id) state.toast = null;
  }, ms);
}

// ─── Data loading ────────────────────────────────────────────────────────────

async function cargarTablero(): Promise<void> {
  state.loading = true;
  try {
    const [camas, internaciones] = await Promise.all([
      api<Cama[]>("/camas"),
      api<Internacion[]>("/internaciones"),
    ]);
    const map: Record<number, Internacion> = {};
    for (const i of internaciones) map[i.id] = i;
    state.internacionesById = map;
    state.camas = camas;
  } catch {
    toast("No se pudo cargar el tablero", "err");
  } finally {
    state.loading = false;
  }
}

async function abrirDetalle(camaId: number): Promise<void> {
  try {
    const detalle = await api<Cama>("/camas/" + camaId);
    state.pendingAction = null;
    state.egreso = null;
    state.detalle = detalle;
    const intern = internOf(detalle);
    if (intern && ["PROCESO_DE_ALTA", "LIMPIEZA_TERMINAL"].includes(detalle.estado_gestion)) {
      try {
        state.egreso = await api<Egreso>(`/internaciones/${intern.id}/egreso-activo`);
      } catch (e) {
        if ((e as ApiError).status !== 404) toast((e as Error).message, "err");
      }
    }
  } catch (e) {
    toast((e as Error).message, "err");
  }
}

function cerrarDetalle(): void {
  state.pendingAction = null;
  state.detalle = null;
  state.egreso = null;
}

// ─── Actions ─────────────────────────────────────────────────────────────────

function onAccion(cama: Cama, accion: Accion): void {
  if (accion.needs === "internacion" || accion.needs === "motivo") {
    state.pendingAction = {
      camaId:   cama.id,
      accionId: accion.id,
      rol:      accion.rol,
      needs:    accion.needs,
    };
    return;
  }
  if (accion.needs === "internacion-actual") {
    if (!cama.internacion_actual_id) {
      toast("La reserva no tiene internación", "err");
      return;
    }
    ejecutar(cama.id, accion.id, { internacion_id: cama.internacion_actual_id });
    return;
  }
  ejecutar(cama.id, accion.id, {});
}

async function ejecutar(
  camaId: number,
  accionId: string,
  payload: Record<string, unknown>,
): Promise<void> {
  const body: Record<string, unknown> = { rol: state.rol, ...payload };
  if (state.actorNombre.trim()) body.actor_nombre = state.actorNombre.trim();
  try {
    await api(`/camas/${camaId}/${accionId}`, {
      method: "POST",
      body:   JSON.stringify(body),
    });
    toast("Acción aplicada.", "ok");
    state.pendingAction = null;
    await cargarTablero();
    await abrirDetalle(camaId);
  } catch (e) {
    toast((e as Error).message, "err");
  }
}

async function crearEgreso(
  internacionId: number,
  camaId: number,
  medioEgreso: MedioEgresoValue,
): Promise<void> {
  const body: Record<string, unknown> = { medio_egreso: medioEgreso, rol: state.rol };
  if (state.actorNombre.trim()) body.actor_nombre = state.actorNombre.trim();
  try {
    await api(`/internaciones/${internacionId}/egreso`, {
      method: "POST",
      body:   JSON.stringify(body),
    });
    toast("Egreso creado.", "ok");
    state.egreso = await api<Egreso>(`/internaciones/${internacionId}/egreso-activo`);
  } catch (e) {
    toast((e as Error).message, "err");
  }
}

async function recargarEgreso(internacionId: number): Promise<void> {
  try {
    state.egreso = await api<Egreso>(`/internaciones/${internacionId}/egreso-activo`);
  } catch (e) {
    if ((e as ApiError).status !== 404) toast((e as Error).message, "err");
    else state.egreso = null;
  }
}

// ─── Traslado modal ──────────────────────────────────────────────────────────

const _DISCREP_MOTIVOS: DiscrepanciaMotivo[] = [
  "ambulancia_demorada",
  "familiar_ausente",
  "documentacion_incompleta",
  "cama_destino_no_disponible",
  "paciente_se_niega",
  "demora_responsable",
  "otro",
];

const _LABEL_ORDEN_TRASLADO = "Orden de traslado emitida por el médico";
const _MEDIOS_CON_ORDEN = new Set<MedioEgresoValue>(["ambulancia", "derivacion"]);

function _pedirDatosTraslado(datosPrevios: Partial<DatosTraslado> = {}): Promise<DatosTraslado | null> {
  return new Promise((resolve) => {
    const overlay = el("div", { class: "overlay open", style: "z-index:300;" });
    const modal   = el("div", {
      class: "card",
      style: "position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:301;width:min(480px,92vw);max-height:85vh;overflow-y:auto;",
    });

    modal.append(el("div", { class: "card-head" },
      el("div", { class: "card-title" }, "Datos de traslado — Orden médica"),
    ));
    const body = el("div", { class: "card-body", style: "display:flex;flex-direction:column;gap:12px;" });

    type SelectOption = [string, string];

    const _sel = (id: string, opts: SelectOption[], val?: string): HTMLSelectElement => {
      const s = el("select", { class: "field-sel", id, style: "height:44px;" },
        ...opts.map(([v, lbl]) => el("option", { value: v }, lbl)),
      );
      if (val) s.value = val;
      return s;
    };

    const _chk = (id: string, lbl: string, val?: boolean): HTMLLabelElement => {
      const inp  = document.createElement("input");
      inp.type   = "checkbox";
      inp.id     = id;
      inp.checked = !!val;
      inp.style.cssText = "width:20px;height:20px;cursor:pointer;";
      return el("label", { style: "display:flex;align-items:center;gap:8px;cursor:pointer;height:44px;" }, inp, lbl);
    };

    const _txt = (id: string, val?: string): HTMLInputElement => {
      const t    = document.createElement("input");
      t.type     = "text";
      t.id       = id;
      t.className = "field-sel";
      t.value    = val ?? "";
      t.style.height = "44px";
      return t;
    };

    const _lbl = (txt: string): HTMLElement =>
      el("div", { class: "field-lbl" }, txt);

    const selDestino  = _sel("f-destino", [
      ["domicilio","Domicilio"], ["sanatorio","Sanatorio"],
      ["tercer_nivel","Tercer nivel"], ["geriatrico","Geriátrico"],
      ["psiquiatrico","Psiquiátrico"], ["otro","Otro"],
    ], datosPrevios.destino_tipo);
    const txtDireccion = _txt("f-direccion", datosPrevios.destino_direccion);
    const txtPrestador = _txt("f-prestador", datosPrevios.prestador);
    const chkMedico    = _chk("f-medico", "Médico a bordo",  datosPrevios.medico_a_bordo);
    const chkAcomp     = _chk("f-acomp",  "Acompañante",     datosPrevios.acompanante);
    const chkOxigeno   = _chk("f-oxig",   "Oxígeno",         datosPrevios.oxigeno);
    const selAccesib   = _sel("f-accesib", [
      ["planta_baja","Planta baja"], ["escaleras","Escaleras"],
      ["ascensor","Ascensor"], ["no_aplica","No aplica"],
    ], datosPrevios.accesibilidad_destino);
    const selIntDom    = _sel("f-intdom", [
      ["desconocido","Desconocido (confirmar antes del cierre)"],
      ["si","Sí — requiere internación domiciliaria"],
      ["no","No"],
    ], datosPrevios.internacion_domiciliaria ?? "desconocido");

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

    const cleanup = (): void => { overlay.remove(); modal.remove(); };

    const foot = el("div", { class: "action-foot" },
      el("button", { class: "btn-primary", style: "height:44px;", onClick: () => {
        const dir = txtDireccion.value.trim();
        const pre = txtPrestador.value.trim();
        if (!dir || !pre) { toast("Dirección y prestador son obligatorios.", "err"); return; }
        cleanup();
        resolve({
          destino_tipo:          selDestino.value,
          destino_direccion:     dir,
          prestador:             pre,
          medico_a_bordo:        (chkMedico.querySelector("input") as HTMLInputElement).checked,
          acompanante:           (chkAcomp.querySelector("input") as HTMLInputElement).checked,
          oxigeno:               (chkOxigeno.querySelector("input") as HTMLInputElement).checked,
          accesibilidad_destino: selAccesib.value,
          internacion_domiciliaria: selIntDom.value,
        });
      }}, "Confirmar orden"),
      el("button", { class: "btn-ghost", style: "height:44px;", onClick: () => { cleanup(); resolve(null); } }, "Cancelar"),
    );
    body.append(foot);
    modal.append(body);

    overlay.addEventListener("click", () => { cleanup(); resolve(null); });
    document.body.append(overlay, modal);
  });
}

function _pedirDiscrepancia(): { motivo: DiscrepanciaMotivo; nota: string | null } | null {
  const lista = _DISCREP_MOTIVOS.join(", ");
  const motivo = prompt(`Override ADMISION — motivo obligatorio:\n${lista}`);
  if (motivo === null) return null;
  const m = motivo.trim() as DiscrepanciaMotivo;
  if (!_DISCREP_MOTIVOS.includes(m)) {
    toast(`Motivo inválido. Usá uno de: ${lista}`, "err");
    return null;
  }
  const nota = prompt("Nota adicional (opcional — Enter para omitir):") ?? "";
  return { motivo: m, nota: nota.trim() || null };
}

// ─── Egreso actions ──────────────────────────────────────────────────────────

async function marcarItemEgreso(
  egresoId: number,
  itemId: number,
  internacionId: number,
  responsableItem: string,
  itemLabel: string,
  egreso: Egreso,
): Promise<void> {
  const body: Record<string, unknown> = { rol: state.rol };
  if (state.actorNombre.trim()) body.actor_nombre = state.actorNombre.trim();
  if (state.rol === "ADMISION" && responsableItem !== "admision") {
    const disc = _pedirDiscrepancia();
    if (disc === null) return;
    body.discrepancia = disc;
  }
  if (itemLabel === _LABEL_ORDEN_TRASLADO && _MEDIOS_CON_ORDEN.has(egreso.medio_egreso)) {
    const datos = await _pedirDatosTraslado(egreso.datos_traslado ?? {});
    if (datos === null) return;
    body.datos_traslado = datos;
  }
  try {
    await api(`/egresos/${egresoId}/checklist/${itemId}`, {
      method: "PATCH",
      body:   JSON.stringify(body),
    });
    await recargarEgreso(internacionId);
  } catch (e) {
    toast((e as Error).message, "err");
  }
}

async function darOkAdmin(egresoId: number, internacionId: number): Promise<void> {
  const body: Record<string, unknown> = { rol: state.rol };
  if (state.actorNombre.trim()) body.actor_nombre = state.actorNombre.trim();
  try {
    await api(`/egresos/${egresoId}/egreso-admin`, { method: "PATCH", body: JSON.stringify(body) });
    toast("OK administrativo registrado.", "ok");
    await recargarEgreso(internacionId);
  } catch (e) {
    toast((e as Error).message, "err");
  }
}

async function confirmarSalidaFisica(egresoId: number, camaId: number): Promise<void> {
  const body: Record<string, unknown> = { rol: state.rol };
  if (state.actorNombre.trim()) body.actor_nombre = state.actorNombre.trim();
  try {
    await api(`/egresos/${egresoId}/salida-fisica`, { method: "PATCH", body: JSON.stringify(body) });
    toast("Salida física confirmada.", "ok");
    await cargarTablero();
    await abrirDetalle(camaId);
  } catch (e) {
    toast((e as Error).message, "err");
  }
}

async function marcarItemLimpieza(
  egresoId: number,
  itemId: number,
  camaId: number,
): Promise<void> {
  const body: Record<string, unknown> = { rol: state.rol };
  if (state.actorNombre.trim()) body.actor_nombre = state.actorNombre.trim();
  if (state.rol === "ADMISION") {
    const disc = _pedirDiscrepancia();
    if (disc === null) return;
    body.discrepancia = disc;
  }
  try {
    const r = await api<{ liberacion_bloqueada?: string }>(
      `/egresos/${egresoId}/limpieza/${itemId}`,
      { method: "PATCH", body: JSON.stringify(body) },
    );
    if (r.liberacion_bloqueada === "mantenimiento_pendiente")
      toast("Limpieza OK. Pendiente: mantenimiento.", "warn");
    await cargarTablero();
    await abrirDetalle(camaId);
  } catch (e) {
    toast((e as Error).message, "err");
  }
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function internacionesLibres(): Internacion[] {
  const ocupadas = new Set(state.camas.map((c) => c.internacion_actual_id).filter(Boolean));
  return Object.values(state.internacionesById).filter(
    (i) => !i.finalizada_at && !ocupadas.has(i.id),
  );
}

// ─── Renderers ───────────────────────────────────────────────────────────────

function renderEgresoPanel(egreso: Egreso, intern: Internacion, cama: Cama): HTMLElement {
  const wrap  = el("div", { class: "card" });
  const estado = egreso.estado;

  const badgeColor: Record<string, string> = {
    info:        "var(--p)",
    bloqueado:   "var(--s-warn)",
    egreso_admin:"var(--s-ok)",
    liberado:    "var(--s-ok)",
  };
  const estadoLabel: Record<string, string> = {
    info:         "En proceso",
    bloqueado:    "Bloqueado",
    egreso_admin: "Admin OK",
    liberado:     "Liberado",
  };
  const color = badgeColor[estado] ?? "var(--p)";
  const head  = el("div", { class: "card-head" },
    el("div", { class: "card-title" }, "Egreso"),
    el("span", {
      style: `background:${color}20; color:${color}; padding:2px 8px; border-radius:4px; font-size:12px;`,
    }, estadoLabel[estado] ?? estado),
  );
  wrap.append(head);

  const body = el("div", { class: "card-body", style: "display:flex; flex-direction:column; gap:12px;" });

  if (egreso.responsable_actual) {
    const { rol, tarea } = egreso.responsable_actual;
    const isDelayed = egreso.minutos_trabado > 120;
    body.append(el("div", { class: isDelayed ? "alert-box" : "wait-box", style: "margin:0;" },
      `Esperando: ${rol.toUpperCase()} — ${tarea}`,
      isDelayed ? el("span", { class: "num" }, ` ⚠ Hace ${Math.round(egreso.minutos_trabado)} min`) : "",
    ));
  }

  body.append(el("div", { style: "font-size:13px; color:var(--txt-2);" },
    el("strong", {}, "Medio: "), egreso.medio_egreso,
  ));

  if (_MEDIOS_CON_ORDEN.has(egreso.medio_egreso)) {
    const ordenPendiente = egreso.items_checklist?.some(
      (i) => i.label === _LABEL_ORDEN_TRASLADO && !i.done,
    );
    if (ordenPendiente) {
      body.append(el("div", { class: "banner-warn" },
        "Este egreso requiere orden de traslado del médico: datos del paciente, destino y dirección, " +
        "requerimientos (oxígeno, médico a bordo, acompañante) e internación domiciliaria si va a domicilio.",
      ));
    }
  }

  if (["info", "bloqueado", "egreso_admin"].includes(estado) && egreso.items_checklist?.length) {
    const rolNorm   = state.rol.toLowerCase();
    const isAdmision = state.rol === "ADMISION";

    const renderCheckItem = (item: ChecklistItem): HTMLElement =>
      el("div", { class: "check-row" },
        el("div", { class: `check-box ${item.done ? "cb-done" : "cb-pend"}` }, item.done ? "✓" : ""),
        el("div", { class: "check-label" },
          item.label,
          item.requerido_legal
            ? el("span", { style: "color:var(--s-err); font-size:11px; margin-left:4px;" }, "legal")
            : null,
        ),
        !item.done
          ? (rolNorm === item.responsable || isAdmision
            ? el("button", { class: "btn-sm tap", onClick: () => marcarItemEgreso(egreso.id, item.id, intern.id, item.responsable, item.label, egreso) }, "Marcar")
            : el("span", { class: "ri-meta" }, `Pendiente: ${item.responsable}`))
          : el("span", { class: "ri-meta" }, item.autor ?? ""),
      );

    const grp  = el("div", { class: "check-group" });
    const done = egreso.items_checklist.filter((i) => i.done).length;
    grp.append(el("div", { class: "cg-head cg-medico" },
      el("div", {}, "Checklist de egreso"),
      el("div", { class: "num" }, `${done}/${egreso.items_checklist.length}`),
    ));
    const gbody = el("div", { class: "cg-body" });

    if (isAdmision) {
      for (const item of egreso.items_checklist) gbody.append(renderCheckItem(item));
    } else {
      const myItems    = egreso.items_checklist.filter((i) => i.responsable === rolNorm);
      const otherItems = egreso.items_checklist.filter((i) => i.responsable !== rolNorm);
      for (const item of myItems) gbody.append(renderCheckItem(item));
      if (otherItems.length) {
        const otherPending = otherItems.filter((i) => !i.done);
        const byRole = otherPending.reduce<Record<string, number>>((acc, i) => {
          acc[i.responsable] = (acc[i.responsable] ?? 0) + 1;
          return acc;
        }, {});
        const breakdown   = Object.entries(byRole).map(([r, n]) => `${r} ${n}`).join(", ");
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

    const todosDone = egreso.items_checklist.every((i) => i.done);
    if (estado === "info" || estado === "bloqueado") {
      if (todosDone)
        body.append(el("button", { class: "btn-primary", onClick: () => darOkAdmin(egreso.id, intern.id) }, "OK Administrativo"));
    } else if (estado === "egreso_admin" && !egreso.salida_fisica_at) {
      body.append(el("button", { class: "btn-primary", onClick: () => confirmarSalidaFisica(egreso.id, cama.id) }, "Confirmar Salida Física"));
    }
  } else if (estado === "egreso_admin" && !egreso.salida_fisica_at) {
    body.append(el("button", { class: "btn-primary", onClick: () => confirmarSalidaFisica(egreso.id, cama.id) }, "Confirmar Salida Física"));
  }

  if (cama.estado_gestion === "LIMPIEZA_TERMINAL" && egreso.limpieza_checklist?.length) {
    const renderLimpiezaItem = (item: LimpiezaItem): HTMLElement => {
      let accion: HTMLElement;
      if (!item.done) {
        const esSupervision = item.codigo === "SUPERVISION";
        const puedeMarcar   = esSupervision
          ? ["HOTELERIA", "ADMISION"].includes(state.rol)
          : ["LIMPIEZA", "HOTELERIA", "ADMISION"].includes(state.rol);
        if (puedeMarcar)
          accion = el("button", { class: "btn-sm tap", onClick: () => marcarItemLimpieza(egreso.id, item.id, cama.id) }, "Marcar");
        else if (esSupervision && state.rol === "LIMPIEZA")
          accion = el("span", { class: "ri-meta" }, "Pendiente: supervisión de hotelería");
        else
          accion = el("span", { class: "ri-meta" }, "Pendiente: limpieza");
      } else {
        accion = el("span", { class: "ri-meta" }, item.autor ?? "");
      }
      return el("div", { class: "check-row" },
        el("div", { class: `check-box ${item.done ? "cb-done" : "cb-pend"}` }, item.done ? "✓" : ""),
        el("div", { class: "check-label" }, item.label),
        accion,
      );
    };

    const grp  = el("div", { class: "check-group" });
    const done = egreso.limpieza_checklist.filter((i) => i.done).length;
    grp.append(el("div", { class: "cg-head cg-medico" },
      el("div", {}, "Limpieza terminal"),
      el("div", { class: "num" }, `${done}/${egreso.limpieza_checklist.length}`),
    ));
    const gbody = el("div", { class: "cg-body" });

    if (["LIMPIEZA", "HOTELERIA", "ADMISION"].includes(state.rol)) {
      for (const item of egreso.limpieza_checklist) gbody.append(renderLimpiezaItem(item));
    } else {
      const pending     = egreso.limpieza_checklist.filter((i) => !i.done).length;
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

    if (egreso.mantenimiento_requerido && egreso.limpieza_checklist.every((i) => i.done))
      body.append(el("div", { class: "banner-warn" }, "Liberación bloqueada: mantenimiento pendiente"));
  }

  if (egreso.discrepancias?.length) {
    const grp = el("div", {});
    grp.append(el("div", { class: "field-lbl", style: "margin-bottom:4px;" }, "Discrepancias"));
    for (const d of egreso.discrepancias) {
      grp.append(el("div", { class: "nota" },
        el("div", { class: "nota-txt" }, `${d.motivo}: ${d.nota ?? ""}`),
        el("div", { class: "nota-meta" }, `${fmtFecha(d.hora)} · ${d.autor}`),
      ));
    }
    body.append(grp);
  }

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

function renderCrearEgresoPanel(intern: Internacion, cama: Cama): HTMLElement {
  const wrap   = el("div", { class: "card" });
  wrap.append(el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Iniciar egreso")));
  const body   = el("div", { class: "card-body", style: "display:flex; flex-direction:column; gap:8px;" });
  const select = el("select", { class: "field-sel" },
    ...MEDIOS_EGRESO.map((m) => el("option", { value: m.value }, m.label)),
  );

  const bannerOrden = el("div", {
    class: "banner-warn",
    style: "display:none;",
  },
    "Este egreso requiere orden de traslado del médico: datos del paciente, destino y dirección, " +
    "requerimientos (oxígeno, médico a bordo, acompañante) e internación domiciliaria si va a domicilio.",
  );

  const actualizarBanner = (): void => {
    bannerOrden.style.display = _MEDIOS_CON_ORDEN.has(select.value as MedioEgresoValue) ? "" : "none";
  };
  select.addEventListener("change", actualizarBanner);
  actualizarBanner();

  body.append(
    el("div", { class: "field-lbl" }, "Medio de egreso"),
    select,
    bannerOrden,
    el("div", { class: "action-foot" },
      el("button", {
        class: "btn-primary",
        onClick: () => crearEgreso(intern.id, cama.id, select.value as MedioEgresoValue),
      }, "Crear egreso"),
    ),
  );
  wrap.append(body);
  return wrap;
}

function renderResumen(): void {
  const host = document.getElementById("resumen");
  if (!host) return;
  host.innerHTML = "";
  if (state.camas.length === 0) return;

  const conteo: Partial<Record<EstadoGestion, number>> = {};
  for (const e of ESTADOS_ORDEN) conteo[e] = 0;
  for (const c of state.camas) conteo[c.estado_gestion] = (conteo[c.estado_gestion] ?? 0) + 1;

  const cont = el("div", { style: "display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px" });
  const pillMap: Record<EstadoGestion, string> = {
    DISPONIBLE: "ok", OCUPADA: "info", RESERVADA: "info",
    PROCESO_DE_ALTA: "alta", LIMPIEZA_TERMINAL: "warn", BLOQUEADA: "err",
  };
  for (const e of ESTADOS_ORDEN) {
    cont.append(el("span", { class: `pill p-${pillMap[e]}` }, `${conteo[e] ?? 0} ${ESTADO_PLURAL[e]}`));
  }
  host.append(cont);
}

function renderBoard(): void {
  const host = document.getElementById("board");
  if (!host) return;
  host.innerHTML = "";
  if (state.camas.length === 0) return;

  const sectores = new Map<string, Cama[]>();
  for (const c of state.camas) {
    if (!sectores.has(c.sector)) sectores.set(c.sector, []);
    sectores.get(c.sector)!.push(c);
  }

  const estadoCssMap: Record<EstadoGestion, string> = {
    DISPONIBLE: "libre", OCUPADA: "ocup", RESERVADA: "demo",
    PROCESO_DE_ALTA: "alta", LIMPIEZA_TERMINAL: "limp", BLOQUEADA: "bloq",
  };

  for (const [sector, camas] of sectores) {
    const grid = el("div", { class: "cama-grid" });
    for (const c of camas) {
      const intern = internOf(c);
      const estado = estadoCssMap[c.estado_gestion] ?? "libre";
      const card   = el("div", { class: `cama ${estado}`, onClick: () => abrirDetalle(c.id) },
        el("div", { class: "cama-top" },
          el("div", { class: "cama-num" }, c.nombre),
          el("div", { class: "cama-state-dot", style: `background: var(--cama-${estado === "demo" ? "demo" : estado})` }),
        ),
        el("div", { class: `cama-status bst-${estado}`, style: "margin-bottom:6px;" }, ESTADO_LABEL[c.estado_gestion]),
        c.estado_gestion === "BLOQUEADA"
          ? el("div", { class: "cama-espera" }, c.motivo_bloqueo ?? "Bloqueada")
          : intern
            ? el("div", {},
                el("div", { class: "cama-pac" }, nombrePaciente(intern)),
                el("div", { class: "cama-proc" }, cobLinea(intern)),
              )
            : el("div", { class: c.estado_gestion === "DISPONIBLE" ? "cama-libre-lbl" : "cama-proc" },
                c.estado_gestion === "DISPONIBLE" ? "Disponible" : "Esperando limpieza",
              ),
      );
      grid.append(card);
    }
    host.append(
      el("div", { style: "margin-bottom: 24px;" },
        el("h3", {
          style: "font-size: 14px; margin-bottom: 12px; color: var(--txt); border-bottom: 1px solid var(--brd); padding-bottom:6px;",
        }, `${sector} (${camas.length})`),
        grid,
      ),
    );
  }
}

function renderDetalle(): void {
  const host = document.getElementById("drawer-host");
  if (!host) return;
  const _prevScroll = host.querySelector(".drawer-body")?.scrollTop ?? 0;
  host.innerHTML = "";
  const d = state.detalle;
  if (!d) return;
  const intern = internOf(d);

  const overlay    = el("div", { class: "overlay open", onClick: cerrarDetalle });
  const drawerBody = el("div", { class: "drawer-body" });

  if (intern) {
    drawerBody.append(el("div", { class: "card" },
      el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Paciente y cobertura")),
      el("div", { class: "card-body", style: "display:flex; flex-direction:column; gap:8px;" },
        el("div", {}, el("strong", {}, "Paciente: "), nombrePaciente(intern)),
        el("div", {}, el("strong", {}, "DNI: "), intern.paciente_dni ?? "—"),
        el("div", {}, el("strong", {}, "Cobertura: "), intern.cobertura ?? "—"),
      ),
    ));
  }

  drawerBody.append(renderAccionesArea(d));

  if (intern && ["PROCESO_DE_ALTA", "LIMPIEZA_TERMINAL"].includes(d.estado_gestion)) {
    if (state.egreso) drawerBody.append(renderEgresoPanel(state.egreso, intern, d));
    else if (d.estado_gestion === "PROCESO_DE_ALTA") drawerBody.append(renderCrearEgresoPanel(intern, d));
  }

  const hitosCont = el("div", { class: "card" });
  hitosCont.append(el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Traza de hitos")));
  const hitosList = el("div", { class: "card-body" });
  hitosCont.append(hitosList);
  renderHitos(d.hitos ?? [], hitosList);
  drawerBody.append(hitosCont);

  const notasCont = el("div", { class: "card" });
  notasCont.append(el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Notas de la cama")));
  const notasList = el("div", { class: "card-body" });
  notasCont.append(notasList);
  renderNotas(d.notas ?? [], notasList);
  drawerBody.append(notasCont);

  const drawer = el("aside", { class: "drawer open" },
    el("div", { class: "drawer-head" },
      el("div", { class: "dh-av", style: "background:var(--p-light); color:var(--p-dark);" }, "🛏️"),
      el("div", { class: "dh-info" },
        el("div", { class: "dh-name" }, d.nombre),
        el("div", { class: "dh-sub" }, `${ESTADO_LABEL[d.estado_gestion]} · ${TIPO_LABEL[d.tipo] ?? d.tipo}`),
      ),
      el("button", { class: "drawer-close", onClick: cerrarDetalle }, "×"),
    ),
    drawerBody,
  );

  host.append(overlay, drawer);
  if (_prevScroll) drawerBody.scrollTop = _prevScroll;
}

function renderAccionesArea(d: Cama): HTMLElement {
  const wrap = el("div", { class: "card" });
  wrap.append(el("div", { class: "card-head" }, el("div", { class: "card-title" }, "Acciones")));

  const cont = el("div", { class: "card-body", style: "display:flex; flex-direction:column; gap:8px;" });
  if (state.pendingAction?.camaId === d.id) {
    if (state.pendingAction.needs === "motivo") {
      const input = el("input", { class: "field-sel", id: "f-motivo", placeholder: "Ej. Mantenimiento..." });
      cont.append(
        el("div", { class: "field-lbl" }, "Motivo del bloqueo"),
        input,
        el("div", { class: "action-foot" },
          el("button", { class: "btn-primary", onClick: () => { if (input.value) ejecutar(d.id, "bloquear", { motivo_bloqueo: input.value }); } }, "Confirmar"),
          el("button", { class: "btn-ghost", onClick: () => (state.pendingAction = null) }, "Cancelar"),
        ),
      );
    } else {
      const libres = internacionesLibres();
      const select = el("select", { class: "field-sel" },
        el("option", { value: "" }, "— elegir internación existente —"),
        ...libres.map((i) => el("option", { value: String(i.id) }, `${nombrePaciente(i)} - ${i.paciente_dni}`)),
      );
      const pa = state.pendingAction;
      cont.append(
        el("div", { class: "field-lbl" }, "Seleccionar internación"),
        select,
        el("div", { class: "action-foot" },
          el("button", { class: "btn-primary", onClick: () => { if (select.value) ejecutar(d.id, pa.accionId, { internacion_id: Number(select.value) }); } }, "Confirmar"),
          el("button", { class: "btn-ghost", onClick: () => (state.pendingAction = null) }, "Cancelar"),
        ),
      );
    }
  } else {
    const acciones = ACCIONES[d.estado_gestion] ?? [];
    if (!acciones.length) cont.append(el("p", { class: "ri-meta" }, "Sin acciones disponibles."));
    for (const a of acciones) {
      cont.append(el("button", {
        class:   a.kind === "warn" ? "btn-warn" : "btn-primary",
        onClick: () => onAccion(d, a),
      }, a.label));
    }
  }
  wrap.append(cont);
  return wrap;
}

function renderToast(): void {
  const host = document.getElementById("toast-host");
  if (!host) return;
  host.innerHTML = "";
  if (!state.toast) return;
  const t = state.toast;
  host.append(el("div", { class: `toast toast--${t.kind}` },
    el("span", { class: "toast-text" }, t.text),
    el("button", { class: "toast-close", onClick: () => { state.toast = null; } }, "×"),
  ));
}

function renderHitos(hitos: Hito[], host: HTMLElement): void {
  host.innerHTML = "";
  if (!hitos.length) {
    host.innerHTML = '<p class="muted">Sin hitos registrados.</p>';
    return;
  }
  const container = el("div", { class: "tl" });
  hitos.forEach((h) => {
    container.append(el("div", { class: "tl-item" },
      el("div", { class: "tl-lw" },
        el("div", { class: "tl-dot", style: "background:var(--p)" }),
        el("div", { class: "tl-vert" }),
      ),
      el("div", { class: "tl-body" },
        el("div", { class: "tl-time" }, fmtFecha(h.registrado_at)),
        el("div", { class: "tl-event" }, h.hito_codigo),
        el("div", { class: "tl-who" }, `${h.actor_rol ?? ""} · ${h.actor_nombre ?? ""}`),
      ),
    ));
  });
  host.append(container);
}

function renderNotas(notas: NotaCama[], host: HTMLElement): void {
  host.innerHTML = "";
  if (!notas.length) {
    host.innerHTML = '<p class="muted">Sin notas.</p>';
    return;
  }
  notas.forEach((n) => {
    host.append(el("div", { class: "nota" },
      el("div", { class: "nota-txt" }, n.texto),
      el("div", { class: "nota-meta" }, `${fmtFecha(n.creada_at)} · ${n.creada_por_rol ?? ""}`),
    ));
  });
}

// ─── Bootstrap ───────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  const sel = document.getElementById("rol-select") as HTMLSelectElement | null;
  if (sel) {
    for (const r of ROLES) sel.append(el("option", { value: r }, r));
    sel.value = state.rol;
    sel.addEventListener("change", () => { state.rol = sel.value as Rol; });
  }

  document.getElementById("btn-refresh")?.addEventListener("click", cargarTablero);
  cargarTablero();

  setInterval(async () => {
    if (document.hidden) return;
    await cargarTablero();
    if (state.detalle && !state.pendingAction) {
      const intern = internOf(state.detalle);
      if (intern && state.egreso) await recargarEgreso(intern.id);
    }
  }, 15_000);
});
