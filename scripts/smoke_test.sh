#!/usr/bin/env bash
# End-to-end check of the deployed stack: tagging queue trigger, CORS preflight,
# POST /upload-url, PUT to the presigned URL, tagging (EventBridge -> SQS ->
# Lambda -> Cloud Run -> DynamoDB), duplicate rejection, bad requests and the website.
# Cleans up its test file afterwards.
# Needs AWS credentials. Usage: ./scripts/smoke_test.sh
set -uo pipefail
export AWS_PAGER=""

STACK_NAME="${STACK_NAME:-aussie-ecoguard}"
REGION="${AWS_REGION:-us-east-1}"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
API="$(output UploadUrlEndpoint)"
BUCKET="$(output MediaBucketName)"
TABLE="$(output MediaTableName)"
ORIGIN="https://$(output WebsiteBucketName).s3.amazonaws.com"

FAILURES=0
check() { # check <name> <actual> <expected>
  if [ "$2" = "$3" ]; then echo "PASS  $1"; else echo "FAIL  $1 (got '$2', expected '$3')"; FAILURES=$((FAILURES + 1)); fi
}
json() { python -c "import json,sys; print(json.load(sys.stdin)$1)"; }
# meta <path>: one attribute of the test file's META item, e.g. status.S ("None" if missing)
meta() {
  aws dynamodb get-item --table-name "$TABLE" --region "$REGION" \
    --key "{\"file_id\":{\"S\":\"$SHA\"},\"sk\":{\"S\":\"META\"}}" \
    --query "Item.$1" --output text
}

WORK="$(mktemp -d)"
FILE="$WORK/smoke.jpg"
head -c 2048 /dev/urandom > "$FILE" # random bytes, so the checksum is always new
SHA="$(sha256sum "$FILE" | cut -d' ' -f1)"
echo "API: $API"
echo "Test file sha256: $SHA"

cleanup() {
  aws s3 rm "s3://$BUCKET/uploads/$SHA" --region "$REGION" >/dev/null 2>&1
  # Tagging writes TAG# items next to META, so delete every item of the file.
  # A leftover TAG# item would turn up in tag searches as a file that does not exist.
  aws dynamodb query --table-name "$TABLE" --region "$REGION" \
    --key-condition-expression "file_id = :id" \
    --expression-attribute-values "{\":id\":{\"S\":\"$SHA\"}}" \
    --query "Items[].sk.S" --output text 2>/dev/null | tr '\t' '\n' | tr -d '\r' |
    while IFS= read -r SK; do
      [ -n "$SK" ] && [ "$SK" != "None" ] || continue
      aws dynamodb delete-item --table-name "$TABLE" --region "$REGION" \
        --key "{\"file_id\":{\"S\":\"$SHA\"},\"sk\":{\"S\":\"$SK\"}}" >/dev/null 2>&1
    done
  rm -rf "$WORK"
}
trap cleanup EXIT

# 0. Tagging queue trigger. Lambda turns it off by itself when LabRole loses its
#    SQS permissions (e.g. after a Learner Lab session ends); uploads then stay PENDING.
ESM_STATE="$(aws lambda list-event-source-mappings --function-name "$(output TaggingFunctionName)" \
  --region "$REGION" --query "EventSourceMappings[0].State" --output text)"
check "tagging queue trigger enabled" "$ESM_STATE" "Enabled"
[ "$ESM_STATE" = "Enabled" ] || echo "      Run ./infra/deploy_aws.sh to enable it again (see README Troubleshooting)"

# 1. CORS preflight
PREFLIGHT="$(curl -s -i -X OPTIONS "$API" -H "Origin: $ORIGIN" \
  -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: content-type")"
check "preflight status" "$(echo "$PREFLIGHT" | head -1 | cut -d' ' -f2)" "200"
check "preflight allows POST" "$(echo "$PREFLIGHT" | grep -ci 'access-control-allow-methods:.*POST')" "1"

# 2. POST /upload-url for a new file
BODY="$WORK/body.json"
STATUS="$(curl -s -o "$BODY" -w '%{http_code}' -X POST "$API" -H "Content-Type: application/json" \
  -d "{\"sha256\": \"$SHA\", \"filename\": \"smoke.jpg\"}")"
check "POST new file" "$STATUS" "200"
URL="$(json "['url']" < "$BODY")"
CHECKSUM="$(json "['headers']['x-amz-checksum-sha256']" < "$BODY")"

# 3. PUT the file to the presigned URL
STATUS="$(curl -s -o /dev/null -w '%{http_code}' -X PUT -T "$FILE" "$URL" \
  -H "Content-Type: image/jpeg" -H "x-amz-checksum-sha256: $CHECKSUM")"
check "PUT to presigned URL" "$STATUS" "200"

# 4. Different content with the same URL must be rejected (checksum is signed)
head -c 2048 /dev/urandom > "$WORK/other.jpg"
STATUS="$(curl -s -o /dev/null -w '%{http_code}' -X PUT -T "$WORK/other.jpg" "$URL" \
  -H "Content-Type: image/jpeg" -H "x-amz-checksum-sha256: $CHECKSUM")"
check "PUT different content rejected" "$STATUS" "400"

# 5. Tagging: S3 event -> EventBridge -> SQS -> tagging Lambda -> Cloud Run -> DynamoDB.
#    Usually done in about 10 s. The stub only hashes the file, so random bytes work.
#    TODO(real model): random bytes cannot be decoded, so use a real image then.
STATE="$(meta status.S)"
for _ in $(seq 1 30); do
  case "$STATE" in READY | FAILED) break ;; esac
  sleep 2
  STATE="$(meta status.S)"
done
check "tagging finished (META status)" "$STATE" "READY"
[ "$STATE" != "FAILED" ] || echo "      error: $(meta error.S)"
check "expires_at removed (no TTL on stored files)" "$(meta expires_at.N)" "None"
TAG_COUNT="$(aws dynamodb query --table-name "$TABLE" --region "$REGION" \
  --key-condition-expression "file_id = :id AND begins_with(sk, :tag)" \
  --expression-attribute-values "{\":id\":{\"S\":\"$SHA\"},\":tag\":{\"S\":\"TAG#\"}}" \
  --select COUNT --query Count --output text)"
check "TAG# items written" "$([ "${TAG_COUNT:-0}" -ge 1 ] && echo yes || echo no)" "yes"

# 6. The same file again is a duplicate now
STATUS="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API" -H "Content-Type: application/json" \
  -d "{\"sha256\": \"$SHA\", \"filename\": \"smoke.jpg\"}")"
check "POST same file after READY returns 409" "$STATUS" "409"

# 7. GET /files/{sha256} and GET /files show the tagged file
FILES_API="$(output ApiUrl)/files"
STATUS="$(curl -s -o "$WORK/file.json" -w '%{http_code}' "$FILES_API/$SHA")"
check "GET /files/{sha256}" "$STATUS" "200"
check "GET /files/{sha256} is READY with tags" \
  "$(python -c "import json,sys; f = json.load(sys.stdin); print(f['status'], bool(f['tags']))" < "$WORK/file.json")" "READY True"
check "GET /files lists the file" \
  "$(curl -s "$FILES_API" | python -c "import json,sys; print(any(f['file_id'] == '$SHA' for f in json.load(sys.stdin)['files']))")" "True"
STATUS="$(curl -s -o /dev/null -w '%{http_code}' "$FILES_API/$(printf '0%.0s' {1..64})")"
check "GET /files/{unknown} returns 404" "$STATUS" "404"
STATUS="$(curl -s -o /dev/null -w '%{http_code}' "$FILES_API/abc")"
check "GET /files/{bad sha256} returns 400" "$STATUS" "400"

# 8. Bad requests
STATUS="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API" -H "Content-Type: application/json" \
  -d '{"sha256": "abc", "filename": "a.jpg"}')"
check "POST bad sha256" "$STATUS" "400"
STATUS="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API" -H "Content-Type: application/json" \
  -d "{\"sha256\": \"$SHA\", \"filename\": \"a.gif\"}")"
check "POST unsupported type" "$STATUS" "400"

# 9. Website: the page is served over HTTPS and config.js points at this stack's API
SITE="$(output WebsiteHttpsURL)"
STATUS="$(curl -s -o /dev/null -w '%{http_code}' "$SITE")"
check "website index.html" "$STATUS" "200"
check "website config.js has this stack's ApiUrl" \
  "$(curl -s "${SITE%index.html}config.js" | grep -c "\"$(output ApiUrl)\"")" "1"

echo
if [ "$FAILURES" -eq 0 ]; then echo "All smoke tests passed"; else echo "$FAILURES smoke test(s) failed"; fi
exit "$FAILURES"
