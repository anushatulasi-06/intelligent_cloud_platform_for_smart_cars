#!/usr/bin/env bash
# Option B: run the whole app in Docker on an EC2 instance (Amazon Linux 2023, x86_64), as ec2-user
# from a clone of this repo:
#
#   sudo dnf install -y git
#   git clone https://github.com/anushatulasi-06/intelligent_cloud_platform_for_smart_cars.git ~/autosound
#   cd ~/autosound    # full guide, incl. other ways to get the code: infra/aws/docker/SETUP.md
#   bash infra/aws/docker/setup_ec2_docker.sh
#
# The first run installs Docker and stops after creating backend/.env from
# infra/aws/backend.env.production.example: fill in the <...> values, then run it again.
# Safe to run again; after `git pull` it also deploys the new code (images are rebuilt).
#
# Options (environment variables, pass them on every run):
#   SKIP_SEED=1         do not load the demo users, cars, devices and alerts
#   ENABLE_EDGE_DEMO=1  also run the local edge client (C2 Simulator live without CARLA)
#   ENABLE_DEMO_DATA=1  also run scripts/demo_data.py --live (moving cars on Map/Tracking)
#   Both demo feeds read infra/aws/edge.env (created from edge.env.example on first use).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
COMPOSE_FILE="$REPO_DIR/infra/aws/docker/docker-compose.yml"
COMPOSE_VERSION=v2.29.7
BUILDX_VERSION=v0.17.1
log() { printf '\n==> %s\n' "$*"; }

[ "$(uname -m)" = "x86_64" ] || { echo "Use an x86_64 instance (e.g. t3.medium): the backup tools are amd64 only." >&2; exit 1; }

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

ENV_FILE="$REPO_DIR/backend/.env"
if [ ! -f "$ENV_FILE" ]; then
  cp "$REPO_DIR/infra/aws/backend.env.production.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  log "Created backend/.env. Fill in the <...> values (CORS_ORIGINS=http://<elastic-ip>), then run this script again."
  exit 0
fi
# Comment lines may mention <...>; only settings must be filled in.
if grep -v '^[[:space:]]*#' "$ENV_FILE" | grep -q '<'; then
  echo "backend/.env still has <...> placeholders:" >&2
  grep -n -v '^[[:space:]]*#' "$ENV_FILE" | grep '<' | cut -d= -f1 >&2
  echo "Fill them in and run again." >&2
  exit 1
fi

profiles=()
[ "${ENABLE_EDGE_DEMO:-0}" = "1" ] && profiles+=(edge)
[ "${ENABLE_DEMO_DATA:-0}" = "1" ] && profiles+=(demo-data)
if [ "${#profiles[@]}" -gt 0 ] && [ ! -f "$REPO_DIR/infra/aws/edge.env" ]; then
  cp "$REPO_DIR/infra/aws/edge.env.example" "$REPO_DIR/infra/aws/edge.env"
  chmod 600 "$REPO_DIR/infra/aws/edge.env"
  log "Created infra/aws/edge.env with the seeded demo devices (edit it to use other devices)"
fi
COMPOSE_PROFILES="$(IFS=,; echo "${profiles[*]:-}")"

log "Build and start (profiles: ${COMPOSE_PROFILES:-none}). The first build downloads torch and takes several minutes."
sudo env SKIP_SEED="${SKIP_SEED:-0}" COMPOSE_PROFILES="$COMPOSE_PROFILES" \
  docker compose -f "$COMPOSE_FILE" up --build -d

log "Health check"
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:8000/health >/dev/null && curl -fsS http://localhost/api/health >/dev/null; then
    curl -fsS http://localhost:8000/health; echo
    log "Done. UI: http://<elastic-ip>/   API: http://<elastic-ip>:8000"
    exit 0
  fi
  sleep 5
done
echo "Not healthy yet. Check: sudo docker compose -f $COMPOSE_FILE logs --tail=100 api" >&2
exit 1
