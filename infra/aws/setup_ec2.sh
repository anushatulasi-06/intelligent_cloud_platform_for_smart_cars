#!/usr/bin/env bash
# Option A: first-time setup of the backend on an EC2 instance (Amazon Linux 2023), run as
# ec2-user from a clone of this repo:
#
#   sudo dnf install -y git
#   git clone https://github.com/anushatulasi-06/intelligent_cloud_platform_for_smart_cars.git ~/autosound
#   cd ~/autosound    # full guide, incl. other ways to get the code: infra/aws/SETUP.md
#   bash infra/aws/setup_ec2.sh
#
# After explicit deployment/cost authorization, a missing settings file stops the first run after creating backend/.env from
# backend.env.production.example: fill in the <...> values, then run it again to finish.
#
# Options (environment variables):
#   SKIP_SEED=1         default: no public demo users or seeded alerts; bootstrap private accounts
#   ENABLE_EDGE_DEMO=1  also run the local edge client as a service (autosound-edge): keeps the
#                       C2 Simulator live and feeds C3/C4 without CARLA
#   ENABLE_DEMO_DATA=1  also run scripts/demo_data.py --live as a service (autosound-demo-data):
#                       moving cars on the C1 Map and Tracking pages
#   Both demo feeds read infra/aws/edge.env (created from edge.env.example on first use).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
SERVICE=autosound-api
log() { printf '\n==> %s\n' "$*"; }

source "$REPO_DIR/infra/aws/deployment_guard.sh"
require_aws_deployment_consent
require_aws_demo_consent

# Fail before package downloads, migrations, or service changes. Preparing a template also
# requires the explicit deployment consent above, but does not contact AWS.
if [ ! -f "$BACKEND_DIR/.env" ]; then
  cp "$REPO_DIR/infra/aws/backend.env.production.example" "$BACKEND_DIR/.env"
  chmod 600 "$BACKEND_DIR/.env"
  log "Created backend/.env. Fill in settings, validate locally, then rerun only when authorized."
  exit 0
fi
python3 "$REPO_DIR/infra/aws/validate_production_env.py" "$BACKEND_DIR/.env"
prepare_aws_edge_settings

log "System packages (Python 3.12, PostgreSQL 16 client for pg_dump, font for edge camera frames)"
sudo dnf install -y python3.12 python3.12-pip git postgresql16 dejavu-sans-fonts

if ! command -v mongodump >/dev/null; then
  log "MongoDB database tools (mongodump, for backups)"
  sudo tee /etc/yum.repos.d/mongodb-org-7.0.repo >/dev/null <<'EOF'
[mongodb-org-7.0]
name=MongoDB Repository
baseurl=https://repo.mongodb.org/yum/amazon/2023/mongodb-org/7.0/$basearch/
gpgcheck=1
enabled=1
gpgkey=https://pgp.mongodb.com/server-7.0.asc
EOF
  sudo dnf install -y mongodb-database-tools
fi

log "Python virtualenv and dependencies (CPU-only torch)"
cd "$BACKEND_DIR"
[ -d .venv ] || python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
# Install CPU torch first so requirements.txt does not pull the much larger CUDA build.
# Same pinned versions as backend/Dockerfile, so both deployment options run the same model stack.
.venv/bin/pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r requirements.txt

log "Database schema (alembic upgrade head)"
.venv/bin/alembic upgrade head

# The unit files assume /home/ec2-user/autosound; point them at wherever this repo actually is.
install_unit() {
  sed "s#/home/ec2-user/autosound#$REPO_DIR#g; s#^User=.*#User=$(id -un)#; s#^Group=.*#Group=$(id -gn)#" \
    "$REPO_DIR/infra/aws/$1.service" | sudo tee "/etc/systemd/system/$1.service" >/dev/null
}

log "systemd service $SERVICE"
install_unit "$SERVICE"
sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE
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

for feed in edge:ENABLE_EDGE_DEMO demo-data:ENABLE_DEMO_DATA; do
  unit="autosound-${feed%%:*}"; flag="${feed##*:}"
  if [ "${!flag:-0}" = "1" ]; then
    log "Optional service $unit"
    install_unit "$unit"
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$unit"
    sudo systemctl restart "$unit"
  elif systemctl is-enabled --quiet "$unit" 2>/dev/null; then
    log "Disable optional service $unit (not requested this run)"
    sudo systemctl disable --now "$unit"
  fi
done

log "Done. API listens on 127.0.0.1:8000 only. Use the SSH tunnel in infra/aws/SETUP.md."
