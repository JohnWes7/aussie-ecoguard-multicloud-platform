"""Unit tests for lambdas/search: thumbnail URL -> original and lost pet search."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import load_lambda

ORANGE_CAT = hashlib.sha256(b"orange cat").hexdigest()
BLACK_CAT = hashlib.sha256(b"black cat").hexdigest()
OLD_CAT = hashlib.sha256(b"old cat").hexdigest()
DOG = hashlib.sha256(b"dog").hexdigest()


@pytest.fixture
def app(aws):
    return load_lambda("search")


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def put_file(table, sha, tags, attributes=(), created_at=None, status="READY", thumb=True):
    meta = {
        "file_id": sha, "sk": "META", "status": status, "file_url": f"uploads/{sha}",
        "file_type": "image", "filename": f"{sha[:4]}.jpg", "attributes": list(attributes),
        "created_at": created_at or ago(1),
    }
    if thumb:
        meta["thumb_url"] = f"thumbnails/{sha}.jpg"
    table.put_item(Item=meta)
    for species, count in tags.items():
        table.put_item(Item={"file_id": sha, "sk": f"TAG#{species}", "tag": species, "tag_count": count})


def call(app, body):
    response = app.handler({"body": json.dumps(body)}, None)
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"
    return response["statusCode"], json.loads(response["body"])


@pytest.fixture
def pets(table):
    put_file(table, ORANGE_CAT, {"cat": 1}, ["orange"], ago(2))
    put_file(table, BLACK_CAT, {"cat": 2}, ["black", "white"], ago(1))
    put_file(table, OLD_CAT, {"cat": 1}, ["orange"], ago(60))
    put_file(table, DOG, {"dog": 1}, ["orange"], ago(1))


# ---------- thumbnail URL -> original ----------

def test_signed_thumbnail_url_gives_the_original(app, table):
    put_file(table, ORANGE_CAT, {"cat": 1})
    url = f"https://bucket.s3.ap-southeast-2.amazonaws.com/thumbnails/{ORANGE_CAT}.jpg?X-Amz-Signature=abc"
    status, body = call(app, {"thumbnail_url": url})
    assert status == 200
    assert body["file_id"] == ORANGE_CAT
    assert f"uploads/{ORANGE_CAT}" in body["file_url"]
    assert body["file_type"] == "image"


def test_bare_key_and_path_style_url_work(app, table):
    put_file(table, ORANGE_CAT, {"cat": 1})
    for url in (f"thumbnails/{ORANGE_CAT}.jpg",
                f"https://s3.amazonaws.com/bucket/thumbnails/{ORANGE_CAT}.jpg"):
        status, body = call(app, {"thumbnail_url": url})
        assert status == 200 and body["file_id"] == ORANGE_CAT


@pytest.mark.parametrize("url", ["", "https://example.com/cat.jpg", f"uploads/{ORANGE_CAT}", 42])
def test_not_a_thumbnail_url_is_400(app, url):
    status, _ = call(app, {"thumbnail_url": url})
    assert status == 400


def test_unknown_or_unfinished_file_is_404(app, table):
    put_file(table, BLACK_CAT, {}, status="PROCESSING")
    for sha in (ORANGE_CAT, BLACK_CAT):
        status, _ = call(app, {"thumbnail_url": f"thumbnails/{sha}.jpg"})
        assert status == 404


# ---------- lost pet ----------

def ids(body):
    return [r["file_id"] for r in body["results"]]


def test_species_only_returns_every_sighting_newest_first(app, pets):
    status, body = call(app, {"lost_pet": {"species": "cat"}})
    assert status == 200
    assert ids(body) == [BLACK_CAT, ORANGE_CAT, OLD_CAT]


def test_colour_filters_sightings(app, pets):
    _, body = call(app, {"lost_pet": {"species": "Cats", "colours": ["Orange"]}})
    assert ids(body) == [ORANGE_CAT, OLD_CAT]
    result = body["results"][0]
    assert result["attributes"] == ["orange"] and result["tags"] == {"cat": 1}
    assert result["thumbnail_url"] and result["file_url"]


def test_any_of_several_colours_matches(app, pets):
    _, body = call(app, {"lost_pet": {"species": "cat", "colours": ["white", "grey"]}})
    assert ids(body) == [BLACK_CAT]


def test_days_keeps_only_recent_sightings(app, pets):
    _, body = call(app, {"lost_pet": {"species": "cat", "colours": ["orange"], "days": 30}})
    assert ids(body) == [ORANGE_CAT]


def test_no_match_is_an_empty_list(app, pets):
    status, body = call(app, {"lost_pet": {"species": "cat", "colours": ["brown"]}})
    assert status == 200 and body["results"] == []


@pytest.mark.parametrize("query", [
    "cat", {}, {"species": ""}, {"species": "cat", "colours": "orange"},
    {"species": "cat", "days": "soon"}, {"species": "cat", "days": 0},
])
def test_bad_lost_pet_query_is_400(app, query):
    status, _ = call(app, {"lost_pet": query})
    assert status == 400


def test_tag_search_still_works(app, pets):
    response = app.handler({"body": json.dumps({"tags": {"cat": 2}})}, None)
    assert response["statusCode"] == 200
    assert [r["file_id"] for r in json.loads(response["body"])["results"]] == [BLACK_CAT]
