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
from urllib.parse import urlparse

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
            "last_seen": _last_seen(ctx),
            "last_seen_human": _ago(_last_seen(ctx)),
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
    """A short, human-meaningful detail for one event.

    Different event kinds carry their signal in different places — exceptions in
    the message, pageviews in the URL path, custom events in business props. We
    deliberately do NOT fall back to $current_url for custom events, or every
    autocaptured event shows a noisy full URL.
    """
    name = e.get("event") or ""
    p = e.get("properties", {})

    if name == "$exception" or p.get("$exception_message"):
        msg = p.get("$exception_message")
        if not msg:
            lst = p.get("$exception_list") or []
            if isinstance(lst, list) and lst:
                first = lst[0] or {}
                msg = first.get("value") or first.get("type")
        return str(msg) if msg else ""

    if name == "$pageview" and p.get("$current_url"):
        return urlparse(p["$current_url"]).path or p["$current_url"]

    for key in ("subject", "to", "amount", "flag", "type", "plan"):
        if p.get(key) not in (None, ""):
            return str(p[key])
    return ""


def _last_seen(ctx: PersonContext) -> str | None:
    """PostHog has no `last_seen` person property by default — fall back to the
    timestamp of the newest event (events are stored oldest-first)."""
    return ctx.properties.get("last_seen") or (
        ctx.events[-1].get("timestamp") if ctx.events else None
    )


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
        f"**Org:** {p.get('organization', '—')}  •  **Last seen:** {_ago(_last_seen(ctx))}",
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


def _h(text: object) -> str:
    """Escape user/data values before putting them in HTML."""
    return (
        str(text if text is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_summary_html(ctx: PersonContext) -> str:
    """HTML for a Zendesk internal note (posted via `html_body`).

    Same content as build_summary, but Zendesk renders the body as HTML, so
    markdown asterisks/dashes would show literally. This emits real tags.
    """
    if not ctx.found:
        return (
            f"<p>🦔 <strong>PostHog</strong>: no person found for "
            f"<code>{_h(ctx.email)}</code> (source: {_h(ctx.source)}). "
            f"They may use a different email in-product.</p>"
        )

    p = ctx.properties
    parts = [
        f"<p>🦔 <strong>PostHog context for {_h(p.get('name') or ctx.email)}</strong> "
        f"<em>(source: {_h(ctx.source)})</em></p>",
        f"<p><strong>Plan:</strong> {_h(p.get('plan', '—'))} &nbsp;•&nbsp; "
        f"<strong>MRR:</strong> ${_h(p.get('mrr', 0))} &nbsp;•&nbsp; "
        f"<strong>Org:</strong> {_h(p.get('organization', '—'))} &nbsp;•&nbsp; "
        f"<strong>Last seen:</strong> {_h(_ago(_last_seen(ctx)))}</p>",
    ]
    meta = []
    if p.get("sdk"):
        meta.append(f"<strong>SDK:</strong> {_h(p['sdk'])}")
    if ctx.cohorts:
        meta.append(f"<strong>Cohorts:</strong> {_h(', '.join(ctx.cohorts))}")
    if meta:
        parts.append("<p>" + "<br>".join(meta) + "</p>")

    signals = _signals(ctx)
    if signals:
        items = "".join(
            f"<li><code>{_h(s.get('event'))}</code> — {_h(_event_detail(s))} "
            f"({_h(_ago(s.get('timestamp')))})</li>"
            for s in signals
        )
        parts.append(f"<p><strong>⚠️ Friction in the last session:</strong></p><ul>{items}</ul>")

    if ctx.events:
        items = ""
        for e in reversed(ctx.events[-8:]):
            detail = _event_detail(e)
            suffix = f" — {_h(detail)}" if detail else ""
            items += (
                f"<li>{_h(e.get('event'))}{suffix} "
                f"<em>{_h(_ago(e.get('timestamp')))}</em></li>"
            )
        parts.append(f"<p><strong>Recent activity (newest first):</strong></p><ul>{items}</ul>")

    if ctx.recordings:
        items = ""
        for r in ctx.recordings:
            mins = round((r.get("duration_seconds") or 0) / 60, 1)
            errs = r.get("console_errors", 0)
            err_note = f", {errs} console errors" if errs else ""
            items += (
                f'<li><a href="{_h(r.get("url"))}">{mins}m recording</a> '
                f"({_h(_ago(r.get('start')))}{_h(err_note)})</li>"
            )
        parts.append(f"<p><strong>📹 Session recordings:</strong></p><ul>{items}</ul>")

    active_flags = [k for k, v in ctx.flags.items() if v]
    if active_flags:
        parts.append(f"<p><strong>🚩 Active flags:</strong> {_h(', '.join(active_flags))}</p>")

    return "\n".join(parts)


def _cli() -> None:
    email = sys.argv[1] if len(sys.argv) > 1 else "victoria@thetest.ai"
    client = PostHogClient()
    ctx = client.get_person_context(email)
    print(f"\n=== mode: {client.mode} ===\n")
    print(build_summary(ctx))


if __name__ == "__main__":
    _cli()
