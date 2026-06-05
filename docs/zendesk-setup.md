# Installing the sidebar into a Zendesk sandbox

You need a Zendesk instance you can install a private app into. A free developer
sandbox works: https://developer.zendesk.com → "Get a trial".

## 1. Make the bridge reachable

The sidebar (running in Zendesk's cloud) needs to reach your enricher API.

- **Local testing:** expose `localhost:8123` with a tunnel, e.g. `ngrok http 8123`,
  and use the resulting `https://…ngrok…` URL as the bridge URL.
- **Hosted:** deploy `enricher/` to Railway / Render / a Cloudflare Worker and use
  that URL. (It's a stock FastAPI app — `uvicorn enricher.app:app`.)

## 2. Install the app (ZAT)

```bash
npm install -g @zendesk/zcli           # Zendesk CLI (successor to ZAT)
cd zendesk-app
zcli apps:validate .                   # sanity-check the manifest
zcli apps:create .                     # upload as a private app to your instance
```

When prompted (or in Admin Center → Apps → your app → Settings), set:

| Setting | Value |
|---------|-------|
| `bridge_url` | The public URL of your running enricher, e.g. `https://abc123.ngrok.io` |

Open any ticket — the **PostHog Context** panel appears in the right sidebar and
loads automatically from the requester's email.

## 3. (Optional) Wire up the webhook for auto-notes

To have a context note posted the instant a ticket is created:

1. **Admin Center → Apps and integrations → Webhooks → Create webhook.**
   - Endpoint URL: `https://<your-bridge>/webhooks/zendesk`
   - Request method: `POST`, format `JSON`.
2. **Admin Center → Objects and rules → Triggers → Create trigger.**
   - Condition: *Ticket is Created*.
   - Action: *Notify active webhook* → select the webhook above.
   - JSON body:
     ```json
     {
       "ticket_id": {{ticket.id}},
       "requester_email": "{{ticket.requester.email}}"
     }
     ```
3. Put your Zendesk creds in `.env` (`ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`,
   `ZENDESK_API_TOKEN`) so the bridge can post the note back. Without them the
   bridge runs in demo mode and just returns the note in the HTTP response.

## Local-only demo (no Zendesk at all)

You don't need any of the above to demo the UI. Serve the app statically and open
it with a query param:

```
http://localhost:5599/zendesk-app/assets/iframe.html?email=victoria%2Btest@thetest.ai&bridge=http://localhost:8123
```

This is the fastest path to a screen recording for the application.
