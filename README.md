# AI Integration Hub | APIs, Business Systems & Workflow Automation

**Role:** AI Engineer | Automation & Integration Developer

## What this is

Most businesses don't need an AI that replaces their CRM, their order system, or their support inbox — they need the AI they already have connected *to* those systems, so a human isn't the one copy-pasting between them. This project is a working webhook-driven integration hub: it receives an inbound event (a contact-form submission, a chat message, an API call), uses AI-powered processing to pull structured data out of unstructured text, applies real business rules to decide what should happen, and then actually does it — writes to a database, calls a CRM's REST API, and sends a notification — instead of just producing a suggestion someone has to act on manually.

It's deliberately built around *connecting* AI to existing business infrastructure (a database, a CRM, a notification channel) rather than being a standalone chatbot demo. That's the gap this piece of the portfolio is meant to close: this is the part of "AI Agent Development & Business Automation" that's specifically about wiring AI into the systems a business already runs.

## Key capabilities

- **REST API integration** — real HTTP calls (via `httpx`) to a mock CRM's `/contacts` and `/deals` endpoints and a mock notification gateway's `/notify` endpoint; every call has explicit timeout/connection-error handling, not a bare try/except.
- **AI-powered data processing** — turns a free-text inbound message into structured fields (company name, employee count, urgency, spam flag) via rule-based extraction, with a genuine optional LLM path behind an API key (see "Why rule-based by default" below).
- **Automated workflows** — an event goes from intake to a CRM record and a notification with no human step in between, for the cases the business rules say should be automatic.
- **CRM/SaaS integration** — upsert-by-email contact creation and deal creation against a real (mocked) CRM API, matching how HubSpot/Salesforce/Pipedrive-shaped APIs actually behave.
- **Database connectivity** — every event, lead, and notification is persisted to a real SQLite database via parameterized SQL, with a UNIQUE constraint on email making lead upserts genuinely idempotent (verified below, not just claimed).
- **AI-driven classification & decision workflows** — urgency, spam, and company-size classification directly drive which of four business-rule branches an event takes.
- **Webhooks / event-driven automation** — the hub's own entry point (`POST /webhook/event`) is a webhook receiver; it also emits its own outbound calls to the CRM and notification services in the same event-driven style.
- **Data transformation** — a dedicated module (`transform.py`) maps between the shape an inbound event arrives in and the shape each downstream system needs, separate from the classification and routing logic.
- **Automated notifications** — high-priority Slack-shaped notifications fire automatically for hot leads, review flags, and sync failures; routine standard-tier syncs stay silent by design, to avoid alert fatigue.
- **Error handling** — a CRM outage doesn't lose the lead or fail silently: the record is still saved with status `crm_sync_failed` and a human is notified. This is demonstrated with a real failure injection, not just described (see "Resilience check" below).

## Workflow

```
Input/Event → AI Processing → Business Logic → API/Database/CRM → Automated Action → Notification/Result
```

Concretely, in `src/orchestrator.py::handle_event`:

1. **Input/Event** — a webhook payload (`source`, `email`, `raw_text`) arrives at `POST /webhook/event` and is logged to the `events` table first, as an audit trail, before anything else happens.
2. **AI Processing** — `transform.normalize_event` validates the payload, then `classifier.classify` extracts company, employee count, urgency, and a spam flag from the raw text.
3. **Business Logic** — a fixed decision tree (spam → reject; urgent + known medium/large company → hot; urgent + small/unknown company → flag for review; everything else → standard sync) decides the action.
4. **API/Database/CRM** — the lead is written to SQLite, then (for hot and standard leads) synced to the mock CRM via `POST /contacts` and `POST /deals`.
5. **Automated Action** — the lead's final status (`synced_hot`, `synced_standard`, `flagged_for_review`, `rejected_spam`, `rejected_invalid`, or `crm_sync_failed`) is recorded.
6. **Notification/Result** — hot leads, review flags, and sync failures trigger a real HTTP call to the mock notification gateway; the caller of `/webhook/event` also gets the full result back in the response.

## Why a plain function pipeline, not LangGraph

`ai-business-automation-agent` and `rag-knowledge-assistant` (the other two pieces of this portfolio) both use LangGraph, because both are genuinely agentic — a planner decides what to do next based on what retrieval or a tool call finds. This project is different: the routing rules here *are* the business logic a real integration needs, and they're fixed at design time, not decided dynamically. Adding a graph framework here would add a dependency without adding a capability — a plain function chain with explicit `try/except` around every external call is also easier to read and debug for exactly this kind of "connect system A to system B" work, which is the point of this piece.

## Why rule-based classification by default, and the real optional LLM path

This sandbox has no outbound network route to an LLM API, so `src/classifier.py::classify()` is real, tested, deterministic logic by default — regex-based entity extraction (company name, employee count) plus keyword-based urgency and spam detection — not a placeholder standing in for "AI." If `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is set, `classify()` instead makes a genuine LLM call asking for the same four fields as structured JSON, and falls back to the rule-based path if that call fails for any reason. `data/eval_results.json` records that every test case in this environment used the `rule_based` path — not because the LLM path doesn't exist, but because no key was reachable here to test it against.

## Skills & tech stack actually used

Python, FastAPI, SQLite (via the standard-library `sqlite3` — no ORM), httpx (REST API integration), Pydantic, webhook-based event handling, REST API design, rule-based NLP/classification with a genuine optional LLM path (Anthropic/OpenAI).

**n8n and Zapier are intentionally not listed.** Neither was actually used to build this — there's no way to genuinely wire either into this sandboxed environment, and this portfolio's rule is to select only technologies actually implemented and tested in a given project, not every automation tool that's plausibly adjacent to the idea. The workflow-automation behavior itself (event → decision → API calls → notification) is implemented directly in code, which is also a legitimate and common way to build this kind of integration — just not via those specific platforms.

## Real measured results

Measured by `src/evaluate.py` against 28 hand-labeled test events (`data/test_events.json`) covering hot leads, leads flagged for review, standard leads, spam, malformed/invalid events, and a same-email resubmission pair — run end to end through the real orchestrator against the real (mock) CRM and notification services, not simulated:

| Metric | Result |
|---|---|
| Business-action routing accuracy | **96.4%** (27/28) |
| Company-name extraction accuracy (when a company was actually stated) | **66.7%** (10/15) |
| Runtime errors | **0** |
| Avg / p95 latency per event | **~100ms / ~219ms** |
| Idempotency check (duplicate email → update, not duplicate) | **Passed** |
| Resilience check (CRM unreachable → graceful `crm_sync_failed`, not a crash) | **Passed** |

Full per-case results, including which case missed and why, are in `data/eval_results.json`.

### Honest limitations

- **Negation isn't understood.** Urgency detection is keyword-based, so "not urgent at all" still contains the substring "urgent" and is classified as high urgency. This is the one routing miss in the 28-case set (case #17): a message explicitly saying it's *not* urgent gets flagged for human review instead of routed as a standard lead. A real fix would need actual language understanding (or the optional LLM path) rather than another keyword rule, since negation shows up in too many shapes to enumerate.
- **Company-name extraction only catches specific phrasing.** The regex looks for a capitalized name immediately after "from", "at", or "with" (e.g. "from Acme Corp"). It reliably misses phrasing like "we're Acme Corp", "we are Acme Corp", "Acme Corp here", and "a company called Acme Corp" — five of the fifteen company-bearing test cases miss for exactly this reason. Notably, these misses don't break the actual business-routing decision (hot/standard/review), since routing depends on urgency and employee count, not on whether a company name was captured — but the `company` field itself is genuinely incomplete more often than it's right, and that's worth knowing before relying on it downstream.
- **Employee-count extraction requires a specific number+noun pattern.** "150-person team" and "300 person company" (singular "person", or a hyphenated count) aren't caught, only "N employees/people/staff/team members" is. This is why some of the review-vs-standard-tier boundary cases in the test set depend on how the number happens to be phrased.
- **The rule-based path is what actually ran for every test case.** The LLM path in `classifier.py` is real, working code, but this sandbox has no outbound route to Anthropic's or OpenAI's APIs, so it's never exercised here. `eval_results.json` records this explicitly rather than implying otherwise.

## Running it locally

```bash
pip install -r requirements.txt

# Three separate processes:
uvicorn src.crm_service:app --port 8006
uvicorn src.notification_service:app --port 8007
uvicorn src.app:app --reload --port 8005

curl -X POST http://localhost:8005/webhook/event \
     -H "Content-Type: application/json" \
     -d '{"source": "contact_form", "email": "jane@acme.example", "raw_text": "Hi, I am Jane from Acme Corp, we have 300 employees and need this set up ASAP."}'
```

To reproduce the measured results above from a clean checkout:

```bash
pip install -r requirements.txt
python -m src.evaluate
```

`src/evaluate.py` starts its own isolated copies of the CRM and notification services on separate ports and writes to a temporary SQLite database (`HUB_DB_PATH`), so running it never touches `data/hub.db` or any data used by a normal run of `src/app.py`.

## Project structure

```
src/
  schema.py               # SQLite schema + idempotent lead upsert
  crm_service.py          # Mock CRM API (contacts, deals) — FastAPI
  notification_service.py # Mock notification gateway — FastAPI
  transform.py             # Inbound-payload validation & schema mapping
  classifier.py            # Rule-based extraction + optional real LLM path
  crm_client.py             # REST client for the CRM service
  notifier.py                # REST client for the notification gateway
  orchestrator.py            # The business-logic pipeline (handle_event)
  app.py                       # FastAPI webhook intake service
  evaluate.py                   # Runs the labeled test set, writes real metrics
data/
  test_events.json      # 28 hand-labeled test events
  eval_results.json     # Real measured output of the last evaluate.py run
```

## License

MIT — see `LICENSE`.
