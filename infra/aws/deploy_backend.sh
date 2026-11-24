#!/usr/bin/env bash
# Option A: deploy a new version of the backend on an instance already set up by setup_ec2.sh.
# Run as ec2-user from the repo clone:
#
#   bash infra/aws/deploy_backend.sh            # pulls the current branch
#   BRANCH=main bash infra/aws/deploy_backend.sh
#   SKIP_PULL=1 bash infra/aws/deploy_backend.sh   # code was copied with rsync instead of git
#
# Pulls the code (including any new model/artifacts/<version>/ folders), installs dependencies,
# runs migrations, restarts the service and waits for GET /health.
# A new model version must be registered and activated through reviewed model management;
# development seeding is prohibited in production.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVICE=autosound-api
log() { printf '\n==> %s\n' "$*"; }

source "$REPO_DIR/infra/aws/deployment_guard.sh"
require_aws_deployment_consent
require_aws_demo_consent
python3 "$REPO_DIR/infra/aws/validate_production_env.py" "$REPO_DIR/backend/.env"
prepare_aws_edge_settings

cd "$REPO_DIR"
if [ "${SKIP_PULL:-0}" = "1" ]; then
  log "Using the code already on disk (SKIP_PULL=1)"
else
  log "Pull latest code"
  if [ -n "${BRANCH:-}" ]; then git fetch origin "$BRANCH" && git checkout "$BRANCH"; fi
  git pull --ff-only
  git log -1 --oneline
fi

# The pulled revision may change validation; check again before dependency/database changes.
python3 "$REPO_DIR/infra/aws/validate_production_env.py" "$REPO_DIR/backend/.env"

cd backend
log "Dependencies"
.venv/bin/pip install -r requirements.txt

log "Migrations"
.venv/bin/alembic upgrade head

log "Restart $SERVICE"
sudo systemctl restart $SERVICE

log "Health check"
healthy=0
for _ in $(seq 1 30); do
  if curl -fsS http://localhost:8000/health; then echo; healthy=1; break; fi
  sleep 2
done
if [ "$healthy" != 1 ]; then
  echo "API did not become healthy. Check: journalctl -u $SERVICE -n 100" >&2
  exit 1
fi

# Optional feeds are never silently restarted without the current-run request and cost consent.
for feed in edge:ENABLE_EDGE_DEMO demo-data:ENABLE_DEMO_DATA; do
  unit="autosound-${feed%%:*}"; flag="${feed##*:}"
  if [ "${!flag:-0}" = 1 ] && systemctl is-enabled --quiet "$unit" 2>/dev/null; then
    log "Restart $unit"
    sudo systemctl restart "$unit"
  elif systemctl is-enabled --quiet "$unit" 2>/dev/null; then
    sudo systemctl disable --now "$unit"
  fi
done
log "Deployed."
