# Contributions and task board

This file records who built what, and lists the work that is still open so that everyone can pick tasks. Git history is the source of truth for authorship (`git shortlog -sn`, `git log --author=<name>`); this file links the two together.

Members (GitLab username): Yijiang Luo (`yluo0117`), `tlee0177`, `llii0177`.

Deadline: **2026-11-02 23:55** (code, team report, individual reports), then the Zoom demo and interview.

## How to pick a task

1. Choose one or more **Open** tasks below. Tasks marked ★ are a good start: they only touch their own files and cannot break the deployed system.
2. Claim it: create a branch, put your name and the date in the **Owner** column, and commit that change first. That commit is the claim.
3. Work on a branch named after the task, e.g. `d4-search-tags-page`. Do not push to `main`.
4. Open a merge request into `main` when it works. In the description, say how you tested it (command output or a screenshot). Yijiang reviews it, merges it, and deploys it.
5. One task per merge request, so each can be reviewed and merged on its own.

Rules that keep `main` working:

- Run `pytest tests/` before opening a merge request if you changed anything under `lambdas/`. A new Lambda needs its own `tests/test_<name>.py` (see [README.md § Unit tests](README.md#unit-tests)).
- Only change the files listed for your task. `infra/template.yaml`, `infra/deploy_*.sh` and the existing Lambdas are wired together; changes there go through Yijiang, so ask in the group chat if your task needs one.
- APIs that do not exist yet: build against the request/response format in [architecture/README.md § 5](architecture/README.md#5-core-features-spec-4-requirement-by-requirement) and use a hard-coded sample response until the real endpoint is deployed.
- Never commit AWS or GCP credentials, `web/config.js` or account IDs.

Suggested dates (to be agreed in the group chat):

| Date | Milestone |
|---|---|
| 2026-10-15 | Every member has claimed at least one task |
| 2026-10-19 | First merge request from every member |
| 2026-10-26 | All claimed tasks merged |
| 2026-10-30 | Feature freeze: only bug fixes, report and demo practice |
| 2026-11-02 | Submission |

A task with no merge request one week after it was claimed goes back to **Open**.

Levels: ★ easy (HTML/JS or writing, no AWS needed), ★★ medium (one Lambda with unit tests, or a page that calls an API), ★★★ hard (infrastructure, cross-cloud, ML).

Status: **Done** · **In progress** · **Reserved** (core or integration work, Yijiang) · **Open** · **Blocked** (waiting for another task).

---

## Done

| Area | What was built | By | Date | Commits |
|---|---|---|---|---|
| Repository | Created the GitLab group and the empty project, invited the other two members | Yijiang Luo | 2026-10-02 | none (GitLab → Manage → Members: yluo0117 is Owner, the others were added by yluo0117) |
| Repository | Added a one-line `README.md` (`# FIT3184 A2`) as the first commit | tlee0177 | 2026-10-07 | `a9e5856` |
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

---

## Open and planned work

Each table covers one area of the spec. **Done when** is what the merge request must show.

### A. Authentication (Spec §3)

| ID | Task | Files | Level | Owner | Status | Done when |
|---|---|---|---|---|---|---|
| A1 | Cognito User Pool + Hosted UI (email, first name, last name, verification email) | `infra/template.yaml` | ★★★ | Yijiang Luo | Reserved | Sign-up sends a verification email; sign-in returns a token |
| A2 | Cognito authorizer on every API route, `OPTIONS` routes for CORS | `infra/template.yaml` | ★★★ | Yijiang Luo | Reserved | Requests without a token get 401 |
| A3 | `web/auth.js`: read the token after Hosted UI sign-in, `getToken()`, `signOut()`, redirect to sign-in when there is no valid token | `web/auth.js` (new) | ★★ | — | Open | Works in a local page with a token pasted in by hand; expired token redirects |
| A4 | Navbar: show the signed-in user's email and a **Sign out** button | `web/index.html`, `web/auth.js` | ★ | — | Blocked (A3) | Screenshot signed in and after sign-out |
| A5 | Send the token with every API call from the website | `web/upload.js`, `web/library.js` | ★★ | Yijiang Luo | Reserved | Smoke test passes with auth on |
| A6 | AWS → Cloud Run authentication (Google ID token from Secrets Manager) | `lambdas/tagging/`, `inference/`, template | ★★★ | Yijiang Luo | Reserved | Cloud Run rejects calls without a valid token |

### B. File handling (Spec §4.1, §4.2)

| ID | Task | Files | Level | Owner | Status | Done when |
|---|---|---|---|---|---|---|
| B1 | `thumbnail` Lambda: skip non-images by `Content-Type`, scale the longest side to 256 px keeping the aspect ratio, save JPEG quality 70 to `thumbnails/<sha256>.jpg` (architecture § 5.2) | `lambdas/thumbnail/app.py`, `tests/test_thumbnail.py` (new) | ★★ | — | Open | `pytest tests/test_thumbnail.py` passes: image → thumbnail with the right size; video → skipped |
| B2 | Deploy the thumbnail Lambda: image library packaging, EventBridge rule, template | `infra/` | ★★★ | Yijiang Luo | Blocked (B1) | Uploading an image creates its thumbnail |
| B3 | Library cards show the thumbnail instead of the original, falling back to the original if it is missing | `web/library.js` | ★ | — | Blocked (B2) | Screenshot; network tab shows the small file |
| B4 | Real model in Cloud Run: MegaDetector → SpeciesNet → CLIP, config in Cloud Storage so the model can change without code changes | `inference/` | ★★★ | Yijiang Luo | Done | Test images get correct species and counts |
| B5 | Videos: one frame every 10 seconds | `inference/` | ★★★ | Yijiang Luo | Done | A test video gets the highest count per species |
| B6 | Find test videos with animals (at least 30 s, free licence) and record their source and licence in a CSV | `experiments/test-media/` (new) | ★ | Yijiang Luo | Done | 3+ videos listed with source, licence and the animals in them |

### C. Query and edit APIs (Spec §4.3)

| ID | Task | Files | Level | Owner | Status | Done when |
|---|---|---|---|---|---|---|
| C1 | `search` Lambda: `POST /search/tags` (tags with minimum counts, AND), `POST /search/species` | `lambdas/search/` | ★★★ | Yijiang Luo | Reserved | Unit tests + smoke test |
| C2 | `POST /lost-pet` (species + colour + recent days) | `lambdas/search/` | ★★ | Yijiang Luo | Reserved | Unit tests |
| C3 | `GET /search/original?thumb_url=` (thumbnail URL → original URL) | `lambdas/search/` | ★★ | — | Open | Unit tests: valid URL, bad URL → 400, unknown file → 404 |
| C4 | `edit-tags` Lambda: `POST /tags` add (`operation: 1`) or remove (`operation: 0`) tags on many files (architecture § 5.3) | `lambdas/edit_tags/app.py`, `tests/test_edit_tags.py` (new) | ★★ | — | Open | Unit tests: add, remove, remove a missing tag is ignored, unknown file rejected |
| C5 | `delete-files` Lambda: `POST /delete` removes the original, the thumbnail and every DynamoDB item of each file | `lambdas/delete_files/app.py`, `tests/test_delete_files.py` (new) | ★★ | — | Open | Unit tests: S3 objects and all items gone; unknown file does not crash |
| C6 | Cloud Run `POST /detect`: run the model on an uploaded file in memory, never store it (search by file, lost pet by photo) | `inference/` | ★★★ | Yijiang Luo | Reserved | File is not in S3, GCS or DynamoDB afterwards |
| C7 | Add C3–C5 to the template and the smoke test | `infra/`, `scripts/` | ★★ | Yijiang Luo | Blocked (C3–C5) | Smoke test passes |

### D. Website (Spec §5)

All pages use Bootstrap 5 like the existing ones and add a tab to `web/index.html`. Use sample data until the API exists.

| ID | Task | Files | Level | Owner | Status | Done when |
|---|---|---|---|---|---|---|
| D1 | Toast messages: one `showToast(message, type)` helper (Bootstrap toasts) for success / error / info | `web/toast.js` (new), `web/index.html` | ★ | Thanh Tri Le | In Progress| Demo button or console call shows all three types |
| D2 | Use toasts for upload results and API errors instead of plain text | `web/upload.js`, `web/library.js` | ★ | — | Blocked (D1) | Screenshots of success and error |
| D3 | Loading states: spinner while the library loads, buttons disabled while a request runs, "No files yet" empty state | `web/library.js`, `web/style.css` | ★ | — | Open | Screenshots of loading and empty states |
| D4 | **Search by tags** tab: rows of species + minimum count (add / remove rows), builds `{"fox": 2, "cat": 1}`, shows results as a card grid | `web/search.js` (new) | ★★ | — | Open | Correct JSON sent (console log); results shown from sample data |
| D5 | **Lost pet** form: species, colour, last N days; results newest first | `web/lostpet.js` (new) | ★★ | — | Open | Same as D4 |
| D6 | **Thumbnail → original** form: paste a thumbnail URL, show the original | `web/search.js` | ★ | — | Open | Works against sample data |
| D7 | **Search by file**: pick a file, call `/detect`, then search with the detected tags | `web/search.js` | ★★ | — | Blocked (C6) | Screenshot of results |
| D8 | Library: checkbox on each card, **Select all**, count of selected files | `web/library.js` | ★★ | — | Open | Selection survives the 3-second polling redraw |
| D9 | **Edit tags** dialog for the selected files (add / remove), calls `POST /tags` | `web/library.js` or `web/edit-tags.js` (new) | ★★ | — | Blocked (D8) | Correct JSON sent; cards show the new tags |
| D10 | **Delete** selected files with a confirmation dialog, calls `POST /delete` | `web/library.js` | ★ | — | Blocked (D8) | Confirm → cards disappear; cancel → nothing happens |
| D11 | **Notifications** tab: choose species to subscribe to, calls `POST /subscriptions` | `web/subscribe.js` (new) | ★ | — | Open | Correct JSON sent; message about the confirmation email |
| D12 | **Chat** tab: message list, input box, shows the links / thumbnails the bot returns | `web/chat.js` (new) | ★★ | — | Open | Works against sample replies |
| D13 | File page `#/file/<sha256>` for links in notification emails (uses `GET /files/{sha256}`) | `web/file.js` (new) | ★★ | — | Open | Opening the link shows that file's preview and tags |
| D14 | Stop previews downloading again on every redraw: move the old `<img>` / `<video>` into the new card when the file is the same | `web/library.js` | ★ | — | Open | Network tab shows one download per file |
| D15 | Check every page on a phone-sized screen and fix layout problems | `web/style.css` | ★ | — | Open | Screenshots at 375 px wide |

### E. Chatbot (Spec §4.3)

| ID | Task | Files | Level | Owner | Status | Done when |
|---|---|---|---|---|---|---|
| E1 | Design the intents (`FindSightings`, `FindLostPet`), slots and at least 10 example sentences each | `chatbot/README.md` (new) | ★ | — | Open | Document reviewed in the group |
| E2 | Build the Lex V2 bot in the console from E1 and export its definition into the repository | `chatbot/` | ★★ | — | Blocked (E1) | Console test window resolves the example sentences; export committed |
| E3 | `chat` Lambda: `POST /chat` → Lex `RecognizeText`, returns the bot's reply | `lambdas/chat/`, `tests/test_chat.py` (new) | ★★ | — | Open | Unit tests with a mocked Lex client |
| E4 | Lex fulfillment: call the search code and return links | `lambdas/search/` | ★★★ | Yijiang Luo | Blocked (C1, E2) | "show me all fox photos found yesterday" returns links |

### F. Notifications (Spec §4.4)

| ID | Task | Files | Level | Owner | Status | Done when |
|---|---|---|---|---|---|---|
| F1 | `subscribe` Lambda: email from the token, SNS `Subscribe` with a filter policy on `species` | `lambdas/subscribe/`, `tests/test_subscribe.py` (new) | ★★ | — | Open | Unit tests with moto SNS |
| F2 | `notify` Lambda: read DynamoDB Stream records, group by file, publish one SNS message per file with the `species` attribute and a link to the file page | `lambdas/notify/`, `tests/test_notify.py` (new) | ★★ | — | Open | Unit tests: two new tags on one file → one message |
| F3 | Email wording: subject and body text | `lambdas/notify/` | ★ | — | Open | Example email in the merge request |
| F4 | SNS topic, DynamoDB Stream and event filter in the template | `infra/` | ★★★ | Yijiang Luo | Blocked (F1, F2) | Adding a tag sends an email to a subscriber |

### G. Quality

| ID | Task | Files | Level | Owner | Status | Done when |
|---|---|---|---|---|---|---|
| G1 | `.gitattributes` so `.sh` files keep LF line endings on Windows | `.gitattributes` (new) | ★ | — | Open | Fresh clone on Windows runs `./scripts/smoke_test.sh` without `$'\r'` errors |
| G2 | GitLab CI: run `pytest tests/` on every merge request | `.gitlab-ci.yml` (new) | ★★ | Yijiang Luo | Reserved | Pipeline runs on a merge request |
| G3 | Demo test plan: for every spec feature, the steps to show it and the expected result | `docs/test-plan.md` (new) | ★ | — | Open | Covers every bullet in Spec §3–§5 |

### H. Report and demo (Spec §6)

| ID | Task | Files | Level | Owner | Status | Done when |
|---|---|---|---|---|---|---|
| H1 | Service evaluation: for each service, why we chose it, its pros and cons, and the alternative from the other cloud | `docs/report/services.md` (new) | ★ | — | Open | Every service in architecture § 1 covered |
| H2 | Cost: free tier limits we rely on, and what we spent | `docs/report/cost.md` (new) | ★ | — | Open | Table per service |
| H3 | UI screenshots for the report | `docs/report/screenshots/` | ★ | — | Blocked (D tasks) | One per feature |
| H4 | 3-minute architecture talk and demo script for the interview | `docs/report/demo-script.md` (new) | ★ | — | Open | Practised once by the whole group |
| H5 | Team report (max 1000 words): put the sections together, contribution table | — | ★★ | Everyone | Open | Submitted by 2026-11-02 |

Individual reports are written by each member alone (Spec §6.3) and are not tracked here.

---

## Log

Short entries, newest first: date, who, what (claims, merges, task changes).

| Date | Who | Entry |
|---|---|---|
| 2026-10-13 | Yijiang Luo | B4 (real model), B5 (videos) and B6 (test videos) done; C6 unblocked |
| 2026-10-11 | Yijiang Luo | Created this file with the work done so far and the open tasks |
