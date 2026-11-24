#!/usr/bin/env bash
# The former helper enabled a public HTTP S3 website and synced --delete.
# It is disabled. Use the private SSH UI workflows; neither needs a public UI bucket.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$REPO_DIR/infra/aws/deployment_guard.sh"
require_aws_deployment_consent
echo "Public S3 website publishing is disabled: this helper cannot provide HTTPS and secure access." >&2
echo "Use infra/aws/SETUP.md (private local UI) or infra/aws/docker/SETUP.md (private Docker UI)." >&2
echo "A public deployment needs a separate HTTPS/authentication review and cost authorization." >&2
exit 1
