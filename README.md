# FIT3184 A2: Aussie EcoGuard

A multi-cloud, serverless platform for storing camera-trap photos and videos and finding them by detected species (pests and pets). AWS hosts the web app, API, storage and database; GCP Cloud Run runs ML inference.

The full design is in [architecture/README.md](architecture/README.md). This README covers how to deploy and test what is built so far.

## Current status

The backbone works end to end: website → upload to S3 → inference with the real model → DynamoDB. The website and the AWS API require a Cognito sign-in (Cloud Run does not check tokens yet). The query APIs come next.

| Part | Status |
|---|---|
| Media bucket, `Media` DynamoDB table, website bucket | Deployed |
| `POST /upload-url` (Lambda + API Gateway, deduplication by SHA-256) | Deployed, Cognito sign-in required |
| `tagging` (EventBridge rule → SQS queue → Lambda → Cloud Run → DynamoDB) | Deployed |
| Cloud Run inference service: MegaDetector → SpeciesNet → CLIP (see [Model selection](#model-selection)), images only | Deployed, no auth yet |
| `GET /files`, `GET /files/{sha256}` (file list and status for the website) | Deployed, Cognito sign-in required |
| Website (`web/`, Bootstrap): upload panel and library of files with their tags | Deployed |
| Thumbnails: `thumbnail` Lambda (EventBridge rule → Pillow → `thumbnails/<sha256>.jpg`), shown on the library cards | Deployed |
| Videos: one frame every 10 seconds, up to 3 minutes | Deployed |
| `POST /subscriptions` (subscribe to email notifications per species, SNS) | Deployed, Cognito sign-in required |
| Email notifications: `notify` Lambda on the DynamoDB Stream emails subscribers when a file gains a species they chose | Deployed |

## Model selection

We compared ten detection/classification pipelines, including MegaDetector + CLIP, YOLOv8 Open Images, Amazon Rekognition and Google SpeciesNet, on a hand-labelled test set of 27 images. The method, results and reasoning are in [experiments/model-selection/README.md](experiments/model-selection/README.md).

Chosen pipeline: **MegaDetector v5a** (entry model) finds and counts animals → the **SpeciesNet** classifier names each box, with Australia-specific mapping rules and image-level smoothing → **CLIP** zero-shot checks every box: it names the boxes of images SpeciesNet is unsure about, and overrules SpeciesNet when it is almost certain (≥ 0.98) of another species → CLIP gives the colour of cats and dogs. It got the species exactly right on 23 of 26 test images, with a total count error of 9. MegaDetector + CLIP alone scored 21–22 with a count error of 31–37, and Rekognition scored 17. How it runs on Cloud Run and how to swap models: [architecture/README.md §7](architecture/README.md#7-model).

Known limits of the deployed model:

- The test set is small (26 scored images, only 5 camera-trap photos), and the thresholds were not tuned on a larger set.
- Very small, distant animals are sometimes named wrongly: a group of 17 feral pigs comes out as 11 feral pigs and 4 cattle. Smoothing only fixes boxes that got no tag at all.
- SpeciesNet is trained on camera-trap photos, so close-up pet photos can fool it: it called 6 of 9 pet rabbits `cat` or `goat`. CLIP overrules it when CLIP is at least 0.98 sure, which fixed 5 of them (17 pet photos: 11 → 16 right) without changing any result on the 27 test images. An old sepia photo of a rabbit is still tagged `cat`. The 0.98 threshold sits between CLIP's most confident mistake on the test images (tiny pigs called cattle at 0.96) and the pet rabbits (0.99+), so it rests on few images.
- Cane toads are SpeciesNet's weak spot and depend on the CLIP fallback; a toad held in a bird's beak is not found.
- The mapping rules group by family or genus: every felid is `cat`, every *Canis* (dingo included) is `dog`. Animals with no rule, such as quolls, become `other animal`.
- Colour is only given for cats and dogs, and was checked on just 6 images.
- Videos: each species keeps its highest count in any checked frame, so a mistake in a single frame stays in the result (a cat video gained `cattle` from one dark frame). Videos longer than 3 minutes are refused (`FAILED`).

## Repository layout

```
architecture/        Architecture doc and diagram (edit the .drawio, then export the .png)
infra/
  terraform/aws/     Terraform for the AWS resources and the website (everyone deploys their own)
  terraform/gcp/     Terraform for the shared inference service on GCP (deployed once)
  upload_model.sh    Uploads one model version (weights + config) to Cloud Storage
  fetch_models.py    Downloads the model weights into models/ (used by upload_model.sh)
  model/config.json  Model settings: thresholds, species list, mapping rules, CLIP labels
lambdas/
  upload_url/app.py  POST /upload-url
  tagging/app.py     Runs inference on each new upload and saves the tags
  thumbnail/app.py   Saves a small JPEG of each new image upload in thumbnails/
  files/app.py       GET /files and GET /files/{sha256}
  subscribe/app.py   POST /subscriptions
  notify/app.py      Emails subscribers about new tags (DynamoDB Stream -> SNS)
  notify/email_text.py  Subject and body of that email
inference/           Cloud Run service (FastAPI + Dockerfile)
  main.py            Web API: downloads the image, runs the models
  model.py           The model pipeline and its decision rules
web/                 Static website (HTML + JS + Bootstrap from a CDN, no build step)
  upload.js          Upload panel: SHA-256, POST /upload-url, PUT to S3
  library.js         Library: GET /files, polls unfinished files
  subscribe.js       Notifications tab: POST /subscriptions
  config.js          Written by Terraform into the website bucket; a local copy is git-ignored
tests/               Unit tests (pytest + moto, no AWS account needed)
  fixtures/          Test image used by the smoke test
experiments/
  model-selection/   How the inference models were chosen: report, scripts, labels, results
  test-media/        Test videos: sources, scripts to download and re-encode them, expected results
scripts/
  smoke_test.sh      End-to-end checks against the deployed AWS resources
  upload.sh          Uploads one file through the real flow and prints its tags
```

## Prerequisites

- AWS CLI v2.32 or newer (for `aws login`)
- Terraform 1.5 or newer
- Python 3.10 or newer, with `pip3` on your PATH (`apply` uses it to install Pillow for the `thumbnail` Lambda)
- A bash shell. On Windows use **Git Bash**, not PowerShell (see [Troubleshooting](#troubleshooting))
- A personal AWS account: your own, or access to the team's shared one (Yijiang's). AWS Academy Learner Lab does not work, because it blocks Amazon Lex and cannot create IAM roles
- Only for the person hosting the inference service: the gcloud CLI and a GCP project with billing enabled

Each of us either uses the team's shared deployment or deploys our own copy of the AWS part in our own personal account. Resource names that must be globally unique include the account ID, so the same Terraform code works in every account. Each deployment has its own Terraform state (`infra/terraform/aws/terraform.tfstate`, git-ignored): it records what Terraform created in *that* account, so do not share or commit it.

Everything on AWS runs in `ap-southeast-2` (Sydney), set once as `local.region` in `infra/terraform/aws/versions.tf`. The shared account's organization blocks every other region.

## AWS credentials

1. Sign in to the AWS console with your account.
2. Run `aws login` and confirm in the browser tab it opens. The CLI refreshes the credentials by itself for up to 12 hours; after that, run `aws login` again.
3. Set the region once: `aws configure set region ap-southeast-2`
4. Check: `aws sts get-caller-identity` should show your account ID.

Terraform (aws provider 6.23 or newer) reads the same login, so no access keys are needed. Never copy credentials into this repository or paste them in screenshots.

## Deploy

The infrastructure is in two Terraform directories, each with its own state:

| Directory | What | Who deploys it |
|---|---|---|
| `infra/terraform/aws/` | Everything on AWS (buckets, DynamoDB, Lambdas, API, queue) and the website | Whoever runs a deployment: the shared one, or your own in your personal account |
| `infra/terraform/gcp/` | The inference service on GCP (Cloud Run, model bucket, image build) | **One person** (Yijiang); the whole team shares it |

**Why two directories:** not everyone in the team has a GCP account (it needs billing enabled), so only one person runs the GCP part and everyone's AWS calls that shared Cloud Run service through its URL (`inference_url`). With one directory, everyone would need GCP credentials just to run `terraform plan`. The service only reads presigned URLs of buckets named `aussie-ecoguard-media-<account-id>`, so it works for every teammate's media bucket. The shared service has at most 2 instances: when several people upload at once, the extra requests are refused and SQS retries them a little later, so files only take longer to become `READY`.

### AWS (everyone)

1. Log in to AWS (see [AWS credentials](#aws-credentials)).
2. Create your variables file. The example already contains the shared Cloud Run URL:

   ```bash
   cp infra/terraform/aws/terraform.tfvars.example infra/terraform/aws/terraform.tfvars
   ```

3. Deploy (`init` only the first time):

   ```bash
   terraform -chdir=infra/terraform/aws init
   terraform -chdir=infra/terraform/aws apply
   ```

4. Run `./scripts/smoke_test.sh` (see [Smoke test](#smoke-test)).

Run the same `apply` after every change. Terraform shows the plan (what it will create, change or delete) and asks for `yes`. It only changes resources whose definition changed. One `apply` does everything:

- Zips each `lambdas/<name>/` folder (`archive_file`) and updates a function only when its code changed.
- On the first `apply`, installs Pillow for Lambda's platform (Linux, Python 3.12) with `pip3` into `infra/terraform/aws/build/pillow-layer/` and publishes it as a Lambda layer for `thumbnail`. Later `apply`s reuse it, until `pillow_version` in `thumbnails.tf` changes.
- Publishes the REST API to the `api` stage whenever `api.tf` or its routes change (`triggers` on `aws_api_gateway_deployment`).
- Turns the tagging queue trigger (SQS → `tagging` Lambda) back on if Lambda has disabled it (see [Troubleshooting](#troubleshooting)): the config says `enabled = true`, so Terraform puts it back.
- Uploads `web/` to the website bucket with `Cache-Control: no-cache` (a reload always picks up the new version), plus a generated `config.js` with this deployment's API URL. A file deleted from `web/` is deleted from the bucket.

Print the URLs again at any time with `terraform -chdir=infra/terraform/aws output` (e.g. `website_https_url`, `upload_url_endpoint`). Run `terraform -chdir=infra/terraform/aws plan` to see what would change without changing anything; right after an `apply` it should say `No changes`.

### GCP (only the person hosting the inference service)

1. Log in:

   ```bash
   gcloud auth login                         # for the gcloud commands Terraform runs
   gcloud auth application-default login     # for Terraform's google provider
   gcloud config set project <project-id>
   ```

2. `cp infra/terraform/gcp/terraform.tfvars.example infra/terraform/gcp/terraform.tfvars` and fill in the project ID and `trusted_user_pools`: the Cognito user pool ID of every teammate's AWS deployment. Each teammate gets theirs with `terraform -chdir=infra/terraform/aws output -raw cognito_user_pool_id` and sends it to you. Add a new one and `apply` again; until then that teammate's uploads become `FAILED` (`inference returned 401`).
3. Deploy with `terraform -chdir=infra/terraform/gcp init`, then `terraform -chdir=infra/terraform/gcp apply`. Run the same `apply` after every change to `inference/` or `gcp.tf`.

`apply` builds a new inference image when anything in `inference/` changed (Cloud Build, 5–10 minutes, no local Docker) and deploys it to Cloud Run. The image tag is a hash of those files, so an unchanged `inference/` is never rebuilt. The service URL (`terraform -chdir=infra/terraform/gcp output -raw inference_url`) stays the same across deploys, so the AWS side does not need a new `apply`. If the service ever moves (a new project), update `inference_url` in `infra/terraform/aws/terraform.tfvars.example` and tell everyone to update their `terraform.tfvars` and `apply`.

### Files

| File | What it defines |
|---|---|
| `aws/versions.tf` | Terraform and provider versions, AWS region (`local.region`), default tags |
| `aws/iam.tf` | The execution role shared by every Lambda |
| `aws/variables.tf` | `inference_url` |
| `aws/storage.tf` | Media and website buckets, DynamoDB table and its stream |
| `aws/tagging.tf` | EventBridge rule, SQS queue + dead-letter queue, `tagging` Lambda and its queue trigger |
| `aws/thumbnails.tf` | Pillow layer, `thumbnail` Lambda, and its target on the EventBridge rule |
| `aws/api.tf` | REST API routes and the `upload-url`, `files` and `subscribe` Lambdas |
| `aws/notifications.tf` | SNS topic for email notifications, the `notify` Lambda and its stream trigger |
| `aws/cognito.tf` | Cognito user pool, app client, sign-in domain and the smoke test's user |
| `aws/website.tf` | Uploads `web/` and `config.js` |
| `aws/outputs.tf` | URLs and names printed after `apply`, read by the scripts |
| `gcp/versions.tf` | Terraform and provider versions, GCP project and region, default labels |
| `gcp/variables.tf` | `gcp_project_id`, `gcp_region`, `model_version` |
| `gcp/gcp.tf` | Model bucket, image repository and build, Cloud Run service, GCP APIs and permissions |
| `gcp/outputs.tf` | `inference_url`, model bucket, current image |
| `*/terraform.tfvars.example` | Templates for your `terraform.tfvars` (git-ignored) |

The two S3 buckets are created and deleted with the aws CLI (`terraform_data` in `aws/storage.tf`), not `aws_s3_bucket`. This started as the Week 11 lab's workaround for Learner Lab, which blocks `s3:GetBucketObjectLockConfiguration`, a permission `aws_s3_bucket` needs on every read, and was kept after the move to personal accounts. Their settings (CORS, events, website, policy) are normal Terraform resources. As a result, Terraform does not notice if a bucket is deleted by hand; `terraform apply -replace=terraform_data.media_bucket` creates it again.

### What Terraform creates

| Cloud | Resource | Name |
|---|---|---|
| AWS | Media bucket (private) | `aussie-ecoguard-media-<account-id>` |
| AWS | Website bucket (public read, static hosting) | `aussie-ecoguard-web-<account-id>`. Open it via the `website_https_url` output: browser SHA-256 (`crypto.subtle`) needs HTTPS |
| AWS | DynamoDB table | `aussie-ecoguard-media`, with GSI `tag-count-index`, TTL on `expires_at` and a stream (`NEW_IMAGE`) read by `notify` |
| AWS | Lambdas | `aussie-ecoguard-upload-url`, `aussie-ecoguard-tagging`, `aussie-ecoguard-files`, `aussie-ecoguard-subscribe`, `aussie-ecoguard-notify` (role: `aussie-ecoguard-lambda`) |
| AWS | IAM role | `aussie-ecoguard-lambda`, the execution role of every Lambda, with the AWS managed policies for CloudWatch Logs, SQS triggers, S3, DynamoDB and SNS |
| AWS | REST API | `aussie-ecoguard-api`, stage `api` |
| AWS | Cognito user pool | `aussie-ecoguard-users` with app client `aussie-ecoguard-web` and sign-in domain `aussie-ecoguard-<account-id>` (managed login pages). Users sign up themselves with their email. A second, machine client `aussie-ecoguard-tagging` (client credentials, scope `aussie-ecoguard/infer` from resource server `aussie-ecoguard`) gives the `tagging` Lambda its tokens for Cloud Run; its secret is in the Lambda's environment variables |
| AWS | Cognito test user | `smoke-test@example.com`, password `Smoke-Test-2026` (written in `aws/cognito.tf`), for the smoke test. Leave it in place. Anyone who can read this repository can sign in with it |
| AWS | SNS topic | `aussie-ecoguard-notifications`, display name "Aussie EcoGuard" (the sender name on its emails). One email subscription per user, each with a filter policy on `species` |
| AWS | EventBridge rule | `aussie-ecoguard-upload-created`: new objects under `uploads/` in the media bucket → tagging queue and `thumbnail` Lambda |
| AWS | Thumbnail Lambda and layer | `aussie-ecoguard-thumbnail` (role: `aussie-ecoguard-lambda`) with the layer `aussie-ecoguard-pillow` (Pillow 11.3.0) |
| AWS | SQS queues | `aussie-ecoguard-tagging` (read by the `tagging` Lambda, at most 2 at a time) and its dead-letter queue `aussie-ecoguard-tagging-dlq` |
| GCP (shared) | Cloud Run service | `aussie-ecoguard-inference` in `us-east4`, public. It was chosen to sit next to AWS when AWS ran in `us-east-1`; AWS now runs in `ap-southeast-2` |
| GCP (shared) | Model bucket (private) | `aussie-ecoguard-models-<project-number>`, one folder per model version |
| GCP (shared) | Image repository | Artifact Registry `aussie-ecoguard`; keeps the 2 newest images (each ~0.7 GB; the free tier is 0.5 GB) |
### Inference service (GCP Cloud Run)

The service has 4 CPUs and 8 GiB, handles one image at a time per instance, scales to zero when idle and is capped at 2 instances. The model files (~1.1 GB) are not in the image: they live in the model bucket, one folder per version, and the service downloads its version (`MODEL_GCS`) at startup.

A new instance needs about a minute before it answers (download ~17 s, then loading the models), so the first upload after about 15 idle minutes takes about a minute; after that, inference takes 4–15 s per image and a few seconds per checked video frame. The `tagging` Lambda waits up to 300 s for an answer (a cold start plus a 3-minute video).

**Changing the model** needs no code change and no new image. Every revision keeps the version it started with, so a version is never edited in place. Make a new version folder, then point the service at it:

```bash
B=gs://aussie-ecoguard-models-<project-number>
gcloud storage cp -r "$B/v2" "$B/v3"                   # copy inside Cloud Storage
gcloud storage cp my-config.json "$B/v3/config.json"   # or new weights
```

or build one from scratch with `infra/upload_model.sh v3` (it runs `infra/fetch_models.py`, so it needs Python with `inference/requirements.txt` installed, see [Run the inference service locally](#run-the-inference-service-locally); it refuses to overwrite an existing version). Then set `model_version = "v3"` in `infra/terraform/gcp/terraform.tfvars` and run `apply` there, which deploys a new revision. If it fails to start, traffic stays on the old one. To go back, set the old version again and `apply`. `/health` shows the `model_label` from the loaded `config.json`.

**Moving the service to a new, empty project:** Cloud Run cannot start before its model version exists, so create the bucket first, upload, then deploy the rest:

```bash
terraform -chdir=infra/terraform/gcp apply -target=google_storage_bucket_iam_member.models_reader
PYTHON=.venv-inference/Scripts/python ./infra/upload_model.sh v2
terraform -chdir=infra/terraform/gcp apply
```

## API

**Who can do what.** Every route requires a signed-in Cognito user: send the user's ID token in the `Authorization` header (the raw token, no `Bearer `). Without a valid one, API Gateway's Cognito authorizer answers 401 and no Lambda runs. Once signed in, every user can see and change every file: there is no per-user ownership, like a public sightings board (see [architecture §2](architecture/README.md#2-authentication-and-authorisation)). `POST /upload-url` will record the uploader as `owner`, for information only.

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
| 401 | No valid ID token (answered by API Gateway's Cognito authorizer, see [API](#api)) | `{message}` |
| 409 | File already uploaded (`PROCESSING`) or done (`READY`) | `{error, file_id, status, key}` |

After a 200, `PUT` the file to `url` with exactly the returned `headers` (`Content-Type` and `x-amz-checksum-sha256`). Both are part of the signature, and S3 rejects the upload if the content does not match the SHA-256. The URL is valid for 15 minutes.

The object key is `uploads/<sha256>`, with no extension: it depends only on the content, so the same bytes uploaded as `fox.jpg` and `fox.png` end up as one object. The extension only decides the file type, which is stored as the object's `Content-Type` and as `file_type` (`image` / `video`) in the `META` item. `META` also gets `owner`, the Cognito `sub` (user ID) of whoever reserved the file last, taken from the token's claims that the authorizer passes to the Lambda. It is only recorded; any signed-in user may upload, see or change any file.

The file's `META` item in DynamoDB moves through these states:

| Status | Meaning | Another `POST /upload-url` for the same file |
|---|---|---|
| `PENDING` | URL issued, file not in S3 yet | 200 with a fresh URL, so a failed upload can simply be retried |
| `PROCESSING` | File in S3, tagging running | 409 |
| `READY` | Tagged and searchable | 409 |
| `FAILED` | Tagging failed | 200, upload again |

Unused `PENDING` items are deleted by DynamoDB TTL about 24 hours later.

### Tagging (after the upload)

There is no API call for this step: the upload itself triggers it.

```
S3 Object Created (uploads/) → EventBridge rule → SQS queue → tagging Lambda → Cloud Run POST /infer → DynamoDB
```

1. The `tagging` Lambda moves `META` from `PENDING` to `PROCESSING` and removes `expires_at`, so TTL never deletes a stored file. Objects without a `META` item (not uploaded through `/upload-url`) and files that are already `READY` or `FAILED` are skipped.
2. It sends Cloud Run a presigned GET URL for the object and the `file_type`, with a Cognito access token in `Authorization: Bearer ...`. The Lambda gets the token as the machine client `aussie-ecoguard-tagging` (OAuth client credentials, scope `aussie-ecoguard/infer`) and reuses it until 5 minutes before it expires (tokens last 1 hour; Cognito bills each request). If no token can be had, the file is retried like any temporary error.
3. On success, one transaction sets `META` to `READY` (with `attributes`, `model`, and `thumb_url` for images) and writes one `TAG#<species>` item per species, e.g. `{tag: "fox", tag_count: 2}`. This usually takes 10–20 seconds after the upload, or about a minute when Cloud Run has to start an instance first.

The queue limits tagging to 2 files at a time, the same as Cloud Run's instance limit, so a burst of uploads waits in the queue instead of overloading Cloud Run.

| Cloud Run result | What happens |
|---|---|
| 200 | `READY` |
| Network error, timeout, 429 or 5xx | The Lambda fails and SQS delivers the message again after 31.5 minutes (the queue's visibility timeout). On the 3rd attempt the file becomes `FAILED` |
| Other 4xx, or a result in the wrong format | `FAILED` at once; trying again would give the same answer |

`FAILED` items have an `error` attribute with the reason, and the file can be uploaded again. If the Lambda itself crashes or times out on every delivery (5 in total), the message moves to the dead-letter queue and the file stays `PROCESSING` (see [Troubleshooting](#troubleshooting)).

Lambda logs: CloudWatch → Log groups → `/aws/lambda/aussie-ecoguard-tagging`. Each file logs one line such as `<sha256>: READY {'fox': 2}`, or the reason it was skipped or failed.

### Thumbnails (after the upload)

Also no API call: the same EventBridge rule invokes the `thumbnail` Lambda directly, next to the tagging queue.

```
S3 Object Created (uploads/) → EventBridge rule → thumbnail Lambda → S3 thumbnails/<sha256>.jpg
```

1. `thumbnail` reads the object and checks its `Content-Type` (set by `/upload-url`; keys have no extension). Anything that is not `image/*` is skipped before its body is read, so videos get no thumbnail.
2. It scales the image down with Pillow so the longest side is 512 px (`THUMB_SIZE`), keeping the aspect ratio. Smaller images are not enlarged. Phone photos are turned upright from their EXIF orientation, and transparent PNGs get a white background.
3. It saves a JPEG at quality 70 (`THUMB_QUALITY`) to `thumbnails/<sha256>.jpg`, usually 20–60 KB instead of several MB. The rule only matches `uploads/`, so this does not trigger anything again.

The thumbnail's key depends only on the file's SHA-256, so `tagging` stores `thumb_url` in `META` when the image becomes `READY` without waiting for `thumbnail`, and `GET /files` returns it signed. The thumbnail is normally there seconds before tagging finishes. Running the function twice just writes the same object again.

Images that cannot be read (corrupt, or a format Pillow does not know) and images over 50 MB (`MAX_BYTES`) are logged and skipped without an error, because retrying would not help; the library then shows the original. Other errors (e.g. S3 throttling) make Lambda retry twice. `delete-files` already deletes `thumbnails/<sha256>.jpg` with the original.

Lambda logs: `/aws/lambda/aussie-ecoguard-thumbnail`, one line per file such as `saved thumbnails/<sha256>.jpg (31406 bytes)` or the reason it was skipped.

### `GET /files`

Lists every file with its tags, newest first. The website's library uses it.

```json
{"files": [
  {"file_id": "<sha256>", "filename": "fox.jpg", "status": "READY", "file_type": "image",
   "created_at": "2026-10-11T08:27:43+00:00", "tags": {"fox": 2, "cat": 1}, "attributes": ["orange"],
   "model": "md5a+speciesnet-4.0.3a+clip-b32-v2", "preview_url": "<presigned GET URL, valid for 1 hour>",
   "thumb_url": "<presigned GET URL of the thumbnail, valid for 1 hour>"}
]}
```

- `PENDING` files are listed only while their upload URL is still valid (15 minutes). After that nobody can upload them, so they are abandoned reservations.
- `FAILED` files have an `error` field.
- `READY` images also have `thumb_url`, a presigned GET URL of `thumbnails/<sha256>.jpg` (see [Thumbnails](#thumbnails-after-the-upload)). Videos and unfinished files have none. In rare cases (the image could not be read) the object does not exist, so show `preview_url` if it fails to load.
- `PENDING` files also have `uploaded`: `false` while the browser's `PUT` has not arrived, `true` once the file is in S3 and only waits in the tagging queue (at most 2 files are tagged at a time, so a burst of uploads waits there). The Lambda checks this with an S3 `HeadObject`, because the table only changes when tagging starts. A `preview_url` is given as soon as the file is in S3.
- `filename` is the name sent to `/upload-url` (empty for files uploaded before it was stored). It is only for display.
- The Lambda reads the whole table with a `Scan`, which is fine for a few hundred files. Paging would need a GSI on `created_at`.

### `GET /files/{sha256}`

Returns one file in the same shape, in any status including `PENDING`. The website polls it every 3 seconds for unfinished files until they are `READY` or `FAILED` (the usual status-endpoint pattern for asynchronous work).

| Status | Meaning | Body |
|---|---|---|
| 200 | Found | one file, as above |
| 400 | Not a 64-character hex SHA-256 | `{error}` |
| 404 | No such file (never reserved, or its reservation was cleaned up) | `{error, file_id}` |

Both routes have an `OPTIONS` method for the CORS preflight, like `/upload-url`. A plain `GET` would not need one, but the website sends the Cognito token in an `Authorization` header, and that makes the browser ask first.

Errors that API Gateway answers itself, without running a Lambda (for example 403 for a route that does not exist, and later 401 from the Cognito authorizer), also carry `Access-Control-Allow-Origin` (gateway responses `DEFAULT_4XX` / `DEFAULT_5XX`). Without it the browser hides the status from the page and shows only a CORS error.

### `POST /subscriptions`

Subscribes the signed-in user to email notifications for the species they choose (the Notifications tab, `web/subscribe.js`).

```json
{"tags": ["fox", "cane toad"]}
```

The email address comes from the Cognito ID token, never from the body, so a user can only subscribe their own address. The `subscribe` Lambda calls SNS `Subscribe` on the `aussie-ecoguard-notifications` topic with that email and the filter policy `{"species": ["cane toad", "fox"]}`; the `notify` Lambda publishes each file's new tags in a `species` message attribute, so SNS only emails users who chose at least one of them. `tags` must be species from `infra/model/config.json` (`species` plus `other animal`); case and duplicates do not matter.

Each user has one subscription. Subscribing again replaces their species.

| Status | Meaning | Body |
|---|---|---|
| 200 | New subscription, or the same species again before confirming: SNS sends a confirmation email from "Aussie EcoGuard", and nothing is delivered until the user clicks "Confirm subscription" | `{email, tags, pending: true}` |
| 200 | Already confirmed: the species are changed in place, no email is sent | `{email, tags, pending: false}` |
| 400 | Body is not JSON, `tags` missing or empty, or an unknown species | `{error}` |
| 409 | Other species than before while the first subscription still waits for confirmation (SNS cannot change a pending subscription). Confirm it first, then subscribe again | `{error, email}` |

Like `/upload-url`, it has an `OPTIONS` method for the CORS preflight. To stop the emails, users click the unsubscribe link at the bottom of any SNS email.

### Notification emails (`notify` Lambda)

Not an API route. Lambda reads the table's DynamoDB Stream and invokes `notify` with up to 100 records at a time. The trigger's filter passes only `INSERT` records whose `sk` begins with `TAG#`, so each record means "this file gained this tag", from the `tagging` Lambda or a manual tag edit. Changes to `META` items and new counts for an existing tag do not send anything.

`notify` groups the records by file and publishes one SNS message per file to `aussie-ecoguard-notifications`, with the message attribute `species` (`String.Array`, e.g. `["cat", "fox"]`). SNS emails every confirmed subscriber whose filter policy shares at least one species with it. The subject and body come from `lambdas/notify/email_text.py`: the new tags with their counts, the file name and upload time from `META`, and a link to `<website_https_url>#/file/<sha256>`.

If a publish fails, `notify` reports that file as the batch's failure, so Lambda retries from it without emailing the files before it again. After 3 retries the records are skipped and the error stays in the function's CloudWatch log.

### Inference service (Cloud Run)

Called by the `tagging` Lambda (AWS → GCP only; GCP never holds AWS credentials).

`POST /infer` needs `Authorization: Bearer <Cognito access token>`. The token must be signed by one of the user pools in `trusted_user_pools` (`infra/terraform/gcp/terraform.tfvars`), not expired, and carry the scope `aussie-ecoguard/infer`. Only each deployment's machine client `aussie-ecoguard-tagging` can get that scope; signed-in users' tokens cannot. The token is checked before the body. `GET /health` and `GET /docs` need no token.

`POST /infer`

```json
{"url": "<presigned S3 GET URL for uploads/...>", "file_type": "image"}
```

| Status | Meaning | Body |
|---|---|---|
| 200 | Done | `{"tags": {"fox": 2, "cat": 1}, "attributes": ["orange"], "model": "md5a+speciesnet-4.0.3a+clip-b32-v2"}` |
| 401 | No bearer token; or it is expired, not validly signed, or from a user pool that is not trusted | `{"error": "missing bearer token"}`, `{"error": "invalid token"}`, `{"error": "token is not from a trusted user pool"}` |
| 403 | A valid token without the `aussie-ecoguard/infer` scope, e.g. a signed-in user's | `{"error": "token lacks the aussie-ecoguard/infer scope"}` |
| 413 | The image is larger than 30 MB, or the video larger than 500 MB | `{"error": "file is larger than 30 MB"}` |
| 422 | `url` is not a presigned URL for an `aussie-ecoguard-media-*` bucket's `uploads/`, bad `file_type`, or the body is not JSON (FastAPI validation) | `{"detail": [...]}` |
| 422 | The file cannot be decoded as an image / video | `{"error": "file is not a valid image"}`, `{"error": "file is not a valid video"}` |
| 422 | The video is longer than 3 minutes | `{"error": "video is 190 s long; the limit is 3 minutes"}` |
| 502 | The file could not be downloaded (e.g. the URL or the credentials that signed it expired) | `{"error": "could not download file (S3 returned 403)"}` |
| 503 | The user pool's public keys could not be fetched to check the token | `{"error": "could not fetch the user pool's keys"}` |

Cloud Run downloads the file itself from the presigned URL instead of receiving it base64-encoded: Cloud Run requests are limited to 32 MiB and base64 adds 33%, so videos would not fit.

**Videos** (`file_type: "video"`, `.mp4` or `.mov`, H.264 or HEVC): the service downloads the whole file to `/tmp` (at most 500 MB), reads one frame every 10 seconds (0 s, 10 s, 20 s, ...) with OpenCV, runs every frame through the image pipeline, and returns each species' highest count in any single frame (the same animal appears in many frames, so the counts are not added up) and the colours seen in any frame. Phone videos filmed in portrait are turned upright from their rotation metadata. The file is downloaded first rather than read from the URL because the presigned URL could expire while the frames are read. A 3-minute video has 18 frames, which with a cold start fits the 300 s timeout.

`tags` uses the labels in `infra/model/config.json`: the six target species (`fox`, `rabbit`, `feral pig`, `cane toad`, `cat`, `dog`), 16 other common animals (e.g. `kangaroo`, `deer`, `bird`) and `other animal` for an animal the models could not name. Counts are the boxes MegaDetector found. `attributes` only holds colours of cats and dogs. `model` is the `model_label` of the loaded config. The pipeline and its limits are described in [Model selection](#model-selection) and `inference/model.py`.

`POST /detect` is for the queries that start from a file (Spec §4.3: find files with the same tags as a file; find a lost pet by its photo). The website sends the image itself as the request body and gets the same JSON as `/infer` back; it then searches with those tags. The image is only held in memory and never stored. It needs the signed-in user's ID token (`getIdToken()` in `web/auth.js`) from a trusted user pool; the machine client's token is refused. Images only (JPEG or PNG, at most 30 MB; Cloud Run refuses request bodies over 32 MiB anyway).

```
POST /detect
Authorization: Bearer <ID token>
Content-Type: image/jpeg            (or image/png)
<the image bytes>
```

| Status | Meaning | Body |
|---|---|---|
| 200 | Done | `{"tags": {"cat": 1}, "attributes": ["orange"], "model": "..."}` |
| 401 | No token; or expired, not validly signed, or from a user pool that is not trusted | `{"error": "..."}` |
| 403 | A valid token that is not a user's ID token (e.g. the machine client's) | `{"error": "a signed-in user's ID token is required"}` |
| 413 | Larger than 30 MB | `{"error": "file is larger than 30 MB"}` |
| 415 | `Content-Type` is not `image/jpeg` or `image/png` (e.g. a video) | `{"error": "send a JPEG or PNG image, not video/mp4"}` |
| 422 | The bytes are not a valid image | `{"error": "file is not a valid image"}` |

The website calls it from another origin, so the service answers CORS preflights (FastAPI's `CORSMiddleware`, any origin, `POST` with `Authorization` and `Content-Type`) and adds `Access-Control-Allow-Origin` to every answer to the website, including 401s. Requests without an `Origin` header, such as the `tagging` Lambda's, are not affected.

`GET /health` returns `{"status": "ok", "model": "md5a+speciesnet-4.0.3a+clip-b32-v2"}`.

`GET /docs` is the interactive API documentation that FastAPI generates from the request and response types in `inference/main.py` (`/openapi.json` has the raw schema).

## Testing

### Unit tests

These need no AWS account: [moto](https://docs.getmoto.org/) fakes DynamoDB (and its stream), S3, SNS and SQS. Run them after every change under `lambdas/`.

```bash
python -m venv .venv                  # first time only (.venv is git-ignored)
source .venv/Scripts/activate         # Git Bash on Windows; macOS/Linux: source .venv/bin/activate
pip install -r tests/requirements.txt # first time only

pytest tests/
```

- `tests/conftest.py` starts a fake AWS with the same table and bucket as the template. `load_lambda("<folder>")` imports a function's `app.py`.
- Add a `tests/test_<function>.py` for every new Lambda.

### Smoke test

This checks the deployed system end to end and needs valid credentials. Run it after every deploy.

```bash
./scripts/smoke_test.sh
```

It reads the endpoint from the Terraform outputs (so run it on the machine that ran `apply`) and uploads `tests/fixtures/smoke_cat.jpg` with 16 random bytes appended, so the checksum is new on every run. It signs in as the Cognito test user (`smoke-test@example.com`, see [What Terraform creates](#what-terraform-creates)) with `aws cognito-idp admin-initiate-auth` and sends the ID token with every API call, like the website. It checks that the tagging queue trigger is enabled, that the test user can sign in, the CORS preflights of all three routes, that Cloud Run refuses `/infer` without the machine token and that `/detect` (preflight, 401 without a token) finds the cat in the test photo, that API Gateway's own errors carry a CORS header, `POST /upload-url`, the presigned `PUT`, that different content is rejected on the same URL, that tagging makes the file `READY` with `TAG#` items and `owner` set to the test user (it waits up to 3 minutes, enough for a cold start), that the `thumbnail` Lambda saved `thumbnails/<sha256>.jpg`, that the same file then gets 409, that `GET /files/{sha256}` shows it `READY` with tags and a `thumb_url` that downloads, and `GET /files` lists it (unknown SHA-256 → 404, invalid → 400), that bad requests return 400, and that the website is served with a `config.js` pointing at this deployment's API and the inference service. Afterwards it deletes its test object, its thumbnail and every DynamoDB item of it (`META` and `TAG#`). It exits with 0 only if every check passes.

### Website

Open the `website_https_url` output (`https://aussie-ecoguard-web-<account-id>.s3.ap-southeast-2.amazonaws.com/index.html`). Use this regional address: sign-in only returns to it, and the media bucket's CORS only allows it. The page uses Bootstrap 5 from a CDN and has one tab per feature; so far only **Library**. Besides sign-in, it has two independent parts that share no data:

- **Sign-in** (`web/auth.js`) runs before the other scripts and keeps the page hidden until the user is signed in with Cognito. Every API call goes through its `apiFetch(path, options)`, a `fetch()` that adds the ID token in the `Authorization` header; **use it for any new API call**, or the request will be refused once the API requires sign-in. Do not use it for the presigned S3 URLs, which carry their own signature.
- **Detect** (`web/detect.js`) has no UI of its own. `detectFile(file)` sends an image to Cloud Run `POST /detect` with the user's ID token and returns `{tags, attributes, model}`; the image is never stored. It is for the searches that start from a file (same tags as a file, lost pet by photo):

  ```js
  try {
    const { tags, attributes } = await detectFile(file);  // {"cat": 1}, ["orange"]
    // ...then search with them through the API (apiFetch)
  } catch (err) {
    showToast(err.message, "error");                   // e.g. "Choose a JPEG or PNG image"
  }
  ```

  JPEG or PNG only, at most 30 MB (checked before sending). The first call after a quiet period can take about a minute while Cloud Run starts, so show that it is working. To try it, run this in the browser console on the website and pick an image:

  ```js
  const i = document.createElement("input"); i.type = "file";
  i.onchange = async () => console.log(await detectFile(i.files[0])); i.click();
  ```
- **Upload panel** (`web/upload.js`) only talks to `POST /upload-url` and S3. For each chosen file it computes the SHA-256, reserves it, and PUTs it to S3 with a progress bar, then shows "Uploaded", "Already uploaded" (409) or the error. After an upload it fires a `files-changed` browser event that carries no data.
- **Notifications tab** (`web/subscribe.js`) only talks to `POST /subscriptions`. The user ticks species and subscribes; the tab then says to confirm the email, or that the species were updated if they had confirmed before.
- **Library** (`web/library.js`) only talks to the files API. It loads `GET /files` when the page opens, on **Refresh** and on `files-changed`, and shows one card per file: preview (image, or a video player), name, date, and the status or the tags as badges. Cards that are not `READY` or `FAILED` yet are polled with `GET /files/{sha256}` every 3 seconds (up to 15 minutes), so a new upload goes Uploading → Queued for tagging → Detecting species → tags. A card is only redrawn when that stage changes.

`READY` images show their thumbnail (`thumb_url`) and open the original in a new tab when clicked. If the thumbnail does not load, the card shows the original instead. Videos and files that are not `READY` yet use the original (`preview_url`). Each response is signed again, so redrawing a card downloads the image again.

Use the HTTPS address. The browser only computes SHA-256 (`crypto.subtle`) on HTTPS pages or `localhost`, so on the HTTP website endpoint the page shows a warning and disables the file picker. Files over 500 MB are refused because the browser reads the whole file into memory to hash it.

To try changes to `web/` without deploying, serve the folder locally (the media bucket's CORS allows `http://localhost:8000`):

```bash
python -m http.server 8000 --directory web   # then open http://localhost:8000
```

This needs `web/config.js` (git-ignored; Terraform only writes the copy in the bucket). Create it by hand from the outputs of `terraform -chdir=infra/terraform/aws output`:

```js
window.CONFIG = {
  apiUrl: "<api_url>",
  cognitoDomain: "<cognito_domain_url>",
  clientId: "<cognito_client_id>",
  inferenceUrl: "<inference_url>",
};
```

Sign-in sends you back to `http://localhost:8000/index.html`, one of the app client's callback URLs, so use port 8000.

### Upload a real file

```bash
./scripts/upload.sh path/to/fox.jpg
```

Uploads the file the same way the website does (signed in as the Cognito test user), waits for tagging and prints the stored items. A file that is already in the system gets 409, and the script prints its items instead. The file stays in the system.

### Manual test of the inference service

```bash
TF="terraform -chdir=infra/terraform/aws output -raw"
SVC=$($TF inference_url)
curl "$SVC/health"
# A token as the machine client, the way the tagging Lambda gets it (valid for 1 hour)
TOKEN=$(curl -s -X POST "$($TF cognito_domain_url)/oauth2/token" \
  -u "$($TF cognito_machine_client_id):$($TF cognito_machine_client_secret)" \
  -d "grant_type=client_credentials&scope=aussie-ecoguard/infer" |
  python -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
URL=$(aws s3 presign "s3://aussie-ecoguard-media-<account-id>/uploads/<sha256>" --expires-in 300)
curl -X POST "$SVC/infer" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"url\": \"$URL\", \"file_type\": \"image\"}"
```

`aws s3 presign` works offline, so with expired credentials it still prints a URL, but S3 rejects it (`502 ... S3 returned 400`). Refresh the credentials first.

### Run the inference service locally

With the real models (a CPU is enough; loading takes 10–20 s and needs about 3 GB of memory):

```bash
python -m venv .venv-inference                                      # first time only (git-ignored)
.venv-inference/Scripts/pip install -r inference/requirements.txt   # macOS/Linux: .venv-inference/bin/...
.venv-inference/Scripts/python infra/fetch_models.py                # ~1.1 GB into models/ (git-ignored)
cd inference && MODEL_DIR=../models ../.venv-inference/Scripts/python -m uvicorn main:app --port 8080
```

Without `MODEL_GCS` the service reads the models straight from `MODEL_DIR`. After changing `infra/model/config.json`, copy it to `models/config.json` (or run `fetch_models.py` again). Only presigned URLs of the media bucket are accepted, so test `/infer` as in the previous section with `SVC=http://localhost:8080`. It needs a token too, so also set `TRUSTED_USER_POOLS=<your cognito_user_pool_id>` when starting uvicorn.

### Manual test of upload-url

`scripts/upload.sh` does all of this; the steps by hand are:

```bash
TF="terraform -chdir=infra/terraform/aws output -raw"
API=$($TF upload_url_endpoint)
# An ID token for the test user (valid for 1 hour)
TOKEN=$(aws cognito-idp admin-initiate-auth --region ap-southeast-2   --user-pool-id "$($TF cognito_user_pool_id)" --client-id "$($TF cognito_client_id)"   --auth-flow ADMIN_USER_PASSWORD_AUTH   --auth-parameters "USERNAME=$($TF smoke_test_username),PASSWORD=$($TF smoke_test_password)"   --query AuthenticationResult.IdToken --output text)
SHA=$(sha256sum fox.jpg | cut -d' ' -f1)
curl -i -X POST "$API" -H "Authorization: $TOKEN" -H "Content-Type: application/json" \
  -d "{\"sha256\": \"$SHA\", \"filename\": \"fox.jpg\"}"

# Use the url and headers from the response (quote the URL, it contains &):
curl -i -X PUT -T fox.jpg \
  -H "Content-Type: image/jpeg" \
  -H "x-amz-checksum-sha256: <value from headers>" \
  "<url>"
```

You can also invoke the function in the Lambda console (**Test** tab) with this event, which has the same shape API Gateway sends after the authorizer accepted a token (without `requestContext` the function answers 401):

```json
{"body": "{\"sha256\": \"<HASH>\", \"filename\": \"fox.jpg\"}",
 "requestContext": {"authorizer": {"claims": {"sub": "console-test"}}}}
```

## Tear down

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
aws s3 rm "s3://aussie-ecoguard-media-$ACCOUNT_ID" --recursive    # destroy refuses to delete user media
terraform -chdir=infra/terraform/aws destroy
```

The shared inference service (only its host, and only when nobody needs it any more):

```bash
NUMBER=$(gcloud projects describe "$(gcloud config get-value project)" --format='value(projectNumber)')
gcloud storage rm "gs://aussie-ecoguard-models-$NUMBER/**"    # destroy refuses to delete the models
terraform -chdir=infra/terraform/gcp destroy
```

The GCP APIs stay enabled (`disable_on_destroy = false`), since other things in the project may use them.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Running `./scripts/smoke_test.sh` in PowerShell opens a "choose an app" dialog | PowerShell cannot run `.sh` files. Close the dialog without choosing "Always". Switch the VS Code terminal to **Git Bash**, or run `& "C:\Program Files\Git\bin\bash.exe" ./scripts/smoke_test.sh` |
| `ExpiredToken` / `InvalidClientTokenId`, or the CLI asks you to log in | The `aws login` session ended (after 12 hours). Run `aws login` again |
| `AccessDenied` / `UnauthorizedOperation` with `explicit deny in a service control policy` | The command or `apply` ran in a region other than `ap-southeast-2`, which the shared account's organization blocks. Check `aws configure get region`, and use `--region ap-southeast-2` |
| Uploads stay `PENDING`; the tagging queue has messages but nothing processes them; smoke test fails `tagging queue trigger enabled` | The execution role lost its SQS permissions (on Learner Lab this happened whenever a session ended). Lambda then disables the queue trigger by itself (CloudTrail event `LambdaESMDisabled`; the mapping shows `USER_INITIATED`, which is misleading) and does not turn it back on. Run `terraform -chdir=infra/terraform/aws apply`, which restores the role's policies and enables the trigger. Queued messages are kept for 4 days and are processed once it is enabled |
| A file is `FAILED` | Its `META` item has an `error` attribute with the reason, also in the `tagging` logs. Fix the cause and upload the file again (`POST /upload-url` allows it) |
| A file stays `PROCESSING` | The `tagging` Lambda crashed or timed out on every delivery and the message is in the dead-letter queue. Check the logs, fix the cause, then SQS console → `aussie-ecoguard-tagging-dlq` → **Start DLQ redrive** to send it back; tagging continues from `PROCESSING` |
| Website says "must be opened over HTTPS" or "config.js is missing" | Open the `website_https_url` output, not the `http://...s3-website...` address. For a local copy, create `web/config.js` (see [Website](#website)) |
| Website upload fails with `Failed to fetch` or `Network error` | Usually CORS: the page's address must be one of the media bucket's allowed origins (the two website addresses or `http://localhost:8000`). The browser console (F12) shows the blocked request |
| `terraform apply` asks for `var.inference_url` (or `var.gcp_project_id`) | `terraform.tfvars` is missing in that directory. Copy it from `terraform.tfvars.example` (see [Deploy](#deploy)) |
| Uploads become `FAILED` with a connection error or 404 from the inference service | `inference_url` in `infra/terraform/aws/terraform.tfvars` is wrong or the shared service moved. Compare it with `terraform.tfvars.example`, then `apply` |
| Terraform says `could not find default credentials` for google | Run `gcloud auth application-default login` |
| `terraform apply` fails with `reading S3 Bucket ... object lock configuration ... explicit deny in a service control policy` | Someone used `aws_s3_bucket` in an account that blocks a permission it needs (Learner Lab does). Create buckets with `terraform_data` as in `storage.tf` |
| Scripts print `No outputs found`, or `apply` fails because the resources already exist | The Terraform state is on another machine or was deleted. Each person deploys and tests from the machine that holds their `terraform.tfstate` |
| `/infer` returns `502 could not download file (S3 returned 400)` for a URL from `aws s3 presign` | `aws s3 presign` signs offline, even with expired credentials, and S3 then answers `ExpiredToken`. Refresh the credentials and presign again |
| The first upload after a quiet period takes about a minute | Cloud Run scaled to zero, and a new instance downloads and loads the models first (`Downloaded ... in` and `Loaded models in` in its logs). Later uploads are fast. `--min-instances 1` would avoid it but is billed all the time |
| A new Cloud Run revision does not start; its logs show `no model files under gs://...` or a 403 from Cloud Storage | The version folder does not exist (run `./infra/upload_model.sh <version>`), or the service account lost read access to the model bucket (running `upload_model.sh` again adds it back) |
| The image build fails with `storage.objects.get` denied | Cloud Build runs as the Compute Engine default service account, which needs `roles/run.builder`. Terraform grants it (`gcp.tf`), but a new grant can take a minute to apply; run `apply` in `infra/terraform/gcp` again |
| Uploads become `FAILED` with `inference returned 401: token is not from a trusted user pool` | The shared Cloud Run does not trust your Cognito user pool yet. Send `terraform -chdir=infra/terraform/aws output -raw cognito_user_pool_id` to the person hosting the inference service; they add it to `trusted_user_pools` and `apply` the GCP side. Then upload the file again |
| API returns 401 `Unauthorized`, or 403 `Access Denied` | The Cognito authorizer refused the token. 401: the `Authorization` header is missing, not a JWT, or expired (ID tokens last 1 hour). 403 `Access Denied` (not `Missing Authentication Token`): a JWT whose signature does not check out, e.g. edited by hand or from another user pool. The website gets a fresh token by itself; reload the page or sign in again. For curl, get a new token as in [Manual test of upload-url](#manual-test-of-upload-url). Your own code that calls the API must use `apiFetch()` (see [Website](#website)) |
| API returns 403 `Missing Authentication Token` | Not an auth problem: the path or method does not exist, for example a `GET` on `/upload-url`, a typo, or a missing `/api` stage. Right after a deploy that **adds** a route, the new route can answer 403 for about a minute until every API Gateway node has the new deployment; the smoke test can fail in that window, so run it again |
| `apply` fails at `terraform_data.pillow_layer` with `pip3: not found` (or `'pip3' is not recognized`) | Install Python 3.10+ with pip, make sure `pip3 --version` works in a new terminal, then `apply` again |
| `plan` or `apply` fails with `could not archive missing directory` for `build/pillow-layer` | The git-ignored `build/` folder was deleted after the layer was built. Run `terraform -chdir=infra/terraform/aws apply -replace=terraform_data.pillow_layer` to install Pillow again |
| Library cards of new images show the original instead of a thumbnail | Check `/aws/lambda/aussie-ecoguard-thumbnail`: no log line means the EventBridge target or its permission is missing (`apply`); `skipped ... cannot read the image` means Pillow could not open that file |
| `PUT` returns 403 `SignatureDoesNotMatch` | The URL was not copied completely, or the headers differ from the ones returned |
| `PUT` returns 400 `BadDigest` | The file content does not match the SHA-256 that was reserved |
| `PUT` returns 403 `Request has expired` | More than 15 minutes passed, or the lab was restarted. Request a new URL |
| Output stops at `-- More --` | AWS CLI pager. Press `q`. The scripts set `AWS_PAGER=""` to avoid it |
