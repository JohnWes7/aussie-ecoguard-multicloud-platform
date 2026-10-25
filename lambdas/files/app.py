"""Read-only file API for the website.

GET /files           -> {"files": [file, ...]}, newest first. A PENDING item is
                        only listed while its upload URL is still valid; after
                        that nobody can upload the file, so it was abandoned.
GET /files/{sha256}  -> one file, any status (the website polls it until the
                        file is READY or FAILED). 404 if unknown, 400 if not a SHA-256.

A file looks like:
  {"file_id", "filename", "status", "file_type", "created_at",
   "tags": {"fox": 2}, "attributes": ["orange"], "model", "error",
   "uploaded",      PENDING only: true once the file is in S3 (queued for tagging)
   "preview_url",   presigned GET URL of the original, absent until it is in S3
   "thumb_url"}     presigned GET URL of the thumbnail, READY images only
"""

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError

TABLE_NAME = os.environ["TABLE_NAME"]
BUCKET_NAME = os.environ["BUCKET_NAME"]
PREVIEW_URL_EXPIRES = int(os.environ.get("PREVIEW_URL_EXPIRES", "3600"))  # seconds
URL_EXPIRES = int(os.environ.get("URL_EXPIRES", "900"))  # upload-url's PUT URL lifetime

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

    now = datetime.now(timezone.utc)
    files = [to_file(items) for items in items_by_file.values()]
    files = [f for f in files if f is not None and is_listed(f, now)]
    return sorted(files, key=lambda f: f["created_at"], reverse=True)


def is_listed(file, now):
    """Hide PENDING files whose upload URL has expired.

    upload-url rewrites created_at every time it issues a URL, so a PENDING
    file older than URL_EXPIRES can no longer be uploaded.
    """
    if file["status"] != "PENDING":
        return True
    age = now - datetime.fromisoformat(file["created_at"])
    return age.total_seconds() <= URL_EXPIRES


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
    # A PENDING file is either still waiting for the browser's PUT, or already
    # in S3 and waiting in the tagging queue (at most 2 files are tagged at a
    # time). The table only changes when tagging starts, so ask S3.
    if meta["status"] == "PENDING":
        file["uploaded"] = object_exists(meta["file_url"])
    if meta["status"] != "PENDING" or file["uploaded"]:
        file["preview_url"] = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_NAME, "Key": meta["file_url"]},
            ExpiresIn=PREVIEW_URL_EXPIRES,
        )
    # tagging sets thumb_url (READY images only) without waiting for the
    # thumbnail Lambda, so the object may not exist yet, or ever if the image
    # could not be read. The website falls back to preview_url then.
    if "thumb_url" in meta:
        file["thumb_url"] = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_NAME, "Key": meta["thumb_url"]},
            ExpiresIn=PREVIEW_URL_EXPIRES,
        )
    return file


def object_exists(key):
    try:
        s3.head_object(Bucket=BUCKET_NAME, Key=key)
        return True
    except ClientError:  # 404 Not Found: the PUT has not happened yet
        return False


def response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
