"""OSOP Text Report Generator.

Plain ASCII text + optional ANSI color.
Dot-aligned node names, errors first with `!` prefix.
AI metadata compressed to one line.
"""
from __future__ import annotations

import yaml


# ANSI codes
_R = "\x1b[31m"
_G = "\x1b[32m"
_B = "\x1b[34m"
_M = "\x1b[35m"
_O = "\x1b[38;5;208m"
_D = "\x1b[2m"
_BO = "\x1b[1m"
_X = "\x1b[0m"

TYPE_ANSI: dict[str, str] = {
    "human": _O, "agent": _M,
    "api": _B, "mcp": _B, "cli": _B,
    "git": _D, "docker": _D, "cicd": _D, "system": _D,
    "infra": _D, "gateway": _D,
    "db": _G, "data": _G,
    "company": _O, "event": _D,
}


def _ms(v: int | float | None) -> str:
    if v is None:
        return "-"
    if v < 1000:
        return f"{int(v)}ms"
    if v < 60000:
        return f"{v / 1000:.1f}s"
    return f"{v / 60000:.1f}m"


def _pad(s: str, length: int) -> str:
    return s + " " * max(0, length - len(s))


def _dots(name: str, max_len: int) -> str:
    gap = max(2, max_len - len(name))
    return name + " " + "." * gap + " "


def generate_text_report(
    osop_yaml: str,
    osoplog_yaml: str | None = None,
    ansi: bool = False,
) -> str:
    """Generate a plain-text (or ANSI-colored) report.

    Args:
        osop_yaml: Raw YAML string of the .osop workflow spec.
        osoplog_yaml: Raw YAML string of the .osoplog execution log.
        ansi: If True, include ANSI color escape codes.

    Returns:
        Report as a string.
    """
    o: dict = yaml.safe_load(osop_yaml) or {}
    log: dict | None = yaml.safe_load(osoplog_yaml) if osoplog_yaml else None

    def c(code: str, text: str) -> str:
        return code + text + _X if ansi else text

    lines: list[str] = []
    title = o.get("name") or o.get("id") or "OSOP Report"
    lines.append(c(_BO, f"OSOP Report: {title}"))
    lines.append("=" * min(60, len(title) + 14))

    if log:
        # --- Execution mode ---
        status_str = log.get("status", "UNKNOWN")
        sc = c(_G, "COMPLETED") if status_str == "COMPLETED" else c(_R, status_str)
        parts = [f"Status: {sc}", _ms(log.get("duration_ms"))]
        cost_total = (log.get("cost") or {}).get("total_usd")
        if cost_total:
            parts.append(f"${cost_total:.3f}")
        latest: dict[str, dict] = {}
        for r in log.get("node_records") or []:
            nid = r.get("node_id", "")
            prev = latest.get(nid)
            if not prev or r.get("attempt", 0) > prev.get("attempt", 0):
                latest[nid] = r
        parts.append(f"{len(latest)} nodes")
        lines.append(" | ".join(parts))

        meta: list[str] = []
        if log.get("run_id"):
            meta.append("Run: " + str(log["run_id"])[:8])
        runtime = log.get("runtime") or {}
        if runtime.get("agent"):
            meta.append("Agent: " + str(runtime["agent"]))
        trigger = log.get("trigger") or {}
        if trigger.get("actor"):
            meta.append("Actor: " + str(trigger["actor"]))
        if meta:
            lines.append(c(_D, " | ".join(meta)))

        # Errors first
        all_records = log.get("node_records") or []
        failures = [r for r in all_records if r.get("status") == "FAILED"]
        if failures:
            lines.append("")
            for f in failures:
                nid = f.get("node_id", "")
                l = latest.get(nid)
                retried = (
                    l is not None
                    and l.get("status") == "COMPLETED"
                    and l.get("attempt", 0) > f.get("attempt", 0)
                )
                suffix = c(_G, " -> retried ok") if retried else ""
                err = f.get("error") or {}
                lines.append(
                    c(_R, f"! {nid} FAILED (attempt {f.get('attempt', '?')})")
                    + f" -> {err.get('code', '')}: {err.get('message', '')}{suffix}"
                )

        # Node list
        lines.append("")
        nodes = o.get("nodes") or []
        max_name = max((len(n.get("id", "")) for n in nodes), default=10)
        dot_len = max_name + 4

        for node in nodes:
            nid = node.get("id", "")
            ntype = node.get("type", "system")
            rec = latest.get(nid)
            if not rec:
                continue
            tc = TYPE_ANSI.get(ntype, _D)
            type_str = _pad(ntype.upper(), 7)
            name_str = _dots(nid, dot_len)
            dur_str = _pad(_ms(rec.get("duration_ms")), 7)

            status = rec.get("status", "")
            status_display = c(_G, "ok") if status == "COMPLETED" else c(_R, status)
            extras: list[str] = []

            node_recs = [r for r in all_records if r.get("node_id") == nid]
            if len(node_recs) > 1:
                extras.append("(retry)")
            ai = rec.get("ai_metadata")
            if ai and isinstance(ai, dict):
                pt = ai.get("prompt_tokens")
                ct = ai.get("completion_tokens", 0)
                if pt is not None:
                    extras.append(f"{pt:,}->{ct:,} tok")
                if ai.get("cost_usd"):
                    extras.append(f"${ai['cost_usd']:.3f}")
                conf = ai.get("confidence")
                if conf is not None:
                    extras.append(f"{conf * 100:.0f}%")
            hm = rec.get("human_metadata")
            if hm and isinstance(hm, dict) and hm.get("decision"):
                extras.append("decision=" + str(hm["decision"]))

            extra_str = "  " + c(_D, "  ".join(extras)) if extras else ""
            lines.append(f"  {c(tc, type_str)} {name_str}{dur_str} {status_display}{extra_str}")

        # Summary
        if log.get("result_summary"):
            lines.append("")
            lines.append(c(_D, "Summary: " + str(log["result_summary"])))
    else:
        # --- Spec mode ---
        nodes = o.get("nodes") or []
        edges = o.get("edges") or []
        lines.append(f"{len(nodes)} nodes, {len(edges)} edges")
        lines.append("")
        for node in nodes:
            ntype = node.get("type", "system")
            nname = node.get("name", node.get("id", ""))
            tc = TYPE_ANSI.get(ntype, _D)
            lines.append(f"  {c(tc, _pad(ntype.upper(), 7))} {nname}")

    lines.append("")
    return "\n".join(lines)
