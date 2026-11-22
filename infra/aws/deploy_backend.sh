#!/usr/bin/env bash
# Deploy a new version of the backend on an instance already set up by setup_ec2.sh.
# Run as ec2-user from the repo clone:
#
#   bash infra/aws/deploy_backend.sh            # pulls the current branch
#   BRANCH=main bash infra/aws/deploy_backend.sh
#
# Pulls the code (including any new model/artifacts/<version>/ folders), installs dependencies,
# runs migrations, restarts the service and waits for GET /health.
# A new model version still has to be registered (seed) and activated in F8b; see README.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVICE=autosound-api
log() { printf '\n==> %s\n' "$*"; }

cd "$REPO_DIR"
log "Pull latest code"
if [ -n "${BRANCH:-}" ]; then git fetch origin "$BRANCH" && git checkout "$BRANCH"; fi
git pull --ff-only
git log -1 --oneline

cd backend
log "Dependencies"
.venv/bin/pip install -r requirements.txt

log "Migrations"
.venv/bin/alembic upgrade head

log "Restart $SERVICE"
sudo systemctl restart $SERVICE

log "Health check"
for _ in $(seq 1 30); do
  if curl -fsS http://localhost:8000/health; then echo; log "Deployed."; exit 0; fi
  sleep 2
done
echo "API did not become healthy. Check: journalctl -u $SERVICE -n 100" >&2
exit 1
