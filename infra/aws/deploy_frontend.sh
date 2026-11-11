#!/usr/bin/env bash
# Build the React frontend and publish it to an S3 static-website bucket.
# Run from any machine with Node 18+ and the AWS CLI configured:
#
#   FRONTEND_BUCKET=<frontend-bucket> API_BASE_URL=https://<api-domain> \
#     bash infra/aws/deploy_frontend.sh
#
# The first run also turns on static website hosting and applies frontend-bucket-policy.json
# (public read). The bucket's "Block Public Access" settings must allow a public bucket policy.
# Afterwards, add the printed website URL to CORS_ORIGINS in backend/.env on the instance.
set -euo pipefail

: "${FRONTEND_BUCKET:?set FRONTEND_BUCKET to the S3 bucket name}"
: "${API_BASE_URL:?set API_BASE_URL to the backend URL, e.g. https://api.example.com}"
AWS_REGION="${AWS_REGION:-us-west-2}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
log() { printf '\n==> %s\n' "$*"; }

log "Build (VITE_USE_MOCK=false, VITE_API_BASE_URL=$API_BASE_URL)"
cd "$REPO_DIR/frontend"
npm ci
# Vite reads VITE_* variables from the environment, and they override .env files.
VITE_USE_MOCK=false VITE_API_BASE_URL="$API_BASE_URL" npm run build

log "Static website hosting on s3://$FRONTEND_BUCKET"
# Single-page app: unknown paths (e.g. /alerts/42 on reload) are served index.html.
aws s3 website "s3://$FRONTEND_BUCKET" --index-document index.html --error-document index.html
sed "s#<frontend-bucket>#$FRONTEND_BUCKET#g" "$REPO_DIR/infra/aws/frontend-bucket-policy.json" \
  > /tmp/frontend-bucket-policy.$$.json
aws s3api put-bucket-policy --bucket "$FRONTEND_BUCKET" \
  --policy "file:///tmp/frontend-bucket-policy.$$.json"
rm -f /tmp/frontend-bucket-policy.$$.json

log "Upload dist/"
# Hashed assets can be cached forever; index.html must always be revalidated.
aws s3 sync dist/ "s3://$FRONTEND_BUCKET" --delete \
  --exclude index.html --cache-control "public, max-age=31536000, immutable"
aws s3 cp dist/index.html "s3://$FRONTEND_BUCKET/index.html" \
  --cache-control "no-cache" --content-type "text/html"

log "Done: http://$FRONTEND_BUCKET.s3-website-$AWS_REGION.amazonaws.com"
