#!/usr/bin/env bash
# Deploy (create or update) the AWS stack and print its outputs.
# Usage: ./infra/deploy.sh
set -euo pipefail

STACK_NAME="${STACK_NAME:-ecoguard}"
REGION="${AWS_REGION:-us-east-1}"
cd "$(dirname "$0")"

aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file template.yaml \
  --region "$REGION" \
  --no-fail-on-empty-changeset

aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs" \
  --output table
