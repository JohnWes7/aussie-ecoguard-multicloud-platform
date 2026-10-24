"""Unit tests for lambdas/upload_url (POST /upload-url)."""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import load_lambda

SHA = hashlib.sha256(b"fox").hexdigest()


@pytest.fixture
def app(aws):
    return load_lambda("upload_url")


def event(raw_body, sub="user-1"):
    """An event as API Gateway's proxy integration sends it, after the Cognito
    authorizer accepted the token of user `sub`."""
    return {
        "body": raw_body,
        "requestContext": {"authorizer": {"claims": {"sub": sub, "email": f"{sub}@example.com"}}},
    }


def call(app, body, sub="user-1"):
    response = app.handler(event(json.dumps(body), sub), None)
    return response["statusCode"], json.loads(response["body"])


def get_meta(table):
    return table.get_item(Key={"file_id": SHA, "sk": "META"})["Item"]


def set_status(table, status):
    table.update_item(
        Key={"file_id": SHA, "sk": "META"},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status},
    )


def test_new_file_returns_presigned_url(app, table):
    status, body = call(app, {"sha256": SHA, "filename": "fox.jpg"})

    assert status == 200
    assert body["key"] == f"uploads/{SHA}"
    assert body["headers"]["Content-Type"] == "image/jpeg"
    # Both headers must be part of the signature, or the browser could change them.
    signed = parse_qs(urlparse(body["url"]).query)["X-Amz-SignedHeaders"][0]
    assert signed == "content-type;host;x-amz-checksum-sha256"


def test_new_file_writes_pending_meta_with_24h_ttl(app, table):
    call(app, {"sha256": SHA, "filename": "fox.jpg"})

    item = get_meta(table)
    assert item["status"] == "PENDING"
    assert item["file_type"] == "image"
    assert item["file_url"] == f"uploads/{SHA}"
    assert item["filename"] == "fox.jpg"
    assert 86000 < int(item["expires_at"]) - time.time() <= 86400


def test_checksum_header_is_base64_of_sha256(app, table):
    _, body = call(app, {"sha256": SHA, "filename": "fox.jpg"})

    assert base64.b64decode(body["headers"]["x-amz-checksum-sha256"]).hex() == SHA


@pytest.mark.parametrize("filename, file_type, content_type", [
    ("Fox.JPEG", "image", "image/jpeg"),
    ("fox.jpeg", "image", "image/jpeg"),
    ("fox.PNG", "image", "image/png"),
    ("clip.mp4", "video", "video/mp4"),
    ("clip.MOV", "video", "video/quicktime"),
])
def test_extension_sets_type_but_not_key(app, table, filename, file_type, content_type):
    _, body = call(app, {"sha256": SHA, "filename": filename})

    assert body["key"] == f"uploads/{SHA}"
    assert body["headers"]["Content-Type"] == content_type
    assert get_meta(table)["file_type"] == file_type


def test_pending_file_gets_a_new_url_for_the_same_key(app, table):
    # A different extension must not create a second object for the same bytes.
    _, first = call(app, {"sha256": SHA, "filename": "fox.jpg"})
    status, second = call(app, {"sha256": SHA, "filename": "fox.png"})

    assert status == 200
    assert second["key"] == first["key"]


@pytest.mark.parametrize("current", ["PROCESSING", "READY"])
def test_uploaded_file_is_rejected(app, table, current):
    call(app, {"sha256": SHA, "filename": "fox.jpg"})
    set_status(table, current)

    status, body = call(app, {"sha256": SHA, "filename": "fox.jpg"})

    assert status == 409
    assert body["status"] == current
    assert body["key"] == f"uploads/{SHA}"


def test_failed_file_can_be_uploaded_again(app, table):
    call(app, {"sha256": SHA, "filename": "fox.jpg"})
    set_status(table, "FAILED")

    status, _ = call(app, {"sha256": SHA, "filename": "fox.jpg"})

    assert status == 200
    assert get_meta(table)["status"] == "PENDING"


@pytest.mark.parametrize("body", [
    {"sha256": "abc", "filename": "fox.jpg"},       # too short
    {"sha256": "z" * 64, "filename": "fox.jpg"},    # not hex
    {"filename": "fox.jpg"},                        # missing sha256
    {"sha256": SHA, "filename": "fox.gif"},         # unsupported type
    {"sha256": SHA, "filename": "fox"},             # no extension
])
def test_invalid_request_returns_400(app, table, body):
    status, _ = call(app, body)
    assert status == 400


def test_body_that_is_not_json_returns_400(app, table):
    response = app.handler(event("{"), None)
    assert response["statusCode"] == 400


def test_responses_allow_cors(app, table):
    response = app.handler(event(json.dumps({"sha256": SHA, "filename": "fox.jpg"})), None)
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"


def test_owner_is_the_cognito_sub(app, table):
    call(app, {"sha256": SHA, "filename": "fox.jpg"}, sub="user-1")

    assert get_meta(table)["owner"] == "user-1"


def test_pending_file_goes_to_another_user_who_becomes_owner(app, table):
    # Files are shared: owner is only recorded, so nobody is refused.
    call(app, {"sha256": SHA, "filename": "fox.jpg"}, sub="user-1")
    status, _ = call(app, {"sha256": SHA, "filename": "fox.jpg"}, sub="user-2")

    assert status == 200
    assert get_meta(table)["owner"] == "user-2"


def test_uploaded_file_keeps_its_owner(app, table):
    call(app, {"sha256": SHA, "filename": "fox.jpg"}, sub="user-1")
    set_status(table, "READY")

    status, _ = call(app, {"sha256": SHA, "filename": "fox.jpg"}, sub="user-2")

    assert status == 409
    assert get_meta(table)["owner"] == "user-1"


def test_event_without_cognito_claims_returns_401(app, table):
    # Only possible when the function is invoked without API Gateway.
    response = app.handler({"body": json.dumps({"sha256": SHA, "filename": "fox.jpg"})}, None)

    assert response["statusCode"] == 401
    assert "Item" not in table.get_item(Key={"file_id": SHA, "sk": "META"})
