#!/usr/bin/env bash
# Build and deploy the inference service to Cloud Run, then print its URL.
# Cloud Build builds the image from inference/Dockerfile, so no local Docker is needed.
# Usage: MODEL_VERSION=v1 ./infra/deploy_gcp.sh
#   (uses the current gcloud project unless GCP_PROJECT is set)
# The model files are not in the image: upload them first with ./infra/upload_model.sh.
set -euo pipefail

PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
# us-east4 (N. Virginia) sits next to AWS us-east-1, so the Lambda call and the S3 download stay in one metro.
REGION="${GCP_REGION:-us-east4}"
SERVICE="${SERVICE:-aussie-ecoguard-inference}"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format 'value(projectNumber)')"
MODEL_BUCKET="${MODEL_BUCKET:-aussie-ecoguard-models-$PROJECT_NUMBER}"
MODEL_VERSION="${MODEL_VERSION:-v1}"
cd "$(dirname "$0")/.."

if ! gcloud storage ls "gs://$MODEL_BUCKET/$MODEL_VERSION/config.json" >/dev/null 2>&1; then
  echo "gs://$MODEL_BUCKET/$MODEL_VERSION/config.json not found. Run ./infra/upload_model.sh $MODEL_VERSION first." >&2
  exit 1
fi

echo "Deploying $SERVICE to project $PROJECT ($REGION), model $MODEL_VERSION"

# --allow-unauthenticated: Cloud Run's built-in IAM check cannot validate Cognito
# tokens, so (per the architecture) requests will be checked in code instead.
# TODO(auth): verify Google ID tokens (AWS) and Cognito JWTs (browser) in main.py.
# --min-instances 0 scales to zero when idle; --max-instances 2 caps the cost
# (and matches the tagging queue's MaximumConcurrency 2).
# Model: MODEL_GCS names one version folder in the model bucket; the service
# downloads it at startup (main.py), so every revision keeps the model it started
# with. Changing the model = upload a new version + `gcloud run services update
# --update-env-vars MODEL_GCS=gs://<bucket>/<version>`. (Reading the files through
# a Cloud Storage volume mount instead took about 3 minutes to load.)
# --clear-volumes/--clear-volume-mounts remove that mount from older revisions;
# they change nothing when there is none.
# Resources: the models need about 3 GB at peak, plus ~1.1 GB while the
# downloaded files sit in /tmp (memory on Cloud Run), so 8Gi.
# 4 CPUs: inference takes half as long as with 2 (fox photo ~4 s instead of
# ~7.5 s), and as memory is billed per second too, an image costs slightly less.
# --concurrency 1: one inference at a time per instance; a second request would
# only compete for the same CPUs. OMP_NUM_THREADS=4 makes torch use the 4 CPUs
# it has rather than the host's core count. --cpu-boost gives extra CPU while
# the instance starts and loads the models.
# --timeout 300: a cold start (~1 min: download + load the models) plus the
# longest video accepted (3 minutes = 18 frames, a few seconds each). The
# tagging Lambda waits the same time (INFER_TIMEOUT in infra/terraform/tagging.tf).
gcloud run deploy "$SERVICE" \
  --source inference \
  --project "$PROJECT" \
  --region "$REGION" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 2 \
  --execution-environment gen2 \
  --clear-volumes \
  --clear-volume-mounts \
  --set-env-vars "MODEL_GCS=gs://$MODEL_BUCKET/$MODEL_VERSION,OMP_NUM_THREADS=4" \
  --memory 8Gi \
  --cpu 4 \
  --concurrency 1 \
  --cpu-boost \
  --timeout 300 \
  --quiet

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')"
echo "Service URL: $URL"
