"""Unit tests for lambdas/tagging (SQS -> Cloud Run -> DynamoDB).

Cloud Run and Cognito's token endpoint are replaced by a fake urlopen, so no
network is needed.
"""

import base64
import hashlib
import io
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest
from boto3.dynamodb.conditions import Key

from conftest import BUCKET_NAME, load_lambda

SHA = hashlib.sha256(b"fox").hexdigest()
KEY = f"uploads/{SHA}"
RESULT = {"tags": {"fox": 2, "cat": 1}, "attributes": ["orange"], "model": "stub-v1"}
TOKEN_URL = "https://auth.example/oauth2/token"


@pytest.fixture
def app(aws, monkeypatch):
    monkeypatch.setenv("INFERENCE_URL", "https://inference.example")
    monkeypatch.setenv("COGNITO_TOKEN_URL", TOKEN_URL)
    monkeypatch.setenv("COGNITO_CLIENT_ID", "client-1")
    monkeypatch.setenv("COGNITO_CLIENT_SECRET", "secret-1")
    return load_lambda("tagging")


def token_reply(expires_in=3600):
    """What Cognito's token endpoint answers to a client credentials request."""
    body = {"access_token": "token-1", "expires_in": expires_in, "token_type": "Bearer"}
    return io.BytesIO(json.dumps(body).encode())


@pytest.fixture
def cloud_run(monkeypatch):
    """Fake Cloud Run plus Cognito's token endpoint. Set `.reply` to a dict
    (200 JSON), an int (error status) or an exception; `.requests` collects the
    JSON bodies Cloud Run received and `.auth` their Authorization headers.
    `.token_reply` and `.token_requests` do the same for the token endpoint."""

    class FakeCloudRun:
        def __init__(self):
            self.reply = RESULT
            self.requests = []
            self.auth = []
            self.token_reply = token_reply
            self.token_requests = []

        def urlopen(self, request, timeout):
            if request.full_url == TOKEN_URL:
                self.token_requests.append(request)
                if isinstance(self.token_reply, Exception):
                    raise self.token_reply
                return self.token_reply()
            self.requests.append(json.loads(request.data))
            self.auth.append(request.get_header("Authorization"))
            if isinstance(self.reply, Exception):
                raise self.reply
            if isinstance(self.reply, int):
                body = io.BytesIO(json.dumps({"error": "boom"}).encode())
                raise urllib.error.HTTPError(request.full_url, self.reply, "error", {}, body)
            return io.BytesIO(json.dumps(self.reply).encode())

    fake = FakeCloudRun()
    monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)
    return fake


def sqs_event(key=KEY, attempt=1):
    """What SQS hands the function: one record wrapping the EventBridge event."""
    s3_event = {
        "source": "aws.s3",
        "detail-type": "Object Created",
        "detail": {"bucket": {"name": BUCKET_NAME}, "object": {"key": key, "size": 3}},
    }
    return {"Records": [{
        "body": json.dumps(s3_event),
        "attributes": {"ApproximateReceiveCount": str(attempt)},
    }]}


def put_meta(table, status="PENDING", file_type="image"):
    """The META item upload-url leaves behind."""
    table.put_item(Item={
        "file_id": SHA, "sk": "META", "status": status, "expires_at": 9999999999,
        "file_url": KEY, "file_type": file_type, "created_at": "2026-10-11T00:00:00+00:00",
    })


def get_meta(table):
    return table.get_item(Key={"file_id": SHA, "sk": "META"})["Item"]


def get_tags(table):
    items = table.query(KeyConditionExpression=Key("file_id").eq(SHA) & Key("sk").begins_with("TAG#"))["Items"]
    return {item["tag"]: int(item["tag_count"]) for item in items}


# ---------- success ----------

def test_image_becomes_ready_with_tags(app, table, cloud_run):
    put_meta(table)

    app.handler(sqs_event(), None)

    meta = get_meta(table)
    assert meta["status"] == "READY"
    assert "expires_at" not in meta  # TTL must not delete a stored file
    assert meta["attributes"] == ["orange"]
    assert meta["model"] == "stub-v1"
    assert meta["thumb_url"] == f"thumbnails/{SHA}.jpg"
    assert get_tags(table) == {"fox": 2, "cat": 1}


def test_tag_items_are_in_the_gsi_shape(app, table, cloud_run):
    put_meta(table)

    app.handler(sqs_event(), None)

    item = table.get_item(Key={"file_id": SHA, "sk": "TAG#fox"})["Item"]
    assert item["tag"] == "fox" and item["tag_count"] == 2


def test_cloud_run_gets_a_presigned_url_and_the_file_type(app, table, cloud_run):
    put_meta(table, file_type="video")

    app.handler(sqs_event(), None)

    [request] = cloud_run.requests
    assert request["file_type"] == "video"
    assert f"{BUCKET_NAME}" in request["url"] and KEY in request["url"]
    assert "X-Amz-Signature" in request["url"]


def test_video_gets_a_thumbnail_url_too(app, table, cloud_run):
    # The thumbnail Lambda saves a frame of the video under the same key.
    put_meta(table, file_type="video")

    app.handler(sqs_event(), None)

    assert get_meta(table)["thumb_url"] == f"thumbnails/{SHA}.jpg"


def test_no_animals_is_ready_without_tags(app, table, cloud_run):
    put_meta(table)
    cloud_run.reply = {"tags": {}, "attributes": [], "model": "stub-v1"}

    app.handler(sqs_event(), None)

    assert get_meta(table)["status"] == "READY"
    assert get_tags(table) == {}


def test_redelivery_while_processing_continues(app, table, cloud_run):
    put_meta(table, status="PROCESSING")

    app.handler(sqs_event(attempt=2), None)

    assert get_meta(table)["status"] == "READY"


# ---------- skipped ----------

@pytest.mark.parametrize("status", ["READY", "FAILED"])
def test_finished_file_is_skipped(app, table, cloud_run, status):
    put_meta(table, status=status)

    app.handler(sqs_event(), None)

    assert get_meta(table)["status"] == status
    assert cloud_run.requests == []


def test_file_without_meta_is_skipped(app, table, cloud_run):
    app.handler(sqs_event(), None)

    assert "Item" not in table.get_item(Key={"file_id": SHA, "sk": "META"})
    assert cloud_run.requests == []


@pytest.mark.parametrize("key", [
    f"uploads/{SHA}.jpg",        # old key format
    f"thumbnails/{SHA}.jpg",     # not an upload
    "uploads/abc",               # not a sha256
])
def test_other_keys_are_ignored(app, table, cloud_run, key):
    app.handler(sqs_event(key=key), None)

    assert cloud_run.requests == []


def test_result_from_another_delivery_is_not_overwritten(app, table, monkeypatch):
    put_meta(table)

    # While this delivery waits for Cloud Run, another one finishes the file.
    def finish_first(request, timeout):
        if request.full_url == TOKEN_URL:
            return token_reply()
        table.update_item(
            Key={"file_id": SHA, "sk": "META"},
            UpdateExpression="SET #s = :ready",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":ready": "READY"},
        )
        return io.BytesIO(json.dumps(RESULT).encode())
    monkeypatch.setattr(urllib.request, "urlopen", finish_first)

    app.handler(sqs_event(), None)  # must not raise

    assert get_meta(table)["status"] == "READY"
    assert get_tags(table) == {}  # this delivery's transaction was cancelled


# ---------- Cognito token for Cloud Run ----------

def test_cloud_run_gets_the_cognito_token(app, table, cloud_run):
    put_meta(table)

    app.handler(sqs_event(), None)

    assert cloud_run.auth == ["Bearer token-1"]
    [request] = cloud_run.token_requests
    # Client credentials: the client ID and secret as HTTP Basic auth, and the scope.
    basic = request.get_header("Authorization").removeprefix("Basic ")
    assert base64.b64decode(basic).decode() == "client-1:secret-1"
    assert urllib.parse.parse_qs(request.data.decode()) == {
        "grant_type": ["client_credentials"], "scope": ["aussie-ecoguard/infer"],
    }


def test_token_is_reused_while_valid(app, table, cloud_run):
    put_meta(table)
    app.handler(sqs_event(), None)
    table.delete_item(Key={"file_id": SHA, "sk": "META"})
    put_meta(table)

    app.handler(sqs_event(), None)

    assert len(cloud_run.requests) == 2
    assert len(cloud_run.token_requests) == 1


def test_token_close_to_expiry_is_renewed(app, table, cloud_run):
    cloud_run.token_reply = lambda: token_reply(expires_in=60)  # less than TOKEN_MARGIN
    put_meta(table)
    app.handler(sqs_event(), None)
    table.delete_item(Key={"file_id": SHA, "sk": "META"})
    put_meta(table)

    app.handler(sqs_event(), None)

    assert len(cloud_run.token_requests) == 2


def test_token_error_is_temporary_and_cloud_run_is_not_called(app, table, cloud_run):
    put_meta(table)
    cloud_run.token_reply = urllib.error.URLError("connection refused")

    with pytest.raises(app.TemporaryError):
        app.handler(sqs_event(attempt=1), None)

    assert get_meta(table)["status"] == "PROCESSING"  # SQS will try again
    assert cloud_run.requests == []


# ---------- failures ----------

TEMPORARY = [
    503,                                          # Cloud Run error
    429,                                          # Cloud Run busy
    urllib.error.URLError("connection refused"),  # no answer
    TimeoutError(),                               # too slow
]


@pytest.mark.parametrize("reply", TEMPORARY)
def test_temporary_error_raises_so_sqs_retries(app, table, cloud_run, reply):
    put_meta(table)
    cloud_run.reply = reply

    with pytest.raises(app.TemporaryError):
        app.handler(sqs_event(attempt=1), None)

    meta = get_meta(table)
    assert meta["status"] == "PROCESSING"
    assert "expires_at" not in meta


@pytest.mark.parametrize("reply", TEMPORARY)
def test_temporary_error_on_last_attempt_marks_failed(app, table, cloud_run, reply):
    put_meta(table)
    cloud_run.reply = reply

    app.handler(sqs_event(attempt=3), None)  # must not raise, or SQS would retry again

    meta = get_meta(table)
    assert meta["status"] == "FAILED"
    assert meta["error"].startswith("gave up after 3 attempts")
    assert get_tags(table) == {}


@pytest.mark.parametrize("reply", [
    400,                                                   # Cloud Run rejected the request
    {"tags": {"fox": "two"}, "attributes": []},            # count is not a number
    {"tags": {"fox": 0}, "attributes": []},                # count below 1
    {"attributes": []},                                    # no tags at all
])
def test_permanent_error_marks_failed_without_retry(app, table, cloud_run, reply):
    put_meta(table)
    cloud_run.reply = reply

    app.handler(sqs_event(attempt=1), None)

    assert get_meta(table)["status"] == "FAILED"
    assert len(cloud_run.requests) == 1


def test_error_message_does_not_leak_the_presigned_url(app, table, cloud_run):
    put_meta(table)
    cloud_run.reply = 400

    app.handler(sqs_event(), None)

    error = get_meta(table)["error"]
    assert error == "inference returned 400: boom"
    assert "X-Amz" not in error


def test_fastapi_422_detail_is_not_stored(app, table, monkeypatch):
    put_meta(table)

    # FastAPI's validation error echoes the request, presigned URL included.
    def reject(request, timeout):
        if request.full_url == TOKEN_URL:
            return token_reply()
        url = json.loads(request.data)["url"]
        body = io.BytesIO(json.dumps({"detail": [{"msg": "bad", "input": url}]}).encode())
        raise urllib.error.HTTPError(request.full_url, 422, "error", {}, body)
    monkeypatch.setattr(urllib.request, "urlopen", reject)

    app.handler(sqs_event(), None)

    meta = get_meta(table)
    assert meta["status"] == "FAILED"
    assert meta["error"] == "inference returned 422: "
