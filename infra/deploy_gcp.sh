#!/usr/bin/env bash
# Build and deploy the inference service to Cloud Run, then print its URL.
# Cloud Build builds the image from inference/Dockerfile, so no local Docker is needed.
# Usage: MODEL_VERSION=v1 ./infra/deploy_gcp.sh
#   (uses the current gcloud project unless GCP_PROJECT is set)
# The model files are not in the image: upload them first with ./infra/upload_model.sh.
set -euo pipefail
# Git Bash on Windows rewrites arguments that look like Unix paths
# (mount-path=/models -> C:/Program Files/Git/models). Skip that for the two
# arguments below that start with these prefixes. (Turning it off for everything
# with MSYS_NO_PATHCONV breaks the gcloud wrapper script.) No effect elsewhere.
export MSYS2_ARG_CONV_EXCL="volume=;MODEL_DIR="

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
# Model: the whole bucket is mounted read-only at /models and MODEL_DIR picks one
# version folder, so every revision keeps the model it started with. Changing the
# model = upload a new version + `gcloud run services update --update-env-vars
# MODEL_DIR=/models/<version>`. --clear-volumes/--clear-volume-mounts first, so
# running this script again replaces the mount instead of adding a second one.
# Resources: the models need about 3 GB at peak, so 4Gi (which needs 2 CPUs).
# --concurrency 1: one inference at a time per instance; a second request would
# only compete for the same CPUs. OMP_NUM_THREADS=2 makes torch use the 2 CPUs
# it has rather than the host's core count. --cpu-boost gives extra CPU while
# the instance starts and loads the models.
# --timeout 300 for now, while cold start and inference times are measured (7c).
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
  --add-volume "name=models,type=cloud-storage,bucket=$MODEL_BUCKET,readonly=true" \
  --add-volume-mount "volume=models,mount-path=/models" \
  --set-env-vars "MODEL_DIR=/models/$MODEL_VERSION,OMP_NUM_THREADS=2" \
  --memory 4Gi \
  --cpu 2 \
  --concurrency 1 \
  --cpu-boost \
  --timeout 300 \
  --quiet

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')"
echo "Service URL: $URL"
