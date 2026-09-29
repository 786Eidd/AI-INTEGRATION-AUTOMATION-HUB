"""AI Integration Hub — package root. Empty on purpose; makes `src` an
explicit package so relative imports between modules (schema, crm_service,
notification_service, transform, classifier, notifier, orchestrator, app)
resolve the same way whether you run `uvicorn src.app:app` or
`python -m src.evaluate`.
"""
