"""Unit tests for inference/main.py (Cloud Run stub). No network or GCP needed."""

import hashlib
import importlib.util
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

MAIN = Path(__file__).resolve().parent.parent / "inference" / "main.py"
GOOD_URL = (
    "https://aussie-ecoguard-media-357614057244.s3.amazonaws.com/uploads/abc.jpg"
    "?X-Amz-Signature=x"
)


@pytest.fixture
def main():
    spec = importlib.util.spec_from_file_location("inference_main", MAIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(main):
    return TestClient(main.app)


class FakeResponse:
    """Stands in for the streamed requests.get() response."""

    def __init__(self, content=b"", status=200, url=""):
        self.content = content
        self.status_code = status
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        # Like requests, the message includes the full URL.
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error for url: {self.url}", response=self)

    def iter_content(self, chunk_size):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i:i + chunk_size]


@pytest.fixture
def s3_file(main, monkeypatch):
    """Make downloads return the given bytes; records the URLs that were fetched."""
    fetched = []

    def use(content=b"fox image bytes", status=200):
        def fake_get(url, **kwargs):
            fetched.append(url)
            return FakeResponse(content, status, url)
        monkeypatch.setattr(main.requests, "get", fake_get)
        return fetched

    return use


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model": "stub-v1"}


def test_infer_returns_tags_from_the_downloaded_file(client, main, s3_file):
    fetched = s3_file(b"fox image bytes")

    response = client.post("/infer", json={"url": GOOD_URL, "file_type": "image"})

    assert response.status_code == 200
    assert fetched == [GOOD_URL]
    body = response.json()
    assert body["model"] == "stub-v1"
    assert 1 <= len(body["tags"]) <= 2
    assert all(species in main.SPECIES for species in body["tags"])
    assert all(1 <= count <= 3 for count in body["tags"].values())
    expected = main.fake_detections(hashlib.sha256(b"fox image bytes").digest())
    assert (body["tags"], body["attributes"]) == expected


def test_same_file_gets_same_tags_and_files_differ(main):
    digests = [hashlib.sha256(str(i).encode()).digest() for i in range(50)]
    results = [main.fake_detections(d) for d in digests]

    assert main.fake_detections(digests[0]) == results[0]
    assert len({str(tags) for tags, _ in results}) > 5


def test_colour_only_for_pets(main):
    for i in range(200):
        tags, attributes = main.fake_detections(hashlib.sha256(str(i).encode()).digest())
        if "cat" in tags or "dog" in tags:
            assert len(attributes) == 1 and attributes[0] in main.COLOURS
        else:
            assert attributes == []


def test_regional_s3_url_is_allowed(client, s3_file):
    s3_file()
    url = GOOD_URL.replace(".s3.amazonaws.com", ".s3.us-east-1.amazonaws.com")
    response = client.post("/infer", json={"url": url, "file_type": "video"})
    assert response.status_code == 200


@pytest.mark.parametrize("url", [
    "https://example.com/uploads/abc.jpg",                                   # other host
    "http://aussie-ecoguard-media-357614057244.s3.amazonaws.com/uploads/a",  # not https
    "https://aussie-ecoguard-media-357614057244.s3.amazonaws.com/thumbnails/a.jpg",
    "https://aussie-ecoguard-media-357614057244.s3.amazonaws.com.evil.com/uploads/a",
    "https://other-bucket.s3.amazonaws.com/uploads/abc.jpg",
    None,
])
def test_urls_outside_the_media_bucket_are_rejected(client, s3_file, url):
    fetched = s3_file()
    response = client.post("/infer", json={"url": url, "file_type": "image"})
    assert response.status_code == 422
    assert fetched == []  # never fetched


@pytest.mark.parametrize("file_type", [None, "audio", ""])
def test_bad_file_type_is_rejected(client, s3_file, file_type):
    s3_file()
    response = client.post("/infer", json={"url": GOOD_URL, "file_type": file_type})
    assert response.status_code == 422


def test_body_that_is_not_json_is_rejected(client):
    response = client.post("/infer", content="{", headers={"Content-Type": "application/json"})
    assert response.status_code == 422


def test_failed_download_returns_502_without_leaking_the_url(client, s3_file):
    s3_file(status=400)  # e.g. ExpiredToken: the signing credentials expired
    response = client.post("/infer", json={"url": GOOD_URL, "file_type": "image"})

    assert response.status_code == 502
    error = response.json()["error"]
    assert error == "could not download file (S3 returned 400)"
    assert "X-Amz" not in response.text


def test_network_error_returns_502(client, main, monkeypatch):
    def broken_get(url, **kwargs):
        raise requests.ConnectionError(f"failed to connect to {url}")
    monkeypatch.setattr(main.requests, "get", broken_get)

    response = client.post("/infer", json={"url": GOOD_URL, "file_type": "image"})

    assert response.status_code == 502
    assert response.json()["error"] == "could not download file"


def test_docs_describe_the_infer_endpoint(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/infer" in schema["paths"]
    assert "InferRequest" in schema["components"]["schemas"]
