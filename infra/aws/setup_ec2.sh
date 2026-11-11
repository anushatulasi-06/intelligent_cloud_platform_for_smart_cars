#!/usr/bin/env bash
# First-time setup of the backend on an EC2 instance (Amazon Linux 2023), run as ec2-user from
# a clone of this repo:
#
#   sudo dnf install -y git
#   git clone <repo-url> ~/autosound && cd ~/autosound
#   bash infra/aws/setup_ec2.sh
#
# Safe to run again. The first run stops after creating backend/.env from
# backend.env.production.example: fill in the <...> values, then run it again to finish.
#
# Options (environment variables):
#   SKIP_SEED=1   do not load demo data (python -m app.db.seed is idempotent, but it adds demo
#                 users, cars and alerts, so skip it for a non-demo deployment)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
SERVICE=autosound-api
log() { printf '\n==> %s\n' "$*"; }

log "System packages (Python 3.12, PostgreSQL 16 client for pg_dump)"
sudo dnf install -y python3.12 python3.12-pip git postgresql16

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
.venv/bin/pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
  cp "$REPO_DIR/infra/aws/backend.env.production.example" .env
  chmod 600 .env
  log "Created backend/.env. Fill in the <...> values, then run this script again."
  exit 0
fi
if grep -q '<' .env; then
  echo "backend/.env still has <...> placeholders. Fill them in and run again." >&2
  exit 1
fi

log "Database schema (alembic upgrade head)"
.venv/bin/alembic upgrade head

if [ "${SKIP_SEED:-0}" != "1" ]; then
  log "Seed data (demo users, cars, alert types, cnn-v1 model row)"
  .venv/bin/python -m app.db.seed
fi

log "systemd service $SERVICE"
# The unit file assumes /home/ec2-user/autosound; point it at wherever this repo actually is.
sed "s#/home/ec2-user/autosound#$REPO_DIR#g; s#^User=.*#User=$(id -un)#; s#^Group=.*#Group=$(id -gn)#" \
  "$REPO_DIR/infra/aws/$SERVICE.service" | sudo tee /etc/systemd/system/$SERVICE.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE
sudo systemctl restart $SERVICE

log "Health check"
for _ in $(seq 1 30); do
  if curl -fsS http://localhost:8000/health; then echo; log "Done. API is on port 8000."; exit 0; fi
  sleep 2
done
echo "API did not become healthy. Check: journalctl -u $SERVICE -n 100" >&2
exit 1
