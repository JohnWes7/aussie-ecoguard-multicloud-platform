#!/usr/bin/env bash
# Build and deploy the inference service to Cloud Run, then print its URL.
# Cloud Build builds the image from inference/Dockerfile, so no local Docker is needed.
# Usage: ./infra/deploy_gcp.sh   (uses the current gcloud project unless GCP_PROJECT is set)
set -euo pipefail

PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GCP_REGION:-australia-southeast1}"
SERVICE="${SERVICE:-aussie-ecoguard-inference}"
cd "$(dirname "$0")/.."

echo "Deploying $SERVICE to project $PROJECT ($REGION)"

# --allow-unauthenticated: Cloud Run's built-in IAM check cannot validate Cognito
# tokens, so (per the architecture) requests will be checked in code instead.
# TODO(auth): verify Google ID tokens (AWS) and Cognito JWTs (browser) in main.py.
# --min-instances 0 scales to zero when idle; --max-instances 2 caps the cost.
gcloud run deploy "$SERVICE" \
  --source inference \
  --project "$PROJECT" \
  --region "$REGION" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 2 \
  --memory 512Mi \
  --cpu 1 \
  --timeout 60 \
  --quiet

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')"
echo "Service URL: $URL"
