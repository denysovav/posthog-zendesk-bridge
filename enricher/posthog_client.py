"""PostHog data access for the Zendesk bridge.

One interface, two backends:
  - LIVE  : real PostHog REST API, used when POSTHOG_API_KEY is set
  - MOCK  : enricher/fixtures/mock_data.json, used otherwise

Both return the same `PersonContext` shape, so the rest of the codebase
(enrich.py, the FastAPI app, the sidebar's JSON contract) never has to know
which backend produced the data. Swapping from demo to live is a .env change.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()  # pulls .env at repo root into os.environ if present
except ModuleNotFoundError:  # dotenv optional; mock mode works without it
    pass

FIXTURES = Path(__file__).parent / "fixtures" / "mock_data.json"


@dataclass
class PersonContext:
    """Everything the sidebar needs about one person, backend-agnostic."""

    found: bool
    email: str
    distinct_id: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    cohorts: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    recordings: list[dict[str, Any]] = field(default_factory=list)
    flags: dict[str, bool] = field(default_factory=dict)
    source: str = "mock"  # "live" or "mock" — surfaced in the UI for honesty

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PostHogClient:
    def __init__(
        self,
        api_key: str | None = None,
        project_id: str | None = None,
        host: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("POSTHOG_API_KEY")
        self.project_id = project_id or os.getenv("POSTHOG_PROJECT_ID")
        self.host = (host or os.getenv("POSTHOG_HOST") or "https://us.posthog.com").rstrip("/")
        self.live = bool(self.api_key and self.project_id)

    @property
    def mode(self) -> str:
        return "live" if self.live else "mock"

    def get_person_context(self, email: str, event_limit: int = 15) -> PersonContext:
        if self.live:
            return self._live_context(email, event_limit)
        return self._mock_context(email, event_limit)

    # ------------------------------------------------------------------ mock
    def _mock_context(self, email: str, event_limit: int) -> PersonContext:
        data = json.loads(FIXTURES.read_text())
        record = data.get("persons", {}).get(email.lower())
        if not record:
            return PersonContext(found=False, email=email, source="mock")
        return PersonContext(
            found=True,
            email=email,
            distinct_id=record.get("distinct_id"),
            properties=record.get("properties", {}),
            cohorts=record.get("cohorts", []),
            events=record.get("events", [])[-event_limit:],
            recordings=record.get("recordings", []),
            flags=record.get("flags", {}),
            source="mock",
        )

    # ------------------------------------------------------------------ live
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _api(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.host}/api/projects/{self.project_id}/{path.lstrip('/')}"
        resp = requests.get(url, headers=self._headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _live_context(self, email: str, event_limit: int) -> PersonContext:
        # 1. Find the person by email.
        people = self._api("persons/", params={"search": email}).get("results", [])
        person = next((p for p in people if self._email_of(p) == email.lower()), None)
        if not person:
            return PersonContext(found=False, email=email, source="live")

        # A person commonly has several distinct_ids (an anonymous one from before
        # they were identified, plus their email). Events are spread across all of
        # them, so we must query each — using only [0] silently drops events.
        distinct_ids = person.get("distinct_ids") or []
        # Prefer the non-UUID (identified) id for flag evaluation; fall back to first.
        distinct_id = next(
            (d for d in distinct_ids if "@" in d), distinct_ids[0] if distinct_ids else None
        )
        props = person.get("properties", {})

        # 2. Recent events across every distinct_id, merged.
        raw: list[dict[str, Any]] = []
        for did in distinct_ids:
            try:
                raw.extend(
                    self._api("events/", params={"distinct_id": did, "limit": event_limit})
                    .get("results", [])
                )
            except requests.HTTPError:
                continue
        # PostHog returns newest-first; our downstream convention is oldest-first
        # (build_payload/build_summary reverse for display). Normalise to ascending.
        raw.sort(key=lambda e: e.get("timestamp") or "")
        events = [
            {
                "event": e.get("event"),
                "timestamp": e.get("timestamp"),
                "properties": {
                    k: v for k, v in (e.get("properties") or {}).items()
                    if not k.startswith("$set")
                },
            }
            for e in raw[-event_limit:]
        ]

        # 3. Session recordings for this person.
        recordings = []
        try:
            rec = self._api(
                "session_recordings/",
                params={"person_uuid": person.get("uuid"), "limit": 5},
            ).get("results", [])
            recordings = [
                {
                    "id": r.get("id"),
                    "start": r.get("start_time"),
                    "duration_seconds": r.get("recording_duration"),
                    "console_errors": (r.get("console_error_count") or 0),
                    "url": f"{self.host}/replay/{r.get('id')}",
                }
                for r in rec
            ]
        except requests.HTTPError:
            pass  # recordings API requires the feature enabled; degrade gracefully

        # 4. Feature flags via /decide.
        flags = self._live_flags(distinct_id) if distinct_id else {}

        return PersonContext(
            found=True,
            email=email,
            distinct_id=distinct_id,
            properties=props,
            cohorts=[],  # cohort membership needs a separate paginated call; omitted in v1
            events=events,
            recordings=recordings,
            flags=flags,
            source="live",
        )

    @property
    def ingestion_host(self) -> str:
        """/decide and event capture live on the ingestion host (us.i.posthog.com),
        not the API host (us.posthog.com). Derive it from the configured host."""
        if "://us.posthog.com" in self.host:
            return "https://us.i.posthog.com"
        if "://eu.posthog.com" in self.host:
            return "https://eu.i.posthog.com"
        return self.host  # self-hosted: same host serves both

    def _live_flags(self, distinct_id: str) -> dict[str, bool]:
        project_api_key = os.getenv("POSTHOG_PROJECT_API_KEY")
        if not project_api_key:
            return {}
        try:
            resp = requests.post(
                f"{self.ingestion_host}/decide/?v=3",
                json={"api_key": project_api_key, "distinct_id": distinct_id},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("featureFlags", {})
        except requests.HTTPError:
            return {}

    @staticmethod
    def _email_of(person: dict[str, Any]) -> str:
        return (person.get("properties", {}).get("email") or "").lower()
