#!/usr/bin/env bash
# Upload one model version (config.json + weights) to the Cloud Storage bucket
# that the inference service downloads at startup.
# Usage: ./infra/upload_model.sh v3
#   Run it in the environment from inference/requirements.txt (e.g. .venv-inference),
#   because it first runs infra/fetch_models.py to build models/.
#   Only needed to create a NEW version. Teammates copy existing versions with
#   model_source in terraform.tfvars instead (see README).
#
# Terraform creates the bucket and its read access (infra/terraform/gcp.tf),
# so run `terraform apply` once before this.
#
# Versions are never overwritten: each Cloud Run revision downloads the version
# in its MODEL_GCS at startup, so changing a version in place would change the
# model of new instances of a running revision. To change the model, upload a
# new version and set model_version (see README).
set -euo pipefail

VERSION="${1:?usage: $0 <version>, e.g. v3}"
PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format 'value(projectNumber)')"
BUCKET="${MODEL_BUCKET:-aussie-ecoguard-models-$PROJECT_NUMBER}"
cd "$(dirname "$0")/.."

if ! gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" >/dev/null 2>&1; then
  echo "gs://$BUCKET not found. Run terraform apply in infra/terraform first; it creates the bucket." >&2
  exit 1
fi

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
echo "To use it: set model_version = \"$VERSION\" in infra/terraform/terraform.tfvars, then terraform apply"
