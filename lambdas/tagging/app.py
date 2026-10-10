"""tagging: run inference on a newly uploaded file and save its tags.

Trigger: S3 Object Created -> EventBridge rule -> SQS queue -> this function.
Each SQS record's body is the EventBridge event; the object key is in
body["detail"]["object"]["key"] and looks like "uploads/<sha256>".

Steps for one file:
  1. Move META from PENDING to PROCESSING (conditional). Files without a META
     item (not uploaded through upload-url) or already READY / FAILED are skipped.
  2. Send a presigned GET URL to Cloud Run POST /infer.
  3. Success: one transaction sets META to READY and writes a TAG#<species>
     item per species.
     Failure: META becomes FAILED, so the user can upload the file again.

Retries are left to SQS. On a temporary error (network, timeout, 429, 5xx)
the function raises, and SQS delivers the message again after the visibility
timeout. From attempt MAX_ATTEMPTS on it writes FAILED instead. A 4xx answer
will not change on retry, so it is FAILED straight away. The queue allows more
deliveries than MAX_ATTEMPTS, so a crash or timeout on the last attempt still
gets another delivery. Only if every delivery crashes does the message move to
the dead-letter queue, leaving META in PROCESSING until it is redriven.
"""

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

TABLE_NAME = os.environ["TABLE_NAME"]
BUCKET_NAME = os.environ["BUCKET_NAME"]
INFERENCE_URL = os.environ["INFERENCE_URL"]  # Cloud Run service URL, no trailing path
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))  # below the queue's maxReceiveCount (5)
INFER_TIMEOUT = int(os.environ.get("INFER_TIMEOUT", "60"))  # seconds, Cloud Run's own limit
GET_URL_EXPIRES = 300  # seconds Cloud Run has to start downloading the file

KEY_RE = re.compile(r"^uploads/([0-9a-f]{64})$")

table = boto3.resource("dynamodb").Table(TABLE_NAME)
dynamodb = boto3.client("dynamodb")  # transactions need the low-level client
s3 = boto3.client("s3", config=Config(signature_version="s3v4"))


class TemporaryError(Exception):
    """Inference may succeed if tried again later."""


class PermanentError(Exception):
    """Trying again will give the same result."""


def handler(event, context):
    # The queue's batch size is 1, but handle a list anyway.
    for record in event["Records"]:
        process(record)


def process(record):
    s3_event = json.loads(record["body"])
    key = s3_event["detail"]["object"]["key"]
    attempt = int(record["attributes"]["ApproximateReceiveCount"])

    match = KEY_RE.match(key)
    if not match:
        print(f"skip {key}: not a media upload key")
        return
    file_id = match.group(1)

    meta = start_processing(file_id)
    if meta is None:
        return

    try:
        result = call_inference(key, meta["file_type"])
    except TemporaryError as e:
        if attempt < MAX_ATTEMPTS:
            print(f"{file_id}: attempt {attempt} failed, SQS will retry: {e}")
            raise
        mark_failed(file_id, f"gave up after {attempt} attempts: {e}")
        return
    except PermanentError as e:
        mark_failed(file_id, str(e))
        return

    save_result(file_id, meta["file_type"], result)


def start_processing(file_id):
    """PENDING -> PROCESSING. Returns the updated META item, or None to skip.

    PROCESSING is also accepted because SQS may deliver the same message again
    (a retry, or a second copy) while the file is still being processed.
    """
    try:
        return table.update_item(
            Key={"file_id": file_id, "sk": "META"},
            # expires_at is the TTL for abandoned reservations; the file is in
            # S3 now, so remove it or DynamoDB would delete this item later.
            UpdateExpression="SET #status = :processing REMOVE expires_at",
            # A missing item has no status, so this also rejects files that
            # were not uploaded through upload-url.
            ConditionExpression="#status IN (:pending, :processing)",
            ExpressionAttributeNames={"#status": "status"},  # reserved word
            ExpressionAttributeValues={":pending": "PENDING", ":processing": "PROCESSING"},
            ReturnValues="ALL_NEW",
            ReturnValuesOnConditionCheckFailure="ALL_OLD",
        )["Attributes"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        status = e.response.get("Item", {}).get("status", {}).get("S", "no META item")
        print(f"skip {file_id}: status is {status}")
        return None


def call_inference(key, file_type):
    """POST the file's presigned URL to Cloud Run and return {tags, attributes, model}."""
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET_NAME, "Key": key},
        ExpiresIn=GET_URL_EXPIRES,
    )
    request = urllib.request.Request(
        f"{INFERENCE_URL}/infer",
        data=json.dumps({"url": url, "file_type": file_type}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Error messages never include `url`: it carries temporary credentials.
    try:
        with urllib.request.urlopen(request, timeout=INFER_TIMEOUT) as response:
            body = json.load(response)
    except urllib.error.HTTPError as e:  # Cloud Run answered with an error status
        message = f"inference returned {e.code}: {error_text(e)}"
        if 400 <= e.code < 500 and e.code != 429:  # 429 = busy, worth retrying
            raise PermanentError(message)
        raise TemporaryError(message)
    except urllib.error.URLError as e:  # no answer at all (DNS, connection refused, ...)
        raise TemporaryError(f"inference unreachable: {e.reason}")
    except TimeoutError:
        raise TemporaryError(f"inference timed out after {INFER_TIMEOUT}s")

    return check_result(body)


def error_text(http_error):
    """The "error" field of Cloud Run's JSON error body, shortened for storage."""
    try:
        return str(json.load(http_error).get("error", ""))[:200]
    except (ValueError, AttributeError):
        return ""


def check_result(body):
    """Make sure the response looks like {"tags": {"fox": 2}, "attributes": [...], "model": "..."}."""
    tags = body.get("tags") if isinstance(body, dict) else None
    attributes = body.get("attributes", []) if isinstance(body, dict) else None
    valid = (
        isinstance(tags, dict)
        and all(isinstance(s, str) and s and type(n) is int and n >= 1 for s, n in tags.items())
        and isinstance(attributes, list)
        and all(isinstance(a, str) for a in attributes)
    )
    if not valid:
        raise PermanentError("inference returned an invalid result")
    return {"tags": tags, "attributes": attributes, "model": str(body.get("model", ""))}


def save_result(file_id, file_type, result):
    """PROCESSING -> READY plus one TAG# item per species, all or nothing."""
    update = "SET #status = :ready, #attributes = :attributes, model = :model, tagged_at = :now"
    values = {
        ":ready": {"S": "READY"},
        ":processing": {"S": "PROCESSING"},
        ":attributes": {"L": [{"S": a} for a in result["attributes"]]},
        ":model": {"S": result["model"]},
        ":now": {"S": datetime.now(timezone.utc).isoformat()},
    }
    # Thumbnail keys are fixed, so the URL can be saved before the thumbnail exists.
    if file_type == "image":
        update += ", thumb_url = :thumb"
        values[":thumb"] = {"S": f"thumbnails/{file_id}.jpg"}

    items = [{
        "Update": {
            "TableName": TABLE_NAME,
            "Key": {"file_id": {"S": file_id}, "sk": {"S": "META"}},
            "UpdateExpression": update,
            # Only one delivery of the same message may write the result.
            "ConditionExpression": "#status = :processing",
            "ExpressionAttributeNames": {"#status": "status", "#attributes": "attributes"},
            "ExpressionAttributeValues": values,
        }
    }]
    for species, count in result["tags"].items():
        items.append({
            "Put": {
                "TableName": TABLE_NAME,
                "Item": {
                    "file_id": {"S": file_id},
                    "sk": {"S": f"TAG#{species}"},
                    "tag": {"S": species},
                    "tag_count": {"N": str(count)},
                },
            }
        })

    try:
        dynamodb.transact_write_items(TransactItems=items)
    except ClientError as e:
        reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
        if "ConditionalCheckFailed" not in reasons:
            raise  # e.g. throttling: let SQS retry
        print(f"skip {file_id}: no longer PROCESSING, another delivery finished it")
        return
    print(f"{file_id}: READY {result['tags']}")


def mark_failed(file_id, reason):
    """PROCESSING -> FAILED, keeping a short reason for debugging."""
    print(f"{file_id}: FAILED: {reason}")
    try:
        table.update_item(
            Key={"file_id": file_id, "sk": "META"},
            UpdateExpression="SET #status = :failed, #error = :reason",
            ConditionExpression="#status = :processing",
            ExpressionAttributeNames={"#status": "status", "#error": "error"},
            ExpressionAttributeValues={
                ":failed": "FAILED",
                ":processing": "PROCESSING",
                ":reason": reason,
            },
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        print(f"{file_id}: not PROCESSING any more, FAILED not written")
