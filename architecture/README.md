# Aussie EcoGuard: System Architecture

This document explains the multi-cloud architecture shown in [architecture.png](architecture.png) (editable source: [architecture.drawio](architecture.drawio)). The circled numbers ① to ⑪ used below match the numbered arrows in the diagram.

![Architecture diagram](architecture.png)

## 1. Overview

AWS is the primary cloud. It hosts authentication, the REST API, storage, the database, the chatbot and notifications. Google Cloud (GCP) runs the machine learning inference service and stores the model files.

| Cloud | Service | Role in the system |
|---|---|---|
| AWS | Amazon S3 (static website) | Hosts the web UI |
| AWS | Amazon Cognito | User sign-up, email verification and sign-in (Hosted UI); issues JWTs |
| AWS | Amazon API Gateway (REST) | Public API. A Cognito authorizer rejects requests that do not carry a valid JWT |
| AWS | AWS Lambda (API functions) | `upload-url`, `files`, `search`, `edit-tags`, `delete-files`, `chat`, `subscribe` |
| AWS | Amazon S3 (media bucket) | Original files in `uploads/`, thumbnails in `thumbnails/` |
| AWS | Amazon EventBridge | Routes each S3 "Object Created" event to the thumbnail Lambda and to the tagging queue (see §4) |
| AWS | Amazon SQS | Queues uploads for the tagging Lambda, so at most two files are tagged at once; a dead-letter queue keeps messages that keep failing (see §4) |
| AWS | AWS Lambda (event functions) | `thumbnail`, `tagging`, `notify` |
| AWS | Amazon DynamoDB | Single `Media` table holding file metadata and tags (§6); DynamoDB Streams drive notifications |
| AWS | Amazon Lex V2 | Chatbot that turns natural-language queries into intents and slots |
| AWS | Amazon SNS | Sends tag-based email notifications, filtered per subscriber |
| AWS | AWS Secrets Manager | Holds the GCP service-account key that AWS uses to call Cloud Run |
| GCP | Cloud Run | Containerised inference API: MegaDetector → SpeciesNet → CLIP (§7) |
| GCP | Cloud Storage | Model versions: one folder per version with the weights and a `config.json` (§7.5) |

AWS runs in `us-east-1`, the region AWS Academy Learner Lab allows. GCP runs in `us-east4` (Northern Virginia), next to `us-east-1`: the only caller of Cloud Run is the `tagging` Lambda, and Cloud Run downloads each file from the S3 bucket in `us-east-1`, so keeping both clouds in the same area avoids a trans-Pacific round trip on every call and every video frame read.

## 2. Authentication and authorisation

1. **① Load the UI.** The browser loads the static web app from S3.
2. **② Sign up and sign in.** These go through the Cognito Hosted UI. When a user signs up, Cognito records their email, first name, last name and password, then sends a verification email. After sign-in, the browser holds a Cognito **ID token (JWT)**.
3. **③ API calls.** Every API request carries the JWT in the `Authorization` header. The API Gateway **Cognito authorizer** validates it before any Lambda runs. A user who is not signed in can only reach the sign-up and sign-in pages.
4. **Second cloud (⑪).** Cloud Run never trusts a request just because it arrives. Requests from the browser must carry the same Cognito JWT, and Cloud Run checks its signature against the User Pool's public **JWKS** endpoint (`https://cognito-idp.us-east-1.amazonaws.com/<userPoolId>/.well-known/jwks.json`). It also checks the `iss`, `aud`/`client_id` and `exp` claims. Requests from AWS (⑦) carry a Google-signed ID token for a dedicated GCP service account instead. Google's built-in Cloud Run IAM check cannot validate Cognito tokens, so it is turned off (`--allow-unauthenticated`). The service checks every request in its own code instead, and rejects anything that is neither a valid Cognito JWT nor a Google ID token issued to the AWS caller's service account.
5. **Least privilege.** Each Lambda gets only the permissions it needs, for example `s3:DeleteObject` only for `delete-files`. On AWS Academy every function shares `LabRole`. That is a known limitation, and with a personal AWS account this would become one IAM role per function.

## 3. Upload pipeline

The file never passes through API Gateway or the API Lambdas. API Gateway limits request bodies to 10 MB and synchronous Lambda calls to 6 MB, which is too small for video. Instead the browser uploads straight to S3 using a **presigned URL**.

```
Browser                API Gateway + upload-url λ        S3 media bucket        EventBridge        thumbnail λ / tagging λ
   │  ③ POST /upload-url {sha256, filename} + JWT            │                       │                     │
   │ ─────────────────────────────►│                          │                       │                     │
   │                               │ conditional write: META = PENDING                │                     │
   │   409 duplicate  ◄────────────┤ (PROCESSING / READY)     │                       │                     │
   │   presigned PUT URL ◄─────────┤ (new, PENDING or FAILED) │                       │                     │
   │  ④ PUT file (direct to S3) ─────────────────────────────►│                       │                     │
   │                                                          │ Object Created ──────►│ rule 1 ──► thumbnail ⑤
   │                                                          │                       │ rule 2 ──► SQS ──► tagging ⑥
```

1. **Checksum in the browser.** The browser computes the file's **SHA-256** and calls `POST /upload-url` with the checksum and the file name (③). The Lambda derives the file type and `Content-Type` from the extension (`jpg`/`jpeg`, `png`, `mp4`, `mov`) instead of trusting a type sent by the client.
2. **Deduplication.** The `upload-url` Lambda reserves the checksum with one conditional write of a `META` item with status `PENDING`. If the file is already `PROCESSING` or `READY`, the write fails and the API returns `409 Duplicate` with the existing status and key, and nothing is uploaded. The write is atomic, so two simultaneous requests for the same new file cannot both reserve it. §3.1 describes the statuses.
3. **Presigned URL.** For a new file, the Lambda returns a short-lived presigned **PUT** URL for `uploads/<sha256>`. The `ChecksumSHA256` and the `Content-Type` are part of the signature, so the browser cannot change either of them, and S3 rejects any upload whose content does not match the checksum. Because S3 itself checks the checksum, the key always matches the content and nothing has to re-hash the object later.
4. **Direct upload (④).** The browser PUTs the file straight to S3.
5. **Event routing.** S3 sends an "Object Created" event to EventBridge. Two EventBridge rules, both filtered to the `uploads/` prefix, start the two processing Lambdas in parallel:
   - **⑤ `thumbnail`** reads the object with `GetObject` and first checks the `Content-Type` in the response. Keys have no extension, so the rule cannot filter by suffix; for anything that is not `image/*` the Lambda returns before reading the body. For images it resizes the picture while keeping the aspect ratio, compresses it with OpenCV, and writes the result to `thumbnails/`. Because the rules only match `uploads/`, writing a thumbnail does not trigger the pipeline again.
   - **⑥ `tagging`** is fed through an SQS queue (§4). It first moves the `META` item from `PENDING` to `PROCESSING`, then creates a presigned **GET** URL for the object and sends it, with the `file_type`, to Cloud Run (⑦). The call is authenticated with a GCP ID token minted from the service-account key held in Secrets Manager.
6. **⑦ ⑧ Inference on GCP.** When a Cloud Run instance starts, it downloads its model version (weights and `config.json`) from Cloud Storage and loads the models once (§7.5). For each request it downloads the file from the presigned URL, runs the pipeline in §7 and returns per-species counts and colour attributes such as `{"tags": {"fox": 2, "cat": 1}, "attributes": ["orange"]}`. **Videos:** one frame is extracted every 10 seconds and each frame is run through the detector. The stored count for each species is the highest count seen in any single frame, so the same animal is not counted twice across frames.
7. **⑨ Persist.** `tagging` writes to the `Media` table in a single transaction: it updates the `META` item from `PROCESSING` to `READY`, adding the thumbnail URL and colour attributes (file type and file URL are already there from `upload-url`), and puts one `TAG#<species>` item per species with its count (§6). If inference fails, the `META` item becomes `FAILED` instead (§3.1).
8. **⑩ Notify.** Each new `TAG#` item appears on the DynamoDB Stream. The `notify` Lambda publishes to SNS with the species as message attributes, and each subscriber's **filter policy** decides who receives an email.

### 3.1 File status

Every file's `META` item has a `status` that the two Lambdas move forward with conditional writes:

```
           upload-url              tagging starts              tagging succeeds
  (none) ───────────────► PENDING ───────────────► PROCESSING ───────────────► READY
                           │    ▲                      │
    TTL, if never          │    │ upload-url           │ tagging gives up
    uploaded               ▼    │ (upload again)       ▼
                       (deleted)└────────────────── FAILED
```

| Status | Meaning | Another `POST /upload-url` for the same file |
|---|---|---|
| `PENDING` | URL issued, file not in S3 yet | 200 with a fresh URL for the same key, so a failed upload can simply be retried |
| `PROCESSING` | File in S3, tagging running | 409 |
| `READY` | Tagged and searchable | 409 |
| `FAILED` | Tagging gave up; the item has an `error` attribute | 200, upload again |

- **Only `upload-url` creates `META` items.** `tagging` skips objects that have none, which can only be files someone put into the bucket by hand, bypassing the checksum check.
- **`PENDING` is re-issued to anyone.** Every URL for a file points at the same key and is bound to the same checksum, so every upload writes the same bytes. Once Cognito is in place, a fresh URL will only go to the user who reserved the file, and others will get 409.
- **`expires_at` only cleans up.** `upload-url` sets it 24 hours ahead as the table's TTL attribute, so abandoned reservations disappear. DynamoDB deletes expired items within a few days and they stay readable until then, so no code decides anything from it. `tagging` removes it when the file reaches `PROCESSING`.
- **Duplicate deliveries are harmless.** SQS delivers at least once, so `tagging` may see the same upload twice. It accepts `PENDING` or `PROCESSING` when it starts, and it writes `READY` only while the item is still `PROCESSING`. A second delivery therefore cannot write the tags twice, and one for a `READY` or `FAILED` file is skipped.
- **Retries.** Temporary errors from Cloud Run (network, timeout, 429, 5xx) are retried by SQS, and the file becomes `FAILED` on the 3rd attempt. Other 4xx answers and malformed results make it `FAILED` at once. If the Lambda itself crashes on every delivery, the message ends up in the dead-letter queue and the file stays `PROCESSING` until the message is redriven.
- **Searches only see finished files.** `TAG#` items are written in the same transaction that sets `READY`, so files that are still being processed or have failed never appear in tag searches.

## 4. Why EventBridge and SQS sit between S3 and the Lambdas

S3 event notifications do **not** allow two configurations with the same event type and an overlapping prefix or suffix. As a result, `uploads/` cannot trigger both `thumbnail` and `tagging` directly. EventBridge solves this without any extra code:

- With "Send notifications to Amazon EventBridge" turned on, the bucket publishes every event to the account's default **event bus**.
- Each **rule** has an event pattern, for example `source = aws.s3`, `detail-type = Object Created`, `bucket = <media bucket>` and `key` prefix `uploads/`, plus a **target** (a Lambda).
- Two rules match the same event, so both Lambdas run in parallel. Adding another consumer later is just another rule, with no change to the bucket or to existing code.
- EventBridge also provides retries and optional dead-letter queues for failed invocations.

An SNS topic fan-out (S3 → SNS → two Lambdas) would also work. EventBridge was chosen because its rules can filter on the object key and it needs no extra topic.

**SQS in front of `tagging`.** Rule 2 does not invoke `tagging` directly. It sends the event to an SQS queue, and a Lambda event source mapping reads the queue one message at a time:

- **Flow control.** The mapping's `MaximumConcurrency` is 2, the same as Cloud Run's instance limit. With a real model each instance handles about one file at a time, so if every upload invoked `tagging` straight away, a burst of uploads would overload Cloud Run and fail with 429. With the queue, the extra uploads simply wait.
- **Nothing is lost while the function cannot run.** Messages stay in the queue for up to 4 days, for example while Lambda has turned the queue trigger off after a Learner Lab session ended. Capping a directly invoked function with reserved concurrency would also limit the load, but Lambda keeps throttled asynchronous events for at most 6 hours, and retries a failed invocation only twice.
- **Retries without waiting inside Lambda.** When Cloud Run has a temporary problem, `tagging` raises an error and SQS delivers the message again after the visibility timeout. The function does not sleep between attempts, so no Lambda time is billed while waiting.
- **Dead-letter queue.** A message that keeps crashing the function moves to `aussie-ecoguard-tagging-dlq` and is kept for 14 days. After a fix it can be sent back with one click ("Start DLQ redrive").
- **Settings follow the AWS guidance for SQS event sources:** a visibility timeout of six times the function timeout (810 s for 135 s) and a `maxReceiveCount` of 5. The function timeout covers one Cloud Run call of up to 120 s: a cold start (about a minute to download and load the models) plus inference. `tagging` itself gives up and writes `FAILED` on the 3rd attempt (§3.1), so the 4th and 5th deliveries are only a margin for crashes.

## 5. Core features (Spec §4), requirement by requirement

This section maps each requirement in Spec §4 to the flow that implements it. Every REST call goes through API Gateway (③) with the Cognito JWT, except the call to Cloud Run in ⑪, which carries the same JWT straight to GCP (§2).

**Keys and URLs.** Every object key is derived from the file's SHA-256: `uploads/<sha256>` and `thumbnails/<sha256>.jpg`. The original's key has no extension, which is the usual design for content-addressed storage: the key depends only on the content, so the same bytes uploaded as `fox.jpg` and `fox.png` become one object. The type is kept as the object's `Content-Type` (used by browsers) and as `file_type` in the `META` item (used by `tagging` and the queries). Image and video decoders recognise the format from the file's first bytes, so the model does not need an extension either. Any file URL or thumbnail URL can therefore be turned back into its `file_id` by parsing the path, with no extra lookup. The media bucket is private, so `search` returns short-lived **presigned GET URLs**.

### 5.1 Model handling (Spec §4.1)

| Requirement | How it is met | Diagram |
|---|---|---|
| Entry model must be MegaDetector, Ultralytics YOLO or a managed service | **MegaDetector v5a** finds and counts the animals. It was chosen after comparing ten pipelines, including YOLOv8 Open Images, Amazon Rekognition and MegaDetector + CLIP, on a labelled test set (§7.1). Plain COCO-trained YOLO is ruled out because COCO has `cat`, `dog` and `bird` but no fox, rabbit, feral pig or cane toad | ⑦ |
| Use a new model version **without changing source code or Lambdas** | Each model version is a folder in Cloud Storage with its weights and `config.json` (thresholds, species list, mapping rules). To change model, species list or threshold, copy a version, edit it, and point Cloud Run at it with one environment variable; Cloud Run starts a new revision that downloads that version (§7.5) | ⑧ |
| Optional model chaining | Three models in a chain: MegaDetector boxes → the **SpeciesNet** classifier names each box → a **CLIP** zero-shot classifier checks every box: it names the boxes when SpeciesNet is unsure about the whole image, and overrules SpeciesNet when it is almost certain of another species. `cat` / `dog` boxes go through CLIP again for their colour (§7.2) | ⑦ |
| No training or fine-tuning | Only pretrained weights are used | n/a |

### 5.2 File handling (Spec §4.2)

| Requirement | How it is met | Diagram |
|---|---|---|
| Upload to cloud storage via REST or SDK | `POST /upload-url` returns a presigned PUT URL and the browser uploads straight to S3 (§3) | ③ ④ |
| **Every upload triggers an event and a serverless function** | S3 **Object Created** → EventBridge → `thumbnail` Lambda, and → SQS → `tagging` Lambda (§4) | ⑤ ⑥ |
| Deduplication by checksum | The browser sends the SHA-256 first; `upload-url` reserves it with a conditional write of a `PENDING` `META` item and returns `409` if the file is already `PROCESSING` or `READY` (§3.1). The presigned PUT is bound to that checksum so the content cannot be swapped, and because the key is `uploads/<sha256>` the same content is never stored twice | ③ |
| Thumbnail generation | Keys have no extension, so the `thumbnail` Lambda checks the object's `Content-Type` and skips anything that is not an image. It uses OpenCV to scale the longest side to 256 px, keeping the aspect ratio, saves a JPEG at quality 70 to `thumbnails/<sha256>.jpg`, and has `s3:GetObject` on `uploads/*` and `s3:PutObject` on `thumbnails/*` | ⑤ |
| Tag the file and save type, tags, file URL and thumbnail URL | `tagging` sends a presigned GET URL to Cloud Run and writes the result to `Media` (the `META` item becomes `READY`, plus one `TAG#` item per species). Because thumbnail keys are deterministic, `tagging` can store `thumb_url` without waiting for the `thumbnail` Lambda | ⑥ ⑦ ⑧ ⑨ |
| Videos: **one frame every 10 seconds** | For a video, Cloud Run opens the presigned URL with OpenCV, seeks in 10-second steps (`CAP_PROP_POS_MSEC`), runs detection on each sampled frame, and keeps the highest count per species across frames | ⑦ |

### 5.3 Queries (Spec §4.3)

#### Find files by tags with minimum counts

- **Request:** `POST /search/tags` with `{"feral pig": 1, "cane toad": 3}`
- **Flow:** `search` runs one query per tag on the GSI `tag-count-index`, for example `tag = "cane toad" AND tag_count >= 3`. It intersects the resulting `file_id` sets (logical **AND**), then `BatchGetItem`s the matching `META` items (keys `{file_id, sk: "META"}`).
- **Response:** thumbnail URLs for images and file URLs for videos.

#### Find files by species, and find a lost pet

- **By species:** `POST /search/species` with `{"species": ["cat"]}`. This is the spec's `{"cat"}` written as valid JSON. It is the query above with a minimum count of 1.
- **Lost pet by description:** `POST /lost-pet` with `{"species": "cat", "attributes": ["orange"], "days": 14}`. `search` queries `tag-count-index` for the species and batch-gets the `META` items. It keeps a file only if every requested colour or breed term appears in its `attributes` or as a manual tag (a `TAG#<term>` item, found with the same GSI query), and keeps only those whose `created_at` falls within the last `days` days (recent sightings), newest first.
- **Lost pet by photo:** first ⑪. The browser sends the pet photo to Cloud Run `/detect`, which returns its species and attributes without storing the image. Then it calls `POST /lost-pet` with them.

#### Find the original file from a thumbnail URL

- **Request:** `GET /search/original?thumb_url=<url>`
- **Flow:** `search` parses `<sha256>` from the `thumbnails/<sha256>.jpg` path and does a `GetItem` on `Media` with key `{file_id, sk: "META"}`, then returns the original file's URL.

#### Find files using the tags of an uploaded file (⑪)

1. The browser sends the file and the Cognito JWT to Cloud Run `POST /detect`.
2. Cloud Run verifies the JWT, runs detection **in memory** and returns the detected tag set, e.g. `{"fox": 2, "cat": 1}`. The file is never written to S3, Cloud Storage or DynamoDB, which meets the privacy requirement.
3. The browser calls `POST /search/tags` with each detected tag at a minimum count of 1, and gets back every file that contains the whole tag set.

#### Add or remove tags manually, in bulk

- **Request:** `POST /tags` with `{"url": ["<url1>", "<url2>"], "operation": 1, "tags": {"fox": 1, "cat": 2}}`
- **`operation: 1` (add):** sets each tag's count on every listed file.
- **`operation: 0` (remove):** deletes those tags; counts are ignored, and a tag the file does not have is silently skipped.
- **Flow:** `edit-tags` parses each `file_id` from its URL. Because each tag is its own item, every change is a single-item write:
  - add: a `Put` of `{file_id, sk: "TAG#fox", tag: "fox", tag_count: 1}`, paired in a small transaction with a `ConditionCheck` that the file's `META` item exists, so tags cannot be attached to an unknown URL
  - remove: a `Delete` of `{file_id, sk: "TAG#fox"}`; deleting an item that does not exist is a no-op, which covers "ignore tags the file does not have"
- **Notifications:** a newly added `TAG#` item appears on the DynamoDB Stream, so subscribers of that tag are notified (5.4).

#### Delete files

- **Request:** `POST /delete` with `{"url": ["<url1>", "<url2>"]}`
- **Flow:** `delete-files` parses each `file_id`, deletes `uploads/<sha256>` and `thumbnails/<sha256>.jpg` from S3, then runs one query, `file_id = <sha256>`, to get the file's `META` item and all of its `TAG#` items, and removes them with `BatchWriteItem`.

#### Chatbot with natural-language queries

1. The browser sends `POST /chat` with `{"text": "show me all fox photos found yesterday", "sessionId": "…"}`.
2. The `chat` Lambda passes the text to Lex V2 `RecognizeText`.
3. Lex resolves the **intent** and **slots**:
   - `FindSightings`: `species = fox`, `date = yesterday`, which the built-in `AMAZON.Date` slot type resolves to an actual date
   - `FindLostPet`: `species = cat`, `colour = orange`
4. Lex calls `search` as its fulfillment code hook. `search` runs the matching query above and filters by `created_at` when a date is given.
5. The links are returned through Lex and `chat` to the chat window.

### 5.4 Tag-based notifications (Spec §4.4)

1. **Subscribe:** `POST /subscriptions` with `{"tags": ["fox", "cane toad"]}`. A small `subscribe` Lambda reads the user's email from the JWT claims and calls SNS `Subscribe` with an email endpoint and a filter policy such as `{"species": ["fox", "cane toad"]}`. SNS sends a one-time confirmation email.
2. **Trigger:** the `notify` Lambda reads the `Media` table's DynamoDB Stream (⑩). Its trigger has an **event filter** that passes only `INSERT` records whose `sk` begins with `TAG#`. Every record that reaches `notify` therefore means "file X gained tag Y", whether from automatic tagging or a manual edit, and no before/after comparison is needed. Changes to `META` items and count updates never invoke it.
3. **Deliver:** `notify` groups the records in each batch by `file_id` and publishes one message per file, with the message attribute `species` set to the file's new tags, plus a link to the file's page in the web app, `https://<site>/file/<sha256>`, which loads the file with `GET /files/{sha256}` (§5.5). A presigned URL is not used because it expires along with the Lambda's temporary credentials. SNS emails only the subscribers whose filter policy matches.

### 5.5 Website support (not a Spec query)

Two read-only routes, served by one `files` Lambda, exist for the web UI rather than for a Spec §4.3 query:

- **`GET /files`** lists every file with its tags, newest first. The library tab shows it as a grid of cards. `PENDING` files appear only while their upload URL is still valid (15 minutes); older ones are abandoned reservations.
- **`GET /files/{sha256}`** returns one file in any status. Tagging runs asynchronously after the upload, so the browser has no other way to learn the result. This is the usual **asynchronous request-reply** pattern: the work is accepted at once, and the client polls a status resource until it reports a final state (`READY` or `FAILED`). The library polls unfinished cards every 3 seconds. The same route serves the file page linked from notification emails (§5.4).

Both return a presigned GET URL of the original as the preview until thumbnails are shown instead. The upload panel and the library share no data: after an upload the panel only fires a browser event, and the library reloads `GET /files` itself.

## 6. Data model (DynamoDB single-table design)

The data has no relationships that need SQL joins: every query is "files with these tags". DynamoDB is serverless, needs no VPC (an RDS database would put the Lambdas in a VPC and require a NAT gateway to reach GCP), bills per request, and its Streams feed the notification flow directly.

All data lives in **one table, `Media`**. A file's metadata and its tags share the same partition key (`file_id`), so each file forms one **item collection**, much like a folder. DynamoDB items are schemaless apart from the key: `META` items and `TAG` items carry different attributes.

### 6.1 Example

| `file_id` (PK) | `sk` (SK) | `status` | `file_url` | `thumb_url` | `file_type` | `attributes` | `owner` | `created_at` | `tag` | `tag_count` |
|---|---|---|---|---|---|---|---|---|---|---|
| `aaa…` | `META` | `READY` | `uploads/aaa…` | `thumbnails/aaa….jpg` | `image` | `[]` | `<sub>` | `2026-10-05T…` | | |
| `aaa…` | `TAG#fox` | | | | | | | | `fox` | `2` |
| `aaa…` | `TAG#cat` | | | | | | | | `cat` | `1` |
| `bbb…` | `META` | `READY` | `uploads/bbb…` | `thumbnails/bbb….jpg` | `image` | `["orange"]` | `<sub>` | `2026-10-06T…` | | |
| `bbb…` | `TAG#cat` | | | | | | | | `cat` | `1` |
| `ccc…` | `META` | `PENDING` | `uploads/ccc…` | | `video` | | `<sub>` | `2026-10-07T…` | | |

Empty cells are attributes the item simply does not have. They are not stored as nulls.

### 6.2 Keys and item types

| Item type | `sk` value | Attributes |
|---|---|---|
| File metadata | `META` | `status` (`PENDING` / `PROCESSING` / `READY` / `FAILED`, §3.1), `file_url`, `file_type` (`image` / `video`), `filename` (the uploaded file's name, for display only), `owner` (Cognito `sub`), `created_at` (ISO timestamp, used for "recent sightings"). Added by `tagging` when the file is `READY`: `thumb_url` (absent for videos), `attributes` (colour / breed terms, e.g. `["orange"]`), `model`, `tagged_at`. Only while `PENDING`: `expires_at` (TTL). Only when `FAILED`: `error` |
| Tag | `TAG#<species>`, e.g. `TAG#cane toad` | `tag` (species name), `tag_count` (number) |

- **Partition key `file_id`** is the file's SHA-256 (see "Keys and URLs" in §5). It is also the deduplication key: `upload-url` reserves it with a conditional write of the `META` item (§3.1).
- **Sort key `sk`**: the `TAG#` prefix tells the item types apart and allows `begins_with(sk, "TAG#")` to fetch only a file's tags.
- The count attribute is named `tag_count` because `count` is a DynamoDB reserved word.
- Key strings are built in one shared helper module, packaged as a Lambda Layer, so no Lambda hand-writes `"TAG#" + name`.

### 6.3 Global secondary index `tag-count-index`

| Setting | Value |
|---|---|
| Partition key | `tag` (String) |
| Sort key | `tag_count` (Number) |
| Projection | Keys only (the index then holds `tag`, `tag_count`, `file_id`, `sk`) |

This is a **sparse index**: `META` items have no `tag` attribute, so DynamoDB leaves them out automatically and the index contains only tag items. A query like `tag = "fox" AND tag_count >= 2` reads only matching items, with no padding tricks and no filter pass.

### 6.4 Access patterns

| Operation | DynamoDB calls |
|---|---|
| Reserve an upload (`upload-url`) | `PutItem` `META` with `status = PENDING` and `expires_at`, condition `attribute_not_exists(file_id) OR status IN (PENDING, FAILED)` |
| Start tagging | `UpdateItem` `META`: `SET status = PROCESSING REMOVE expires_at`, condition `status IN (PENDING, PROCESSING)`; returns the item, including `file_type` |
| Save a newly tagged file | `TransactWriteItems`: `Update` `META` to `READY` (condition `status = PROCESSING`) + `Put` one `TAG#` item per species |
| Tagging failed | `UpdateItem` `META`: `SET status = FAILED, error = <reason>`, condition `status = PROCESSING` |
| Get a file with all its tags (`GET /files/{sha256}`) | One strongly consistent `Query`: `file_id = :id`. This returns `META` and every `TAG#` item together and plays the role of the join |
| List all files with their tags (`GET /files`) | One paginated `Scan` of the whole table, grouped by `file_id` in the Lambda and sorted by `created_at`. Fine for a few hundred files; for more, a sparse GSI keyed on `created_at` would let it `Query` newest first, one page at a time |
| Search by tags with minimum counts | One `Query` on `tag-count-index` per tag, intersect the `file_id`s, then `BatchGetItem` the `META` items |
| Thumbnail URL to original | Parse `file_id` from the URL, then `GetItem` `{file_id, sk: "META"}` |
| Add a tag | `Put` `{file_id, sk: "TAG#<tag>"}` (with a `ConditionCheck` on `META`) |
| Remove a tag | `Delete` `{file_id, sk: "TAG#<tag>"}` |
| Delete a file | `Query` `file_id = :id`, then `BatchWriteItem` deletes every returned item |
| Notify on new tags | DynamoDB Stream (`NEW_IMAGE`) with an event filter: `INSERT` and `sk` begins with `TAG#` |

### 6.5 Consistency

- **Tags are stored exactly once**, as `TAG#` items. There is no second copy that could drift out of sync, and adding or removing a tag touches one item.
- **Every status change is a conditional write** (§3.1). Two requests or two deliveries of the same event cannot both win, and a file's status only changes along the arrows of the diagram there.
- **The GSI is eventually consistent.** A new tag normally becomes searchable within a second. A search issued immediately after an upload may miss the new file, which is acceptable for this application. Reads by primary key, such as file details or "thumbnail to original", can use strongly consistent reads.

## 7. Model

The inference service has to do two things:

1. **Detect and count animals, and name their species.** This is required, e.g. `{"fox": 2}`; tag queries and notifications depend on it.
2. **Describe pets with attributes such as colour.** The spec only requires that users *can search* by breed or colour. Colour tags can come from manual tagging (§5.3), so automatic colour recognition is an enhancement that makes the lost-pet and chatbot demos work without manual effort.

### 7.1 How the model was chosen

Ten pipelines were compared on 27 hand-labelled images (camera-trap shots including night infrared, groups of 12–17 animals, hidden animals, cats and dogs in one photo, and look-alikes such as kangaroos and a toad statue): MegaDetector (v5a and v1000) + CLIP, MegaDetector + BioCLIP, YOLOv8 trained on Open Images, Amazon Rekognition, Google SpeciesNet (its own ensemble, and with our Australia rules), and chains of SpeciesNet and CLIP. YOLO-World, considered in an earlier draft of this document, was not tested. The method, data and results are in [experiments/model-selection/README.md](../experiments/model-selection/README.md).

| Pipeline | Species exactly right | Total count error |
|---|---|---|
| **MegaDetector → SpeciesNet (per box, smoothed) → CLIP fallback (chosen)** | **23/26** | **9** |
| MegaDetector + CLIP | 21–22/26 | 31–37 |
| SpeciesNet's own ensemble | 15/26 | — |
| Amazon Rekognition | 17/26 | cannot count reliably |

CLIP and SpeciesNet fail in different places, which is why the chain works: SpeciesNet is trained on camera-trap images and is strong on small, blurry and night crops, but weak on cane toads. CLIP is trained on web photos, gets the toads right, and is weak on camera-trap crops.

### 7.2 The pipeline

```
image
  → MegaDetector v5a: one box per animal (confidence ≥ 0.2)
  → SpeciesNet classifier on every box, top 5 classes
      → mapping rules turn its taxonomy into our tags (e.g. family felidae → cat)
      → thresholds: a tag needs score ≥ 0.3; "no animal" needs ≥ 0.99
  → CLIP zero-shot on every box
      → CLIP ≥ 0.98 sure of another species: CLIP's tag replaces SpeciesNet's
      → no box got a tag from SpeciesNet: CLIP's tags are used instead
  → image-level smoothing: unsure boxes take the dominant species
  → count the boxes per tag            {"feral pig": 11, "cattle": 4}
  → CLIP on every cat / dog box for its colour          ["black"]
```

- **Mapping rules.** SpeciesNet knows about 2,500 classes worldwide and answers with a taxonomy such as `mammalia;carnivora;canidae;vulpes;vulpes;red fox`. Rules in `config.json` map taxonomy prefixes to our tags, first match wins: in Australia every felid is a cat, every rabbit or hare is a rabbit, every *Canis* is a dog (dingoes included), every member of the pig family is a feral pig, and so on. The scores of the top 5 classes that map to the same tag are added up.
- **Tags.** The six species the spec names (`fox`, `rabbit`, `feral pig`, `cane toad`, `cat`, `dog`), 16 other common animals (other invasive species such as `deer` and `goat`, natives such as `kangaroo` and `wombat`, and broad groups such as `bird` and `reptile`), and `other animal` for an animal that MegaDetector found but no model could name.
- **Smoothing** (a simplified version of MegaDetector's own): boxes without a confident tag take the image's dominant tag when it has at least 2 boxes, and a tag with a single box is overruled when the dominant tag has at least 4. Without it, only 4 of 17 distant pigs were recognised.
- **CLIP overrules SpeciesNet** only when both name a species, they differ, and CLIP is at least 0.98 sure (`clip_override_threshold`). SpeciesNet is trained on camera-trap images and called 6 of 9 close-up pet rabbits `cat` or `goat` (scores up to 0.93), while CLIP, trained on web photos, said `rabbit` at 0.99 or more. CLIP is confidently wrong too, on tiny distant animals: its highest wrong score on the test images was 0.96 (pigs called cattle). With 0.98, 5 of the 6 rabbits are fixed and none of the 27 test images changes; with 0.95 the pig group breaks. Checking every box costs about 0.1 s per box.
- **Colour** is only given for cats and dogs. `attributes` belongs to the file, not to one animal, so the colour of other animals would make "orange cat" match a photo of an orange fox and a grey cat.

**Known limits** (also in the [repository README](../README.md)): only 26 scored test images; very small distant animals are sometimes called the wrong species (17 pigs → 11 pigs + 4 cattle); the 0.98 override threshold rests on few images, and an old sepia photo of a rabbit is still tagged `cat`; videos are not supported yet (§5.2).

### 7.3 Running it on Cloud Run

| Setting | Value | Why |
|---|---|---|
| Resources | 4 vCPU, 8 GiB | The models need about 3 GB at peak, plus 1.1 GB while the downloaded files are in `/tmp` (memory on Cloud Run). With 4 CPUs inference takes half as long as with 2, so an image costs slightly less |
| Concurrency | 1 request per instance | A second request would only compete for the same CPUs |
| Instances | 0 to 2 | Scales to zero when idle; 2 matches the SQS `MaximumConcurrency` (§4) |
| Request timeout | 120 s | A cold start plus the slowest image measured |

Measured: 4–12 s per image (12 s for 17 animals); a new instance needs about a minute before it answers (download about 17 s, then loading). So the first upload after about 15 idle minutes waits about a minute.

### 7.4 Colour attributes

For each `cat` / `dog` box, CLIP scores prompts such as `"a photo of an orange cat"` and `"a photo of a black cat"` for the colours in `config.json`, and the best colour (probability ≥ 0.4) goes into `attributes`. CLIP is already loaded for the fallback, so this costs no extra model. Breeds could be prompted the same way, with lower accuracy.

### 7.5 Swapping models without changing code

The model files are not in the container image. Each model version is a folder in the Cloud Storage bucket `aussie-ecoguard-models-<project-number>`:

```
gs://aussie-ecoguard-models-<project-number>/
  v1/config.json                  thresholds, tags, mapping rules, CLIP prompts, file names
  v1/speciesnet-v4.0.3a/...       SpeciesNet classifier + MegaDetector weights
  v1/clip-vit-b-32/...            CLIP weights
  v2/...                          a later version
```

```json
{
  "model_label": "md5a+speciesnet-4.0.3a+clip-b32-v2",
  "speciesnet_dir": "speciesnet-v4.0.3a",
  "clip_arch": "ViT-B-32-quickgelu",
  "clip_weights": "clip-vit-b-32/open_clip_model.safetensors",
  "detection_threshold": 0.2,
  "species_threshold": 0.3,
  "clip_override_threshold": 0.98,
  "species": ["fox", "rabbit", "feral pig", "cane toad", "cat", "dog", "..."],
  "species_rules": [["mammalia;carnivora;canidae;vulpes;", "fox"], "..."],
  "colours": ["orange", "black", "white", "grey", "brown"],
  "colour_for": ["cat", "dog"]
}
```

The Cloud Run environment variable `MODEL_GCS` names one version (e.g. `gs://…/v1`). When an instance starts, it downloads that folder in parallel chunks and loads the models once. `model_label` is stored with every tagged file, so it is clear which model tagged what.

To switch models, add a species or change a threshold: copy a version inside Cloud Storage, edit its `config.json` (or replace weights), and set `MODEL_GCS` to the new folder. Neither the Lambda functions nor the container image change. Changing the variable creates a new Cloud Run **revision**; traffic moves to it only once it has started, so a broken version never serves requests. Versions are never edited in place, so every revision keeps the model it started with, and setting `MODEL_GCS` back rolls the model back. The service does not re-read the config while it runs, so two instances never use different models.

Reading the weights through a Cloud Storage FUSE volume mount instead was tried first: loading took 174 s (217 s with buffered reads), against about 17 s to download the files and then load them from `/tmp`. Google's guidance for models on Cloud Run also ranks downloading at startup as the fastest option.

## 8. Key design decisions

| Decision | Reason |
|---|---|
| ML inference on GCP Cloud Run instead of Lambda | Three PyTorch models need about 3 GB of memory and a container of several GB. Cloud Run handles large images, scales to zero, offers more memory and CPU per instance and has an optional GPU. This is a real technical reason to use a second cloud, not multi-cloud for its own sake |
| Model versions as folders in Cloud Storage, chosen by an environment variable | Changing the model needs no code or image change, and every Cloud Run revision keeps the version it started with, so a rollback really restores the old model |
| Download the model at startup instead of mounting the bucket | Reading the weights through a Cloud Storage FUSE mount took about 3 minutes to load; downloading them first takes about 17 s (§7.5) |
| **AWS calls GCP, never the other way round** | AWS Academy credentials expire every few hours, so GCP must not hold AWS keys. AWS sends GCP a presigned URL and gets the result back in the HTTP response, and GCP never touches S3 or DynamoDB with AWS credentials |
| Presigned URL uploads straight to S3 | Gets around the 10 MB API Gateway / 6 MB Lambda payload limits, and the large file never passes through a Lambda |
| Checksum-signed presigned PUT | Deduplication cannot be bypassed by sending one hash and uploading different content |
| Reserve the checksum in `upload-url` (`PENDING`), not when tagging finishes | Closes the gap between issuing a URL and finishing inference, in which the same file could otherwise be uploaded and tagged twice. One conditional write is atomic |
| Object keys without an extension (`uploads/<sha256>`) | Content-addressed storage: the key depends only on the bytes, so renaming a file or changing its extension cannot store it twice. The type lives in `Content-Type` and `file_type` |
| EventBridge between S3 and the processing Lambdas | One S3 event can trigger several Lambdas, and the rules filter by object key prefix |
| SQS between EventBridge and `tagging` | Caps tagging at Cloud Run's capacity, keeps uploads for days while the function cannot run, retries without waiting inside Lambda, and keeps repeated failures in a dead-letter queue |
| DynamoDB instead of RDS | No SQL joins needed; serverless, no VPC or NAT, Streams for notifications |
| Single-table design (`Media`) with a sparse GSI | A file and its tags form one item collection, fetched with one query; each tag is stored once, so there is nothing to keep in sync; the sparse GSI on `tag` + `tag_count` answers "at least N of species X" with a key condition |
| SNS for notifications | Native filter policies match the "notify me about species X" requirement with no custom matching code |

## 9. Diagram maintenance

- The diagram is drawn with draw.io's official AWS and GCP icon libraries. Open `architecture.drawio` in [app.diagrams.net](https://app.diagrams.net) or the VS Code "Draw.io Integration" extension, then export a new `architecture.png` after editing.
- `architecture.drawio` is the single source of truth for the diagram; always edit it rather than the PNG.
