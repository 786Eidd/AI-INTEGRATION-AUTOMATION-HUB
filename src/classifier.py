"""
AI-powered data processing — turning an unstructured inbound message into
the structured fields the business logic and CRM sync need (company name,
team size, urgency, spam/invalid flag).

## Why rule-based by default, and the real optional LLM path

Same constraint as the other two portfolio projects: this sandbox has no
outbound route to an LLM API. `classify()` is real, tested, deterministic
logic — regex entity extraction plus keyword-based urgency/spam
detection — not a placeholder. If `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`
is set, `classify()` instead makes a genuine LLM call asking for the same
four fields as structured JSON, and falls back to the rule-based path if
the call fails for any reason. `data/eval_results.json`'s
`classification_path` field records which path actually ran for every
test case — `"rule_based"` for every one of them in this environment, not
because the LLM path doesn't exist, but because no key was reachable here.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional

_COMPANY_RE = re.compile(
    r"\b(?:from|at|with)\s+((?:[A-Z][\w&']*\.?\s*){1,4})", re.UNICODE
)
_COMPANY_STOPWORDS = {"We", "I", "And", "Is", "Has", "Our", "The", "This"}

_EMPLOYEE_COUNT_RE = re.compile(r"(\d{1,6})\s*(?:\+\s*)?(?:employees|people|staff|team members)", re.IGNORECASE)

_URGENT_KEYWORDS = ["asap", "urgent", "immediately", "emergency", "right away", "today", "critical", "as soon as possible"]
_LOW_URGENCY_KEYWORDS = ["no rush", "whenever", "just curious", "just exploring", "no hurry"]
_SPAM_KEYWORDS = ["click here", "free money", "buy now", "act now", "limited time offer", "congratulations you"]


@dataclass
class Classification:
    company: Optional[str]
    employee_count: Optional[int]
    urgency: str  # "high" | "normal" | "low"
    deal_size_tier: str  # "small" | "medium" | "large" | "unknown"
    is_spam: bool
    path_used: str  # "rule_based" | "llm"


def _extract_company(text: str) -> Optional[str]:
    match = _COMPANY_RE.search(text)
    if not match:
        return None
    words = [w for w in match.group(1).split() if w not in _COMPANY_STOPWORDS]
    if not words:
        return None
    return " ".join(words).rstrip(".,")


def _extract_employee_count(text: str) -> Optional[int]:
    match = _EMPLOYEE_COUNT_RE.search(text)
    return int(match.group(1)) if match else None


def _deal_size_tier(employee_count: Optional[int]) -> str:
    if employee_count is None:
        return "unknown"
    if employee_count < 50:
        return "small"
    if employee_count <= 500:
        return "medium"
    return "large"


def _classify_urgency(text_lower: str) -> str:
    if any(kw in text_lower for kw in _URGENT_KEYWORDS):
        return "high"
    if any(kw in text_lower for kw in _LOW_URGENCY_KEYWORDS):
        return "low"
    return "normal"


def _is_spam(text_lower: str, email: str) -> bool:
    if any(kw in text_lower for kw in _SPAM_KEYWORDS):
        return True
    if len(text_lower.strip()) < 8:
        return True
    return False


def _classify_rule_based(text: str, email: str) -> Classification:
    text_lower = text.lower()
    employee_count = _extract_employee_count(text)
    return Classification(
        company=_extract_company(text),
        employee_count=employee_count,
        urgency=_classify_urgency(text_lower),
        deal_size_tier=_deal_size_tier(employee_count),
        is_spam=_is_spam(text_lower, email),
        path_used="rule_based",
    )


def _classify_with_llm(text: str, email: str) -> Optional[Classification]:
    prompt = (
        "Extract the following fields from this inbound lead message as strict JSON with keys "
        '"company" (string or null), "employee_count" (integer or null), "urgency" '
        '("high", "normal", or "low"), and "is_spam" (boolean). '
        f"Message: {text!r}\nJSON:"
    )

    raw_response = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # optional dependency

            client = anthropic.Anthropic()
            resp = client.messages.create(
                model="claude-3-5-haiku-20241022", max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_response = resp.content[0].text
        except Exception:
            return None
    elif os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI  # optional dependency

            client = OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o-mini", max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_response = resp.choices[0].message.content
        except Exception:
            return None
    else:
        return None

    try:
        parsed = json.loads(raw_response)
        employee_count = parsed.get("employee_count")
        return Classification(
            company=parsed.get("company"),
            employee_count=employee_count,
            urgency=parsed.get("urgency", "normal"),
            deal_size_tier=_deal_size_tier(employee_count),
            is_spam=bool(parsed.get("is_spam", False)),
            path_used="llm",
        )
    except Exception:
        return None  # malformed LLM output falls back to rule-based, not a crash


def classify(text: str, email: str) -> Classification:
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"):
        result = _classify_with_llm(text, email)
        if result is not None:
            return result
    return _classify_rule_based(text, email)
