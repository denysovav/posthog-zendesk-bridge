"""Webhook enricher + sidebar JSON API.

Two jobs:
  POST /webhooks/zendesk   Zendesk fires this on ticket.created. We look the
                           requester up in PostHog and POST an internal note
                           back onto the ticket with the markdown summary.
  GET  /api/context        The sidebar app calls this with ?email=... and
                           renders the JSON into its four panels.

Run:  uvicorn enricher.app:app --reload --port 8000
"""

from __future__ import annotations

import os

import requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .enrich import build_payload, build_summary, build_summary_html
from .posthog_client import PostHogClient

app = FastAPI(title="PostHog ↔ Zendesk Bridge", version="0.1.0")

# The sidebar runs in a Zendesk-hosted iframe on a different origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your Zendesk subdomain in production
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

client = PostHogClient()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "posthog_mode": client.mode}


@app.get("/api/context")
def context(email: str = Query(..., description="Requester email")) -> dict:
    """Backend for the Zendesk sidebar app."""
    ctx = client.get_person_context(email)
    return build_payload(ctx)


class ZendeskWebhook(BaseModel):
    # Zendesk webhook bodies are configurable; this matches the template in
    # docs/zendesk-webhook-setup.md. Extra fields are ignored.
    ticket_id: int
    requester_email: str


@app.post("/webhooks/zendesk")
def zendesk_webhook(payload: ZendeskWebhook) -> dict:
    ctx = client.get_person_context(payload.requester_email)
    html = build_summary_html(ctx)
    posted = _post_internal_note(payload.ticket_id, html)
    return {
        "ticket_id": payload.ticket_id,
        "person_found": ctx.found,
        "posthog_mode": ctx.source,
        "note_posted": posted,
        "note_preview": build_summary(ctx),  # markdown, for human-readable API responses
    }


def _post_internal_note(ticket_id: int, html: str) -> bool:
    """PUT the note as a private HTML comment on the ticket. No-op if Zendesk creds absent."""
    subdomain = os.getenv("ZENDESK_SUBDOMAIN")
    email = os.getenv("ZENDESK_EMAIL")
    token = os.getenv("ZENDESK_API_TOKEN")
    if not (subdomain and email and token):
        return False  # demo mode — note is returned in the response instead
    url = f"https://{subdomain}.zendesk.com/api/v2/tickets/{ticket_id}.json"
    resp = requests.put(
        url,
        # html_body so Zendesk renders formatting; markdown in `body` shows literally.
        json={"ticket": {"comment": {"html_body": html, "public": False}}},
        auth=(f"{email}/token", token),
        timeout=15,
    )
    return resp.ok
