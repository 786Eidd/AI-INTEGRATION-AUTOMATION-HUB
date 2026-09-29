"""
Automated notifications — a real HTTP call to the mock notification
gateway (`src/notification_service.py`), not a `print()` statement dressed
up as an integration. Wrapped so a notification failure never blocks the
lead from being recorded — the CRM sync already happened (or didn't) by
the time this runs; a failed notification means "no one was told," not
"the record is lost."
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import httpx

NOTIFICATION_API_BASE_URL = os.environ.get("NOTIFICATION_API_BASE_URL", "http://127.0.0.1:8007")
REQUEST_TIMEOUT_SECONDS = 5.0


@dataclass
class NotifyResult:
    delivered: bool
    notification_id: Optional[str] = None
    error: Optional[str] = None


def send_notification(channel: str, priority: str, message: str) -> NotifyResult:
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = client.post(
                f"{NOTIFICATION_API_BASE_URL}/notify",
                json={"channel": channel, "priority": priority, "message": message},
            )
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            return NotifyResult(delivered=False, error=str(detail))
        data = resp.json()
        return NotifyResult(delivered=True, notification_id=data.get("notification_id"))
    except httpx.ConnectError:
        return NotifyResult(delivered=False, error=f"Could not reach notification gateway at {NOTIFICATION_API_BASE_URL}.")
    except httpx.TimeoutException:
        return NotifyResult(delivered=False, error="Notification gateway timed out.")
    except Exception as exc:  # noqa: BLE001 — never let a notification failure crash the pipeline
        return NotifyResult(delivered=False, error=f"Unexpected notification error: {exc}")
