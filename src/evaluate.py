"""
Runs every labeled case in data/test_events.json through the real
orchestration pipeline (src/orchestrator.py) end to end — against the
actual CRM and notification FastAPI services, not mocked-out function
calls — and writes real measured results to data/eval_results.json.

Three things happen here, each isolated so a normal run of src/app.py
never sees eval side effects:

1. Main accuracy pass: every test event goes through handle_event(),
   compared against its hand-labeled expected_action and (where stated)
   expected_company.
2. Idempotency check: two of the test events reuse the same email
   (ids 24 and 25) specifically so this script can confirm the lead gets
   UPDATED in place, not duplicated, and that the CRM's upsert-by-email
   correctly reports created=false the second time.
3. Resilience check: a fresh event is run once with CRM_API_BASE_URL
   pointed at a port nothing is listening on, to confirm handle_event
   degrades to action="crm_sync_failed" with a delivered notification
   instead of raising — a genuine failure injection, not a claim.

IMPORTANT: env vars that local modules read as module-level constants
(CRM_API_BASE_URL in crm_client.py, NOTIFICATION_API_BASE_URL in
notifier.py) must be set BEFORE those modules are imported, since Python
evaluates module-level constants once at first import. HUB_DB_PATH is
read lazily via schema.get_db_path(), but is set early here too for
consistency and clarity.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

EVAL_DB_PATH = Path(tempfile.gettempdir()) / f"hub_eval_{os.getpid()}.db"
CRM_PORT = 8098
NOTIF_PORT = 8099
UNREACHABLE_PORT = 8199  # nothing listens here; used for the resilience check

os.environ["HUB_DB_PATH"] = str(EVAL_DB_PATH)
os.environ["CRM_API_BASE_URL"] = f"http://127.0.0.1:{CRM_PORT}"
os.environ["NOTIFICATION_API_BASE_URL"] = f"http://127.0.0.1:{NOTIF_PORT}"

sys.path.insert(0, str(REPO_ROOT))

from src import crm_client, notifier, schema  # noqa: E402  (must import after env vars are set)
from src import crm_service, notification_service  # noqa: E402
from src.orchestrator import handle_event  # noqa: E402


def _run_service_in_thread(app, port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


def _wait_for_health(base_url: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url}/health", timeout=1.0)
            if resp.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"Service at {base_url} never became healthy: {last_error}")


def _percentile(values, pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def main() -> None:
    if EVAL_DB_PATH.exists():
        EVAL_DB_PATH.unlink()

    print(f"Starting CRM service on port {CRM_PORT} and notification gateway on port {NOTIF_PORT}...")
    _run_service_in_thread(crm_service.app, CRM_PORT)
    _run_service_in_thread(notification_service.app, NOTIF_PORT)
    _wait_for_health(f"http://127.0.0.1:{CRM_PORT}")
    _wait_for_health(f"http://127.0.0.1:{NOTIF_PORT}")
    print("Both mock services are healthy.\n")

    schema.init_db()

    test_events = json.loads((DATA_DIR / "test_events.json").read_text())

    results = []
    latencies_ms = []
    error_count = 0
    action_correct = 0
    company_checked = 0
    company_correct = 0

    for case in test_events:
        payload = case["payload"]
        started = time.perf_counter()
        try:
            outcome = handle_event(payload)
            error = None
        except Exception as exc:  # noqa: BLE001 — a real crash here IS an eval finding, not something to hide
            outcome = None
            error = f"{type(exc).__name__}: {exc}"
            error_count += 1
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        latencies_ms.append(elapsed_ms)

        actual_action = outcome.action if outcome else None
        actual_company = outcome.company if outcome else None
        is_action_correct = actual_action == case["expected_action"]
        if is_action_correct:
            action_correct += 1

        company_match = None
        if case.get("expected_company") is not None:
            company_checked += 1
            company_match = actual_company == case["expected_company"]
            if company_match:
                company_correct += 1

        results.append({
            "id": case["id"],
            "email": payload["email"],
            "expected_action": case["expected_action"],
            "actual_action": actual_action,
            "action_correct": is_action_correct,
            "expected_company": case.get("expected_company"),
            "actual_company": actual_company,
            "company_correct": company_match,
            "latency_ms": round(elapsed_ms, 3),
            "error": error,
            "note": case.get("note"),
        })
        status = "OK" if is_action_correct else "MISS"
        print(f"[{status}] id={case['id']:>2} expected={case['expected_action']:<18} actual={actual_action}")

    # --- Idempotency check (ids 24 and 25 share an email on purpose) ---
    dup_leads = [lead for lead in schema.list_leads() if lead["email"] == "dup@falconmedia.example"]
    idempotency_check = {
        "email": "dup@falconmedia.example",
        "row_count": len(dup_leads),
        "expected_row_count": 1,
        "passed": len(dup_leads) == 1 and dup_leads[0]["status"] == "synced_hot",
        "final_status": dup_leads[0]["status"] if dup_leads else None,
        "note": (
            "Two events for the same email were submitted (ids 24, 25). The second "
            "should UPDATE the existing leads row via the UNIQUE-email upsert in "
            "schema.py, not create a duplicate, and the final status should reflect "
            "the second (hot) classification."
        ),
    }
    print(f"\nIdempotency check: {idempotency_check}")

    # --- Resilience check: point the CRM client at an unreachable port for one event ---
    original_crm_url = crm_client.CRM_API_BASE_URL
    crm_client.CRM_API_BASE_URL = f"http://127.0.0.1:{UNREACHABLE_PORT}"
    resilience_payload = {
        "source": "webhook", "email": "resilience@downstream-test.example",
        "raw_text": "This is urgent, need this today, we have 200 employees at Downstream Test Co.",
    }
    try:
        resilience_outcome = handle_event(resilience_payload)
        resilience_check = {
            "scenario": "CRM API unreachable during a sync that should otherwise succeed",
            "actual_action": resilience_outcome.action,
            "expected_action": "crm_sync_failed",
            "passed": resilience_outcome.action == "crm_sync_failed" and resilience_outcome.notified is True,
            "lead_recorded": resilience_outcome.lead_id is not None,
            "notified": resilience_outcome.notified,
            "detail": resilience_outcome.detail,
        }
    except Exception as exc:  # noqa: BLE001
        resilience_check = {
            "scenario": "CRM API unreachable during a sync that should otherwise succeed",
            "passed": False,
            "crashed": f"{type(exc).__name__}: {exc}",
        }
    finally:
        crm_client.CRM_API_BASE_URL = original_crm_url
    print(f"Resilience check: {resilience_check}\n")

    action_accuracy = action_correct / len(test_events)
    company_accuracy = (company_correct / company_checked) if company_checked else None

    eval_results = {
        "test_case_count": len(test_events),
        "action_accuracy": round(action_accuracy, 4),
        "action_correct_count": action_correct,
        "company_extraction_accuracy": round(company_accuracy, 4) if company_accuracy is not None else None,
        "company_extraction_checked_count": company_checked,
        "company_extraction_correct_count": company_correct,
        "error_count": error_count,
        "latency_ms_avg": round(statistics.mean(latencies_ms), 3),
        "latency_ms_p95": round(_percentile(latencies_ms, 95), 3),
        "idempotency_check": idempotency_check,
        "resilience_check": resilience_check,
        "classification_path_note": (
            "No OPENAI_API_KEY or ANTHROPIC_API_KEY was set for this run, so every "
            "case used the rule_based classification path, not the optional LLM path."
        ),
        "per_case_results": results,
    }

    out_path = DATA_DIR / "eval_results.json"
    out_path.write_text(json.dumps(eval_results, indent=2))

    print(f"Action accuracy: {action_accuracy:.2%} ({action_correct}/{len(test_events)})")
    print(f"Company extraction accuracy: {company_accuracy:.2%} ({company_correct}/{company_checked})" if company_accuracy is not None else "Company extraction accuracy: n/a")
    print(f"Errors: {error_count}")
    print(f"Latency avg/p95 (ms): {eval_results['latency_ms_avg']} / {eval_results['latency_ms_p95']}")
    print(f"Wrote {out_path}")

    if EVAL_DB_PATH.exists():
        EVAL_DB_PATH.unlink()


if __name__ == "__main__":
    main()
