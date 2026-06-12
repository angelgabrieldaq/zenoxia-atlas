"""Dedup + reorganización de styles.css — Atlas.
Uso: python3 dedup_css.py
Lee /app/frontend/styles.css, escribe /app/frontend/styles_dedup.css.
"""
from collections import defaultdict, OrderedDict
import re

SRC = "/app/frontend/styles.css"
DST = "/app/frontend/styles_dedup.css"

with open(SRC) as f:
    lines = f.readlines()

# ── Parser ─────────────────────────────────────────────────────────────────────
def parse_css(lines):
    items = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("@") or stripped == "}":
            depth = 0
            raw = []
            while i < len(lines):
                raw.append(lines[i])
                depth += lines[i].count("{") - lines[i].count("}")
                i += 1
                if depth <= 0:
                    break
            items.append(("at", "".join(raw)))
            continue
        if stripped.startswith("/*") or stripped == "" or stripped.startswith("//"):
            i += 1
            continue
        if "{" in lines[i]:
            sel = stripped[: stripped.index("{")].strip()
            if not sel:
                i += 1
                continue
            props = OrderedDict()
            i += 1
            while i < len(lines) and "}" not in lines[i]:
                pline = lines[i].strip().rstrip(";")
                pline = re.sub(r"/\*.*?\*/", "", pline).strip().rstrip(";")
                if ":" in pline and not pline.startswith("*"):
                    k, _, v = pline.partition(":")
                    k = k.strip()
                    v = v.strip()
                    if k:
                        props[k] = v
                i += 1
            i += 1  # skip }
            items.append(("rule", sel, props))
            continue
        i += 1
    return items

items = parse_css(lines)

# ── Collect & merge ────────────────────────────────────────────────────────────
rules_by_sel = defaultdict(list)
rule_order = []
for item in items:
    if item[0] == "rule":
        sel = item[1]
        if sel not in rules_by_sel:
            rule_order.append(sel)
        rules_by_sel[sel].append(item[2])

def merge_props(defs):
    winner = dict(defs[-1])
    for earlier in defs[:-1]:
        for k, v in earlier.items():
            if k not in winner:
                winner[k] = v
    return winner

merged = {sel: merge_props(defs) for sel, defs in rules_by_sel.items()}
dedup_count = sum(1 for defs in rules_by_sel.values() if len(defs) > 1)

# ── Section mapping ────────────────────────────────────────────────────────────
SECTIONS = [
    ("/* ── Reset / Base ──────────────────────────────────────────────── */", [
        "*", "*, *::before, *::after", "*::before", "*::after",
        "html", "body",
    ]),
    ("/* ── Layout principal ───────────────────────────────────────────── */", [
        ".meta", ".meta-title", ".meta-sub", ".slabel", ".logo",
        ".shell", ".topbar", ".tb-left", ".tb-right", ".tb-name", ".tb-role", ".tb-date",
        ".topbar-controls",
        ".map-tabs", ".mtab", ".mtab.active",
        ".map-area", ".map-hint",
        "#topbar", "#board", "#resumen",
    ]),
    ("/* ── Resumen / chips ────────────────────────────────────────────── */", [
        ".summary-panel", ".chip", ".chip .chip-n", ".chip-dot", ".chip-total",
    ]),
    ("/* ── Tablero — cama cards ───────────────────────────────────────── */", [
        ".grid",
        ".cama", ".cama:hover",
        ".cama.libre", ".cama.ocup", ".cama.alta", ".cama.alta-admin",
        ".cama.bloq", ".cama.bloq.demorado", ".cama.limp",
        ".cama-top", ".cama-num", ".cama-pac", ".cama-proc", ".cama-status",
        ".cama-cob", ".cama-motivo", ".cama-empty", ".cama-libre-lbl",
        ".cama-flag", ".cama-espera", ".cama-state-dot",
        ".bst-libre", ".bst-ocup", ".bst-alta", ".bst-alta-admin",
        ".bst-bloq", ".bst-demo", ".bst-limp",
    ]),
    ("/* ── Overlay + Drawer ───────────────────────────────────────────── */", [
        ".overlay",
        ".drawer",
        '.drawer[data-estado="DISPONIBLE"]',
        '.drawer[data-estado="OCUPADA"]',
        '.drawer[data-estado="RESERVADA"]',
        '.drawer[data-estado="PROCESO_DE_ALTA"]',
        '.drawer[data-estado="LIMPIEZA_TERMINAL"]',
        '.drawer[data-estado="BLOQUEADA"]',
        ".drawer.open",
        ".drawer-head", ".drawer-head .codigo", ".drawer-head .meta",
        ".drawer-body",
        ".dh-av", ".dh-info", ".dh-name", ".dh-sub", ".drawer-close",
        ".icon-btn", ".icon-btn:hover",
        ".section-label",
        ".dl", ".dl dt", ".dl dd", ".cobertura-card",
    ]),
    ("/* ── Cards ──────────────────────────────────────────────────────── */", [
        ".card", ".card-head", ".card-title", ".card-badge", ".card-body",
        ".egreso-resumen", ".er-title", ".er-dims", ".er-dim",
        ".er-dim-ico", ".erd-ok", ".erd-pend", ".erd-wait", ".erd-block",
        ".er-dim-label", ".er-dim-state",
        ".pill", ".p-ok", ".p-green", ".p-warn", ".p-err", ".p-info",
        ".p-alta", ".p-cam", ".p-demo",
    ]),
    ("/* ── Acciones / Botones ─────────────────────────────────────────── */", [
        ".acciones", ".acciones .btn",
        ".btn", ".btn:hover", ".btn:active",
        ".btn-primary", ".btn-primary:hover", ".btn-primary:disabled", ".btn-primary:active",
        ".btn-ok", ".btn-ok:hover",
        ".btn-green", ".btn-green:hover",
        ".btn-warn", ".btn-warn:hover", ".btn-warn .rol-hint",
        ".btn-ghost", ".btn-ghost:hover",
        ".btn-sm", ".btn-sm:hover", ".btn-sm.tap", ".btn-sm.tap:hover",
        ".action-foot",
        ".rol-hint", ".rol-hint--warn",
        ".form", ".form h3",
        ".field-lbl", ".field-sel",
        ".responsable-tag",
        ".medio-banner", ".mb-camina", ".mb-ambulancia", ".mb-derivacion", ".mb-pend",
        ".equip-opts", ".equip-chip", ".equip-chip:hover", ".equip-chip.sel",
        ".medio-opts", ".medio-btn", ".medio-btn:hover",
        ".mob-ico", ".mob-label", ".mob-sub",
        ".note-ta", ".note-ta:focus",
    ]),
    ("/* ── Checklist egreso / limpieza ────────────────────────────────── */", [
        ".cg-medico", ".cg-admision", ".cg-enfermeria", ".cg-limpieza",
        ".check-group",
        ".cg-head", ".cg-body",
        ".check-row", ".check-row:last-child",
        ".check-box",
        ".cb-pend", ".cb-pend.editable", ".cb-pend.editable:hover",
        ".cb-done", ".cb-legal", ".cb-legal.editable",
        ".check-label", ".check-label.done", ".check-label .legal-tag",
        ".check-meta", ".cg-locked", ".cg-hint",
        ".ri-meta",
    ]),
    ("/* ── Hitos / Timeline / Notas ───────────────────────────────────── */", [
        ".tl", ".tl-item", ".tl-lw", ".tl-dot", ".tl-vert",
        ".tl-body", ".tl-time", ".tl-event", ".tl-who",
        ".hito", ".hito-cod", ".hito-meta",
        ".notas", ".nota", ".nota-txt", ".nota-meta",
        ".reclamo-item", ".ri-head", ".ri-tipo", ".rit-reclamo", ".rit-novedad", ".ri-text",
        ".role-notice",
        ".discrep-box", ".db-title", ".db-text",
        ".wait-box",
    ]),
    ("/* ── Toasts ─────────────────────────────────────────────────────── */", [
        ".toast-host", ".toast",
        ".toast--ok", ".toast--err", ".toast--warn", ".toast--info",
        ".toast-text", ".toast-close",
    ]),
    ("/* ── Utilidades ─────────────────────────────────────────────────── */", [
        ".num", ".muted",
        ".alert-box", ".info-box", ".demo-box",
        ".modal-bd", ".modal-bd.open", ".modal",
        ".m-head", ".m-title", ".m-sub", ".m-body",
        ".m-opt", ".m-opt:hover", ".m-opt.sel", ".m-foot",
    ]),
]

sel_to_section = {}
for si, (header, sels) in enumerate(SECTIONS):
    for s in sels:
        sel_to_section[s] = si

# @-rules verbatim
at_rules_raw = [item[1] for item in items if item[0] == "at"]

# ── Render ─────────────────────────────────────────────────────────────────────
def render_rule(sel, props):
    if not props:
        return ""
    body = "\n".join(f"  {k}: {v};" for k, v in props.items())
    return f"{sel} {{\n{body}\n}}"

section_rules = defaultdict(list)
unassigned = []
for sel in rule_order:
    si = sel_to_section.get(sel)
    if si is not None:
        section_rules[si].append(sel)
    else:
        unassigned.append(sel)

out = []
out.append("/* styles.css — Atlas · Zenoxia")
out.append(" * Dedup + reorganización 2026-06-11. 0 selectores duplicados.")
out.append(" * Fuente de valores: design-tokens.css")
out.append(" */\n")

for si, (header, _) in enumerate(SECTIONS):
    sels_in = section_rules.get(si, [])
    if not sels_in:
        continue
    out.append(header)
    for sel in sels_in:
        rule = render_rule(sel, merged[sel])
        if rule:
            out.append(rule)
    out.append("")

if unassigned:
    out.append("/* ── Otros ──────────────────────────────────────────────────────── */")
    for sel in unassigned:
        rule = render_rule(sel, merged[sel])
        if rule:
            out.append(rule)
    out.append("")

out.append("/* ── Animaciones + Responsive ───────────────────────────────────── */")
# Deduplicate @-rules by content (some @keyframes appear twice)
seen_at = set()
for at in at_rules_raw:
    key = re.sub(r"\s+", " ", at.strip())
    if key not in seen_at:
        seen_at.add(key)
        out.append(at.rstrip())
out.append("")

final = "\n".join(out)
with open(DST, "w") as f:
    f.write(final)

orig_lines = len(lines)
new_lines = final.count("\n") + 1
print(f"Selectores únicos totales : {len(rules_by_sel)}")
print(f"Duplicados colapsados     : {dedup_count}")
print(f"Sin asignar a sección     : {len(unassigned)}")
print(f"Líneas originales         : {orig_lines}")
print(f"Líneas resultado          : {new_lines}")
if unassigned:
    print("\nSelectores sin asignar (van a sección Otros):")
    for s in unassigned:
        print(f"  {s}")
print(f"\nEscrito: {DST}")
