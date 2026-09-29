"""
Mock notification gateway — a real, separately-runnable FastAPI service
standing in for a Slack/email webhook endpoint. `src/notifier.py` posts to
this over real HTTP, the same shape as posting to a Slack incoming
webhook URL or a transactional-email API.

Run standalone:
    uvicorn src.notification_service:app --port 8007
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Mock Notification Gateway", version="1.0")

_ALLOWED_CHANNELS = {"slack", "email"}
_log: List[dict] = []


class NotifyIn(BaseModel):
    channel: str
    priority: str
    message: str


class NotifyOut(BaseModel):
    notification_id: str
    delivered: bool


@app.post("/notify", response_model=NotifyOut)
def notify(payload: NotifyIn):
    if payload.channel not in _ALLOWED_CHANNELS:
        raise HTTPException(status_code=422, detail=f"Unknown channel '{payload.channel}' — expected one of {sorted(_ALLOWED_CHANNELS)}.")
    if not payload.message.strip():
        raise HTTPException(status_code=422, detail="Notification message cannot be empty.")
    notification_id = f"NOTIF-{uuid4().hex[:8]}"
    _log.append({
        "notification_id": notification_id, "channel": payload.channel,
        "priority": payload.priority, "message": payload.message,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    })
    return NotifyOut(notification_id=notification_id, delivered=True)


@app.get("/log")
def get_log():
    return _log


@app.get("/health")
def health():
    return {"status": "ok", "notifications_sent": len(_log)}
