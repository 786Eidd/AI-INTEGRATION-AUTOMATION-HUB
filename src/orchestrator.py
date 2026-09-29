"""
Business logic — the workflow itself: Input/Event -> AI Processing ->
Business Logic -> API/Database/CRM -> Automated Action -> Notification.

## Why this is a plain function pipeline, not a LangGraph graph

`ai-business-automation-agent` and `rag-knowledge-assistant` both use
LangGraph, because both are genuinely agentic — a planner decides what to
do next based on what retrieval finds. This project is different: the
routing rules ARE the business logic a real integration needs (sync vs.
flag vs. reject), and they're fixed at design time, not decided
dynamically by an agent step. Forcing that into a graph framework here
would add a dependency without adding a capability. `handle_event` below
is a plain function that calls out to `transform`, `classifier`,
`schema`, `crm_client`, and `notifier` in sequence, with explicit
try/except around every external call — which is also easier to read and
debug for exactly this kind of "connect system A to system B" integration
work, the thing this portfolio piece is actually about.

## Routing rules

- **Spam / malformed** → rejected, logged, no CRM sync, no notification.
- **Urgent + company size known (medium/large)** → "hot" lead: synced to
  the CRM immediately, high-priority notification sent.
- **Urgent + company size unknown/small** → flagged for human review
  instead of auto-synced — an urgent claim with no verifiable company
  size is exactly the kind of signal worth a second pair of eyes before
  it becomes a CRM deal, not a reason to either ignore it or blindly
  trust it.
- **Normal/low urgency, not spam** → synced to the CRM as a standard
  lead, no notification (avoids alert fatigue on routine volume).
- **CRM API failure at any point** → the lead is still recorded (status
  `crm_sync_failed`) for retry, and a high-priority notification alerts
  someone that the sync didn't go through — the one thing this pipeline
  never does is silently drop a lead because a downstream system had a
  bad moment.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from . import crm_client, notifier, schema
from .classifier import classify
from .transform import InvalidEventError, build_crm_deal_payload, normalize_event

HOT_DEAL_TIERS = {"medium", "large"}


@dataclass
class OrchestrationResult:
    event_id: Optional[int]
    lead_id: Optional[int]
    action: str
    status: str
    email: Optional[str] = None
    company: Optional[str] = None
    urgency: Optional[str] = None
    deal_size_tier: Optional[str] = None
    classification_path: Optional[str] = None
    crm_contact_id: Optional[str] = None
    crm_deal_id: Optional[str] = None
    notified: bool = False
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def handle_event(raw_payload: Dict[str, Any]) -> OrchestrationResult:
    received_at = _now()
    event_id = schema.insert_event(
        source=str(raw_payload.get("source", "unknown")), raw_payload=raw_payload, received_at=received_at
    )

    try:
        event = normalize_event(raw_payload)
    except InvalidEventError as exc:
        return OrchestrationResult(event_id=event_id, lead_id=None, action="rejected_invalid", status="rejected", detail=str(exc))

    classification = classify(event.raw_text, event.email)

    if classification.is_spam:
        lead_id, _ = schema.upsert_lead(
            event_id=event_id, email=event.email, company=classification.company,
            employee_count=classification.employee_count, urgency=classification.urgency,
            deal_size_tier=classification.deal_size_tier, status="rejected_spam",
            classification_path=classification.path_used, created_at=received_at,
        )
        return OrchestrationResult(
            event_id=event_id, lead_id=lead_id, action="rejected_spam", status="rejected",
            email=event.email, urgency=classification.urgency, classification_path=classification.path_used,
        )

    is_hot = classification.urgency == "high" and classification.deal_size_tier in HOT_DEAL_TIERS
    needs_review = classification.urgency == "high" and classification.deal_size_tier not in HOT_DEAL_TIERS

    if needs_review:
        lead_id, _ = schema.upsert_lead(
            event_id=event_id, email=event.email, company=classification.company,
            employee_count=classification.employee_count, urgency=classification.urgency,
            deal_size_tier=classification.deal_size_tier, status="flagged_for_review",
            classification_path=classification.path_used, created_at=received_at,
        )
        message = (
            f"Lead flagged for review: {event.email} ({classification.company or 'company unknown'}) — "
            f"marked urgent but company size is {classification.deal_size_tier}. Not auto-synced to CRM."
        )
        notify_result = notifier.send_notification(channel="slack", priority="normal", message=message)
        schema.insert_notification(lead_id, "slack", "normal", message, notify_result.delivered, _now())
        return OrchestrationResult(
            event_id=event_id, lead_id=lead_id, action="flagged_for_review", status="pending_review",
            email=event.email, company=classification.company, urgency=classification.urgency,
            deal_size_tier=classification.deal_size_tier, classification_path=classification.path_used,
            notified=notify_result.delivered, detail=notify_result.error,
        )

    # Not spam, not needing review -> sync to the CRM (both "hot" and standard leads).
    lead_id, _ = schema.upsert_lead(
        event_id=event_id, email=event.email, company=classification.company,
        employee_count=classification.employee_count, urgency=classification.urgency,
        deal_size_tier=classification.deal_size_tier, status="pending_sync",
        classification_path=classification.path_used, created_at=received_at,
    )

    contact_result = crm_client.create_or_update_contact(event.email, classification.company)
    if not contact_result.ok:
        schema.update_lead_crm_ids(lead_id, None, None, status="crm_sync_failed")
        message = f"CRM sync failed for {event.email} (contact step): {contact_result.error}"
        notify_result = notifier.send_notification(channel="slack", priority="high", message=message)
        schema.insert_notification(lead_id, "slack", "high", message, notify_result.delivered, _now())
        return OrchestrationResult(
            event_id=event_id, lead_id=lead_id, action="crm_sync_failed", status="error",
            email=event.email, company=classification.company, urgency=classification.urgency,
            deal_size_tier=classification.deal_size_tier, classification_path=classification.path_used,
            notified=notify_result.delivered, detail=contact_result.error,
        )

    contact_id = contact_result.data["contact_id"]
    deal_result = crm_client.create_deal(contact_id, classification.company, classification.deal_size_tier, classification.urgency)
    if not deal_result.ok:
        schema.update_lead_crm_ids(lead_id, contact_id, None, status="crm_sync_failed")
        message = f"CRM sync failed for {event.email} (deal step): {deal_result.error}"
        notify_result = notifier.send_notification(channel="slack", priority="high", message=message)
        schema.insert_notification(lead_id, "slack", "high", message, notify_result.delivered, _now())
        return OrchestrationResult(
            event_id=event_id, lead_id=lead_id, action="crm_sync_failed", status="error",
            email=event.email, company=classification.company, urgency=classification.urgency,
            deal_size_tier=classification.deal_size_tier, classification_path=classification.path_used,
            crm_contact_id=contact_id, notified=notify_result.delivered, detail=deal_result.error,
        )

    deal_id = deal_result.data["deal_id"]
    final_status = "synced_hot" if is_hot else "synced_standard"
    schema.update_lead_crm_ids(lead_id, contact_id, deal_id, status=final_status)

    notified = False
    detail = None
    if is_hot:
        message = (
            f"Hot lead synced: {event.email} ({classification.company or 'company unknown'}, "
            f"{classification.deal_size_tier}) — CRM deal {deal_id} created."
        )
        notify_result = notifier.send_notification(channel="slack", priority="high", message=message)
        schema.insert_notification(lead_id, "slack", "high", message, notify_result.delivered, _now())
        notified = notify_result.delivered
        detail = notify_result.error

    return OrchestrationResult(
        event_id=event_id, lead_id=lead_id, action=final_status, status="resolved",
        email=event.email, company=classification.company, urgency=classification.urgency,
        deal_size_tier=classification.deal_size_tier, classification_path=classification.path_used,
        crm_contact_id=contact_id, crm_deal_id=deal_id, notified=notified, detail=detail,
    )
