#!/usr/bin/env bash
# Upload one file the same way the website will (POST /upload-url, then PUT to
# S3), wait for tagging to finish and print what was stored in DynamoDB.
# The file stays in the system; delete it by hand if it was only a test.
# Needs AWS credentials. Usage: ./scripts/upload.sh <file.jpg|png|mp4|mov>
set -euo pipefail
export AWS_PAGER=""

FILE="${1:?usage: ./scripts/upload.sh <file>}"
REGION="${AWS_REGION:-us-east-1}"

# output <name>: one value from infra/terraform/aws/outputs.tf (read from the local state).
output() {
  terraform -chdir="$(dirname "$0")/../infra/terraform/aws" output -raw "$1"
}
json() { python -c "import json,sys; print(json.load(sys.stdin)$1)"; }
API="$(output upload_url_endpoint)"
TABLE="$(output media_table_name)"

# Read from stdin: given a Windows path with backslashes, sha256sum prefixes its output with "\".
SHA="$(sha256sum < "$FILE" | cut -d' ' -f1)"
# Only the extension matters to upload-url, so send a fixed name and avoid
# quoting problems with unusual file names.
EXT="${FILE##*.}"
echo "File:    $FILE"
echo "SHA-256: $SHA"

# Prints every item of the file (META and its TAG# items).
show_items() {
  aws dynamodb query --table-name "$TABLE" --region "$REGION" \
    --key-condition-expression "file_id = :id" \
    --expression-attribute-values "{\":id\":{\"S\":\"$SHA\"}}" \
    --query "Items" --output json | python -c '
import json, sys
def plain(value):  # {"S": "fox"} -> "fox", {"L": [{"S": "a"}]} -> ["a"]
    (kind, inner), = value.items()
    return [plain(v) for v in inner] if kind == "L" else inner
for item in json.load(sys.stdin):
    print("  " + json.dumps({k: plain(v) for k, v in item.items() if k != "file_id"}))'
}

# 1. Reserve the checksum and get a presigned PUT URL
RESPONSE="$(curl -s -w '\n%{http_code}' -X POST "$API" -H "Content-Type: application/json" \
  -d "{\"sha256\": \"$SHA\", \"filename\": \"upload.$EXT\"}")"
STATUS="${RESPONSE##*$'\n'}"
BODY="${RESPONSE%$'\n'*}"
if [ "$STATUS" = "409" ]; then
  echo "Already in the system (status $(echo "$BODY" | json "['status']")):"
  show_items
  exit 0
fi
if [ "$STATUS" != "200" ]; then
  echo "POST /upload-url returned $STATUS: $BODY" >&2
  exit 1
fi
URL="$(echo "$BODY" | json "['url']")"
CONTENT_TYPE="$(echo "$BODY" | json "['headers']['Content-Type']")"
CHECKSUM="$(echo "$BODY" | json "['headers']['x-amz-checksum-sha256']")"

# 2. Upload straight to S3 with exactly the signed headers
STATUS="$(curl -s -o /dev/null -w '%{http_code}' -X PUT -T "$FILE" "$URL" \
  -H "Content-Type: $CONTENT_TYPE" -H "x-amz-checksum-sha256: $CHECKSUM")"
if [ "$STATUS" != "200" ]; then
  echo "PUT to S3 returned $STATUS" >&2
  exit 1
fi
echo "Uploaded, waiting for tagging..."

# 3. Wait until tagging moves META to READY or FAILED. Up to 6 minutes: a cold
#    Cloud Run start takes about a minute, and a 3-minute video has 18 frames.
for _ in $(seq 1 180); do
  STATE="$(aws dynamodb get-item --table-name "$TABLE" --region "$REGION" \
    --key "{\"file_id\":{\"S\":\"$SHA\"},\"sk\":{\"S\":\"META\"}}" \
    --query "Item.status.S" --output text)"
  case "$STATE" in READY | FAILED) break ;; esac
  sleep 2
done
echo "Status: $STATE"
show_items
[ "$STATE" = "READY" ] || [ "$STATE" = "FAILED" ] || {
  echo "Still $STATE after 360 s. See README Troubleshooting." >&2
  exit 1
}
