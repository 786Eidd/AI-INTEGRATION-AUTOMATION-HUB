"""
FastAPI service exposing the integration hub's webhook intake endpoint.

Run (three terminals):
    uvicorn src.crm_service:app --port 8006
    uvicorn src.notification_service:app --port 8007
    uvicorn src.app:app --reload --port 8005

Then:
    curl -X POST http://localhost:8005/webhook/event \
         -H "Content-Type: application/json" \
         -d '{"source": "contact_form", "email": "jane@acme.example", "raw_text": "Hi, I am Jane from Acme Corp, we have 300 employees and need this set up ASAP."}'
"""
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from .orchestrator import handle_event
from .schema import init_db, list_leads

app = FastAPI(
    title="AI Integration Hub",
    description=(
        "Receives an event (webhook), uses AI to classify and extract structured "
        "data from it, applies business logic to decide what should happen, "
        "syncs the result to a real database and a mock CRM's REST API, and "
        "sends an automated notification when a human should know — connecting "
        "AI processing to the systems a business already runs, rather than "
        "requiring it replace them."
    ),
)


@app.on_event("startup")
def startup():
    init_db()


class WebhookEvent(BaseModel):
    source: str = "unknown"
    email: str
    raw_text: str


class WebhookResponse(BaseModel):
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


@app.post("/webhook/event", response_model=WebhookResponse)
def webhook_event(event: WebhookEvent):
    result = handle_event(event.model_dump())
    return WebhookResponse(**result.to_dict())


@app.get("/leads", response_model=List[Dict[str, Any]])
def get_leads():
    return list_leads()


@app.get("/health")
def health():
    return {"status": "ok"}
