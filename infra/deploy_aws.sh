#!/usr/bin/env bash
# Package Lambda code, deploy (create or update) the AWS stack and print its outputs.
# Usage: ./infra/deploy_aws.sh
set -euo pipefail
export AWS_PAGER="" # print output directly instead of opening a "-- More --" pager

STACK_NAME="${STACK_NAME:-aussie-ecoguard}"
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
# Holds the zipped Lambda code. It lives outside the stack because it must
# exist before the stack is deployed.
ARTIFACT_BUCKET="${ARTIFACT_BUCKET:-aussie-ecoguard-artifacts-$ACCOUNT_ID}"
cd "$(dirname "$0")"

# The tagging Lambda calls the Cloud Run service; deploy_gcp.sh must have run first.
if [ -z "${INFERENCE_URL:-}" ]; then
  INFERENCE_URL="$(gcloud run services describe "${SERVICE:-aussie-ecoguard-inference}" \
    --region "${GCP_REGION:-us-east4}" --format 'value(status.url)' 2>/dev/null || true)"
fi
if [ -z "$INFERENCE_URL" ]; then
  echo "Cannot find the Cloud Run URL. Run ./infra/deploy_gcp.sh first, or set INFERENCE_URL=https://..." >&2
  exit 1
fi
echo "Inference service: $INFERENCE_URL"

if ! aws s3api head-bucket --bucket "$ARTIFACT_BUCKET" >/dev/null 2>&1; then
  echo "Creating artifact bucket $ARTIFACT_BUCKET"
  aws s3 mb "s3://$ARTIFACT_BUCKET" --region "$REGION"
fi

# Zip each local Code folder, upload it, and write a template that points to S3.
aws cloudformation package \
  --template-file template.yaml \
  --s3-bucket "$ARTIFACT_BUCKET" \
  --s3-prefix "$STACK_NAME" \
  --output-template-file packaged.yaml

aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file packaged.yaml \
  --region "$REGION" \
  --parameter-overrides "InferenceUrl=$INFERENCE_URL" \
  --no-fail-on-empty-changeset

# CloudFormation does not redeploy the API when its routes change, so publish
# the current API configuration to the "api" stage on every run.
API_ID="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiId'].OutputValue" \
  --output text)"
aws apigateway create-deployment \
  --rest-api-id "$API_ID" \
  --stage-name api \
  --description "deploy_aws.sh" \
  --region "$REGION" >/dev/null
echo "Published API $API_ID to stage api"

# If LabRole loses its SQS permissions (seen after a Learner Lab session ends),
# Lambda turns the queue trigger off by itself and never turns it back on.
# Uploads then stay PENDING, so turn it back on here.
TAGGING_FUNCTION="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='TaggingFunctionName'].OutputValue" \
  --output text)"
ESM_UUID="$(aws lambda list-event-source-mappings \
  --function-name "$TAGGING_FUNCTION" \
  --region "$REGION" \
  --query "EventSourceMappings[0].UUID" \
  --output text)"
esm_state() {
  aws lambda get-event-source-mapping --uuid "$ESM_UUID" --region "$REGION" --query State --output text
}
if [ "$(esm_state)" = "Disabled" ]; then
  echo "Tagging queue trigger was disabled, enabling it"
  aws lambda update-event-source-mapping --uuid "$ESM_UUID" --enabled --region "$REGION" >/dev/null
  # "Enabling" usually takes a few seconds; wait so the smoke test sees "Enabled".
  for _ in $(seq 1 24); do
    [ "$(esm_state)" = "Enabled" ] && break
    sleep 5
  done
fi
echo "Tagging queue trigger: $(esm_state)"

# ============================================================================
# Website: write web/config.js for this stack and upload web/ to the website bucket
# ============================================================================
API_URL="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text)"
WEBSITE_BUCKET="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='WebsiteBucketName'].OutputValue" \
  --output text)"
# config.js is git-ignored: its values differ per account. app.js reads window.CONFIG.
echo "window.CONFIG = { apiUrl: \"$API_URL\" };" > ../web/config.js
# --delete removes files that are no longer in web/. no-cache makes browsers
# check for a new version on every load instead of keeping an old app.js.
aws s3 sync ../web "s3://$WEBSITE_BUCKET" \
  --delete \
  --cache-control no-cache \
  --region "$REGION" >/dev/null
echo "Uploaded web/ to $WEBSITE_BUCKET"
# ============================================================================

aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs" \
  --output table
