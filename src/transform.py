"""
Data transformation — mapping between the shape an inbound event arrives
in and the shape each downstream system (this hub's own database, the
mock CRM's API) actually needs. Kept as its own module, separate from
`classifier.py` (which decides *what the data means*) and
`orchestrator.py` (which decides *what to do about it*), because schema
mapping is a distinct concern with its own failure mode: a required field
missing or malformed here should fail fast with a clear error, not
silently propagate a `None` into a business decision downstream.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InvalidEventError(ValueError):
    pass


@dataclass
class NormalizedEvent:
    source: str
    email: str
    raw_text: str


def normalize_event(raw: Dict[str, Any]) -> NormalizedEvent:
    """Validates and normalizes a raw webhook payload. Raises
    InvalidEventError (caught by the orchestrator, not a crash) if the
    event is missing what every downstream step needs."""
    email = str(raw.get("email", "")).strip().lower()
    if not email or not _EMAIL_RE.match(email):
        raise InvalidEventError(f"Missing or invalid email: {raw.get('email')!r}")

    raw_text = str(raw.get("raw_text", "")).strip()
    if not raw_text:
        raise InvalidEventError("Event has no raw_text to process.")

    source = str(raw.get("source", "unknown")).strip() or "unknown"
    return NormalizedEvent(source=source, email=email, raw_text=raw_text)


def build_crm_contact_payload(event: NormalizedEvent, company: Optional[str]) -> Dict[str, Any]:
    return {"email": event.email, "name": None, "company": company}


def build_crm_deal_payload(contact_id: str, company: Optional[str], deal_size_tier: str, urgency: str) -> Dict[str, Any]:
    return {"contact_id": contact_id, "company": company, "deal_size_tier": deal_size_tier, "urgency": urgency}
