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
| Cloud Run inference service (stub: downloads the file, returns fake tags) | Deployed, no auth yet |
| Web page that uploads files | Not started |

## Repository layout

```
architecture/        Architecture doc and diagram (edit the .drawio, then export the .png)
infra/
  template.yaml      CloudFormation template: every AWS resource lives here
  deploy_aws.sh      Packages the Lambda code and deploys the AWS stack
  deploy_gcp.sh      Builds and deploys the inference service to Cloud Run
lambdas/
  upload_url/app.py  POST /upload-url
inference/           Cloud Run service (FastAPI + Dockerfile), currently a stub
tests/               Unit tests (pytest + moto, no AWS account needed)
scripts/
  smoke_test.sh      End-to-end checks against the deployed AWS stack
```

## Prerequisites

- AWS CLI v2
- Python 3.10 or newer
- A bash shell. On Windows use **Git Bash**, not PowerShell (see [Troubleshooting](#troubleshooting))
- An AWS Academy Learner Lab
- For the inference service: the gcloud CLI, logged in to a GCP project with billing enabled

Each Learner Lab is a separate AWS account, so each of us deploys our own copy of the stack. Resource names that must be globally unique include the account ID, so the same template works in every account.

## Learner Lab credentials

Learner Lab credentials expire after a few hours, so redo this every time you start the lab:

1. AWS Academy → Learner Lab → **Start Lab**, and wait until the indicator turns green.
2. **AWS Details** → **AWS CLI: Show**, then copy the whole `[default]` block.
3. Paste it into `~/.aws/credentials`, replacing the old content. On Windows that is `C:\Users\<you>\.aws\credentials`. Create the file if it does not exist, with no file extension.
4. Set the region once: `aws configure set region us-east-1`
5. Check: `aws sts get-caller-identity` should show `assumed-role/voclabs/...`.

Never copy the credentials into this repository or paste them in screenshots.

Stopping the lab does not delete the stack. Only the credentials change. After starting the lab again, run `./scripts/smoke_test.sh`: when a session ends, Lambda may turn off the tagging queue trigger (see [Troubleshooting](#troubleshooting)).

## Deploy

### AWS stack

From the repository root, in Git Bash:

```bash
./infra/deploy_aws.sh
```

The same command creates the stack the first time and updates it afterwards. It:

1. Creates the bucket `aussie-ecoguard-artifacts-<account-id>` for Lambda code, if it does not exist yet. This bucket is outside the stack because the code has to be uploaded before the stack is deployed.
2. Runs `aws cloudformation package`, which zips each Lambda folder, uploads it, and writes `infra/packaged.yaml` (generated, git-ignored, do not edit) with the S3 location.
3. Runs `aws cloudformation deploy` on the stack `aussie-ecoguard`. CloudFormation only changes resources whose definition changed.
4. Publishes the REST API to the `api` stage. CloudFormation does not redeploy an API when its routes change, so the script does it every time.
5. Turns the tagging queue trigger (SQS → `tagging` Lambda) back on if Lambda has disabled it (see [Troubleshooting](#troubleshooting)).
6. Prints the stack outputs, including `UploadUrlEndpoint`.

Because the template points at local Lambda folders, uploading `template.yaml` in the CloudFormation console does **not** work. Always deploy with the script. The console is still useful for watching progress: CloudFormation → `aussie-ecoguard` → **Events** shows what changed and why a deployment failed.

Optional check before deploying:

```bash
pip install cfn-lint
cfn-lint infra/template.yaml   # the W3002 warning about `package` is expected
```

What the stack creates:

| Resource | Name |
|---|---|
| Media bucket (private) | `aussie-ecoguard-media-<account-id>` |
| Website bucket (public read, static hosting) | `aussie-ecoguard-web-<account-id>`. Open it via the `WebsiteHttpsURL` output: browser SHA-256 (`crypto.subtle`) needs HTTPS |
| DynamoDB table | `aussie-ecoguard-media`, with GSI `tag-count-index` and TTL on `expires_at` |
| Lambda | `aussie-ecoguard-upload-url` (role: `LabRole`) |
| REST API | `aussie-ecoguard-api`, stage `api` |

Your account also contains a stack with a long random name. Learner Lab created it, so leave it alone.

### Inference service (GCP Cloud Run)

```bash
gcloud auth login                       # first time only
gcloud config set project <project-id>  # first time only
./infra/deploy_gcp.sh
```

`gcloud run deploy --source inference` uploads the folder and Cloud Build builds the image from `inference/Dockerfile`, so Docker is not needed locally. The script prints the service URL at the end. The service is `aussie-ecoguard-inference` in `us-east4`, next to AWS `us-east-1`. It scales to zero when idle and is capped at 2 instances.

If the first deploy fails with `...-compute@developer.gserviceaccount.com does not have storage.objects.get access`, grant the Compute Engine default service account (which Cloud Build runs as) the Cloud Run Builder role, then deploy again:

```bash
PROJECT=$(gcloud config get-value project)
NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/run.builder"
```

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

### Inference service (Cloud Run)

Called by the `tagging` Lambda (AWS → GCP only; GCP never holds AWS credentials).

`POST /infer`

```json
{"url": "<presigned S3 GET URL for uploads/...>", "file_type": "image"}
```

| Status | Meaning | Body |
|---|---|---|
| 200 | Done | `{"tags": {"fox": 2, "cat": 1}, "attributes": ["orange"], "model": "stub-v1"}` |
| 422 | `url` is not a presigned URL for an `aussie-ecoguard-media-*` bucket's `uploads/`, bad `file_type`, or the body is not JSON (FastAPI validation) | `{"detail": [...]}` |
| 502 | The file could not be downloaded (e.g. the URL or the credentials that signed it expired) | `{"error": "could not download file (S3 returned 403)"}` |

Cloud Run downloads the file itself from the presigned URL instead of receiving it base64-encoded: Cloud Run requests are limited to 32 MiB and base64 adds 33%, so videos would not fit.

The stub really downloads the file but runs no model. Its tags are derived from the file's SHA-256 (1–2 species, counts 1–3, plus a colour for cats and dogs), so the same file always gets the same tags. The request and response format will stay the same when the real model replaces it.

`GET /health` returns `{"status": "ok", "model": "stub-v1"}`.

`GET /docs` is the interactive API documentation that FastAPI generates from the request and response types in `inference/main.py` (`/openapi.json` has the raw schema).

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

It reads the endpoint from the stack outputs and uses a random file. It checks that the tagging queue trigger is enabled, the CORS preflight, `POST /upload-url`, the presigned `PUT`, that different content is rejected on the same URL, and that bad requests return 400. Afterwards it deletes its test object and DynamoDB item. It exits with 0 only if every check passes.

### Manual test of the inference service

```bash
SVC=<service URL printed by deploy_gcp.sh>
curl "$SVC/health"
URL=$(aws s3 presign "s3://aussie-ecoguard-media-<account-id>/uploads/<sha256>.jpg" --expires-in 300)
curl -X POST "$SVC/infer" -H "Content-Type: application/json" \
  -d "{\"url\": \"$URL\", \"file_type\": \"image\"}"
```

`aws s3 presign` works offline, so with expired Learner Lab credentials it still prints a URL, but S3 rejects it (`502 ... S3 returned 400`). Refresh the credentials first.

### Manual test of upload-url

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

gcloud run services delete aussie-ecoguard-inference --region us-east4
gcloud artifacts repositories delete cloud-run-source-deploy --location us-east4    # images built by --source
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Running `./infra/deploy_aws.sh` in PowerShell opens a "choose an app" dialog | PowerShell cannot run `.sh` files. Close the dialog without choosing "Always". Switch the VS Code terminal to **Git Bash**, or run `& "C:\Program Files\Git\bin\bash.exe" ./infra/deploy_aws.sh` |
| `ExpiredToken` / `InvalidClientTokenId` | Learner Lab credentials expired. Copy fresh ones into `~/.aws/credentials` |
| Uploads stay `PENDING`; the tagging queue has messages but nothing processes them; smoke test fails `tagging queue trigger enabled` | When a Learner Lab session ends, `LabRole` loses its SQS permissions for a while. Lambda then disables the queue trigger by itself (CloudTrail event `LambdaESMDisabled`; the mapping shows `USER_INITIATED`, which is misleading) and does not turn it back on. Run `./infra/deploy_aws.sh` to enable it. Queued messages are kept for 4 days and are processed once it is enabled |
| `/infer` returns `502 could not download file (S3 returned 400)` for a URL from `aws s3 presign` | `aws s3 presign` signs offline, even with expired credentials, and S3 then answers `ExpiredToken`. Refresh the credentials and presign again |
| First `deploy_gcp.sh` fails with `storage.objects.get` denied | Grant the Compute Engine default service account `roles/run.builder` (see [Inference service](#inference-service-gcp-cloud-run)) |
| API returns 403 `Missing Authentication Token` | Not an auth problem: the path or method does not exist, for example a `GET`, a typo, or a missing `/api` stage |
| `PUT` returns 403 `SignatureDoesNotMatch` | The URL was not copied completely, or the headers differ from the ones returned |
| `PUT` returns 400 `BadDigest` | The file content does not match the SHA-256 that was reserved |
| `PUT` returns 403 `Request has expired` | More than 15 minutes passed, or the lab was restarted. Request a new URL |
| Output stops at `-- More --` | AWS CLI pager. Press `q`. The scripts set `AWS_PAGER=""` to avoid it |
| Blue "Information" hints on `template.yaml` in VS Code | Best-practice suggestions from the linter (logging, versioning, KMS), not errors |
