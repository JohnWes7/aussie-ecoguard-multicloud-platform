"""POST /upload-url: reserve a file and return a presigned S3 PUT URL.

Request body:  {"sha256": "<64 hex chars>", "filename": "fox.jpg"}
200 response:  {"file_id", "key", "url", "headers", "expires_in"}
               The browser must PUT the file to `url` with exactly `headers`.
409 response:  the file is already uploaded (PROCESSING) or done (READY).

Deduplication: before handing out a URL we write a META item with status
PENDING. META status lifecycle:
  PENDING     URL issued, file not in S3 yet. Any new request just gets a
              fresh URL: the URL is bound to the checksum, so every upload
              writes the same bytes to the same key.
              TODO(cognito): only re-issue to the same owner, 409 for others.
  PROCESSING  file is in S3, set by the tagging Lambda -> 409
  READY       tagged and searchable -> 409
  FAILED      tagging failed -> a new upload is allowed
expires_at is a DynamoDB TTL attribute that removes abandoned PENDING items;
tagging removes it when the file reaches PROCESSING.
"""

import base64
import json
import os
import re
import time
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

TABLE_NAME = os.environ["TABLE_NAME"]
BUCKET_NAME = os.environ["BUCKET_NAME"]
URL_EXPIRES = int(os.environ.get("URL_EXPIRES", "900"))  # seconds
PENDING_TTL = int(os.environ.get("PENDING_TTL", "86400"))  # seconds until TTL cleanup

# Allowed extensions -> (file_type, Content-Type). Content-Type is derived here,
# not taken from the client, so it always matches the extension.
FILE_TYPES = {
    "jpg": ("image", "image/jpeg"),
    "png": ("image", "image/png"),
    "mp4": ("video", "video/mp4"),
    "mov": ("video", "video/quicktime"),
}
EXT_ALIASES = {"jpeg": "jpg"}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

table = boto3.resource("dynamodb").Table(TABLE_NAME)
s3 = boto3.client("s3", config=Config(signature_version="s3v4"))


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"error": "body must be JSON"})

    sha256 = str(body.get("sha256", "")).lower()
    if not SHA256_RE.match(sha256):
        return response(400, {"error": "sha256 must be 64 hex characters"})

    filename = str(body.get("filename", ""))
    ext = normalise_extension(filename)
    if ext not in FILE_TYPES:
        return response(400, {"error": f"unsupported file type, allowed: {sorted(FILE_TYPES)}"})
    file_type, content_type = FILE_TYPES[ext]

    # The key depends only on the content, not the extension, so the same bytes
    # uploaded as fox.jpg and fox.png land on one object. The type lives in
    # the object's Content-Type and in META's file_type.
    key = f"uploads/{sha256}"
    now = int(time.time())

    # Reserve the checksum. Succeeds unless the file is already in S3
    # (PROCESSING) or finished (READY).
    try:
        table.put_item(
            Item={
                "file_id": sha256,
                "sk": "META",
                "status": "PENDING",
                "expires_at": now + PENDING_TTL,
                "file_url": key,
                "file_type": file_type,
                # Only for display. If the same bytes are reserved again under
                # another name, the latest name wins.
                "filename": filename[:255],
                "created_at": datetime.now(timezone.utc).isoformat(),
                # TODO(cognito): add "owner" from the JWT "sub" claim
            },
            ConditionExpression="attribute_not_exists(file_id) OR #status IN (:pending, :failed)",
            ExpressionAttributeNames={"#status": "status"},  # reserved word
            ExpressionAttributeValues={":pending": "PENDING", ":failed": "FAILED"},
            ReturnValuesOnConditionCheckFailure="ALL_OLD",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        existing = e.response.get("Item", {})
        return response(409, {
            "error": "duplicate",
            "file_id": sha256,
            "status": existing.get("status", {}).get("S"),
            "key": existing.get("file_url", {}).get("S"),
        })

    # S3 accepts the PUT only if the body's SHA-256 matches this value,
    # so a client cannot reserve one checksum and upload different content.
    checksum_b64 = base64.b64encode(bytes.fromhex(sha256)).decode()
    url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": BUCKET_NAME,
            "Key": key,
            "ContentType": content_type,
            "ChecksumSHA256": checksum_b64,
        },
        ExpiresIn=URL_EXPIRES,
    )

    return response(200, {
        "file_id": sha256,
        "key": key,
        "url": url,
        # Both headers are part of the signature, so they must be sent as-is.
        "headers": {
            "Content-Type": content_type,
            "x-amz-checksum-sha256": checksum_b64,
        },
        "expires_in": URL_EXPIRES,
    })


def normalise_extension(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return EXT_ALIASES.get(ext, ext)


def response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
