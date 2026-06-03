"""Fast tests that run fully offline against mock fixtures.

    ./.venv/bin/python -m enricher.test_enrich
"""

from .enrich import build_payload, build_summary
from .posthog_client import PostHogClient


def _ctx(email):
    return PostHogClient(api_key=None, project_id=None).get_person_context(email)


def test_known_person_summary():
    ctx = _ctx("victoria@thetest.ai")
    summary = build_summary(ctx)
    assert ctx.found
    assert ctx.source == "mock"
    assert "Scale" in summary           # plan surfaced
    assert "card_declined" in summary    # friction signal surfaced
    assert "replay" in summary           # recording linked


def test_unknown_person():
    ctx = _ctx("nobody@example.com")
    assert not ctx.found
    assert "no person found" in build_summary(ctx)


def test_payload_shape():
    payload = build_payload(_ctx("victoria@thetest.ai"))
    assert payload["found"] is True
    assert payload["person"]["mrr"] == 1450
    assert any(e["is_signal"] for e in payload["events"])   # at least one signal flagged
    assert payload["signals"]                                # signals extracted
    assert payload["events"][0]["event"] == "support ticket created"  # newest first


def test_signal_free_person_has_no_false_signals():
    payload = build_payload(_ctx("sam@hooli.com"))
    assert payload["found"] is True
    assert payload["signals"] == []


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
            passed += 1
    print(f"\n{passed} passed")
