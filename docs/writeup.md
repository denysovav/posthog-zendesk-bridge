# Why I built this

I applied for a role on PostHog's customer/support side, got declined, and decided the most honest way to make my case was to build the thing I'd want to use on day one rather than argue about it.

## The problem I kept seeing

In support, the expensive part of a ticket isn't the answer — it's *reconstructing context*. A customer writes "it's broken," and the agent spends the first few minutes asking what plan they're on, what they were doing, whether they saw an error, what their account email is. All of that already exists in PostHog. The two systems just don't talk.

PostHog is also building toward its own support product. So the gap between "analytics you already have" and "the support workflow where you need it" is exactly the seam worth closing — and a good place to show I understand the product deeply enough to extend it, and that I see the same problems their customers see.

## What it does

When a ticket lands, the agent sees the requester's PostHog context inline:

1. **Who they are** — plan, MRR, org, SDK, last seen. Triage in one glance.
2. **What went wrong** — friction signals (exceptions, retries, declines) auto-extracted from the recent event stream and pulled to the top in red.
3. **What they were doing** — a timeline of the last ~15 events, error events marked.
4. **Proof** — direct links to session recordings, with console-error counts.
5. **Why it might be flag-specific** — the active feature flags for that user.

There are two ways it delivers this, because different teams work differently:
- A **Zendesk sidebar app** for agents who live in the ticket view.
- A **webhook enricher** that posts the same context as an internal note the moment a ticket is created — useful even if you never install the sidebar.

## Decisions I'm happy to defend

- **One data layer, two backends.** `PostHogClient` returns an identical `PersonContext` from either the live API or mock fixtures. It meant I could build and test the entire enrichment + UI without waiting on account provisioning, and the "demo vs. live" switch is a `.env` file — not a code path. The UI even shows a `mock`/`live` badge so a demo never misrepresents itself.
- **Signal extraction over raw dumps.** A raw event list isn't useful under time pressure. The enricher classifies friction events and surfaces them first — that's the difference between "data" and "answer."
- **Graceful degradation.** No recordings feature enabled? That panel just doesn't render. No Zendesk creds? The webhook returns the note in its response instead of failing. Missing person? A clear empty state, not an error.
- **Standalone-demoable.** The sidebar runs inside Zendesk *or* in a plain browser via `?email=`, so the work is verifiable in 30 seconds without installing anything.

## What I'd do next, given the role

- Live cohort membership (one more paginated call).
- A "create ticket from a PostHog action" path for the proactive/churn angle — never be surprised when a customer goes quiet.
- Signed webhooks + a short TTL cache for production.
- Swap the bespoke summary heuristics for a PostHog-side query so the logic lives where the data does.

The repo is intentionally small and readable. The point isn't the line count — it's that it works end-to-end, makes defensible trade-offs, and closes a real gap between two tools customers use every day.
