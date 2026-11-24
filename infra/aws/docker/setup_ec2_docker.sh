#!/usr/bin/env bash
# Option B: run the whole app in Docker on an EC2 instance (Amazon Linux 2023, x86_64), as ec2-user
# from a clone of this repo:
#
#   sudo dnf install -y git
#   git clone https://github.com/anushatulasi-06/intelligent_cloud_platform_for_smart_cars.git ~/autosound
#   cd ~/autosound    # full guide, incl. other ways to get the code: infra/aws/docker/SETUP.md
#   bash infra/aws/docker/setup_ec2_docker.sh
#
# After explicit deployment/cost authorization, a missing settings file stops the first run after creating backend/.env from
# infra/aws/backend.env.production.example: fill in the <...> values, then run it again.
# Safe to run again; after `git pull` it also deploys the new code (images are rebuilt).
#
# Options (environment variables, pass them on every run):
#   SKIP_SEED=1         default: no public demo users; bootstrap private accounts afterwards
#   ENABLE_EDGE_DEMO=1  also run the local edge client (C2 Simulator live without CARLA)
#   ENABLE_DEMO_DATA=1  also run scripts/demo_data.py --live (moving cars on Map/Tracking)
#   Both demo feeds read infra/aws/edge.env (created from edge.env.example on first use).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
COMPOSE_FILE="$REPO_DIR/infra/aws/docker/docker-compose.yml"
COMPOSE_VERSION=v2.29.7
BUILDX_VERSION=v0.17.1
log() { printf '\n==> %s\n' "$*"; }

source "$REPO_DIR/infra/aws/deployment_guard.sh"
require_aws_deployment_consent
require_aws_demo_consent

ENV_FILE="$REPO_DIR/backend/.env"
if [ ! -f "$ENV_FILE" ]; then
  cp "$REPO_DIR/infra/aws/backend.env.production.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  log "Created backend/.env. Fill in settings and validate locally before an authorized rerun."
  exit 0
fi
python3 "$REPO_DIR/infra/aws/validate_production_env.py" "$ENV_FILE"
prepare_aws_edge_settings

[ "$(uname -m)" = "x86_64" ] || { echo "Use an x86_64 instance (e.g. c7i-flex.large): the backup tools are amd64 only." >&2; exit 1; }

if ! command -v docker >/dev/null; then
  log "Docker"
  sudo dnf install -y docker
fi
sudo systemctl enable --now docker

plugins=/usr/local/lib/docker/cli-plugins
if ! sudo docker compose version >/dev/null 2>&1; then
  log "Docker Compose plugin $COMPOSE_VERSION"
  sudo mkdir -p "$plugins"
  sudo curl -fsSL -o "$plugins/docker-compose" \
    "https://github.com/docker/compose/releases/download/$COMPOSE_VERSION/docker-compose-linux-x86_64"
  sudo chmod +x "$plugins/docker-compose"
fi
if ! sudo docker buildx version >/dev/null 2>&1; then
  log "Docker Buildx plugin $BUILDX_VERSION (used by compose build)"
  sudo mkdir -p "$plugins"
  sudo curl -fsSL -o "$plugins/docker-buildx" \
    "https://github.com/docker/buildx/releases/download/$BUILDX_VERSION/buildx-$BUILDX_VERSION.linux-amd64"
  sudo chmod +x "$plugins/docker-buildx"
fi

profiles=()
[ "${ENABLE_EDGE_DEMO:-0}" = "1" ] && profiles+=(edge)
[ "${ENABLE_DEMO_DATA:-0}" = "1" ] && profiles+=(demo-data)
COMPOSE_PROFILES="$(IFS=,; echo "${profiles[*]:-}")"

log "Build and start (profiles: ${COMPOSE_PROFILES:-none}). The first build downloads torch and takes several minutes."
# Stop previously enabled optional feeds when not explicitly requested now. Compose `up`
# without their profiles would otherwise leave existing containers running.
for feed in edge-demo:ENABLE_EDGE_DEMO demo-data:ENABLE_DEMO_DATA; do
  flag="${feed##*:}"
  if [ "${!flag:-0}" != 1 ]; then
    run_aws_compose stop "${feed%%:*}"
  fi
done
run_aws_compose up --build -d

log "Health check"
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:8000/health >/dev/null && curl -fsS http://localhost/api/health >/dev/null; then
    curl -fsS http://localhost:8000/health; echo
    log "Done. UI/API host ports are loopback-only; use the SSH tunnel in infra/aws/docker/SETUP.md."
    exit 0
  fi
  sleep 5
done
echo "Not healthy yet. With authorized consent, check: bash infra/aws/docker/compose.sh logs --tail=100 api" >&2
exit 1
