#!/usr/bin/env bash
# End-to-end check of the deployed system: tagging queue trigger, sign-in of the
# Cognito test user (its ID token goes with every API call), CORS preflights,
# POST /upload-url, PUT to the presigned URL, tagging (EventBridge -> SQS ->
# Lambda -> Cloud Run -> DynamoDB), the thumbnail, duplicate rejection, bad requests
# and the website.
# Cleans up its test file afterwards.
# Needs AWS credentials. Usage: ./scripts/smoke_test.sh
set -uo pipefail
export AWS_PAGER=""

REGION="${AWS_REGION:-ap-southeast-2}"

# output <name>: one value from infra/terraform/aws/outputs.tf (read from the local state).
output() {
  terraform -chdir="$(dirname "$0")/../infra/terraform/aws" output -raw "$1"
}
API="$(output upload_url_endpoint)"
BUCKET="$(output media_bucket_name)"
TABLE="$(output media_table_name)"
ORIGIN="https://$(output website_bucket_name).s3.$REGION.amazonaws.com"

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
# A real photo (a black cat), so the model has something to find. 16 random
# bytes after the end of the JPEG give a new checksum on every run; image
# decoders ignore anything after the end marker.
cp "$(dirname "$0")/../tests/fixtures/smoke_cat.jpg" "$FILE"
head -c 16 /dev/urandom >> "$FILE"
SHA="$(sha256sum "$FILE" | cut -d' ' -f1)"
echo "API: $API"
echo "Test file sha256: $SHA"

cleanup() {
  aws s3 rm "s3://$BUCKET/uploads/$SHA" --region "$REGION" >/dev/null 2>&1
  aws s3 rm "s3://$BUCKET/thumbnails/$SHA.jpg" --region "$REGION" >/dev/null 2>&1
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

# 0. Tagging queue trigger. Lambda turns it off by itself when the role loses its
#    SQS permissions (e.g. its policy was detached); uploads then stay PENDING.
ESM_STATE="$(aws lambda list-event-source-mappings --function-name "$(output tagging_function_name)" \
  --region "$REGION" --query "EventSourceMappings[0].State" --output text)"
check "tagging queue trigger enabled" "$ESM_STATE" "Enabled"
[ "$ESM_STATE" = "Enabled" ] || echo "      Run terraform apply in infra/terraform/aws to enable it again (see README Troubleshooting)"

# Sign in as the test user (infra/terraform/aws/cognito.tf). Every API call
# below sends its ID token, like the website; S3 presigned URLs do not need it.
TOKEN="$(aws cognito-idp admin-initiate-auth --region "$REGION" \
  --user-pool-id "$(output cognito_user_pool_id)" --client-id "$(output cognito_client_id)" \
  --auth-flow ADMIN_USER_PASSWORD_AUTH \
  --auth-parameters "USERNAME=$(output smoke_test_username),PASSWORD=$(output smoke_test_password)" \
  --query AuthenticationResult.IdToken --output text | tr -d '\r')"
check "test user signed in (ID token)" "$(echo "$TOKEN" | grep -c '^eyJ')" "1"
AUTH=(-H "Authorization: $TOKEN")

# 1. CORS preflight
PREFLIGHT="$(curl -s -i -X OPTIONS "$API" -H "Origin: $ORIGIN" \
  -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: content-type")"
check "preflight status" "$(echo "$PREFLIGHT" | head -1 | cut -d' ' -f2)" "200"
check "preflight allows POST" "$(echo "$PREFLIGHT" | grep -ci 'access-control-allow-methods:.*POST')" "1"
# The GET routes need a preflight too, because the website sends an Authorization header.
FILES_API="$(output api_url)/files"
preflight_get() { # preflight_get <route name> <url>
  PREFLIGHT="$(curl -s -i -X OPTIONS "$2" -H "Origin: $ORIGIN" \
    -H "Access-Control-Request-Method: GET" -H "Access-Control-Request-Headers: authorization")"
  check "preflight $1 status" "$(echo "$PREFLIGHT" | head -1 | cut -d' ' -f2)" "200"
  check "preflight $1 allows Authorization" \
    "$(echo "$PREFLIGHT" | grep -ci 'access-control-allow-headers:.*authorization')" "1"
}
preflight_get "/files" "$FILES_API"
preflight_get "/files/{sha256}" "$FILES_API/$(printf '0%.0s' {1..64})"
# Errors from API Gateway itself (here 403 for a missing route) carry a CORS header,
# so the website can read their status.
check "API Gateway error has CORS header" \
  "$(curl -s -i "$API" -H "Origin: $ORIGIN" | grep -ci '^access-control-allow-origin:')" "1"

# 1b. Sign-in required: the Cognito authorizer refuses the request before any
#     Lambda runs, with a CORS header. A missing or unreadable token gets 401;
#     a well-formed JWT whose signature does not check out gets 403 Access Denied.
check "POST /upload-url without token returns 401" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API" \
  -H "Content-Type: application/json" -d "{\"sha256\": \"$SHA\", \"filename\": \"smoke.jpg\"}")" "401"
check "GET /files without token returns 401" "$(curl -s -o /dev/null -w '%{http_code}' "$FILES_API")" "401"
check "GET /files/{sha256} without token returns 401" \
  "$(curl -s -o /dev/null -w '%{http_code}' "$FILES_API/$(printf '0%.0s' {1..64})")" "401"
check "GET /files with a token that is not a JWT returns 401" \
  "$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: not-a-token" "$FILES_API")" "401"
# The test user's token with its signature (the part after the last dot) replaced.
check "GET /files with a forged token returns 403" \
  "$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: ${TOKEN%.*}.AAAA" "$FILES_API")" "403"
check "401 has CORS header" \
  "$(curl -s -i "$FILES_API" -H "Origin: $ORIGIN" | grep -ci '^access-control-allow-origin:')" "1"
# Cloud Run (shared, GCP) refuses calls without the machine client's token too.
# The tagging Lambda's own token is checked by step 5 (tagging reaches READY).
INFERENCE="$(output inference_url)"
BAD_BODY='{"url": "https://example.com/x", "file_type": "image"}'
check "Cloud Run /infer without token returns 401" "$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  "$INFERENCE/infer" -H "Content-Type: application/json" -d "$BAD_BODY")" "401"
check "Cloud Run /infer with a user's token returns 403" "$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  "$INFERENCE/infer" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$BAD_BODY")" "403"

# 1c. Cloud Run POST /detect: the website sends an image itself with the user's
#     ID token and gets its tags back; nothing is stored. First the browser's
#     CORS preflight, then the cat photo (up to 3 minutes for a cold start).
PREFLIGHT="$(curl -s -i -X OPTIONS "$INFERENCE/detect" -H "Origin: $ORIGIN" \
  -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: authorization,content-type")"
check "Cloud Run /detect preflight status" "$(echo "$PREFLIGHT" | head -1 | cut -d' ' -f2)" "200"
check "Cloud Run /detect without token returns 401" "$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  "$INFERENCE/detect" -H "Content-Type: image/jpeg" --data-binary @"$FILE")" "401"
STATUS="$(curl -s -m 180 -o "$WORK/detect.json" -w '%{http_code}' -X POST "$INFERENCE/detect" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: image/jpeg" --data-binary @"$FILE")"
check "Cloud Run /detect with the user's token" "$STATUS" "200"
check "Cloud Run /detect finds the cat" \
  "$(python -c "import json,sys; print('cat' in json.load(sys.stdin).get('tags', {}))" < "$WORK/detect.json" 2>/dev/null)" "True"

# 2. POST /upload-url for a new file
BODY="$WORK/body.json"
STATUS="$(curl -s "${AUTH[@]}" -o "$BODY" -w '%{http_code}' -X POST "$API" -H "Content-Type: application/json" \
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
#    Usually done in about 10 s; up to about a minute when Cloud Run starts cold
#    (it downloads and loads the models first), so wait up to 3 minutes.
STATE="$(meta status.S)"
for _ in $(seq 1 90); do
  case "$STATE" in READY | FAILED) break ;; esac
  sleep 2
  STATE="$(meta status.S)"
done
check "tagging finished (META status)" "$STATE" "READY"
[ "$STATE" != "FAILED" ] || echo "      error: $(meta error.S)"
check "expires_at removed (no TTL on stored files)" "$(meta expires_at.N)" "None"
# owner is the "sub" claim of the token (its middle part is base64url JSON).
SUB="$(echo "$TOKEN" | cut -d. -f2 | python -c "
import base64, json, sys
s = sys.stdin.read().strip()
print(json.loads(base64.urlsafe_b64decode(s + '=' * (-len(s) % 4)))['sub'])")"
check "META owner is the test user" "$(meta owner.S)" "$SUB"
TAG_COUNT="$(aws dynamodb query --table-name "$TABLE" --region "$REGION" \
  --key-condition-expression "file_id = :id AND begins_with(sk, :tag)" \
  --expression-attribute-values "{\":id\":{\"S\":\"$SHA\"},\":tag\":{\"S\":\"TAG#\"}}" \
  --select COUNT --query Count --output text)"
check "TAG# items written" "$([ "${TAG_COUNT:-0}" -ge 1 ] && echo yes || echo no)" "yes"
check "META thumb_url" "$(meta thumb_url.S)" "thumbnails/$SHA.jpg"

# 5b. Thumbnail: the same S3 event -> EventBridge -> thumbnail Lambda -> thumbnails/.
#     Usually there before tagging finishes; wait up to 30 s more for a cold start.
THUMB_TYPE="None"
for _ in $(seq 1 15); do
  THUMB_TYPE="$(aws s3api head-object --bucket "$BUCKET" --key "thumbnails/$SHA.jpg" --region "$REGION" \
    --query ContentType --output text 2>/dev/null)" && break
  THUMB_TYPE="None"
  sleep 2
done
check "thumbnail saved as JPEG" "$THUMB_TYPE" "image/jpeg"

# 6. The same file again is a duplicate now
STATUS="$(curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}' -X POST "$API" -H "Content-Type: application/json" \
  -d "{\"sha256\": \"$SHA\", \"filename\": \"smoke.jpg\"}")"
check "POST same file after READY returns 409" "$STATUS" "409"

# 7. GET /files/{sha256} and GET /files show the tagged file
STATUS="$(curl -s "${AUTH[@]}" -o "$WORK/file.json" -w '%{http_code}' "$FILES_API/$SHA")"
check "GET /files/{sha256}" "$STATUS" "200"
check "GET /files/{sha256} is READY with tags" \
  "$(python -c "import json,sys; f = json.load(sys.stdin); print(f['status'], bool(f['tags']))" < "$WORK/file.json")" "READY True"
check "GET /files/{sha256} has a thumb_url" \
  "$(python -c "import json,sys; print('/thumbnails/$SHA.jpg' in json.load(sys.stdin).get('thumb_url', ''))" < "$WORK/file.json")" "True"
check "thumbnail downloads from thumb_url" \
  "$(curl -s -o /dev/null -w '%{http_code}' "$(json "['thumb_url']" < "$WORK/file.json")")" "200"
check "GET /files lists the file" \
  "$(curl -s "${AUTH[@]}" "$FILES_API" | python -c "import json,sys; print(any(f['file_id'] == '$SHA' for f in json.load(sys.stdin)['files']))")" "True"
STATUS="$(curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}' "$FILES_API/$(printf '0%.0s' {1..64})")"
check "GET /files/{unknown} returns 404" "$STATUS" "404"
STATUS="$(curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}' "$FILES_API/abc")"
check "GET /files/{bad sha256} returns 400" "$STATUS" "400"

# 8. Bad requests
STATUS="$(curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}' -X POST "$API" -H "Content-Type: application/json" \
  -d '{"sha256": "abc", "filename": "a.jpg"}')"
check "POST bad sha256" "$STATUS" "400"
STATUS="$(curl -s "${AUTH[@]}" -o /dev/null -w '%{http_code}' -X POST "$API" -H "Content-Type: application/json" \
  -d "{\"sha256\": \"$SHA\", \"filename\": \"a.gif\"}")"
check "POST unsupported type" "$STATUS" "400"

# 9. Website: the page is served over HTTPS and config.js points at this deployment's API
SITE="$(output website_https_url)"
STATUS="$(curl -s -o /dev/null -w '%{http_code}' "$SITE")"
check "website index.html" "$STATUS" "200"
check "website config.js has this deployment's api_url" \
  "$(curl -s "${SITE%index.html}config.js" | grep -c "\"$(output api_url)\"")" "1"
check "website config.js has the inference_url" \
  "$(curl -s "${SITE%index.html}config.js" | grep -c "\"$(output inference_url)\"")" "1"

echo
if [ "$FAILURES" -eq 0 ]; then echo "All smoke tests passed"; else echo "$FAILURES smoke test(s) failed"; fi
exit "$FAILURES"
