# FIT3184 A2: Aussie EcoGuard

A multi-cloud, serverless platform for storing camera-trap photos and videos and finding them by detected species (pests and pets). AWS hosts the web app, API, storage and database; GCP Cloud Run runs ML inference.

The full design is in [architecture/README.md](architecture/README.md). This README covers how to deploy and test what is built so far.

## Current status

The backbone is being built first: website → upload to S3 → inference → DynamoDB. Cognito, thumbnails, the real model and the query APIs come after.

| Part | Status |
|---|---|
| Media bucket, `Media` DynamoDB table, website bucket | Deployed |
| `POST /upload-url` (Lambda + API Gateway, deduplication by SHA-256) | Deployed, no auth yet |
| `tagging` Lambda + EventBridge rule | Not started |
| Cloud Run inference service (stub that returns fixed tags) | Not started |
| Web page that uploads files | Not started |

## Repository layout

```
architecture/        Architecture doc and diagram (edit the .drawio, then export the .png)
infra/
  template.yaml      CloudFormation template: every AWS resource lives here
  deploy.sh          Packages the Lambda code and deploys the stack
lambdas/
  upload_url/app.py  POST /upload-url
tests/               Unit tests (pytest + moto, no AWS account needed)
scripts/
  smoke_test.sh      End-to-end checks against the deployed stack
```

## Prerequisites

- AWS CLI v2
- Python 3.10 or newer
- A bash shell. On Windows use **Git Bash**, not PowerShell (see [Troubleshooting](#troubleshooting))
- An AWS Academy Learner Lab

Each Learner Lab is a separate AWS account, so each of us deploys our own copy of the stack. Resource names that must be globally unique include the account ID, so the same template works in every account.

## Learner Lab credentials

Learner Lab credentials expire after a few hours, so redo this every time you start the lab:

1. AWS Academy → Learner Lab → **Start Lab**, and wait until the indicator turns green.
2. **AWS Details** → **AWS CLI: Show**, then copy the whole `[default]` block.
3. Paste it into `~/.aws/credentials`, replacing the old content. On Windows that is `C:\Users\<you>\.aws\credentials`. Create the file if it does not exist, with no file extension.
4. Set the region once: `aws configure set region us-east-1`
5. Check: `aws sts get-caller-identity` should show `assumed-role/voclabs/...`.

Never copy the credentials into this repository or paste them in screenshots.

Stopping the lab does not delete the stack. Only the credentials change.

## Deploy

From the repository root, in Git Bash:

```bash
./infra/deploy.sh
```

The same command creates the stack the first time and updates it afterwards. It:

1. Creates the bucket `aussie-ecoguard-artifacts-<account-id>` for Lambda code, if it does not exist yet. This bucket is outside the stack because the code has to be uploaded before the stack is deployed.
2. Runs `aws cloudformation package`, which zips each Lambda folder, uploads it, and writes `infra/packaged.yaml` (generated, git-ignored, do not edit) with the S3 location.
3. Runs `aws cloudformation deploy` on the stack `aussie-ecoguard`. CloudFormation only changes resources whose definition changed.
4. Publishes the REST API to the `api` stage. CloudFormation does not redeploy an API when its routes change, so the script does it every time.
5. Prints the stack outputs, including `UploadUrlEndpoint`.

Because the template points at local Lambda folders, uploading `template.yaml` in the CloudFormation console does **not** work. Always deploy with the script. The console is still useful for watching progress: CloudFormation → `aussie-ecoguard` → **Events** shows what changed and why a deployment failed.

Optional check before deploying:

```bash
pip install cfn-lint
cfn-lint infra/template.yaml   # the W3002 warning about `package` is expected
```

### What the stack creates

| Resource | Name |
|---|---|
| Media bucket (private) | `aussie-ecoguard-media-<account-id>` |
| Website bucket (public read, static hosting) | `aussie-ecoguard-web-<account-id>`. Open it via the `WebsiteHttpsURL` output: browser SHA-256 (`crypto.subtle`) needs HTTPS |
| DynamoDB table | `aussie-ecoguard-media`, with GSI `tag-count-index` and TTL on `expires_at` |
| Lambda | `aussie-ecoguard-upload-url` (role: `LabRole`) |
| REST API | `aussie-ecoguard-api`, stage `api` |

Your account also contains a stack with a long random name. Learner Lab created it, so leave it alone.

## API

### `POST /upload-url`

Reserves a file by its SHA-256 and returns a presigned URL. The browser then uploads the file straight to S3; the file never passes through API Gateway or Lambda.

Request:

```json
{"sha256": "<64 hex characters>", "filename": "fox.jpg"}
```

Allowed extensions: `jpg` / `jpeg`, `png`, `mp4`, `mov`.

| Status | Meaning | Body |
|---|---|---|
| 200 | Upload allowed | `{file_id, key, url, headers, expires_in}` |
| 400 | Bad `sha256`, unsupported extension or invalid JSON | `{error}` |
| 409 | File already uploaded (`PROCESSING`) or done (`READY`) | `{error, file_id, status, key}` |

After a 200, `PUT` the file to `url` with exactly the returned `headers` (`Content-Type` and `x-amz-checksum-sha256`). Both are part of the signature, and S3 rejects the upload if the content does not match the SHA-256. The URL is valid for 15 minutes.

The file's `META` item in DynamoDB moves through these states:

| Status | Meaning | Another `POST /upload-url` for the same file |
|---|---|---|
| `PENDING` | URL issued, file not in S3 yet | 200 with a fresh URL, so a failed upload can simply be retried |
| `PROCESSING` | File in S3, tagging running | 409 |
| `READY` | Tagged and searchable | 409 |
| `FAILED` | Tagging failed | 200, upload again |

Unused `PENDING` items are deleted by DynamoDB TTL about 24 hours later.

## Testing

### Unit tests

These need no AWS account: [moto](https://docs.getmoto.org/) fakes DynamoDB and S3. Run them after every change under `lambdas/`.

```bash
python -m venv .venv                  # first time only (.venv is git-ignored)
source .venv/Scripts/activate         # Git Bash on Windows; macOS/Linux: source .venv/bin/activate
pip install -r tests/requirements.txt # first time only

pytest tests/
```

- `tests/conftest.py` starts a fake AWS with the same table and bucket as the template. `load_lambda("<folder>")` imports a function's `app.py`.
- Add a `tests/test_<function>.py` for every new Lambda.

### Smoke test

This checks the deployed stack end to end and needs valid credentials. Run it after every deploy.

```bash
./scripts/smoke_test.sh
```

It reads the endpoint from the stack outputs and uses a random file. It checks the CORS preflight, `POST /upload-url`, the presigned `PUT`, that different content is rejected on the same URL, and that bad requests return 400. Afterwards it deletes its test object and DynamoDB item. It exits with 0 only if every check passes.

### Manual test

```bash
API=<UploadUrlEndpoint from the deploy output>
SHA=$(sha256sum fox.jpg | cut -d' ' -f1)
curl -i -X POST "$API" -H "Content-Type: application/json" \
  -d "{\"sha256\": \"$SHA\", \"filename\": \"fox.jpg\"}"

# Use the url and headers from the response (quote the URL, it contains &):
curl -i -X PUT -T fox.jpg \
  -H "Content-Type: image/jpeg" \
  -H "x-amz-checksum-sha256: <value from headers>" \
  "<url>"
```

You can also invoke the function in the Lambda console (**Test** tab) with this event, which has the same shape API Gateway sends:

```json
{"body": "{\"sha256\": \"<HASH>\", \"filename\": \"fox.jpg\"}"}
```

## Tear down

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws s3 rm "s3://aussie-ecoguard-media-$ACCOUNT_ID" --recursive    # a bucket must be empty before deletion
aws s3 rm "s3://aussie-ecoguard-web-$ACCOUNT_ID" --recursive
aws cloudformation delete-stack --stack-name aussie-ecoguard
aws s3 rb "s3://aussie-ecoguard-artifacts-$ACCOUNT_ID" --force    # not part of the stack
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Running `./infra/deploy.sh` in PowerShell opens a "choose an app" dialog | PowerShell cannot run `.sh` files. Close the dialog without choosing "Always". Switch the VS Code terminal to **Git Bash**, or run `& "C:\Program Files\Git\bin\bash.exe" ./infra/deploy.sh` |
| `ExpiredToken` / `InvalidClientTokenId` | Learner Lab credentials expired. Copy fresh ones into `~/.aws/credentials` |
| API returns 403 `Missing Authentication Token` | Not an auth problem: the path or method does not exist, for example a `GET`, a typo, or a missing `/api` stage |
| `PUT` returns 403 `SignatureDoesNotMatch` | The URL was not copied completely, or the headers differ from the ones returned |
| `PUT` returns 400 `BadDigest` | The file content does not match the SHA-256 that was reserved |
| `PUT` returns 403 `Request has expired` | More than 15 minutes passed, or the lab was restarted. Request a new URL |
| Output stops at `-- More --` | AWS CLI pager. Press `q`. The scripts set `AWS_PAGER=""` to avoid it |
| Blue "Information" hints on `template.yaml` in VS Code | Best-practice suggestions from the linter (logging, versioning, KMS), not errors |
