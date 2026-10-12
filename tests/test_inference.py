"""Unit tests for inference/main.py (the Cloud Run web service).

The real models are replaced by FakePipeline, so no torch, weights, network or
GCP are needed. The decision rules inside the models are tested separately in
test_inference_model.py.
"""

import importlib.util
import io
import sys
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient
from PIL import Image

INFERENCE_DIR = Path(__file__).resolve().parent.parent / "inference"
GOOD_URL = (
    "https://aussie-ecoguard-media-357614057244.s3.amazonaws.com/uploads/abc"
    "?X-Amz-Signature=x"
)


class FakePipeline:
    """Stands in for model.Pipeline and records the images it was given."""

    config = {"model_label": "fake-model"}

    def __init__(self, tags=None, attributes=None):
        self.tags = tags if tags is not None else {"fox": 2}
        self.attributes = attributes or []
        self.images = []

    def detect(self, image):
        self.images.append(image)
        return self.tags, self.attributes


@pytest.fixture
def main():
    # main.py does `from model import Pipeline`, so inference/ must be importable.
    sys.path.insert(0, str(INFERENCE_DIR))
    try:
        spec = importlib.util.spec_from_file_location("inference_main", INFERENCE_DIR / "main.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(INFERENCE_DIR))
    module.pipeline = FakePipeline()
    return module


@pytest.fixture
def client(main):
    # Not used as a context manager, so the startup hook (which would load the
    # real models) does not run; main.pipeline is already the fake.
    return TestClient(main.app)


def image_bytes(size=(40, 20), fmt="PNG", exif=None):
    buffer = io.BytesIO()
    kwargs = {"exif": exif} if exif is not None else {}
    Image.new("RGB", size, "orange").save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


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

    def use(content=None, status=200):
        content = image_bytes() if content is None else content

        def fake_get(url, **kwargs):
            fetched.append(url)
            return FakeResponse(content, status, url)
        monkeypatch.setattr(main.requests, "get", fake_get)
        return fetched

    return use


def test_health_reports_the_model(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model": "fake-model"}


def test_infer_runs_the_models_on_the_downloaded_image(client, main, s3_file):
    fetched = s3_file(image_bytes(size=(40, 20)))
    main.pipeline = FakePipeline(tags={"cat": 1, "other animal": 1}, attributes=["orange"])

    response = client.post("/infer", json={"url": GOOD_URL, "file_type": "image"})

    assert response.status_code == 200
    assert response.json() == {"tags": {"cat": 1, "other animal": 1}, "attributes": ["orange"],
                               "model": "fake-model"}
    assert fetched == [GOOD_URL]
    [image] = main.pipeline.images
    assert image.mode == "RGB" and image.size == (40, 20)


def test_image_with_no_animals_returns_empty_tags(client, main, s3_file):
    s3_file()
    main.pipeline = FakePipeline(tags={})
    response = client.post("/infer", json={"url": GOOD_URL, "file_type": "image"})
    assert response.status_code == 200
    assert response.json()["tags"] == {}


def test_jpeg_and_other_formats_are_decoded(client, s3_file):
    s3_file(image_bytes(fmt="JPEG"))
    assert client.post("/infer", json={"url": GOOD_URL, "file_type": "image"}).status_code == 200


def test_phone_photo_is_turned_upright_using_exif(main):
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 degrees to display
    image = main.open_image(image_bytes(size=(40, 20), fmt="JPEG", exif=exif))
    assert image.size == (20, 40)


def test_file_that_is_not_an_image_returns_422(client, main, s3_file):
    s3_file(b"definitely not an image")
    response = client.post("/infer", json={"url": GOOD_URL, "file_type": "image"})
    assert response.status_code == 422
    assert response.json() == {"error": "file is not a valid image"}
    assert main.pipeline.images == []


def test_video_returns_415_without_downloading(client, s3_file):
    fetched = s3_file()
    response = client.post("/infer", json={"url": GOOD_URL, "file_type": "video"})
    assert response.status_code == 415  # 4xx: tagging marks the file FAILED instead of retrying
    assert response.json() == {"error": "video files are not supported yet"}
    assert fetched == []


def test_file_over_the_size_limit_returns_413(client, main, s3_file, monkeypatch):
    monkeypatch.setattr(main, "MAX_IMAGE_BYTES", 1000)
    s3_file(b"x" * 1001)
    response = client.post("/infer", json={"url": GOOD_URL, "file_type": "image"})
    assert response.status_code == 413
    assert main.pipeline.images == []


def test_regional_s3_url_is_allowed(client, s3_file):
    s3_file()
    url = GOOD_URL.replace(".s3.amazonaws.com", ".s3.us-east-1.amazonaws.com")
    response = client.post("/infer", json={"url": url, "file_type": "image"})
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


# ---- loading the models at startup ----

def test_without_model_gcs_the_models_load_from_model_dir(main, monkeypatch):
    monkeypatch.setattr(main, "MODEL_GCS", None)
    monkeypatch.setattr(main, "MODEL_DIR", "/some/local/models")
    monkeypatch.setattr(main, "Pipeline", lambda folder: ("pipeline", folder))
    monkeypatch.setattr(main, "download_models", lambda *args: pytest.fail("should not download"))

    assert main.load_pipeline() == ("pipeline", "/some/local/models")


def test_with_model_gcs_the_models_are_downloaded_loaded_then_deleted(main, monkeypatch, tmp_path):
    local = tmp_path / "models"
    monkeypatch.setattr(main, "MODEL_GCS", "gs://model-bucket/v2")
    monkeypatch.setattr(main, "LOCAL_MODEL_DIR", str(local))
    downloads = []

    def fake_download(gcs_uri, dest):
        downloads.append((gcs_uri, dest))
        (local / "speciesnet").mkdir(parents=True)
        (local / "config.json").write_text("{}")

    def fake_pipeline(folder):
        assert (Path(folder) / "config.json").exists()  # loaded before the files are deleted
        return ("pipeline", folder)

    monkeypatch.setattr(main, "download_models", fake_download)
    monkeypatch.setattr(main, "Pipeline", fake_pipeline)

    assert main.load_pipeline() == ("pipeline", str(local))
    assert downloads == [("gs://model-bucket/v2", str(local))]
    assert not local.exists()  # /tmp is memory on Cloud Run, so the copies are removed
