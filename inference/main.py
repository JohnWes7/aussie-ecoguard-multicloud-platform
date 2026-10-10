"""Inference service for Aussie EcoGuard, deployed on GCP Cloud Run (currently a stub).

POST /infer   {"url": "<presigned S3 GET URL>", "file_type": "image" | "video"}
           -> {"tags": {"fox": 2, "cat": 1}, "attributes": ["orange"], "model": "stub-v1"}
GET  /health  -> {"status": "ok", "model": "stub-v1"}

The stub downloads the file, which proves Cloud Run can read the presigned URL,
but runs no model. Its fake tags are derived from the file's SHA-256, so the
same file always gets the same tags and different files get different ones.
The request/response format stays the same when a real model replaces it.
"""

import hashlib
import re

import requests
from flask import Flask, jsonify, request

MODEL = "stub-v1"
SPECIES = ["fox", "rabbit", "feral pig", "cane toad", "cat", "dog"]
COLOURS = ["orange", "black", "white", "grey", "brown"]

# The service is public (auth comes later), so it only fetches presigned URLs
# for our own media buckets. Otherwise anyone could make it fetch any URL (SSRF).
ALLOWED_URL = re.compile(
    r"^https://aussie-ecoguard-media-\d{12}\.s3(\.[a-z0-9-]+)?\.amazonaws\.com/uploads/"
)

app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify(status="ok", model=MODEL)


@app.post("/infer")
def infer():
    body = request.get_json(silent=True) or {}
    url = body.get("url")
    file_type = body.get("file_type")

    if not isinstance(url, str) or not ALLOWED_URL.match(url):
        return jsonify(error="url must be a presigned URL for the media bucket"), 400
    if file_type not in ("image", "video"):
        return jsonify(error="file_type must be 'image' or 'video'"), 400

    # Never echo the exception: its text contains the presigned URL and token.
    try:
        digest = download_sha256(url)
    except requests.HTTPError as e:
        # e.g. the presigned URL or the credentials that signed it expired
        return jsonify(error=f"could not download file (S3 returned {e.response.status_code})"), 502
    except requests.RequestException:
        return jsonify(error="could not download file"), 502

    tags, attributes = fake_detections(digest)
    return jsonify(tags=tags, attributes=attributes, model=MODEL)


def download_sha256(url):
    """Stream the file and return its SHA-256 digest without holding it in memory."""
    sha = hashlib.sha256()
    with requests.get(url, stream=True, timeout=(5, 30)) as response:
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            sha.update(chunk)
    return sha.digest()


def fake_detections(digest):
    """Pick 1-2 species with counts 1-3 from the hash bytes, plus a colour for pets."""
    tags = {}
    for i in range(1 + digest[0] % 2):
        species = SPECIES[digest[1 + i] % len(SPECIES)]
        tags[species] = 1 + digest[3 + i] % 3
    attributes = []
    if "cat" in tags or "dog" in tags:
        attributes.append(COLOURS[digest[5] % len(COLOURS)])
    return tags, attributes
