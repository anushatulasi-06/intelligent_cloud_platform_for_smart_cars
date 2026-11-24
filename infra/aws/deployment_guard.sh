#!/usr/bin/env bash
# Source this before any installation, download, database write, restart, or AWS operation.
# These acknowledgements prevent accidental execution; they are NOT a billing limit.
require_aws_deployment_consent() {
  if [ "${ALLOW_AWS_DEPLOYMENT:-}" != "I_ACCEPT_AWS_RESOURCE_CHANGES" ] ||
     [ "${AWS_COST_ACK:-}" != "I_ACCEPT_AWS_USAGE_COSTS" ]; then
    echo "Deployment blocked. This operation can consume AWS credits or incur charges." >&2
    echo "Read infra/aws/README.md. Explicit deployment and cost acknowledgements are required." >&2
    return 1
  fi
  local credit_budget="${AWS_CREDIT_BUDGET_USD:-0}"
  if ! [[ "$credit_budget" =~ ^[0-9]+([.][0-9]+)?$ ]] || ! [[ "$credit_budget" =~ [1-9] ]]; then
    echo "Deployment blocked: AWS_CREDIT_BUDGET_USD must be an explicitly authorized positive amount." >&2
    echo "A zero-credit budget permits local review/tests only. This variable is not a hard spending cap." >&2
    return 1
  fi
}

require_aws_demo_consent() {
  local flag
  for flag in SKIP_SEED ENABLE_EDGE_DEMO ENABLE_DEMO_DATA; do
    if [ -n "${!flag:-}" ] && [ "${!flag}" != 0 ] && [ "${!flag}" != 1 ]; then
      echo "Deployment blocked: $flag must be 0 or 1." >&2
      return 1
    fi
  done
  if [ "${SKIP_SEED:-1}" != 1 ]; then
    echo "Production demo seed blocked: use the private empty-database bootstrap, not public development credentials." >&2
    return 1
  fi
  if { [ "${ENABLE_EDGE_DEMO:-0}" = 1 ] || [ "${ENABLE_DEMO_DATA:-0}" = 1 ]; } &&
     [ "${ALLOW_AWS_DEMO_FEEDS:-}" != I_UNDERSTAND_REQUEST_COSTS ]; then
    echo "Demo feeds blocked: acknowledge their continuing database/S3 request costs explicitly." >&2
    return 1
  fi
}

prepare_aws_edge_settings() {
  if [ "${ENABLE_EDGE_DEMO:-0}" != 1 ] && [ "${ENABLE_DEMO_DATA:-0}" != 1 ]; then
    return 0
  fi
  local edge_file="$REPO_DIR/infra/aws/edge.env"
  if [ ! -f "$edge_file" ]; then
    cp "$REPO_DIR/infra/aws/edge.env.example" "$edge_file"
    chmod 600 "$edge_file"
    echo "Created private infra/aws/edge.env template; fill in your private device keys before rerunning." >&2
    return 1
  fi
  python3 "$REPO_DIR/infra/aws/validate_production_env.py" --edge-settings "$edge_file"
}

run_aws_compose() {
  # sudo normally drops exported shell variables. Pass every interpolation value explicitly
  # for stop/admin calls too, not only `up`; never print settings or credentials here.
  sudo env ALLOW_AWS_DEPLOYMENT="$ALLOW_AWS_DEPLOYMENT" AWS_COST_ACK="$AWS_COST_ACK" \
    AWS_CREDIT_BUDGET_USD="$AWS_CREDIT_BUDGET_USD" \
    COMPOSE_PROFILES="${COMPOSE_PROFILES:-}" \
    docker compose -f "$REPO_DIR/infra/aws/docker/docker-compose.yml" "$@"
}
