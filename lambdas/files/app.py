"""Read-only file API for the website.

GET /files           -> {"files": [file, ...]}, newest first. PENDING items are
                        left out: their file may never have been uploaded.
GET /files/{sha256}  -> one file, any status (the website polls it until the
                        file is READY or FAILED). 404 if unknown, 400 if not a SHA-256.

A file looks like:
  {"file_id", "filename", "status", "file_type", "created_at",
   "tags": {"fox": 2}, "attributes": ["orange"], "model", "error",
   "preview_url"}   presigned GET URL of the original, absent while PENDING
"""

import json
import os
import re
from collections import defaultdict

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

TABLE_NAME = os.environ["TABLE_NAME"]
BUCKET_NAME = os.environ["BUCKET_NAME"]
PREVIEW_URL_EXPIRES = int(os.environ.get("PREVIEW_URL_EXPIRES", "3600"))  # seconds

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

table = boto3.resource("dynamodb").Table(TABLE_NAME)
s3 = boto3.client("s3", config=Config(signature_version="s3v4"))


def handler(event, context):
    sha256 = (event.get("pathParameters") or {}).get("sha256")
    if sha256 is None:
        return response(200, {"files": list_files()})

    sha256 = sha256.lower()
    if not SHA256_RE.match(sha256):
        return response(400, {"error": "sha256 must be 64 hex characters"})
    # Strongly consistent: the website polls this right after a status change.
    items = table.query(KeyConditionExpression=Key("file_id").eq(sha256), ConsistentRead=True)["Items"]
    file = to_file(items)
    if file is None:
        return response(404, {"error": "file not found", "file_id": sha256})
    return response(200, file)


def list_files():
    """Every file with its tags, newest first.

    One Scan reads the whole table (META and TAG# items together), which is
    fine for a few hundred files. For more, add a GSI on created_at and page
    through it with Query.
    """
    items_by_file = defaultdict(list)
    scan = {}
    while True:  # a Scan returns at most 1 MB per call
        page = table.scan(**scan)
        for item in page["Items"]:
            items_by_file[item["file_id"]].append(item)
        if "LastEvaluatedKey" not in page:
            break
        scan["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    files = [to_file(items) for items in items_by_file.values()]
    files = [f for f in files if f is not None and f["status"] != "PENDING"]
    return sorted(files, key=lambda f: f["created_at"], reverse=True)


def to_file(items):
    """Turn one file's items (META + TAG#...) into the JSON shape above, or None without META."""
    meta = next((item for item in items if item["sk"] == "META"), None)
    if meta is None:
        return None
    file = {
        "file_id": meta["file_id"],
        "filename": meta.get("filename", ""),
        "status": meta["status"],
        "file_type": meta["file_type"],
        "created_at": meta["created_at"],
        # tag_count comes back as a Decimal, which json.dumps cannot write
        "tags": {item["tag"]: int(item["tag_count"]) for item in items if item["sk"].startswith("TAG#")},
        "attributes": meta.get("attributes", []),
    }
    for optional in ("model", "error"):
        if optional in meta:
            file[optional] = meta[optional]
    # A PENDING file may not be in S3 yet, so there is nothing to show.
    if meta["status"] != "PENDING":
        file["preview_url"] = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_NAME, "Key": meta["file_url"]},
            ExpiresIn=PREVIEW_URL_EXPIRES,
        )
    return file


def response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
