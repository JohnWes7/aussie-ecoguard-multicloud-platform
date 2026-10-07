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
| AWS | AWS Lambda (API functions) | `upload-url`, `search`, `edit-tags`, `delete-files`, `chat`, `subscribe` |
| AWS | Amazon S3 (media bucket) | Original files in `uploads/`, thumbnails in `thumbnails/` |
| AWS | Amazon EventBridge | Routes each S3 "Object Created" event to the thumbnail and tagging Lambdas (see §4) |
| AWS | AWS Lambda (event functions) | `thumbnail`, `tagging`, `notify` |
| AWS | Amazon DynamoDB | `Files` table and `TagIndex` table; DynamoDB Streams drive notifications |
| AWS | Amazon Lex V2 | Chatbot that turns natural-language queries into intents and slots |
| AWS | Amazon SNS | Sends tag-based email notifications, filtered per subscriber |
| AWS | AWS Secrets Manager | Holds the GCP service-account key that AWS uses to call Cloud Run |
| GCP | Cloud Run | Containerised YOLO / MegaDetector inference API |
| GCP | Cloud Storage | Model weights and a `config.json` that names the active model version |

AWS runs in `us-east-1`, the region AWS Academy Learner Lab allows. GCP runs in `australia-southeast1`.

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
   │  ③ POST /upload-url {sha256, filename, type} + JWT      │                       │                     │
   │ ─────────────────────────────►│                          │                       │                     │
   │                               │ look up sha256 in DynamoDB                       │                     │
   │   409 duplicate  ◄────────────┤ (already stored)         │                       │                     │
   │   presigned PUT URL ◄─────────┤ (new file)               │                       │                     │
   │  ④ PUT file (direct to S3) ─────────────────────────────►│                       │                     │
   │                                                          │ Object Created ──────►│ rule 1 ──► thumbnail ⑤
   │                                                          │                       │ rule 2 ──► tagging   ⑥
```

1. **Checksum in the browser.** The browser computes the file's **SHA-256** and calls `POST /upload-url` with the checksum, file name and content type (③).
2. **Deduplication.** The `upload-url` Lambda looks the checksum up in the `Files` table. If it is already there, the API returns `409 Duplicate` with the existing file's URL and nothing is uploaded.
3. **Presigned URL.** For a new file, the Lambda returns a short-lived presigned **PUT** URL for `uploads/<sha256>.<ext>`. The URL is signed with the checksum (`ChecksumSHA256`), so S3 rejects any upload whose content does not match the checksum. As a backstop, `tagging` re-hashes the object and deletes it if the hash does not match its key.
4. **Direct upload (④).** The browser PUTs the file straight to S3.
5. **Event routing.** S3 sends an "Object Created" event to EventBridge. Two EventBridge rules, both filtered to the `uploads/` prefix, invoke the two processing Lambdas in parallel:
   - **⑤ `thumbnail`** downloads the image, resizes it while keeping the aspect ratio, compresses it with OpenCV, and writes the result to `thumbnails/`. Because the rules only match `uploads/`, writing a thumbnail does not trigger the pipeline again.
   - **⑥ `tagging`** creates a presigned **GET** URL for the object and sends it to Cloud Run (⑦). The call is authenticated with a GCP ID token minted from the service-account key held in Secrets Manager.
6. **⑦ ⑧ Inference on GCP.** Cloud Run reads `config.json` from Cloud Storage to find the active model version, loads those weights (cached between requests), runs detection and returns per-species counts such as `{"fox": 2, "cat": 1}`. **Videos:** one frame is extracted every 10 seconds and each frame is run through the detector. The stored count for each species is the highest count seen in any single frame, so the same animal is not counted twice across frames.
7. **⑨ Persist.** `tagging` writes the file type, the tag counts, the file URL and the thumbnail URL to DynamoDB.
8. **⑩ Notify.** The new item appears on the DynamoDB Stream. The `notify` Lambda publishes to SNS with the species as message attributes, and each subscriber's **filter policy** decides who receives an email.

## 4. Why EventBridge sits between S3 and the Lambdas

S3 event notifications do **not** allow two configurations with the same event type and an overlapping prefix or suffix. As a result, `uploads/` cannot trigger both `thumbnail` and `tagging` directly. EventBridge solves this without any extra code:

- With "Send notifications to Amazon EventBridge" turned on, the bucket publishes every event to the account's default **event bus**.
- Each **rule** has an event pattern, for example `source = aws.s3`, `detail-type = Object Created`, `bucket = <media bucket>` and `key` prefix `uploads/`, plus a **target** (a Lambda).
- Two rules match the same event, so both Lambdas run in parallel. Adding another consumer later is just another rule, with no change to the bucket or to existing code.
- EventBridge also provides retries and optional dead-letter queues for failed invocations.

An SNS topic fan-out (S3 → SNS → two Lambdas) would also work. EventBridge was chosen because its rules can filter on the object key and it needs no extra topic.

## 5. Core features (Spec §4), requirement by requirement

This section maps each requirement in Spec §4 to the flow that implements it. Every REST call goes through API Gateway (③) with the Cognito JWT, except the call to Cloud Run in ⑪, which carries the same JWT straight to GCP (§2).

**Keys and URLs.** Every object key is derived from the file's SHA-256: `uploads/<sha256>.<ext>` and `thumbnails/<sha256>.jpg`. Any file URL or thumbnail URL can therefore be turned back into its `file_id` by parsing the path, with no extra lookup. The media bucket is private, so `search` returns short-lived **presigned GET URLs**.

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
| **Every upload triggers an event and a serverless function** | S3 **Object Created** → EventBridge → `thumbnail` and `tagging` Lambdas (§4) | ⑤ ⑥ |
| Deduplication by checksum | The browser sends the SHA-256 first; `upload-url` rejects a checksum already in `Files` with `409`. The presigned PUT is bound to that checksum so the content cannot be swapped, and the conditional write `attribute_not_exists(file_id)` stops two uploads racing | ③ |
| Thumbnail generation | The EventBridge rule for `thumbnail` matches image suffixes only (`.jpg`, `.jpeg`, `.png`). The Lambda uses OpenCV to scale the longest side to 256 px, keeping the aspect ratio, saves a JPEG at quality 70 to `thumbnails/<sha256>.jpg`, and has `s3:GetObject` on `uploads/*` and `s3:PutObject` on `thumbnails/*` | ⑤ |
| Tag the file and save type, tags, file URL and thumbnail URL | `tagging` sends a presigned GET URL to Cloud Run and writes the result to `Files` and `TagIndex`. Because thumbnail keys are deterministic, `tagging` can store `thumb_url` without waiting for the `thumbnail` Lambda | ⑥ ⑦ ⑧ ⑨ |
| Videos: **one frame every 10 seconds** | For a video, Cloud Run opens the presigned URL with OpenCV, seeks in 10-second steps (`CAP_PROP_POS_MSEC`), runs detection on each sampled frame, and keeps the highest count per species across frames | ⑦ |

### 5.3 Queries (Spec §4.3)

#### Find files by tags with minimum counts

- **Request:** `POST /search/tags` with `{"feral pig": 1, "cane toad": 3}`
- **Flow:** `search` runs one `TagIndex` query per tag, for example `tag = "cane toad" AND sk >= "0003#"`. It intersects the resulting `file_id` sets (logical **AND**), then `BatchGetItem`s the matching `Files` items.
- **Response:** thumbnail URLs for images and file URLs for videos.

#### Find files by species, and find a lost pet

- **By species:** `POST /search/species` with `{"species": ["cat"]}`. This is the spec's `{"cat"}` written as valid JSON. It is the query above with a minimum count of 1.
- **Lost pet by description:** `POST /lost-pet` with `{"species": "cat", "attributes": ["orange"], "days": 14}`. `search` queries `TagIndex` for the species, keeps files where every requested colour or breed term appears in `attributes` or as a manual tag in `tags`, and keeps only those whose `created_at` falls within the last `days` days (recent sightings), newest first.
- **Lost pet by photo:** first ⑪. The browser sends the pet photo to Cloud Run `/detect`, which returns its species and attributes without storing the image. Then it calls `POST /lost-pet` with them.

#### Find the original file from a thumbnail URL

- **Request:** `GET /search/original?thumb_url=<url>`
- **Flow:** `search` parses `<sha256>` from the `thumbnails/<sha256>.jpg` path and does a `GetItem` on `Files` by primary key, then returns the original file's URL.

#### Find files using the tags of an uploaded file (⑪)

1. The browser sends the file and the Cognito JWT to Cloud Run `POST /detect`.
2. Cloud Run verifies the JWT, runs detection **in memory** and returns the detected tag set, e.g. `{"fox": 2, "cat": 1}`. The file is never written to S3, Cloud Storage or DynamoDB, which meets the privacy requirement.
3. The browser calls `POST /search/tags` with each detected tag at a minimum count of 1, and gets back every file that contains the whole tag set.

#### Add or remove tags manually, in bulk

- **Request:** `POST /tags` with `{"url": ["<url1>", "<url2>"], "operation": 1, "tags": {"fox": 1, "cat": 2}}`
- **`operation: 1` (add):** sets each tag's count on every listed file.
- **`operation: 0` (remove):** deletes those tags; counts are ignored, and a tag the file does not have is silently skipped.
- **Flow:** `edit-tags` parses each `file_id` from its URL and updates the `tags` map in `Files` and the matching `TagIndex` items in a single `TransactWriteItems`.
- **Notifications:** the change appears on the DynamoDB Stream, so subscribers of a newly added tag are notified (5.4).

#### Delete files

- **Request:** `POST /delete` with `{"url": ["<url1>", "<url2>"]}`
- **Flow:** `delete-files` parses each `file_id`, deletes `uploads/<sha256>.<ext>` and `thumbnails/<sha256>.jpg` from S3, then deletes the `Files` item and all of its `TagIndex` items.

#### Chatbot with natural-language queries

1. The browser sends `POST /chat` with `{"text": "show me all fox photos found yesterday", "sessionId": "…"}`.
2. The `chat` Lambda passes the text to Lex V2 `RecognizeText`.
3. Lex resolves the **intent** and **slots**:
   - `FindSightings`: `species = fox`, `date = yesterday`, which the built-in `AMAZON.Date` slot type resolves to an actual date
   - `FindLostPet`: `species = cat`, `colour = orange`
4. Lex calls `search` as its fulfillment code hook. `search` runs the matching query above and filters by `created_at` when a date is given.
5. The links are returned through Lex and `chat` to the chat window.

### 5.4 Tag-based notifications (Spec §4.4)

1. **Subscribe:** `POST /subscriptions` with `{"tags": ["fox", "cane toad"]}`. A small `subscribe` Lambda reads the user's email from the JWT claims and calls SNS `Subscribe` with an email endpoint and a filter policy such as `{"species": ["fox", "cane toad"]}`. SNS sends a one-time confirmation email. (`subscribe` still needs to be added to the diagram's API Lambda group.)
2. **Trigger:** every `INSERT` (new file) and `MODIFY` (tags edited) on `Files` reaches the `notify` Lambda through DynamoDB Streams (⑩). `notify` works out which species are new by comparing the old and new `tags` maps.
3. **Deliver:** `notify` publishes one message per file with the message attribute `species` set to those new tags, plus a link to the file's page in the web app, `https://<site>/file/<sha256>`. A presigned URL is not used because it expires along with the Lambda's temporary credentials. SNS emails only the subscribers whose filter policy matches.

## 6. Data model (DynamoDB)

The data has no relationships that need joins: every query is "files with these tags". DynamoDB is serverless, needs no VPC (an RDS database would put the Lambdas in a VPC and require a NAT gateway to reach GCP), bills per request, and its Streams feed the notification flow directly.

**`Files` table**

| Attribute | Notes |
|---|---|
| `file_id` (PK) | SHA-256 of the file content. It doubles as the deduplication key; writes use `attribute_not_exists(file_id)` |
| `file_url`, `thumb_url` | S3 locations (`thumb_url` is empty for videos) |
| `file_type` | `image` or `video` |
| `tags` | Map of species to count, e.g. `{"fox": 2, "cane toad": 3}` |
| `attributes` | Colour / breed terms from the second-stage classifier, e.g. `["orange"]`; used by the lost-pet search |
| `owner`, `created_at` | Cognito `sub`; ISO timestamp, used for "recent sightings" |

No secondary index is needed for "thumbnail URL to original": the thumbnail key contains the `file_id`, so the lookup is a plain `GetItem`.

**`TagIndex` table** (one item per tag per file)

| Attribute | Notes |
|---|---|
| `tag` (PK) | e.g. `fox` |
| `sk` (SK) | `<zero-padded count>#<file_id>`, e.g. `0002#ab12…`. Zero padding makes text order match number order, so `count >= 2` becomes the key condition `sk >= "0002#"` |

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
| EventBridge between S3 and the processing Lambdas | One S3 event can trigger several Lambdas, and the rules filter by object key prefix |
| DynamoDB instead of RDS | No joins needed; serverless, no VPC or NAT, Streams for notifications |
| SNS for notifications | Native filter policies match the "notify me about species X" requirement with no custom matching code |

## 9. Diagram maintenance

- The diagram is drawn with draw.io's official AWS and GCP icon libraries. Open `architecture.drawio` in [app.diagrams.net](https://app.diagrams.net) or the VS Code "Draw.io Integration" extension, then export a new `architecture.png` after editing.
- `architecture_gen.py` generated the first version. The `.drawio` file has been edited by hand since then and is now the source of truth.
