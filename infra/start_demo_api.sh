#!/bin/sh
set -eu

# Compose waits for SQL, Mongo and bucket bootstrap before starting this process.
alembic upgrade head
python -m app.db.seed
python -c 'from app.services.orchestrator import MLOrchestrator; model = MLOrchestrator().reload_model(); assert model is not None, "No active model"; print("Active model ready:", model.version_label)'
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
