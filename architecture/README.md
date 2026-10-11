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
| GCP | Cloud Run | Containerised YOLO / MegaDetector inference API |
| GCP | Cloud Storage | Model weights and a `config.json` that names the active model version |

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
6. **⑦ ⑧ Inference on GCP.** Cloud Run reads `config.json` from Cloud Storage to find the active model version, loads those weights (cached between requests), runs detection and returns per-species counts such as `{"fox": 2, "cat": 1}`. **Videos:** one frame is extracted every 10 seconds and each frame is run through the detector. The stored count for each species is the highest count seen in any single frame, so the same animal is not counted twice across frames.
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
- **Settings follow the AWS guidance for SQS event sources:** a visibility timeout of six times the function timeout (450 s for 75 s) and a `maxReceiveCount` of 5. `tagging` itself gives up and writes `FAILED` on the 3rd attempt (§3.1), so the 4th and 5th deliveries are only a margin for crashes.

## 5. Core features (Spec §4), requirement by requirement

This section maps each requirement in Spec §4 to the flow that implements it. Every REST call goes through API Gateway (③) with the Cognito JWT, except the call to Cloud Run in ⑪, which carries the same JWT straight to GCP (§2).

**Keys and URLs.** Every object key is derived from the file's SHA-256: `uploads/<sha256>` and `thumbnails/<sha256>.jpg`. The original's key has no extension, which is the usual design for content-addressed storage: the key depends only on the content, so the same bytes uploaded as `fox.jpg` and `fox.png` become one object. The type is kept as the object's `Content-Type` (used by browsers) and as `file_type` in the `META` item (used by `tagging` and the queries). Image and video decoders recognise the format from the file's first bytes, so the model does not need an extension either. Any file URL or thumbnail URL can therefore be turned back into its `file_id` by parsing the path, with no extra lookup. The media bucket is private, so `search` returns short-lived **presigned GET URLs**.

### 5.1 Model handling (Spec §4.1)

| Requirement | How it is met | Diagram |
|---|---|---|
| Entry model must be MegaDetector, Ultralytics YOLO or a managed service | Two candidate pipelines, both starting with an allowed entry model, are compared in §7: **MegaDetector + CLIP** (recommended) and **Ultralytics YOLO-World**. Plain COCO-trained YOLO is ruled out because COCO has `cat`, `dog` and `bird` but no fox, rabbit, feral pig or cane toad | ⑦ |
| Use a new model version **without changing source code or Lambdas** | Weights and settings live in Cloud Storage. To change model, species list or threshold, upload new weights and edit `config.json`; Cloud Run reloads it on cold start and on a refresh interval (§7.5) | ⑧ |
| Optional model chaining | In the recommended pipeline, MegaDetector finds and counts the animals and each crop is passed to a **CLIP zero-shot classifier** (Hugging Face) that names the species. `cat` / `dog` crops go through CLIP a second time to get colour attributes | ⑦ |
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

## 7. Model options

The inference service has to do two things:

1. **Detect and count animals, and name their species.** This is required, e.g. `{"fox": 2}`; tag queries and notifications depend on it.
2. **Describe pets with attributes such as colour.** The spec only requires that users *can search* by breed or colour. Colour tags can come from manual tagging (§5.3), so automatic colour recognition is an enhancement that makes the lost-pet and chatbot demos work without manual effort.

Two pipelines were considered. The snippets below are untested sketches; check exact signatures against the installed package versions.

### 7.1 Option A: Ultralytics YOLO-World (simplest)

YOLO-World is an open-vocabulary detector: it is given the class names as text, so a single model detects, names and counts the species.

```python
from collections import Counter
from ultralytics import YOLOWorld

model = YOLOWorld("yolov8s-worldv2.pt")
model.set_classes(["fox", "rabbit", "feral pig", "cane toad", "cat", "dog"])  # from config.json
r = model.predict("img.jpg", conf=0.3)[0]
counts = Counter(r.names[int(c)] for c in r.boxes.cls)   # {"fox": 2, "cat": 1}
```

- **Pros:** one model and about a dozen lines; the species list is plain text.
- **Cons:** it is a general-purpose model, so accuracy may drop on camera-trap images (night IR, blur, occlusion) and on uncommon species such as cane toads. `set_classes` downloads a CLIP text encoder at run time. To avoid that on cold start, set the classes at image build time and save the result as a new `.pt`.

### 7.2 Option B: MegaDetector + CLIP (recommended)

MegaDetector is trained specifically on camera-trap imagery, but it has only three classes: `animal`, `person` and `vehicle`. It finds and counts animals reliably; a CLIP zero-shot classifier then names each one.

```python
from collections import Counter
from megadetector.detection.run_detector import load_detector
from megadetector.visualization.visualization_utils import load_image
from transformers import pipeline

detector = load_detector("MDV5A")
clf = pipeline("zero-shot-image-classification", model="openai/clip-vit-base-patch32")
SPECIES = ["fox", "rabbit", "feral pig", "cane toad", "cat", "dog"]  # from config.json

img = load_image("img.jpg")
W, H = img.size
counts = Counter()
for d in detector.generate_detections_one_image(img, detection_threshold=0.2)["detections"]:
    if d["category"] != "1":          # 1 = animal, 2 = person, 3 = vehicle
        continue
    x, y, w, h = d["bbox"]            # normalised
    crop = img.crop((x * W, y * H, (x + w) * W, (y + h) * H))
    best = clf(crop, candidate_labels=[f"a photo of a {s}" for s in SPECIES])[0]
    counts[best["label"].removeprefix("a photo of a ")] += 1
```

- **Pros:** accurate detection and counting on this domain; the spec lists it first; the two-stage design is the model chaining that Spec §4.1 describes.
- **Cons:** two models (about 300 MB + 600 MB), more code, and a Cloud Run instance with 2–4 GiB of memory.

### 7.3 Colour attributes

Both options produce a bounding box for each animal, so colour is recognised in the same way:

- **CLIP zero-shot (chosen):** for each `cat` / `dog` crop, score labels such as `"a photo of an orange cat"`, `"a photo of a black cat"`, and store the best colour in `attributes`. Option B already has CLIP loaded, so this costs no extra model. Breeds can be prompted the same way, with lower accuracy.
- **OpenCV dominant colour (fallback):** convert the centre of the crop to HSV and pick the most common hue range. It needs no model but is sensitive to background and lighting.

### 7.4 Comparison and recommendation

| Pipeline | Species | Colour | Models | Effort | Expected accuracy |
|---|---|---|---|---|---|
| YOLO-World + OpenCV colour | YOLO-World | OpenCV | 1 | Lowest | Fair |
| YOLO-World + CLIP colour | YOLO-World | CLIP | 2 | Low | Medium |
| **MegaDetector + CLIP** | MegaDetector boxes, CLIP species | CLIP | 2 | Medium | High |

YOLO-World is the simplest way to get species. Once colour is recognised automatically, though, both options need CLIP, and the extra effort for MegaDetector is small. **Recommendation: Option B, MegaDetector + CLIP.** One CLIP model handles both species and colour, MegaDetector suits the camera-trap scenario, and it is the spec's first listed option. Before committing, run both pipelines on a small labelled sample (foxes, rabbits, cats, a cane toad, a few night camera-trap shots) and compare the accuracy.

### 7.5 Swapping models without changing code

Weights live in Cloud Storage and `config.json` names everything the service loads:

```json
{
  "detector": "MDV5A",
  "detection_threshold": 0.2,
  "classifier": "openai/clip-vit-base-patch32",
  "species": ["fox", "rabbit", "feral pig", "cane toad", "cat", "dog"],
  "colours": ["orange", "black", "white", "grey", "brown"],
  "colour_for": ["cat", "dog"]
}
```

Switching models, adding a species or changing a threshold means uploading weights if needed and editing `config.json`. Neither the Lambda functions nor the Cloud Run container need to change. Cloud Run checks the config on each cold start and at a fixed refresh interval.

## 8. Key design decisions

| Decision | Reason |
|---|---|
| ML inference on GCP Cloud Run instead of Lambda | MegaDetector plus CLIP on PyTorch makes a container of several GB. Cloud Run handles large images, scales to zero, offers more memory per instance and has an optional GPU. This is a real technical reason to use a second cloud, not multi-cloud for its own sake |
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
