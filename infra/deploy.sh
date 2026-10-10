#!/usr/bin/env bash
# Package Lambda code, deploy (create or update) the AWS stack and print its outputs.
# Usage: ./infra/deploy.sh
set -euo pipefail
export AWS_PAGER="" # print output directly instead of opening a "-- More --" pager

STACK_NAME="${STACK_NAME:-aussie-ecoguard}"
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
# Holds the zipped Lambda code. It lives outside the stack because it must
# exist before the stack is deployed.
ARTIFACT_BUCKET="${ARTIFACT_BUCKET:-aussie-ecoguard-artifacts-$ACCOUNT_ID}"
cd "$(dirname "$0")"

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
  --no-fail-on-empty-changeset

aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs" \
  --output table
