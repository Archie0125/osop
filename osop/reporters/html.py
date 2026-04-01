"""OSOP HTML Report Generator.

Zero emoji, zero JS, zero icons.
5-color system, <details>/<summary> expand/collapse, dark mode via CSS.
Target output <15KB.
"""
from __future__ import annotations

import json
from typing import Any

import yaml


# --- 5-color system ---

TYPE_COLOR: dict[str, str] = {
    "human": "#ea580c", "agent": "#7c3aed",
    "api": "#2563eb", "mcp": "#2563eb", "cli": "#2563eb",
    "git": "#475569", "docker": "#475569", "cicd": "#475569",
    "system": "#475569", "infra": "#475569", "gateway": "#475569",
    "db": "#059669", "data": "#059669",
    "company": "#ea580c", "department": "#ea580c", "event": "#475569",
}


def _tc(t: str) -> str:
    return TYPE_COLOR.get(t, "#475569")


def _h(s: str) -> str:
    """HTML-escape a string."""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _ms(v: int | float | None) -> str:
    if v is None:
        return "-"
    if v < 1000:
        return f"{int(v)}ms"
    if v < 60000:
        return f"{v / 1000:.1f}s"
    return f"{v / 60000:.1f}m"


def _usd(v: float | None) -> str:
    if not v:
        return "$0"
    return f"${v:.4f}" if v < 0.01 else f"${v:.3f}"


def _kv_table(obj: Any) -> str:
    if not obj or not isinstance(obj, dict):
        return ""
    entries = list(obj.items())
    if not entries:
        return ""
    rows: list[str] = []
    for k, v in entries:
        val = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
        display = val[:97] + "..." if len(val) > 100 else val
        rows.append(f"<tr><td>{_h(str(k))}</td><td>{_h(display)}</td></tr>")
    return "<table>" + "".join(rows) + "</table>"


# --- CSS (minified inline) ---

CSS = (
    "*{margin:0;padding:0;box-sizing:border-box}"
    ":root{--ok:#16a34a;--err:#dc2626;--warn:#d97706;--bg:#fff;--fg:#1e293b;"
    "--mu:#64748b;--bd:#e2e8f0;--cd:#f8fafc}"
    "@media(prefers-color-scheme:dark){:root{--bg:#0f172a;--fg:#e2e8f0;"
    "--mu:#94a3b8;--bd:#334155;--cd:#1e293b}}"
    "body{font:14px/1.6 system-ui,sans-serif;background:var(--bg);color:var(--fg);"
    "max-width:800px;margin:0 auto;padding:16px}"
    "h1{font-size:1.4rem;font-weight:700}"
    ".st{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0}.st span{font-weight:600}"
    ".s{padding:2px 8px;border-radius:3px;color:#fff;font-size:12px}"
    ".s.ok{background:var(--ok)}.s.err{background:var(--err)}"
    ".desc{color:var(--mu);font-size:13px;margin:4px 0}"
    ".meta{font:11px monospace;color:var(--mu);margin:4px 0}"
    ".eb{background:#fef2f2;border:1px solid #fecaca;color:var(--err);"
    "padding:8px 12px;border-radius:6px;margin:12px 0;font-size:13px}"
    "@media(prefers-color-scheme:dark){.eb{background:#450a0a;border-color:#7f1d1d}}"
    ".n{border:1px solid var(--bd);border-radius:6px;margin:8px 0;overflow:hidden}"
    ".n summary{display:flex;align-items:center;gap:8px;padding:8px 12px;"
    "cursor:pointer;background:var(--cd);font-size:13px;list-style:none}"
    ".n summary::-webkit-details-marker{display:none}"
    ".n.er{border-left:3px solid var(--err)}"
    ".tp{color:#fff;padding:1px 6px;border-radius:3px;font-size:11px;"
    "font-weight:700;text-transform:uppercase;letter-spacing:.03em}"
    ".du{margin-left:auto;color:var(--mu);font-size:12px;font-family:monospace}"
    ".br{height:4px;border-radius:2px;display:inline-block;min-width:2px}"
    ".bd{padding:12px;font-size:13px;border-top:1px solid var(--bd)}"
    ".bd p{color:var(--mu);margin-bottom:8px}"
    ".bd table{width:100%;font-size:12px;border-collapse:collapse}"
    ".bd td{padding:3px 8px;border-bottom:1px solid var(--bd);vertical-align:top}"
    ".bd td:first-child{font-weight:600;color:var(--mu);width:30%;"
    "font-family:monospace;font-size:11px}"
    ".ai{font-size:12px;color:#7c3aed;margin-top:8px;font-family:monospace}"
    "@media(prefers-color-scheme:dark){.ai{color:#a78bfa}}"
    ".er-box{background:#fef2f2;color:var(--err);padding:8px;border-radius:4px;"
    "font-size:12px;margin-top:8px}"
    "@media(prefers-color-scheme:dark){.er-box{background:#450a0a}}"
    ".rt{font-size:12px;color:var(--ok);margin-top:4px}"
    "footer{text-align:center;padding:20px 0;color:var(--mu);font-size:11px}"
    "footer a{color:#2563eb}"
)


def generate_html_report(
    osop_yaml: str,
    osoplog_yaml: str | None = None,
    *,
    title: str | None = None,
) -> str:
    """Generate an HTML report from OSOP YAML (and optional log YAML).

    Args:
        osop_yaml: Raw YAML string of the .osop workflow spec.
        osoplog_yaml: Raw YAML string of the .osoplog execution log.
        title: Override report title.

    Returns:
        Complete HTML document as a string.
    """
    o: dict = yaml.safe_load(osop_yaml) or {}
    log: dict | None = yaml.safe_load(osoplog_yaml) if osoplog_yaml else None
    is_exec = log is not None
    report_title = title or o.get("name") or o.get("id") or "OSOP Report"

    # Build latest record per node
    latest: dict[str, dict] = {}
    failures: list[dict] = []
    if log and log.get("node_records"):
        for r in log["node_records"]:
            nid = r.get("node_id", "")
            prev = latest.get(nid)
            if not prev or r.get("attempt", 0) > prev.get("attempt", 0):
                latest[nid] = r
            if r.get("status") == "FAILED":
                failures.append(r)

    total_ms = log.get("duration_ms") if log else None
    body = ""

    # --- Header ---
    body += "<header>"
    body += f"<h1>{_h(report_title)}</h1>"
    body += '<div class="st">'
    if is_exec and log:
        sc = "ok" if log.get("status") == "COMPLETED" else "err"
        body += f'<span class="s {sc}">{_h(log.get("status", "UNKNOWN"))}</span>'
        body += f"<span>{_ms(log.get('duration_ms'))}</span>"
        cost_total = (log.get("cost") or {}).get("total_usd")
        if cost_total:
            body += f"<span>{_usd(cost_total)}</span>"
        body += f"<span>{len(latest)} nodes</span>"
    else:
        nodes = o.get("nodes") or []
        edges = o.get("edges") or []
        body += f"<span>{len(nodes)} nodes</span>"
        body += f"<span>{len(edges)} edges</span>"
        ver = o.get("version")
        if ver:
            body += f"<span>v{_h(str(ver))}</span>"
    body += "</div>"
    desc = o.get("description")
    if desc:
        body += f'<p class="desc">{_h(str(desc))}</p>'

    # Meta line
    meta_parts: list[str] = []
    if o.get("id"):
        meta_parts.append(str(o["id"]))
    if log:
        if log.get("run_id"):
            meta_parts.append("run:" + str(log["run_id"])[:8])
        if log.get("mode"):
            meta_parts.append(str(log["mode"]))
        runtime = log.get("runtime") or {}
        if runtime.get("agent"):
            meta_parts.append(str(runtime["agent"]))
        trigger = log.get("trigger") or {}
        if trigger.get("actor"):
            meta_parts.append(str(trigger["actor"]))
        started = log.get("started_at")
        if started:
            meta_parts.append(str(started).replace("T", " ").replace("Z", ""))
    if meta_parts:
        body += f'<div class="meta">{" &middot; ".join(_h(p) for p in meta_parts)}</div>'
    body += "</header>"

    # --- Error banner ---
    if failures:
        for f in failures:
            nid = f.get("node_id", "")
            l = latest.get(nid)
            retried_ok = (
                l is not None
                and l.get("status") == "COMPLETED"
                and l.get("attempt", 0) > f.get("attempt", 0)
            )
            err = f.get("error") or {}
            body += f'<div class="eb">{_h(nid)} failed: {_h(err.get("code", ""))} — {_h(err.get("message", "unknown"))}'
            if retried_ok:
                body += " — retried ok"
            body += "</div>"

    # --- Nodes ---
    body += "<main>"
    nodes = o.get("nodes") or []
    sorted_nodes = sorted(nodes, key=lambda n: (
        0 if latest.get(n.get("id", ""), {}).get("status") == "FAILED" else 1
    ))

    all_records = log.get("node_records", []) if log else []

    for node in sorted_nodes:
        nid = node.get("id", "")
        ntype = node.get("type", "system")
        nname = node.get("name", nid)
        ndesc = node.get("description", "")
        rec = latest.get(nid)
        node_recs = [r for r in all_records if r.get("node_id") == nid]
        is_failed = rec is not None and rec.get("status") == "FAILED"
        has_retry = len(node_recs) > 1
        cls = "n er" if is_failed else "n"
        open_attr = " open" if is_failed else ""

        body += f'<details class="{cls}"{open_attr}>'
        body += "<summary>"
        body += f'<span class="tp" style="background:{_tc(ntype)}">{_h(ntype.upper())}</span>'
        body += f"<strong>{_h(nname)}</strong>"
        if rec:
            body += f'<span class="du">{_ms(rec.get("duration_ms"))}</span>'
            status = rec.get("status", "")
            if status == "COMPLETED":
                pct = max(1, round((rec.get("duration_ms") or 0) / total_ms * 100)) if total_ms else 0
                body += f'<span class="br" style="width:{pct}%;background:var(--ok)"></span>'
            elif status == "FAILED":
                body += '<span class="s err">FAILED</span>'
            else:
                body += f'<span class="s ok">{_h(status)}</span>'
        body += "</summary>"

        body += '<div class="bd">'
        if ndesc:
            body += f"<p>{_h(ndesc)}</p>"

        # Inputs/Outputs
        inputs = (rec or {}).get("inputs") or node.get("inputs")
        outputs = (rec or {}).get("outputs") or node.get("outputs")
        if isinstance(inputs, dict):
            body += _kv_table(inputs)
        if isinstance(outputs, dict):
            body += _kv_table(outputs)

        # AI metadata
        ai = (rec or {}).get("ai_metadata")
        if ai and isinstance(ai, dict):
            parts: list[str] = []
            if ai.get("model"):
                parts.append(str(ai["model"]))
            pt = ai.get("prompt_tokens")
            ct = ai.get("completion_tokens", 0)
            if pt is not None:
                parts.append(f"{pt:,}->{ct:,} tok")
            if ai.get("cost_usd"):
                parts.append(_usd(ai["cost_usd"]))
            conf = ai.get("confidence")
            if conf is not None:
                parts.append(f"{conf * 100:.0f}%")
            if parts:
                body += f'<div class="ai">{" &middot; ".join(_h(p) for p in parts)}</div>'

        # Human metadata
        hm = (rec or {}).get("human_metadata")
        if hm and isinstance(hm, dict):
            parts = []
            if hm.get("actor"):
                parts.append(str(hm["actor"]))
            if hm.get("decision"):
                parts.append("decision=" + str(hm["decision"]))
            if hm.get("notes"):
                parts.append(str(hm["notes"]))
            if parts:
                body += (
                    '<div style="font-size:12px;color:var(--mu);margin-top:4px">'
                    + " &middot; ".join(_h(p) for p in parts)
                    + "</div>"
                )

        # Tools used
        tools = (rec or {}).get("tools_used")
        if tools and isinstance(tools, list):
            tool_strs = [_h(str(t.get("tool", ""))) + "x" + str(t.get("calls", 0)) for t in tools]
            body += f'<div style="font-size:12px;color:var(--mu);margin-top:4px">{", ".join(tool_strs)}</div>'

        # Error
        err = (rec or {}).get("error")
        if err and isinstance(err, dict):
            body += f'<div class="er-box">{_h(err.get("code", ""))}: {_h(err.get("message", ""))}'
            if err.get("details"):
                body += f"<br>{_h(str(err['details']))}"
            body += "</div>"

        # Retry history
        if has_retry:
            for r in node_recs:
                if r is rec:
                    continue
                body += (
                    f'<div class="rt">Attempt {r.get("attempt", "?")}: '
                    f'{_h(r.get("status", ""))} {_ms(r.get("duration_ms"))}'
                )
                r_err = r.get("error")
                if r_err:
                    body += f' — {_h(r_err.get("code", ""))}'
                body += "</div>"

        body += "</div></details>"

    body += "</main>"

    # --- Summary ---
    if log and log.get("result_summary"):
        body += (
            '<p style="margin:16px 0;padding:12px;background:var(--cd);'
            'border-radius:6px;font-size:13px;color:var(--mu)">'
            + _h(str(log["result_summary"]))
            + "</p>"
        )

    body += '<footer>OSOP v1.0 &middot; <a href="https://osop.ai">osop.ai</a></footer>'

    return (
        "<!DOCTYPE html><html><head>"
        '<meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_h(report_title)}</title>"
        f"<style>{CSS}</style>"
        f"</head><body>{body}</body></html>"
    )
