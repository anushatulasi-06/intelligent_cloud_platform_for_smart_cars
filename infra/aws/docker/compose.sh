#!/usr/bin/env bash
# Guarded administration of an already authorized AWS deployment; no secret values are printed.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
source "$REPO_DIR/infra/aws/deployment_guard.sh"
require_aws_deployment_consent
require_aws_demo_consent
python3 "$REPO_DIR/infra/aws/validate_production_env.py" "$REPO_DIR/backend/.env"
case "${1:-}" in
  exec|cp|logs|ps|stop) run_aws_compose "$@" ;;
  restart)
    if [ "$#" != 2 ] || [ "$2" != api ]; then
      echo "Only 'restart api' is allowed; use the setup wrapper for other starts/builds." >&2
      exit 1
    fi
    run_aws_compose restart api
    ;;
  *) echo "Use this helper for exec/cp/logs/ps/stop or 'restart api'; use setup_ec2_docker.sh for guarded builds and starts." >&2; exit 1 ;;
esac
