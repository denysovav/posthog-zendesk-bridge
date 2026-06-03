"""Turn a PersonContext into the artifacts the support agent sees.

`build_summary` -> markdown for a Zendesk internal note (webhook path).
`build_payload` -> JSON the sidebar app renders into its four panels.

Run directly to preview against mock data:
    python -m enricher.enrich victoria@thetest.ai
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from .posthog_client import PersonContext, PostHogClient

# Events that signal trouble — used to auto-surface a "what went wrong" line.
SIGNAL_EVENTS = ("$exception", "error", "failed", "declined", "retry")


def _ago(timestamp: str | None) -> str:
    if not timestamp:
        return "unknown time"
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return timestamp
    delta = datetime.now(timezone.utc) - ts
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _signals(ctx: PersonContext) -> list[dict[str, Any]]:
    """Events that look like friction — the heart of 'why did they write in'."""
    out = []
    for e in ctx.events:
        name = (e.get("event") or "").lower()
        msg = str(e.get("properties", {}).get("$exception_message", ""))
        if any(s in name for s in SIGNAL_EVENTS) or msg:
            out.append(e)
    return out


def build_payload(ctx: PersonContext) -> dict[str, Any]:
    """Backend-agnostic JSON consumed by the sidebar app."""
    props = ctx.properties
    return {
        "found": ctx.found,
        "source": ctx.source,
        "email": ctx.email,
        "person": {
            "name": props.get("name"),
            "organization": props.get("organization"),
            "plan": props.get("plan"),
            "mrr": props.get("mrr"),
            "sdk": props.get("sdk"),
            "country": props.get("$geoip_country_name"),
            "last_seen": props.get("last_seen"),
            "last_seen_human": _ago(props.get("last_seen")),
        },
        "cohorts": ctx.cohorts,
        "events": [
            {
                "event": e.get("event"),
                "timestamp": e.get("timestamp"),
                "ago": _ago(e.get("timestamp")),
                "detail": _event_detail(e),
                "is_signal": e in _signals(ctx),
            }
            for e in reversed(ctx.events)  # newest first
        ],
        "recordings": ctx.recordings,
        "flags": ctx.flags,
        "signals": [
            {"event": e.get("event"), "detail": _event_detail(e), "ago": _ago(e.get("timestamp"))}
            for e in _signals(ctx)
        ],
    }


def _event_detail(e: dict[str, Any]) -> str:
    p = e.get("properties", {})
    for key in ("$exception_message", "$current_url", "subject", "to", "flag", "amount", "type"):
        if key in p and p[key] not in (None, ""):
            return f"{p[key]}"
    return ""


def build_summary(ctx: PersonContext) -> str:
    """Markdown for a Zendesk internal note."""
    if not ctx.found:
        return (
            f"🦔 **PostHog**: no person found for `{ctx.email}` "
            f"(source: {ctx.source}). They may use a different email in-product."
        )

    p = ctx.properties
    lines = [
        f"🦔 **PostHog context for {p.get('name') or ctx.email}**  _(source: {ctx.source})_",
        "",
        f"**Plan:** {p.get('plan', '—')}  •  **MRR:** ${p.get('mrr', 0)}  •  "
        f"**Org:** {p.get('organization', '—')}  •  **Last seen:** {_ago(p.get('last_seen'))}",
    ]
    if p.get("sdk"):
        lines.append(f"**SDK:** {p['sdk']}")
    if ctx.cohorts:
        lines.append(f"**Cohorts:** {', '.join(ctx.cohorts)}")

    signals = _signals(ctx)
    if signals:
        lines += ["", "**⚠️ Friction in the last session:**"]
        for s in signals:
            detail = _event_detail(s)
            lines.append(f"- `{s.get('event')}` — {detail} ({_ago(s.get('timestamp'))})")

    if ctx.events:
        lines += ["", "**Recent activity (newest first):**"]
        for e in reversed(ctx.events[-8:]):
            detail = _event_detail(e)
            suffix = f" — {detail}" if detail else ""
            lines.append(f"- {e.get('event')}{suffix}  _{_ago(e.get('timestamp'))}_")

    if ctx.recordings:
        lines += ["", "**📹 Session recordings:**"]
        for r in ctx.recordings:
            mins = round((r.get("duration_seconds") or 0) / 60, 1)
            errs = r.get("console_errors", 0)
            err_note = f", {errs} console errors" if errs else ""
            lines.append(f"- [{mins}m recording]({r.get('url')}) ({_ago(r.get('start'))}{err_note})")

    active_flags = [k for k, v in ctx.flags.items() if v]
    if active_flags:
        lines += ["", f"**🚩 Active flags:** {', '.join(active_flags)}"]

    return "\n".join(lines)


def _cli() -> None:
    email = sys.argv[1] if len(sys.argv) > 1 else "victoria@thetest.ai"
    client = PostHogClient()
    ctx = client.get_person_context(email)
    print(f"\n=== mode: {client.mode} ===\n")
    print(build_summary(ctx))


if __name__ == "__main__":
    _cli()
