# Contributions and task board

This file records who built what and who owns the work that is left. Git history is the source of truth for authorship (`git shortlog -sn`, `git log --author=<name>`); this file links the two together.

Members (GitLab username): Yijiang Luo (`yluo0117`), Thanh Tri Le (`tlee0177`), Leon (`llii0177`).

Deadline: **2026-11-02 23:55** (code, team report, individual reports), then the Zoom demo and interview.

---

## TODO

Each area has one owner, who builds its backend **and** its part of the website. The spec section is the checklist: build what it asks for, nothing more.

| Area | Owner | Spec | What the spec asks for |
|---|---|---|---|
| Authentication | Yijiang Luo | §3 | Done (see Done below). Left: once every member has sent their Cognito user pool ID, switch the shared Cloud Run from trusting any pool back to that list |
| Query API | Leon | §4.3 | Search by tags with minimum counts (AND), by species, lost pet (species / colour, recent sightings), thumbnail URL → original URL, search by an uploaded file (the file is never stored), add / remove tags on many files (`"operation": 1` / `0`), delete files (original, thumbnail and database items). Already there: finding the species in an uploaded file without storing it is `detectFile(file)` in `web/detect.js` (Cloud Run `POST /detect`, README § Website), so the file-based searches and the lost-pet photo search only need to search with the tags it returns; `POST /tags` (`edit_tags`) is merged |
| Chatbot | Leon | §4.3 | Lex bot that understands questions like "show me all fox photos found yesterday" or "find a lost orange cat", calls the query code and returns links to the media |
| Email notifications | Thanh Tri Le | §4.4 | Done (see Done below). Left: after `terraform apply`, check that a real email arrives |
| Thumbnails | Thanh Tri Le | §4.2 | Done (see Done below). Left: review and merge, then `terraform apply` (needs `pip3`, README § Prerequisites) and the smoke test. Search results can show `thumb_url` the same way `GET /files` does |
| Website | Everyone | §5 | Each owner adds the UI their own backend features need (see below) |
| Report and demo | Everyone | §6 | Team report (max 1000 words: services and why, pros and cons, cost), demo script, screenshots. Individual reports are written alone |

### Website

Spec §5 only asks for a **simple** web UI that covers the core features (upload, queries, results, add / remove tags). Polish such as animations or extra layout work is not marked, so do it only once your area works end to end.

- **Whoever builds a backend feature also builds the web UI it needs**, in the same merge request or right after it. That can be a new tab (e.g. Notifications, Search, Chat) or a change to an existing page (e.g. thumbnails in the Library cards); do whatever the feature needs.
- A new tab goes in its own file under `web/` and adds its tab and panel itself, like `web/subscribe.js` does, so `index.html` only needs one `<script>` line.
- When you change a file someone else is working on, say so in the group chat first to avoid merge conflicts.
- Use Bootstrap 5 like the existing pages, and `showToast(message, type)` from `web/toast.js` for short messages.
- Until your API is deployed, build the UI against a hard-coded sample response in the format from [architecture/README.md § 5](architecture/README.md#5-core-features-spec-4-requirement-by-requirement).

### Rules that keep `main` working

- Work on a branch and open a merge request into `main`; say how you tested it. Yijiang reviews, merges and deploys.
- A new Lambda comes with unit tests (`tests/test_<name>.py`); run `pytest tests/` before the merge request (see [README.md § Unit tests](README.md#unit-tests)).
- Terraform (`infra/terraform/`) is shared: tell Yijiang what your Lambda needs (route, trigger, permissions), or keep your merge request to the new resources only.
- Never commit AWS or GCP credentials, `web/config.js` or account IDs.

Suggested dates (to be agreed in the group chat):

| Date | Milestone |
|---|---|
| 2026-10-19 | First merge request from every member |
| 2026-10-26 | All areas merged |
| 2026-10-30 | Feature freeze: only bug fixes, report and demo practice |
| 2026-11-02 | Submission |

---

## Done

| Area | What was built | By | Date | Commits |
|---|---|---|---|---|
| Repository | Created the GitLab group and the empty project, invited the other two members | Yijiang Luo | 2026-10-02 | none (GitLab → Manage → Members: yluo0117 is Owner, the others were added by yluo0117) |
| Repository | Added a one-line `README.md` (`# FIT3184 A2`) as the first commit | Thanh Tri Le | 2026-10-07 | `a9e5856` |
| Architecture | Multi-cloud architecture design, diagram with official icons (`architecture/`), service choices and data model | Yijiang Luo | 2026-10-07 | `2e2b383` `2e7acff` `c9fbb2e` `6cc2d6d` `2462c82` `8ea57cd` |
| AWS infrastructure | CloudFormation template and deploy script: media bucket, website bucket, DynamoDB `Media` table with GSI and TTL | Yijiang Luo | 2026-10-10 | `c24bf1c` `015369b` `0e7d5b0` `5fef2c5` |
| Upload + deduplication | `POST /upload-url` Lambda and API Gateway: SHA-256 deduplication, presigned PUT bound to the checksum, `PENDING` reservations with TTL | Yijiang Luo | 2026-10-10 – 11 | `9805ca8` `ac910b8` `c834c64` |
| GCP inference service | Cloud Run service (FastAPI, stub model), deploy script, moved to `us-east4` | Yijiang Luo | 2026-10-10 – 11 | `58ac326` `67269bd` `43e32f0` |
| Tagging pipeline | S3 → EventBridge → SQS (+ dead-letter queue) → `tagging` Lambda → Cloud Run → DynamoDB, with retries and `FAILED` status; fix for Learner Lab disabling the queue trigger | Yijiang Luo | 2026-10-11 | `d525170` `5c96048` `7bfdd84` `30afced` `2b798f2` |
| Website | Static site on S3: upload panel (browser SHA-256, progress bar) | Yijiang Luo | 2026-10-11 | `6d91aa1` |
| Files API + library | `GET /files`, `GET /files/{sha256}`; Bootstrap library tab with status polling; cards show "Uploading" / "Queued for tagging" with a preview once the file is in S3 | Yijiang Luo | 2026-10-11 – 12 | `26edb83` `46ea80b` `2a06ce1` `850a9f4` `7c3c47d` |
| Model selection | Hand-labelled test set of 27 images, comparison of 10 pipelines (MegaDetector, SpeciesNet, CLIP, YOLOv8, Rekognition), chosen chained pipeline; follow-up with 16 pet photos that led to the CLIP override (`experiments/model-selection/` § 8) | Yijiang Luo | 2026-10-11 – 13 | `f59e58d`, § 8 in the commit after `8e03fe8` |
| Real model (B4) | Cloud Run runs MegaDetector → SpeciesNet → CLIP (22 species + `other animal`, colours of cats and dogs). Model versions are folders in Cloud Storage, downloaded at startup, so the model changes with one environment variable (`infra/upload_model.sh`). 4 CPU / 8 GiB, timeouts measured on Cloud Run. Model v2 adds the CLIP override for close-up pet photos | Yijiang Luo | 2026-10-11 – 13 | `862ec24` `1926f68` `02e2a30` `62bd294` `6919ee4` `9db1252` `5a7716a` `ddf4d1b` `6762a86` `f4f3777` |
| Videos (B5, B6) | One frame every 10 s through the image pipeline, highest count per species, at most 3 minutes (longer → `FAILED`); H.264 / HEVC, portrait phone videos turned upright. Test videos with sources and expected results (`experiments/test-media/`) | Yijiang Luo | 2026-10-13 | `8e03fe8`, test media in the commit after it |
| Testing | Unit tests (pytest + moto, 149 tests), end-to-end `scripts/smoke_test.sh` (uploads a real photo, `tests/fixtures/`), `scripts/upload.sh` | Yijiang Luo | 2026-10-10 – 13 | in the commits above, `7783799` |
| Documentation | `README.md` (deploy, API, testing, troubleshooting, model limits), `architecture/README.md` (§ 7 rewritten for the deployed model) | Yijiang Luo | 2026-10-07 – 13 | in the commits above, `95ea384` |
| Repository | `.gitattributes` so `.sh` files keep LF line endings on Windows | Yijiang Luo | 2026-10-13 | `f6b54f7` |
| Terraform | AWS moved from CloudFormation to Terraform; GCP (Cloud Run, model bucket, image build) in Terraform; split into `infra/terraform/aws/` (everyone deploys their own) and `infra/terraform/gcp/` (one shared inference service) | Yijiang Luo | 2026-10-14 | `5ddfb41` `4a9a687` `eb20560` `44d8a8b` `5bbf350` |
| Web | D1 Toast messages: `showToast(message, type)` helper | Thanh Tri Le | 2026-10-14 | `d5c62fa` |
| Web | D3 Loading states: spinner, disabled Refresh, "No files yet" | Thanh Tri Le | 2026-10-14 | `dff1cbf` |
| Web | D11 Notifications tab: species subscribe, `POST /subscriptions` | Thanh Tri Le | 2026-10-14 | `77fb330` |
| Web | D15 Mobile layout fixes at 375 px | Thanh Tri Le | 2026-10-15 | `929af34` |
| Authentication | Cognito user pool with self sign-up (email, first name, last name, password, email verification) and managed login pages; website sign-in, token refresh and sign-out (`web/auth.js`, `apiFetch()`); every API route needs the user's ID token (API Gateway Cognito authorizer), with CORS preflights and CORS headers on API Gateway's own errors; `upload-url` records the uploader as `owner`; Cognito test user for the smoke test | Yijiang Luo | 2026-10-21 – 24 | `212852e` `a4124ea` `f6e5d81` `f8610fa` `b905b8b` `c4a70e9` `284f30c` `3c23d5f` `3127c9c` |
| AWS → GCP authorisation | The `tagging` Lambda signs in as a Cognito machine client (OAuth client credentials, scope `aussie-ecoguard/infer`) and sends the access token to Cloud Run, which checks its signature against the trusted user pools' public keys (JWKS), its expiry and its scope; architecture diagram and README updated (Secrets Manager replaced) | Yijiang Luo | 2026-10-24 | `282b552` `0d043c7` `691574b` `a1405e1` `4651f03` |
| Detect (search by file) | Cloud Run `POST /detect`: the website sends an image with the user's ID token and gets its species and colours back, nothing is stored (Spec §4.3); CORS for the browser; `detectFile(file)` in `web/detect.js` for the searches | Yijiang Luo | 2026-10-25 | `45bace0` `ade617e` |
| Edit tags | `edit_tags` Lambda and `POST /tags` route (Cognito-protected) for adding / removing tags on many files | Leon | 2026-10-25 | `2d90ee7` `3db4906` |
| Email notifications | `subscribe` Lambda and `POST /subscriptions`: the email comes from the Cognito token, one SNS email subscription per user with a filter policy on `species`; SNS topic `aussie-ecoguard-notifications`. `notify` Lambda on the table's DynamoDB Stream: one email per newly tagged file to everyone subscribed to one of its species, with the tags, file name, upload time and a link. 47 unit tests | Thanh Tri Le | 2026-10-25 | `783a2f2` `3a75f94` |
| Thumbnails | `thumbnail` Lambda on every upload (second target on the EventBridge rule): skips videos, scales images to 512 px on the longest side keeping the aspect ratio (EXIF orientation, transparent PNGs), JPEG quality 70 to `thumbnails/<sha256>.jpg`; Pillow as a Lambda layer built by `terraform apply`. `GET /files` returns a signed `thumb_url`; library cards show the thumbnail, open the original on click and fall back to it if the thumbnail is missing. Smoke test checks. 19 unit tests | Thanh Tri Le | 2026-10-25 | `4525741` |

---

## Log

Short entries, newest first: date, who, what (claims, merges, task changes).

| Date | Who | Entry |
|---|---|---|
| 2026-10-25 | Thanh Tri Le | Email notifications done: `subscribe` and `notify` Lambdas merged. Thumbnails done, merge request open. Both need `terraform apply` (thumbnails needs `pip3` on PATH for the Pillow layer). Both of my areas are now done |
| 2026-10-25 | Yijiang Luo | `POST /detect` and `web/detect.js` done: Leon's search by an uploaded file and lost pet by photo call `detectFile(file)`, then search with the returned tags. Leon's `POST /tags` merged and deployed |
| 2026-10-24 | Yijiang Luo | Authentication done: every API route and Cloud Run now need a Cognito token. **Everyone:** pull, run `terraform -chdir=infra/terraform/aws apply`, and send Yijiang the output of `terraform -chdir=infra/terraform/aws output -raw cognito_user_pool_id`, so the shared Cloud Run trusts your deployment (it accepts any pool until everyone has sent theirs). New API calls in `web/` must use `apiFetch()` (README § Website) |
| 2026-10-17 | Yijiang Luo | Task board replaced by one owner per area (TODO above): Yijiang authentication, Leon query API and chatbot, Thanh Tri Le notifications and thumbnails, report together. Whoever builds a backend feature also builds the web UI it needs |
| 2026-10-15 | Thanh Tri Le | D1, D3, D11, D15 merged; D2 unblocked. D11 needs F1 (subscribe Lambda) live to work end to end |
| 2026-10-14 | Yijiang Luo | Terraform split into `infra/terraform/aws/` (everyone deploys their own) and `infra/terraform/gcp/` (one shared inference service hosted by Yijiang), because not everyone has a GCP account. Teammates only need AWS: see README § Deploy |
| 2026-10-14 | Yijiang Luo | GCP (Cloud Run, model bucket, image build) also in Terraform; `deploy_gcp.sh` removed. Everyone deploys their own AWS + GCP: see README § Deploy → First time, and send Yijiang your Google account for `model_readers` |
| 2026-10-14 | Yijiang Luo | AWS deployment moved from CloudFormation to Terraform (`infra/terraform/`, see README § Deploy); `template.yaml` and `deploy_aws.sh` removed |
| 2026-10-13 | Yijiang Luo | B4 (real model), B5 (videos) and B6 (test videos) done; C6 unblocked |
| 2026-10-12 | Thanh Tri Le | Opened merge request for D1 |
| 2026-10-11 | Yijiang Luo | Created this file with the work done so far and the open tasks |
