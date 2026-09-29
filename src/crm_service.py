"""
Mock CRM / SaaS system — a real, separately-runnable FastAPI service
standing in for a third-party CRM (HubSpot/Salesforce/Pipedrive-shaped:
contacts and deals, not orders). This is a genuinely different domain and
schema from `ai-business-automation-agent`'s mock order-management API —
two different portfolio pieces integrating with two different kinds of
external system, not the same mock reused with new field names.

Why a mock, not a live CRM: this sandbox has no outbound route to
arbitrary third-party hosts (same constraint documented in the other two
portfolio projects), so there's no way to authenticate against a real
HubSpot/Salesforce account here. `src/orchestrator.py` only depends on
this service's HTTP contract (POST /contacts, POST /deals), so pointing at
a real CRM's REST API later is a base-URL-and-auth-header change, not a
rewrite.

Upsert-by-email on POST /contacts mirrors how real CRM APIs generally
behave (creating a contact for an email that already exists updates it
rather than creating a duplicate) — this is also what makes
`src/schema.py`'s idempotent lead upsert meaningful to test end to end.

Run standalone:
    uvicorn src.crm_service:app --port 8006
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Dict, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Mock CRM API", version="1.0")

_lock = threading.Lock()
_contacts_by_email: Dict[str, dict] = {}
_contacts_by_id: Dict[str, dict] = {}
_deals_by_id: Dict[str, dict] = {}


class ContactIn(BaseModel):
    email: str
    name: Optional[str] = None
    company: Optional[str] = None


class ContactOut(BaseModel):
    contact_id: str
    email: str
    name: Optional[str]
    company: Optional[str]
    created: bool


class DealIn(BaseModel):
    contact_id: str
    company: Optional[str] = None
    deal_size_tier: str
    urgency: str


class DealOut(BaseModel):
    deal_id: str
    contact_id: str
    deal_size_tier: str
    urgency: str
    status: str


@app.post("/contacts", response_model=ContactOut)
def create_or_update_contact(contact: ContactIn):
    with _lock:
        existing = _contacts_by_email.get(contact.email)
        if existing:
            existing["name"] = contact.name or existing["name"]
            existing["company"] = contact.company or existing["company"]
            return ContactOut(contact_id=existing["contact_id"], email=contact.email,
                               name=existing["name"], company=existing["company"], created=False)
        contact_id = f"CONTACT-{uuid4().hex[:8]}"
        record = {"contact_id": contact_id, "email": contact.email, "name": contact.name, "company": contact.company}
        _contacts_by_email[contact.email] = record
        _contacts_by_id[contact_id] = record
        return ContactOut(contact_id=contact_id, email=contact.email, name=contact.name, company=contact.company, created=True)


@app.get("/contacts/{contact_id}")
def get_contact(contact_id: str):
    record = _contacts_by_id.get(contact_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No contact found with id '{contact_id}'")
    return record


@app.post("/deals", response_model=DealOut)
def create_deal(deal: DealIn):
    with _lock:
        if deal.contact_id not in _contacts_by_id:
            raise HTTPException(status_code=404, detail=f"No contact found with id '{deal.contact_id}' — create the contact before the deal.")
        if deal.deal_size_tier not in ("small", "medium", "large", "unknown"):
            raise HTTPException(status_code=422, detail=f"Invalid deal_size_tier '{deal.deal_size_tier}'.")
        deal_id = f"DEAL-{uuid4().hex[:8]}"
        record = {
            "deal_id": deal_id, "contact_id": deal.contact_id, "company": deal.company,
            "deal_size_tier": deal.deal_size_tier, "urgency": deal.urgency, "status": "open",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _deals_by_id[deal_id] = record
        return DealOut(deal_id=deal_id, contact_id=deal.contact_id, deal_size_tier=deal.deal_size_tier,
                        urgency=deal.urgency, status="open")


@app.get("/deals/{deal_id}")
def get_deal(deal_id: str):
    record = _deals_by_id.get(deal_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No deal found with id '{deal_id}'")
    return record


@app.get("/health")
def health():
    return {"status": "ok", "contacts": len(_contacts_by_id), "deals": len(_deals_by_id)}
