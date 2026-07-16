#!/usr/bin/env python3
"""Build a file://-safe offline Wiki ZIP (no ES modules, content pre-rendered).

    python scripts/ops/build_wiki_offline_html_zip.py

Output (repo dist/ only):

    dist/cloudlabs-wiki-offline/
    dist/cloudlabs-wiki-offline.zip
"""
from __future__ import annotations

import base64
import html
import json
import re
import shutil
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
OUT = DIST / "cloudlabs-wiki-offline"
ZIP_PATH = DIST / "cloudlabs-wiki-offline.zip"
BASE = "http://127.0.0.1:8000"
GUIDES = ROOT / "frontend" / "wiki" / "guides"
FRONT_WIKI = ROOT / "frontend" / "wiki.html"
BACKENDS_IMG = ROOT / "frontend" / "wiki" / "backends"


def get_json(path: str):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def try_json(path: str, fallback):
    try:
        return get_json(path)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print("warn", path, type(exc).__name__)
        return fallback


# --- minimal markdown (mirrors frontend/js/wiki/markdown.js enough for our guides) ---


def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2">\1</a>',
        s,
    )
    return s


def render_markdown(md: str, *, figure_prefix: str = "figures/") -> str:
    text = str(md or "").replace("\r\n", "\n")
    # rewrite guide figure paths used in wiki
    text = text.replace("/static/wiki/guides/figures/", figure_prefix)
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    in_code = False
    code_buf: list[str] = []
    list_type = None
    table_rows: list[str] = []
    para: list[str] = []

    def flush_list():
        nonlocal list_type
        if list_type:
            out.append(f"</{list_type}>")
            list_type = None

    def flush_para():
        nonlocal para
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para = []

    def flush_table():
        nonlocal table_rows
        if not table_rows:
            return
        rows = [
            r
            for r in table_rows
            if not re.match(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$", r)
        ]
        table_rows = []
        if not rows:
            return

        def parse_row(row: str):
            cells = row.strip().strip("|").split("|")
            return [_inline(c.strip()) for c in cells]

        header = parse_row(rows[0])
        out.append("<table><thead><tr>")
        for c in header:
            out.append(f"<th>{c}</th>")
        out.append("</tr></thead><tbody>")
        for row in rows[1:]:
            out.append("<tr>")
            for c in parse_row(row):
                out.append(f"<td>{c}</td>")
            out.append("</tr>")
        out.append("</tbody></table>")

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            flush_para()
            flush_list()
            flush_table()
            if not in_code:
                in_code = True
                code_buf = []
            else:
                out.append(
                    "<pre><code>"
                    + html.escape("\n".join(code_buf))
                    + "</code></pre>"
                )
                in_code = False
                code_buf = []
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        m_img = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", line.strip())
        if m_img:
            flush_para()
            flush_list()
            flush_table()
            alt, src = m_img.group(1), m_img.group(2)
            out.append(
                f'<img class="guide-figure" src="{html.escape(src)}" alt="{html.escape(alt)}" />'
            )
            i += 1
            continue

        m_h = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m_h:
            flush_para()
            flush_list()
            flush_table()
            level = len(m_h.group(1))
            out.append(f"<h{level}>{_inline(m_h.group(2))}</h{level}>")
            i += 1
            continue

        if re.match(r"^[-*]\s+", line):
            flush_para()
            flush_table()
            if list_type != "ul":
                flush_list()
                list_type = "ul"
                out.append("<ul>")
            out.append(f"<li>{_inline(re.sub(r'^[-*]\\s+', '', line))}</li>")
            i += 1
            continue

        if re.match(r"^\d+\.\s+", line):
            flush_para()
            flush_table()
            if list_type != "ol":
                flush_list()
                list_type = "ol"
                out.append("<ol>")
            out.append(f"<li>{_inline(re.sub(r'^\\d+\\.\\s+', '', line))}</li>")
            i += 1
            continue

        if line.strip().startswith("|") and "|" in line.strip()[1:]:
            flush_para()
            flush_list()
            table_rows.append(line)
            i += 1
            continue

        if not line.strip():
            flush_para()
            flush_list()
            flush_table()
            i += 1
            continue

        flush_table()
        if list_type:
            flush_list()
        para.append(line.strip())
        i += 1

    flush_para()
    flush_list()
    flush_table()
    if in_code and code_buf:
        out.append("<pre><code>" + html.escape("\n".join(code_buf)) + "</code></pre>")
    return "\n".join(out)


MOCK_BACKEND = "mock.default"
SKIP_MEAS = frozenset({"pose", "motor_rotations", "last_optimized_pose"})
SKIP_TUN = frozenset({"reported_pose"})
MOCK_LAB_VIEW = ROOT / "backend" / "lab_communicator" / "mock" / "lab_view"
BACKENDS_SCHEMA = ROOT / "schemas" / "backends.json"

TENSOR_SPEC = {
    "camera_image": {
        "dtype": "uint8",
        "layout": "bgr_hwc_uint8",
        "shape": "(H, W, 3)",
        "domain": "spatial",
        "axes": {
            "H": "rows (y), pixels top-bottom",
            "W": "cols (x), pixels left-right",
            "3": "BGR channels (OpenCV / kernel layout)",
        },
        "wire": "PNG on disk / HTTP - not the analysis type",
    },
    "last_optimization_score": {
        "dtype": "float64",
        "layout": "scalar",
        "shape": "()",
        "domain": "scalar",
        "axes": {},
    },
    "output_power_readback_mw": {
        "dtype": "float64",
        "layout": "scalar",
        "shape": "()",
        "domain": "scalar",
        "axes": {},
        "unit": "mW",
    },
}


def _phys_component(row: dict) -> str:
    typ = str(row.get("type") or "").upper()
    name = row.get("name") or row.get("tag_id") or "This part"
    by = {
        "MIRROR": f"{name} redirects a free-space beam. Moving it on the breadboard changes where the light goes; motor axes (if present) fine-steer tip/tilt without relocating the mount.",
        "LENS": f"{name} focuses or collimates the beam. Pose on the table sets the optical path length and alignment relative to upstream sources.",
        "OPTICAL_CAMERA": f"{name} is an eye on the experiment - gripper or table camera. Images are the raw material for kernels (centroids, scores) and for human supervision.",
        "FILTER": f"{name} attenuates or spectrally selects light. Treat placement as part of the optical recipe, not only a mechanical pose.",
        "OPTICAL_FILTER": f"{name} attenuates or spectrally selects light. Treat placement as part of the optical recipe, not only a mechanical pose.",
        "LASER": f"{name} is a source. Output power and pointing couple into every downstream alignment loop.",
        "LASER_SOURCE": f"{name} is a source. Output power and pointing couple into every downstream alignment loop.",
        "STAGE": f"{name} is a precision actuator. Scripted moves should respect bounds - small steps often matter more than large jumps.",
        "IRIS": f"{name} apertures the beam. Closing it changes power and spatial mode content seen by cameras.",
    }
    if typ in by:
        return by[typ]
    if "CAMERA" in typ:
        return f"{name} captures light as an image. Use RECORD_MEASURABLES / probe_kernel when you need numbers from that image, not only a live view."
    return f"{name} ({typ or 'component'}) participates in the optical layout. Tunables are DOFs you control via primitives; measurables are captured observations without a 1:1 tunable."


def _tensor_text(field: str, decl: dict) -> str:
    base = dict(TENSOR_SPEC.get(field) or {})
    dtype = decl.get("dtype") or base.get("dtype") or "-"
    layout = decl.get("layout") or base.get("layout") or "-"
    shape = decl.get("shape") or decl.get("tensor_shape") or base.get("shape") or "-"
    axes = decl.get("axes") if isinstance(decl.get("axes"), dict) else base.get("axes") or {}
    lines = [f"{dtype} · {layout} · {shape}"]
    if decl.get("unit") or base.get("unit"):
        lines.append(f"unit: {decl.get('unit') or base.get('unit')}")
    if axes:
        for k, v in axes.items():
            lines.append(f"{k}: {v}")
    elif layout == "scalar" or shape == "()":
        lines.append("no axes (0-D scalar)")
    wire = decl.get("wire_format")
    if wire:
        lines.append(f"wire: {wire}")
    elif base.get("wire"):
        lines.append(str(base["wire"]))
    return "\n".join(lines)


def _meas_interp(field: str, decl: dict) -> str:
    if decl.get("physical_interpretation"):
        return str(decl["physical_interpretation"])
    if field == "camera_image" or "image" in field:
        return (
            "A still frame from this camera - intensity on the sensor. Kernels read "
            "BGR pixels; humans see a picture. No tunable sets this field - it must be captured."
        )
    if "power" in field or "score" in field:
        return (
            "A scalar summary of how much light (or how good a match) the current "
            "setting produces. Captured, not commanded."
        )
    return f"Observed quantity '{field}' - a measurement with no matching tunable."


def _normalize_caps(caps) -> dict:
    if not isinstance(caps, dict):
        return {"statecontrol": {"tunables": {}, "measurables": {}}, "primitives": []}
    if caps.get("statecontrol"):
        return {
            "statecontrol": caps.get("statecontrol") or {"tunables": {}, "measurables": {}},
            "primitives": list(caps.get("primitives") or []),
            "telemetry": caps.get("telemetry") or {},
        }
    tun = dict(caps.get("tunables") or {})
    return {
        "statecontrol": {
            "tunables": tun,
            "measurables": dict(caps.get("measurables") or {}),
        },
        "primitives": list(caps.get("primitives") or []),
        "telemetry": caps.get("telemetry") or {},
    }


def load_mock_snapshot() -> dict:
    """Load mock.default catalog from disk (no live server required)."""
    import sys

    backend_root = str(ROOT / "backend")
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    from lab_model.catalog.schema import load_component_library_rows
    from lab_model.optimization.kernels.registry import list_kernels

    lib_path = MOCK_LAB_VIEW / "component_library.json"
    rows = load_component_library_rows(str(lib_path))
    state_path = MOCK_LAB_VIEW / "lab_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    active = sorted(str(t) for t in (state.get("components") or {}).keys())

    reg = json.loads(BACKENDS_SCHEMA.read_text(encoding="utf-8"))
    mock = next(
        (b for b in (reg.get("backends") or []) if b.get("backend_id") == MOCK_BACKEND),
        {"backend_id": MOCK_BACKEND, "label": "Mock bench (default)"},
    )
    mock = {
        **mock,
        "availability": "ready",
        "health": "ok",
        "communicator": "mock",
        "lab_mode": "MOCK",
        "component_count": len(active),
        "control_repos": ["default", "experiment", "laser-cavity"],
        "edge_attached": False,
        "session_lease": None,
        "queued_jobs": 0,
        "system_status": state.get("system_status") or "IDLE",
        "image": "backends-images/mock-default.png",
    }

    # Prefer API enrichment when coordinator is up; otherwise disk is enough.
    api_backends = try_json("/api/backends", None)
    if isinstance(api_backends, dict):
        for b in api_backends.get("backends") or []:
            if b.get("backend_id") == MOCK_BACKEND:
                # Keep local image path for file://; merge runtime status fields.
                img = mock["image"]
                mock = {**mock, **b, "image": img}
                break
    api_rows = try_json(f"/api/catalog/library-rows?backend_id={MOCK_BACKEND}", None)
    if isinstance(api_rows, list) and api_rows:
        rows = api_rows
    elif isinstance(api_rows, dict) and isinstance(api_rows.get("rows"), list) and api_rows["rows"]:
        rows = api_rows["rows"]
    api_tags = try_json(f"/api/catalog/active-tags?backend_id={MOCK_BACKEND}", None)
    if isinstance(api_tags, dict) and api_tags.get("tags"):
        active = sorted(str(t) for t in api_tags["tags"])

    kernels = [k.to_api_dict() for k in list_kernels(lab_view_path=str(MOCK_LAB_VIEW))]
    api_kern = try_json("/api/kernels", None)
    if isinstance(api_kern, list) and api_kern:
        kernels = [k for k in api_kern if isinstance(k, dict)]
    elif isinstance(api_kern, dict):
        kl = api_kern.get("kernels") or api_kern.get("items") or []
        if isinstance(kl, list) and kl:
            kernels = [k for k in kl if isinstance(k, dict)]

    return {
        "backend": mock,
        "rows": rows if isinstance(rows, list) else [],
        "active": active,
        "kernels": kernels,
        "source": "disk+optional-api",
    }


def write_backends_review(out_path: Path, payload: dict) -> str:
    """Write full-text mock.default review MD (appendix / source dump)."""
    mock = payload["backend"]
    rows = payload["rows"]
    active = set(payload["active"])
    klist = payload["kernels"]

    lines: list[str] = []
    lines.append(f"# Backends hub snapshot — `{MOCK_BACKEND}`")
    lines.append("")
    lines.append(
        "Static dump of the live Wiki **Backends** hub for the teaching simulator "
        f"**`{MOCK_BACKEND}`**: gallery card fields, overview, every catalog component, "
        "and kernels. No live server required to read this pack."
    )
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- **Label:** {mock.get('label') or MOCK_BACKEND}")
    lines.append(f"- **Availability:** `{mock.get('availability')}`")
    if mock.get("description"):
        lines.append(f"- **Description:** {mock['description']}")
    lines.append(f"- **Communicator / mode:** `{mock.get('communicator') or mock.get('lab_mode') or 'mock'}`")
    lines.append(f"- **Component count:** {mock.get('component_count', len(active))}")
    repos = mock.get("control_repos") or []
    lines.append(f"- **Control repos:** {', '.join(repos) if repos else '—'}")
    lines.append("")
    lines.append("```python")
    lines.append("from cloudlabs import connect")
    lines.append("")
    lines.append(f'with connect("{MOCK_BACKEND}") as lab:')
    lines.append("    ...")
    lines.append("```")
    lines.append("")
    lines.append("## Components (index)")
    lines.append("")
    lines.append("| Tag | Name | Type | On bench |")
    lines.append("|-----|------|------|----------|")
    for row in rows:
        tag = str(row.get("tag_id") or "")
        lines.append(
            f"| `{tag}` | {row.get('name') or tag} | `{row.get('type') or '—'}` | "
            f"{'on bench' if tag in active else 'library'} |"
        )
    lines.append("")

    for row in rows:
        tag = str(row.get("tag_id") or "")
        name = str(row.get("name") or tag)
        typ = str(row.get("type") or "—")
        on = "on bench" if tag in active else "library only"
        caps = _normalize_caps(row.get("capabilities"))
        tun = (caps["statecontrol"].get("tunables") or {}) if isinstance(caps["statecontrol"], dict) else {}
        meas = (caps["statecontrol"].get("measurables") or {}) if isinstance(caps["statecontrol"], dict) else {}
        prims = caps.get("primitives") or []
        if not isinstance(tun, dict):
            tun = {}
        if not isinstance(meas, dict):
            meas = {}

        lines.append(f"## Component: {name}")
        lines.append("")
        lines.append(f"- **Tag:** `{tag}`")
        lines.append(f"- **Type:** `{typ}`")
        lines.append(f"- **Presence:** {on}")
        lines.append("")
        lines.append("### Physical interpretation")
        lines.append("")
        lines.append(_phys_component(row))
        lines.append("")
        lines.append(f"### Tunables ({sum(1 for f in tun if f not in SKIP_TUN)})")
        lines.append("")
        shown_t = 0
        for field, decl in tun.items():
            if field in SKIP_TUN:
                continue
            decl = decl if isinstance(decl, dict) else {}
            shown_t += 1
            widget = decl.get("widget") or "—"
            unit = decl.get("unit") or ("deg" if "motor" in field or field.endswith("rotation") else "—")
            lines.append(f"#### `tunables.{field}`")
            lines.append("")
            lines.append(f"- **Widget:** `{widget}` · **Unit:** {unit}")
            if field == "nominal_pose":
                for axis, u in (("x", "mm"), ("y", "mm"), ("rotation", "deg")):
                    tpath = f"tunables.nominal_pose.{axis}"
                    lines.append(
                        f"  - `{tpath}` ({u}): "
                        f'`lab.move_component("{tag}", "{tpath}", <value>)`'
                    )
            elif field == "nominal_motor_positions":
                mids = row.get("motor_ids") or ["<motor_id>"]
                for mid in mids:
                    tpath = f"tunables.nominal_motor_positions.{mid}"
                    lines.append(
                        f"  - `{tpath}`: "
                        f'`lab.move_component("{tag}", "{tpath}", <value>)`'
                    )
            lines.append("")
        if not shown_t:
            lines.append("_No tunables declared._")
            lines.append("")

        meas_items = [(f, d) for f, d in meas.items() if f not in SKIP_MEAS]
        lines.append(f"### Measurables ({len(meas_items)})")
        lines.append("")
        if not meas_items:
            lines.append("_No measurables declared._")
            lines.append("")
        for field, decl in meas_items:
            decl = decl if isinstance(decl, dict) else {}
            lines.append(f"#### `measurables.{field}`")
            lines.append("")
            lines.append(f"- **What it measures:** {_meas_interp(field, decl)}")
            lines.append("- **Tensor:**")
            lines.append("```")
            lines.append(_tensor_text(field, decl))
            lines.append("```")
            lines.append(
                f'- **Script:** `lab.measurable("{tag}", "{field}").resolve(record=True)`'
            )
            lines.append("")

        lines.append(f"### Primitives ({len(prims)})")
        lines.append("")
        if not prims:
            lines.append("_No primitives declared on this row._")
        else:
            for p in prims:
                lines.append(f"- `{p}`")
        lines.append("")

    lines.append("## Kernels")
    lines.append("")
    if not klist:
        lines.append("_No kernels in this snapshot._")
    else:
        for k in klist:
            if not isinstance(k, dict):
                continue
            kid = str(k.get("id") or "")
            lines.append(f"### `{kid}`")
            lines.append("")
            if k.get("label"):
                lines.append(f"- **Label:** {k['label']}")
            if k.get("runtime") or k.get("backend"):
                lines.append(f"- **Runtime:** `{k.get('runtime') or k.get('backend')}`")
            if k.get("description"):
                lines.append(f"- **Description:** {k['description']}")
            lines.append("")

    text = "\n".join(lines) + "\n"
    out_path.write_text(text, encoding="utf-8")
    return text


def _comp_detail_html(row: dict, active: set, backend_id: str) -> str:
    tag = str(row.get("tag_id") or "")
    name = str(row.get("name") or tag)
    typ = str(row.get("type") or "")
    on = tag in active
    badge = "on-bench" if on else "library"
    badge_t = "on bench" if on else "library only"
    caps = _normalize_caps(row.get("capabilities"))
    tun = caps["statecontrol"].get("tunables") or {}
    meas = caps["statecontrol"].get("measurables") or {}
    prims = caps.get("primitives") or []
    if not isinstance(tun, dict):
        tun = {}
    if not isinstance(meas, dict):
        meas = {}

    tun_rows = []
    for field, decl in tun.items():
        if field in SKIP_TUN:
            continue
        decl = decl if isinstance(decl, dict) else {}
        widget = decl.get("widget") or "—"
        if field == "nominal_pose":
            for axis, unit in (("x", "mm"), ("y", "mm"), ("rotation", "deg")):
                tpath = f"tunables.nominal_pose.{axis}"
                snip = f'lab.move_component("{tag}", "{tpath}", <value>)'
                note = "Pose tunable. Twin ghost is a visual draft before you confirm; scan/observe recalculates this same field."
                tun_rows.append((tpath, widget, unit, snip, note))
        elif field == "nominal_motor_positions":
            mids = row.get("motor_ids") or ["<id>"]
            for mid in mids:
                tpath = f"tunables.nominal_motor_positions.{mid}"
                snip = f'lab.move_component("{tag}", "{tpath}", <value>)'
                note = "Lab observe / tracker recalculates this same tunable - not a measurable."
                tun_rows.append((tpath, widget, decl.get("unit") or "deg", snip, note))
        else:
            tpath = f"tunables.{field}"
            tun_rows.append(
                (
                    tpath,
                    widget,
                    str(decl.get("unit") or "—"),
                    f"# {tpath}",
                    str(decl.get("physical_interpretation") or ""),
                )
            )

    meas_rows = []
    for field, decl in meas.items():
        if field in SKIP_MEAS:
            continue
        decl = decl if isinstance(decl, dict) else {}
        meas_rows.append(
            (
                f"measurables.{field}",
                _meas_interp(field, decl),
                _tensor_text(field, decl),
                str(decl.get("domain") or TENSOR_SPEC.get(field, {}).get("domain") or "—"),
                f'lab.measurable("{tag}", "{field}").resolve(record=True)',
            )
        )

    params = []
    props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
    for key, value in props.items():
        params.append((f"properties.{key}", value))
    for key in ("motor_ids", "motor_controller", "height_mm", "size", "id", "type"):
        if row.get(key) not in (None, "", [], {}):
            params.append((key, row[key]))

    tun_table = (
        "<table><thead><tr><th>Path</th><th>Widget</th><th>Unit</th>"
        "<th>Script handle</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td class='mono'>{html.escape(p)}</td><td>{html.escape(w)}</td>"
            f"<td>{html.escape(str(u))}</td><td><pre class='snippet'>{html.escape(s)}</pre>"
            f"{('<div class=\"empty\">' + html.escape(n) + '</div>') if n else ''}</td></tr>"
            for p, w, u, s, n in tun_rows
        )
        + "</tbody></table>"
        if tun_rows
        else '<div class="empty">No tunables declared.</div>'
    )
    meas_table = (
        "<table><thead><tr><th>Path</th><th>What it measures</th>"
        "<th>Tensor</th><th>Domain</th><th>Script</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td class='mono'>{html.escape(p)}</td><td>{html.escape(w)}</td>"
            f"<td class='mono' style='white-space:pre-line;font-size:11px'>{html.escape(t)}</td>"
            f"<td>{html.escape(d)}</td><td><pre class='snippet'>{html.escape(s)}</pre></td></tr>"
            for p, w, t, d, s in meas_rows
        )
        + "</tbody></table>"
        if meas_rows
        else '<div class="empty">No measurables declared.</div>'
    )
    params_table = (
        "<table><thead><tr><th>Key</th><th>Value</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td class='mono'>{html.escape(k)}</td>"
            f"<td class='mono'>{html.escape(json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v)}</td></tr>"
            for k, v in params
        )
        + "</tbody></table>"
        if params
        else '<div class="empty">No parameters listed.</div>'
    )
    prim_html = (
        '<div class="primitive-chip-grid">'
        + "".join(f'<code class="primitive-chip">{html.escape(str(p))}</code>' for p in prims)
        + "</div>"
        if prims
        else '<div class="empty">No primitives declared.</div>'
    )

    return (
        f'<div class="hub-comp-detail" id="hub-comp-{html.escape(tag)}" hidden>'
        f'<div class="detail-header"><h2>{html.escape(name)}</h2>'
        f'<code>{html.escape(tag)}</code>'
        f'<span class="badge {badge}">{badge_t}</span>'
        f'<code>{html.escape(typ)}</code></div>'
        f'<section class="section"><h3>Physical interpretation</h3>'
        f'<p class="connect-hint">{html.escape(_phys_component(row))}</p></section>'
        f'<p class="connect-hint">Backend <code>{html.escape(backend_id)}</code></p>'
        f'<section class="section"><h3>Tunables ({len(tun_rows)})</h3>{tun_table}</section>'
        f'<section class="section"><h3>Measurables ({len(meas_rows)})</h3>{meas_table}</section>'
        f'<section class="section"><h3>Parameters ({len(params)})</h3>{params_table}</section>'
        f'<section class="section"><h3>Primitives ({len(prims)})</h3>{prim_html}</section>'
        f"</div>"
    )


def mock_hero_src() -> tuple[str, Path | None]:
    """Return (img src for HTML, source file path to copy into the pack).

    Prefer an inlined data URI so file:// opens always show the hero even if
    relative paths break; still copy the PNG into backends-images/ for the zip.
    """
    png = BACKENDS_IMG / "mock-default.png"
    svg = BACKENDS_IMG / "mock-default.svg"
    if png.exists():
        b64 = base64.b64encode(png.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{b64}", png
    if svg.exists():
        b64 = base64.b64encode(svg.read_bytes()).decode("ascii")
        return f"data:image/svg+xml;base64,{b64}", svg
    return "backends-images/mock-default.png", None


def render_backends_hub_html(payload: dict, *, img_src: str) -> str:
    """Pre-rendered Wiki Backends hub (gallery + mock.default drill-in)."""
    mock = payload["backend"]
    rows = payload["rows"]
    active = set(payload["active"])
    kernels = payload["kernels"]
    bid = MOCK_BACKEND
    label = str(mock.get("label") or bid)
    desc = str(mock.get("description") or "")
    status = str(mock.get("availability") or "ready")
    img = img_src

    # Component list + details
    comp_btns = []
    comp_details = []
    for row in sorted(rows, key=lambda r: str(r.get("tag_id") or "")):
        tag = str(row.get("tag_id") or "")
        name = str(row.get("name") or tag)
        typ = str(row.get("type") or "")
        on = tag in active
        badge = "on-bench" if on else "library"
        badge_t = "on bench" if on else "library"
        comp_btns.append(
            f'<button type="button" class="comp-item" data-hub-tag="{html.escape(tag)}">'
            f'<span class="name">{html.escape(name)} '
            f'<span class="badge {badge}">{badge_t}</span></span>'
            f'<span class="meta">{html.escape(tag)} · {html.escape(typ)}</span></button>'
        )
        comp_details.append(_comp_detail_html(row, active, bid))

    kern_btns = []
    kern_details = []
    for k in kernels:
        kid = str(k.get("id") or "")
        klabel = str(k.get("label") or kid)
        runtime = str(k.get("runtime") or k.get("backend") or "")
        kern_btns.append(
            f'<button type="button" class="comp-item" data-hub-kernel="{html.escape(kid)}">'
            f'<span class="name">{html.escape(klabel)}</span>'
            f'<span class="meta">{html.escape(kid)} · {html.escape(runtime)}</span></button>'
        )
        kdesc = k.get("physical_interpretation") or k.get("description") or "—"
        kern_details.append(
            f'<div class="hub-kern-detail" id="hub-kern-{html.escape(kid)}" hidden>'
            f'<div class="detail-header"><h2>{html.escape(klabel)}</h2>'
            f'<code>{html.escape(kid)}</code></div>'
            f'<section class="section"><h3>Physical interpretation</h3>'
            f'<p class="connect-hint">{html.escape(str(kdesc))}</p></section>'
            f'<section class="section"><h3>Probe</h3>'
            f'<pre class="snippet">lab.probe_kernel("tag_22", "camera_image", '
            f'kernel_id="{html.escape(kid)}")</pre></section></div>'
        )

    overview_dl = f"""
            <dl class="backend-overview-dl">
                <div><dt>Communicator</dt><dd>{html.escape(str(mock.get('communicator') or 'mock'))} ({html.escape(str(mock.get('lab_mode') or 'MOCK'))})</dd></div>
                <div><dt>Health</dt><dd>{html.escape(str(mock.get('health') or mock.get('availability') or '—'))}</dd></div>
                <div><dt>System</dt><dd>{html.escape(str(mock.get('system_status') or '—'))}</dd></div>
                <div><dt>Components</dt><dd>{html.escape(str(mock.get('component_count', len(active))))}</dd></div>
                <div><dt>Control repos</dt><dd>{html.escape(', '.join(mock.get('control_repos') or []) or '—')}</dd></div>
                <div><dt>Edge</dt><dd>{'Attached' if mock.get('edge_attached') else 'Not attached'}</dd></div>
                <div><dt>Session lease</dt><dd>None</dd></div>
                <div><dt>Queued jobs</dt><dd>{html.escape(str(mock.get('queued_jobs') if mock.get('queued_jobs') is not None else 0))}</dd></div>
            </dl>
            <p class="backend-cta-row">
                <button type="button" class="backend-cta ghost" data-hub-goto="components">Browse components</button>
                <button type="button" class="backend-cta ghost" data-hub-goto="kernels">Browse kernels</button>
            </p>"""

    return f"""
<div id="backends-hub" hidden>
  <div class="backends-hub-inner">
    <div id="backends-gallery">
      <div class="backends-gallery-intro">
        <p class="eyebrow">Offline pack · mock.default</p>
        <h2>Backends</h2>
        <p class="lede">Pick the mock lab to explore components and kernels — same layout as the live Wiki Backends hub. All catalog data is baked into this zip.</p>
      </div>
      <div class="backend-card-grid">
        <button type="button" class="backend-card" data-open-mock style="animation-delay:0ms">
          <div class="backend-card-media hero-mock">
            <img src="{html.escape(img)}" alt="" data-hero="mock" />
            <span class="backend-card-glow" aria-hidden="true"></span>
          </div>
          <div class="backend-card-body">
            <div class="backend-card-top">
              <h3>{html.escape(label)}</h3>
              <span class="backend-pill ready">{html.escape(status)}</span>
            </div>
            <code class="backend-id">{html.escape(bid)}</code>
            <p class="backend-desc">{html.escape(desc)}</p>
            <p class="backend-meta">Idle — available (offline snapshot)</p>
          </div>
        </button>
      </div>
    </div>

    <div id="backends-detail" hidden>
      <button type="button" class="backend-back" data-hub-back>← All backends</button>
      <header class="backend-hero">
        <div class="backend-hero-media hero-mock">
          <img src="{html.escape(img)}" alt="" data-hero="mock" />
          <span class="backend-card-glow" aria-hidden="true"></span>
        </div>
        <div class="backend-hero-copy">
          <span class="backend-pill ready">{html.escape(status)}</span>
          <h2>{html.escape(label)}</h2>
          <code>{html.escape(bid)}</code>
          <p>{html.escape(desc)}</p>
        </div>
      </header>
      <div class="backend-tabs" role="tablist">
        <button type="button" role="tab" data-hub-tab="overview" class="active">Overview</button>
        <button type="button" role="tab" data-hub-tab="components">Components</button>
        <button type="button" role="tab" data-hub-tab="kernels">Kernels</button>
        <button type="button" role="tab" data-hub-tab="snapshots">Snapshots</button>
      </div>
      <div class="backend-tab-panels">
        <div id="backend-panel-overview" class="backend-panel">{overview_dl}</div>
        <div id="backend-panel-components" class="backend-panel backend-panel-catalog" hidden>
          <div class="backend-catalog-layout">
            <aside class="backend-catalog-side">
              <div class="comp-list">{''.join(comp_btns)}</div>
            </aside>
            <div class="backend-catalog-detail">
              <p class="empty" id="hub-comp-placeholder">Select a component from the left.</p>
              {''.join(comp_details)}
            </div>
          </div>
        </div>
        <div id="backend-panel-kernels" class="backend-panel backend-panel-catalog" hidden>
          <div class="backend-catalog-layout">
            <aside class="backend-catalog-side">
              <div class="comp-list">{''.join(kern_btns)}</div>
            </aside>
            <div class="backend-catalog-detail">
              <p class="empty" id="hub-kern-placeholder">Select a kernel from the left.</p>
              {''.join(kern_details)}
            </div>
          </div>
        </div>
        <div id="backend-panel-snapshots" class="backend-panel" hidden>
          <p class="empty">Snapshots are live coordinator state — not included in this offline pack.</p>
        </div>
      </div>
    </div>
  </div>
</div>
"""


def extract_wiki_css() -> str:
    raw = FRONT_WIKI.read_text(encoding="utf-8")
    m = re.search(r"<style>(.*?)</style>", raw, re.S)
    if not m:
        raise SystemExit("Could not extract <style> from wiki.html")
    css = m.group(1)
    # drop backends-hub-only bulk if huge — keep guide + chrome styles
    return css


def build_index(
    chapters: list[dict],
    chapter_html: dict[str, str],
    backends_hub_html: str,
) -> str:
    css = extract_wiki_css()
    nav_btns = []
    for ch in chapters:
        nav_btns.append(
            f'<button type="button" class="guide-item" data-chapter="{html.escape(ch["id"])}">'
            f'{html.escape(ch["title"])}</button>'
        )

    articles = []
    for ch in chapters:
        cid = ch["id"]
        articles.append(
            f'<article class="guide-article chapter" id="chapter-{html.escape(cid)}" hidden>'
            f"{chapter_html[cid]}</article>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Cloud Labs | Wiki (offline pack)</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
{css}
.offline-banner {{
  position: relative; z-index: 5;
  padding: 8px 24px;
  background: rgba(14,165,233,0.14);
  border-bottom: 1px solid rgba(56,189,248,0.35);
  font-size: 13px; color: #e2e8f0;
}}
.guide-item {{ display: block; width: 100%; }}
.chapter[hidden] {{ display: none !important; }}
.layout {{ min-height: calc(100vh - 140px); }}
.layout[hidden] {{ display: none !important; }}
header nav a {{ pointer-events: none; opacity: 0.7; }}
#backends-hub[hidden] {{ display: none !important; }}
#backends-detail[hidden] {{ display: none !important; }}
.backend-panel[hidden],
.hub-comp-detail[hidden],
.hub-kern-detail[hidden] {{ display: none !important; }}
.backend-card {{ font: inherit; }}
</style>
</head>
<body>
<div class="wiki-atmosphere" aria-hidden="true"></div>
<div class="offline-banner">
  <strong>Cloud Labs Wiki — offline pack</strong>
  · Open <code>index.html</code> from this folder (no server)
  · Learn + static <strong>Backends</strong> hub for <code>mock.default</code>
</div>
<header>
  <div>
    <h1>Cloud Labs · Wiki</h1>
    <p class="sub">Learn the lab language — then open Backends for the mock bench catalog.</p>
  </div>
  <nav>
    <a href="#">Twin UI</a>
    <a href="#">Operations</a>
    <a href="#">Backends</a>
    <a href="#" class="active">Wiki</a>
  </nav>
</header>
<div class="section-bar" aria-label="Wiki sections">
  <button type="button" class="section-btn active" id="btn-learn">Learn</button>
  <button type="button" class="section-btn" id="btn-backends">Backends</button>
</div>

{backends_hub_html}

<div class="layout" id="learn-layout">
  <aside class="sidebar">
    <div>
      <label id="guide-list-label">Chapters</label>
      <div class="comp-list" id="guide-list">
        {''.join(nav_btns)}
      </div>
    </div>
  </aside>
  <main id="detail">
    {''.join(articles)}
  </main>
</div>
<script>
(function () {{
  const items = Array.from(document.querySelectorAll('[data-chapter]'));
  const chapters = Array.from(document.querySelectorAll('.chapter'));
  const btnLearn = document.getElementById('btn-learn');
  const btnBack = document.getElementById('btn-backends');
  const learnLayout = document.getElementById('learn-layout');
  const hub = document.getElementById('backends-hub');
  const gallery = document.getElementById('backends-gallery');
  const detail = document.getElementById('backends-detail');

  function showLearn(id) {{
    if (hub) hub.hidden = true;
    if (learnLayout) learnLayout.hidden = false;
    btnLearn.classList.add('active');
    btnBack.classList.remove('active');
    chapters.forEach((el) => {{
      el.hidden = el.id !== 'chapter-' + id;
    }});
    items.forEach((btn) => {{
      btn.classList.toggle('active', btn.getAttribute('data-chapter') === id);
    }});
    window.scrollTo(0, 0);
  }}

  function showBackends() {{
    if (learnLayout) learnLayout.hidden = true;
    if (hub) hub.hidden = false;
    btnLearn.classList.remove('active');
    btnBack.classList.add('active');
    window.scrollTo(0, 0);
  }}

  items.forEach((btn) => {{
    btn.addEventListener('click', () => showLearn(btn.getAttribute('data-chapter')));
  }});
  btnLearn.addEventListener('click', () => showLearn(items[0].getAttribute('data-chapter')));
  btnBack.addEventListener('click', showBackends);

  // Backends hub interactions (static; no network)
  function showHubTab(name) {{
    const map = {{
      overview: document.getElementById('backend-panel-overview'),
      components: document.getElementById('backend-panel-components'),
      kernels: document.getElementById('backend-panel-kernels'),
      snapshots: document.getElementById('backend-panel-snapshots'),
    }};
    Object.keys(map).forEach((k) => {{
      if (!map[k]) return;
      if (k === 'components' || k === 'kernels') {{
        // separate panels in offline pack
        map[k].hidden = k !== name;
      }} else {{
        map[k].hidden = k !== name;
      }}
    }});
    document.querySelectorAll('[data-hub-tab]').forEach((b) => {{
      b.classList.toggle('active', b.getAttribute('data-hub-tab') === name);
    }});
    if (name === 'components') {{
      const first = document.querySelector('[data-hub-tag]');
      if (first && !document.querySelector('[data-hub-tag].active')) first.click();
    }}
    if (name === 'kernels') {{
      const first = document.querySelector('[data-hub-kernel]');
      if (first && !document.querySelector('[data-hub-kernel].active')) first.click();
    }}
  }}

  document.querySelector('[data-open-mock]')?.addEventListener('click', () => {{
    if (gallery) gallery.hidden = true;
    if (detail) detail.hidden = false;
    showHubTab('overview');
  }});
  document.querySelector('[data-hub-back]')?.addEventListener('click', () => {{
    if (detail) detail.hidden = true;
    if (gallery) gallery.hidden = false;
  }});
  document.querySelectorAll('[data-hub-tab]').forEach((btn) => {{
    btn.addEventListener('click', () => showHubTab(btn.getAttribute('data-hub-tab')));
  }});
  document.querySelectorAll('[data-hub-goto]').forEach((btn) => {{
    btn.addEventListener('click', () => showHubTab(btn.getAttribute('data-hub-goto')));
  }});
  document.querySelectorAll('[data-hub-tag]').forEach((btn) => {{
    btn.addEventListener('click', () => {{
      const tag = btn.getAttribute('data-hub-tag');
      document.querySelectorAll('[data-hub-tag]').forEach((b) => b.classList.toggle('active', b === btn));
      const ph = document.getElementById('hub-comp-placeholder');
      if (ph) ph.hidden = true;
      document.querySelectorAll('.hub-comp-detail').forEach((el) => {{
        el.hidden = el.id !== 'hub-comp-' + tag;
      }});
    }});
  }});
  document.querySelectorAll('[data-hub-kernel]').forEach((btn) => {{
    btn.addEventListener('click', () => {{
      const kid = btn.getAttribute('data-hub-kernel');
      document.querySelectorAll('[data-hub-kernel]').forEach((b) => b.classList.toggle('active', b === btn));
      const ph = document.getElementById('hub-kern-placeholder');
      if (ph) ph.hidden = true;
      document.querySelectorAll('.hub-kern-detail').forEach((el) => {{
        el.hidden = el.id !== 'hub-kern-' + kid;
      }});
    }});
  }});

  // Start on Learn; hub present but hidden until Backends is clicked
  if (hub) hub.hidden = true;
  showLearn(items[0].getAttribute('data-chapter'));
}})();
</script>
</body>
</html>
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    guides_out = OUT / "guides"
    guides_out.mkdir(parents=True, exist_ok=True)
    figures_out = guides_out / "figures"
    shutil.copytree(GUIDES / "figures", figures_out, dirs_exist_ok=True)
    shutil.copy2(GUIDES / "manifest.json", guides_out / "manifest.json")

    for md in GUIDES.glob("*.md"):
        shutil.copy2(md, guides_out / md.name)

    img_out = OUT / "backends-images"
    img_out.mkdir(exist_ok=True)
    img_src, mock_img = mock_hero_src()
    if mock_img is not None:
        # Always ship the binary in the zip (PNG preferred, SVG fallback).
        dest_name = "mock-default.png" if mock_img.suffix.lower() == ".png" else "mock-default.svg"
        shutil.copy2(mock_img, img_out / dest_name)
        print("packed hero", img_out / dest_name, "bytes", (img_out / dest_name).stat().st_size)
    else:
        print("warn: no mock-default.png/.svg under frontend/wiki/backends/")

    # Bake mock catalog from disk (no live server required)
    payload = load_mock_snapshot()
    data_out = OUT / "data"
    data_out.mkdir(exist_ok=True)
    (data_out / "mock-default.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    # Raw library copy for advisors who want the source file
    lib_src = MOCK_LAB_VIEW / "component_library.json"
    if lib_src.exists():
        shutil.copy2(lib_src, data_out / "mock-component_library.json")

    backends_md_path = guides_out / "backends-review.md"
    write_backends_review(backends_md_path, payload)

    manifest = json.loads((guides_out / "manifest.json").read_text(encoding="utf-8"))
    learn = next(s for s in manifest["sections"] if s["id"] == "learn")
    chapters = learn["chapters"]

    chapter_html: dict[str, str] = {}
    for ch in chapters:
        md_path = guides_out / ch["file"]
        md = md_path.read_text(encoding="utf-8")
        chapter_html[ch["id"]] = render_markdown(md, figure_prefix="guides/figures/")

    hub_html = render_backends_hub_html(payload, img_src=img_src)

    (OUT / "index.html").write_text(
        build_index(chapters, chapter_html, hub_html),
        encoding="utf-8",
    )

    (OUT / "README.txt").write_text(
        """Cloud Labs Wiki — offline HTML pack
====================================

Open index.html from this folder (double-click is fine).
No local server and no localhost connection required.

Contents
--------
- index.html                 Learn + Backends hub UI (static)
- guides/*.md                Learn markdown sources
- guides/backends-review.md  Full mock.default text dump
- guides/figures/            Diagram SVGs
- backends-images/           Mock bench hero PNG (also inlined in index.html)
- data/mock-default.json     Baked catalog + kernels snapshot
- data/mock-component_library.json  Raw mock component library

Backends section
----------------
Matches the live Wiki Backends page layout:
  gallery card (hero image) → mock.default → Overview / Components / Kernels
All mock catalog detail is pre-rendered in the HTML. No API fetch.
The mock hero is both a file under backends-images/ and embedded in the HTML
so it shows even when browsing file:// with broken relative paths.

Regenerate
----------
  python scripts/ops/build_wiki_offline_html_zip.py

(Server optional — builder reads mock lab_view from disk.)
""",
        encoding="utf-8",
    )

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in OUT.rglob("*"):
            if path.is_file():
                zf.write(path, Path("cloudlabs-wiki-offline") / path.relative_to(OUT))

    print("OUT", OUT)
    print("ZIP", ZIP_PATH, "MB", round(ZIP_PATH.stat().st_size / 1e6, 2))
    print("files", sum(1 for p in OUT.rglob("*") if p.is_file()))
    print("components", len(payload["rows"]), "kernels", len(payload["kernels"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
