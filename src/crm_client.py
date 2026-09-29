"""
REST API integration / CRM integration — the client side of
`src/crm_service.py`. Real HTTP calls via `httpx`, uniform result shape so
`src/orchestrator.py` handles success and failure the same way regardless
of which call ran (the same `ToolResult`-style pattern used in
`ai-business-automation-agent/src/tools.py`, for consistency across the
portfolio).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

CRM_API_BASE_URL = os.environ.get("CRM_API_BASE_URL", "http://127.0.0.1:8006")
REQUEST_TIMEOUT_SECONDS = 5.0


@dataclass
class CrmResult:
    ok: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


def _post(path: str, payload: dict) -> CrmResult:
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = client.post(f"{CRM_API_BASE_URL}{path}", json=payload)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            return CrmResult(ok=False, error=str(detail))
        return CrmResult(ok=True, data=resp.json())
    except httpx.ConnectError:
        return CrmResult(ok=False, error=f"Could not reach CRM API at {CRM_API_BASE_URL}.")
    except httpx.TimeoutException:
        return CrmResult(ok=False, error="CRM API timed out.")
    except Exception as exc:  # noqa: BLE001
        return CrmResult(ok=False, error=f"Unexpected CRM error: {exc}")


def create_or_update_contact(email: str, company: Optional[str]) -> CrmResult:
    return _post("/contacts", {"email": email, "name": None, "company": company})


def create_deal(contact_id: str, company: Optional[str], deal_size_tier: str, urgency: str) -> CrmResult:
    return _post("/deals", {"contact_id": contact_id, "company": company, "deal_size_tier": deal_size_tier, "urgency": urgency})
