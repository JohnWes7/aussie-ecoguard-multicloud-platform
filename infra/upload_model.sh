#!/usr/bin/env bash
# Upload one model version (config.json + weights) to the Cloud Storage bucket
# that the inference service mounts read-only at /models.
# Usage: ./infra/upload_model.sh v1
#   Run it in the environment from inference/requirements.txt (e.g. .venv-inference),
#   because it first runs infra/fetch_models.py to build models/.
#
# Versions are never overwritten: a Cloud Run revision reads /models/<version>
# (env var MODEL_DIR), so changing a version in place would change the model of
# revisions that are already running. To change the model, upload a new version
# and point the service at it (see README).
set -euo pipefail

VERSION="${1:?usage: $0 <version>, e.g. v1}"
PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GCP_REGION:-us-east4}"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format 'value(projectNumber)')"
BUCKET="${MODEL_BUCKET:-aussie-ecoguard-models-$PROJECT_NUMBER}"
# Cloud Run runs as the Compute Engine default service account unless told otherwise.
RUNTIME_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"
cd "$(dirname "$0")/.."

if ! gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" >/dev/null 2>&1; then
  echo "Creating bucket gs://$BUCKET ($REGION)"
  gcloud storage buckets create "gs://$BUCKET" --project "$PROJECT" --location "$REGION" \
    --uniform-bucket-level-access --public-access-prevention
fi

# Read access on this bucket only, not the whole project. Adding a binding that
# already exists changes nothing.
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member "serviceAccount:$RUNTIME_SA" --role roles/storage.objectViewer >/dev/null
echo "Read access: $RUNTIME_SA"

if gcloud storage ls "gs://$BUCKET/$VERSION/" >/dev/null 2>&1; then
  echo "gs://$BUCKET/$VERSION/ already exists. Versions are never overwritten; pick a new name." >&2
  exit 1
fi

# Reuses the downloaded files when they are cached, and always copies the
# current infra/model/config.json.
"${PYTHON:-python}" infra/fetch_models.py models

echo "Uploading models/ to gs://$BUCKET/$VERSION/"
gcloud storage rsync --recursive models "gs://$BUCKET/$VERSION"

echo "Local:  $(du -sb models | cut -f1) bytes, $(find models -type f | wc -l) files"
echo "Bucket: $(gcloud storage du --summarize "gs://$BUCKET/$VERSION" | cut -d' ' -f1) bytes," \
  "$(gcloud storage ls "gs://$BUCKET/$VERSION/**" | wc -l) files"
echo "Deploy with: MODEL_VERSION=$VERSION ./infra/deploy_gcp.sh"
