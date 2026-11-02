"""Unit tests for lambdas/files (GET /files and GET /files/{sha256})."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest

from conftest import BUCKET_NAME, load_lambda

FOX = hashlib.sha256(b"fox").hexdigest()
CAT = hashlib.sha256(b"cat").hexdigest()


@pytest.fixture
def app(aws):
    return load_lambda("files")


def put_file(table, sha, status="READY", created_at="2026-10-11T00:00:00+00:00", tags=None, **extra):
    """A META item as upload-url and tagging leave it, plus TAG# items."""
    table.put_item(Item={
        "file_id": sha, "sk": "META", "status": status, "file_url": f"uploads/{sha}",
        "file_type": "image", "filename": f"{sha[:4]}.jpg", "created_at": created_at, **extra,
    })
    for species, count in (tags or {}).items():
        table.put_item(Item={"file_id": sha, "sk": f"TAG#{species}", "tag": species, "tag_count": count})


def list_files(app):
    """Invoke the handler the way API Gateway does for GET /files."""
    response = app.handler({"resource": "/files", "pathParameters": None}, None)
    assert response["statusCode"] == 200
    return json.loads(response["body"])["files"]


def get_file(app, sha):
    """Invoke the handler the way API Gateway does for GET /files/{sha256}."""
    response = app.handler({"resource": "/files/{sha256}", "pathParameters": {"sha256": sha}}, None)
    return response["statusCode"], json.loads(response["body"])


# ---------- GET /files ----------

def test_empty_table_lists_nothing(app, table):
    assert list_files(app) == []


def test_list_has_tags_and_newest_first(app, table):
    put_file(table, FOX, created_at="2026-10-10T00:00:00+00:00", tags={"fox": 2, "cat": 1})
    put_file(table, CAT, created_at="2026-10-11T00:00:00+00:00", tags={"cat": 1}, attributes=["orange"])

    files = list_files(app)

    assert [f["file_id"] for f in files] == [CAT, FOX]
    assert files[1]["tags"] == {"fox": 2, "cat": 1}
    assert files[0]["attributes"] == ["orange"]
    assert files[0]["filename"] == f"{CAT[:4]}.jpg"


def test_list_keeps_every_status_after_pending(app, table):
    put_file(table, FOX, status="PROCESSING")
    put_file(table, CAT, status="FAILED", error="inference returned 400: boom")

    files = {f["file_id"]: f for f in list_files(app)}

    assert set(files) == {FOX, CAT}
    assert files[CAT]["error"] == "inference returned 400: boom"


def ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def test_list_shows_pending_only_while_its_upload_url_is_valid(app, table):
    put_file(table, FOX, status="PENDING", created_at=ago(60))       # may still be uploading
    put_file(table, CAT, status="PENDING", created_at=ago(16 * 60))  # URL expired: abandoned

    files = list_files(app)

    assert [f["file_id"] for f in files] == [FOX]
    assert "preview_url" not in files[0]


def test_tags_without_meta_are_ignored(app, table):
    table.put_item(Item={"file_id": FOX, "sk": "TAG#fox", "tag": "fox", "tag_count": 1})

    assert list_files(app) == []


def test_list_reads_every_scan_page(app, table, monkeypatch):
    for i in range(3):
        put_file(table, hashlib.sha256(bytes([i])).hexdigest())
    # Force one item per Scan call so the LastEvaluatedKey loop is exercised.
    scan = app.table.scan
    monkeypatch.setattr(app.table, "scan", lambda **kwargs: scan(Limit=1, **kwargs))

    assert len(list_files(app)) == 3


# ---------- GET /files/{sha256} ----------

def test_get_ready_file_with_preview_url(app, table):
    put_file(table, FOX, tags={"fox": 2}, model="stub-v1")

    status, file = get_file(app, FOX)

    assert status == 200
    assert file["status"] == "READY"
    assert file["tags"] == {"fox": 2}
    assert file["model"] == "stub-v1"
    assert BUCKET_NAME in file["preview_url"] and f"uploads/{FOX}" in file["preview_url"]
    assert "X-Amz-Signature" in file["preview_url"]


def test_ready_image_has_a_thumbnail_url(app, table):
    # tagging sets thumb_url on READY images.
    put_file(table, FOX, thumb_url=f"thumbnails/{FOX}.jpg")

    status, file = get_file(app, FOX)

    assert BUCKET_NAME in file["thumb_url"] and f"thumbnails/{FOX}.jpg" in file["thumb_url"]
    assert "X-Amz-Signature" in file["thumb_url"]
    assert f"uploads/{FOX}" in file["preview_url"]  # the original is still there to open
    assert list_files(app)[0]["thumb_url"] == file["thumb_url"]


def test_no_thumbnail_url_without_thumb_url(app, table):
    # Files tagged before thumbnails existed, and files that are not READY yet.
    put_file(table, FOX, file_type="video")
    put_file(table, CAT, status="PROCESSING")

    assert "thumb_url" not in get_file(app, FOX)[1]
    assert "thumb_url" not in get_file(app, CAT)[1]


def test_get_pending_file_not_uploaded_yet_has_no_preview(app, table):
    # upload-url has issued the URL but the browser has not PUT the file yet.
    put_file(table, FOX, status="PENDING")

    status, file = get_file(app, FOX)

    assert status == 200
    assert file["status"] == "PENDING"
    assert file["uploaded"] is False
    assert "preview_url" not in file


def test_get_pending_file_already_in_s3_is_uploaded_with_preview(app, table):
    # The PUT is done but the file waits in the tagging queue (still PENDING).
    put_file(table, FOX, status="PENDING")
    boto3.client("s3").put_object(Bucket=BUCKET_NAME, Key=f"uploads/{FOX}", Body=b"fox")

    status, file = get_file(app, FOX)

    assert file["status"] == "PENDING"
    assert file["uploaded"] is True
    assert f"uploads/{FOX}" in file["preview_url"]


def test_uploaded_is_only_reported_for_pending_files(app, table):
    put_file(table, FOX, status="READY")

    status, file = get_file(app, FOX)

    assert "uploaded" not in file


def test_get_accepts_upper_case_sha(app, table):
    put_file(table, FOX)

    status, file = get_file(app, FOX.upper())

    assert status == 200 and file["file_id"] == FOX


def test_get_unknown_file_returns_404(app, table):
    status, body = get_file(app, FOX)

    assert status == 404
    assert body["file_id"] == FOX


@pytest.mark.parametrize("sha", ["abc", "z" * 64, "../" + "a" * 61])
def test_get_bad_sha_returns_400(app, table, sha):
    status, _ = get_file(app, sha)
    assert status == 400


def test_responses_allow_cors(app, table):
    response = app.handler({"resource": "/files", "pathParameters": None}, None)
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"
