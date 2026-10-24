"""Unit tests for inference/main.py (the Cloud Run web service).

The real models are replaced by FakePipeline, so no torch, weights, network or
GCP are needed. The decision rules inside the models are tested separately in
test_inference_model.py.
"""

import importlib.util
import io
import os
import sys
import time
import types
from pathlib import Path

import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
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
    # The token check is switched off here; the tests under "Cognito token"
    # use auth_client, which keeps it.
    main.app.dependency_overrides[main.require_infer_token] = lambda: None
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


class FramePipeline(FakePipeline):
    """Returns the next (tags, attributes) from a list on every call, one per video frame."""

    def __init__(self, per_frame):
        super().__init__()
        self.per_frame = list(per_frame)

    def detect(self, image):
        self.images.append(image)
        return self.per_frame[len(self.images) - 1]


@pytest.fixture
def frames(main, monkeypatch):
    """Replace OpenCV: read_frames returns n small images and records the file it was given."""
    seen = {}

    def use(n=2, error=None):
        def fake_read_frames(path):
            seen["path"], seen["content"] = path, open(path, "rb").read()
            if error:
                raise error
            return [Image.new("RGB", (8, 8)) for _ in range(n)]
        monkeypatch.setattr(main, "read_frames", fake_read_frames)
        return seen

    return use


def test_video_frames_are_checked_and_merged(client, main, s3_file, frames):
    s3_file(b"fake video bytes")
    seen = frames(n=3)
    main.pipeline = FramePipeline([({"fox": 1}, []), ({"fox": 2, "cat": 1}, ["orange"]), ({}, [])])

    response = client.post("/infer", json={"url": GOOD_URL, "file_type": "video"})

    assert response.status_code == 200
    assert response.json() == {"tags": {"fox": 2, "cat": 1}, "attributes": ["orange"], "model": "fake-model"}
    assert len(main.pipeline.images) == 3
    assert seen["content"] == b"fake video bytes"  # downloaded to a local file first
    assert not os.path.exists(seen["path"])          # and deleted afterwards (/tmp is memory)


def test_video_over_the_size_limit_returns_413(client, main, s3_file, frames, monkeypatch):
    monkeypatch.setattr(main, "MAX_VIDEO_BYTES", 1000)
    s3_file(b"x" * 1001)
    frames()
    response = client.post("/infer", json={"url": GOOD_URL, "file_type": "video"})
    assert response.status_code == 413
    assert main.pipeline.images == []


def test_video_longer_than_the_limit_returns_422(client, main, s3_file, frames):
    s3_file(b"video")
    frames(error=main.VideoTooLong(190.4))
    response = client.post("/infer", json={"url": GOOD_URL, "file_type": "video"})
    assert response.status_code == 422  # 4xx: tagging marks the file FAILED instead of retrying
    assert response.json() == {"error": "video is 190 s long; the limit is 3 minutes"}
    assert main.pipeline.images == []


def test_file_that_is_not_a_video_returns_422(client, main, s3_file, frames):
    s3_file(b"not a video")
    frames(error=main.BadVideo())
    response = client.post("/infer", json={"url": GOOD_URL, "file_type": "video"})
    assert response.status_code == 422
    assert response.json() == {"error": "file is not a valid video"}


@pytest.mark.parametrize("duration, times", [
    (0.5, [0]), (9.9, [0]), (10, [0]), (10.1, [0, 10]), (40, [0, 10, 20, 30]),
    (180, list(range(0, 180, 10))),  # 18 frames at the limit
])
def test_frame_times_are_every_10_seconds(main, duration, times):
    assert main.frame_times(duration) == times


def test_merge_frames_keeps_the_highest_count_not_the_sum(main):
    results = [({"fox": 2}, ["grey"]), ({"fox": 1, "cat": 1}, ["white"]), ({"fox": 2}, ["white"])]
    assert main.merge_frames(results) == ({"fox": 2, "cat": 1}, ["white", "grey"])
    assert main.merge_frames([({}, []), ({}, [])]) == ({}, [])


def test_read_frames_with_opencv(main, tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")  # only in the inference venv, not in the unit-test venv
    import numpy as np
    path = str(tmp_path / "clip.mp4")
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 2, (64, 48))
    for i in range(50):  # 25 s at 2 fps
        writer.write(np.full((48, 64, 3), i * 5, np.uint8))
    writer.release()

    frames = main.read_frames(path)
    assert [f.size for f in frames] == [(64, 48)] * 3  # at 0, 10 and 20 s

    monkeypatch.setattr(main, "MAX_VIDEO_SECONDS", 20)
    with pytest.raises(main.VideoTooLong):
        main.read_frames(path)
    with pytest.raises(main.BadVideo):
        main.read_frames(str(tmp_path / "missing.mp4"))


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


# ---------- Cognito token ----------
# Tokens are signed here with a key pair made for the tests; the user pool's
# public key download (JWKS) is replaced by fake_jwks.

POOL = "us-east-1_Trusted1"
ISSUER = f"https://cognito-idp.us-east-1.amazonaws.com/{POOL}"
OTHER_ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Someone2"
INFER = {"url": GOOD_URL, "file_type": "image"}


@pytest.fixture(scope="module")
def keys():
    return {
        "pool": rsa.generate_private_key(public_exponent=65537, key_size=2048),
        "attacker": rsa.generate_private_key(public_exponent=65537, key_size=2048),
    }


def make_token(private_key, **claims):
    """A token like the tagging Lambda's (machine client, infer scope), with
    `claims` changed; a claim set to None is left out."""
    body = {"iss": ISSUER, "token_use": "access", "scope": "aussie-ecoguard/infer",
            "client_id": "machine", "sub": "machine", "exp": time.time() + 3600}
    body.update(claims)
    body = {name: value for name, value in body.items() if value is not None}
    return jwt.encode(body, private_key, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def fake_jwks(main, keys, monkeypatch):
    """Every trusted pool's public key is the test pool key; records the issuers asked for."""
    asked = []

    class FakeJwksClient:
        def get_signing_key_from_jwt(self, token):
            return types.SimpleNamespace(key=keys["pool"].public_key())

    def jwks_client(issuer):
        asked.append(issuer)
        return FakeJwksClient()
    monkeypatch.setattr(main, "jwks_client", jwks_client)
    return asked


@pytest.fixture
def auth_client(main, fake_jwks, s3_file, monkeypatch):
    monkeypatch.setattr(main, "TRUSTED_USER_POOLS", [POOL])
    s3_file()
    return TestClient(main.app)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_machine_token_from_a_trusted_pool_is_accepted(auth_client, keys):
    response = auth_client.post("/infer", json=INFER, headers=bearer(make_token(keys["pool"])))
    assert response.status_code == 200


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}])
def test_missing_token_returns_401(auth_client, main, headers):
    response = auth_client.post("/infer", json=INFER, headers=headers)
    assert response.status_code == 401
    assert main.pipeline.images == []


def test_token_is_checked_before_the_request_body(auth_client):
    response = auth_client.post("/infer", json={"url": "https://example.com/x"})
    assert response.status_code == 401  # not 422: callers without a token learn nothing


def test_expired_token_returns_401(auth_client, keys):
    token = make_token(keys["pool"], exp=time.time() - 60)
    assert auth_client.post("/infer", json=INFER, headers=bearer(token)).status_code == 401


def test_token_signed_with_another_key_returns_401(auth_client, keys):
    # Claims that look right, but not signed by the pool.
    token = make_token(keys["attacker"])
    assert auth_client.post("/infer", json=INFER, headers=bearer(token)).status_code == 401


def test_unsigned_token_returns_401(auth_client):
    token = jwt.encode({"iss": ISSUER, "token_use": "access", "scope": "aussie-ecoguard/infer",
                        "exp": time.time() + 3600}, None, algorithm="none")
    assert auth_client.post("/infer", json=INFER, headers=bearer(token)).status_code == 401


def test_token_from_an_untrusted_pool_returns_401_without_fetching_its_keys(auth_client, keys, fake_jwks):
    # Validly signed by the attacker's own pool, with a scope of the same name.
    token = make_token(keys["attacker"], iss=OTHER_ISSUER)

    response = auth_client.post("/infer", json=INFER, headers=bearer(token))

    assert response.status_code == 401
    assert fake_jwks == []  # the token's own iss never decides where keys come from


@pytest.mark.parametrize("iss", [
    "https://evil.example/us-east-1_Trusted1",                          # not Cognito
    "https://cognito-idp.us-east-1.amazonaws.com.evil.example/us-east-1_Trusted1",
    None,
])
def test_issuer_that_is_not_cognito_returns_401(auth_client, main, keys, fake_jwks, monkeypatch, iss):
    monkeypatch.setattr(main, "TRUSTED_USER_POOLS", ["*"])  # even when any pool is accepted
    token = make_token(keys["pool"], iss=iss)

    assert auth_client.post("/infer", json=INFER, headers=bearer(token)).status_code == 401
    assert fake_jwks == []


def test_star_accepts_any_cognito_pool(auth_client, main, keys, fake_jwks, monkeypatch):
    monkeypatch.setattr(main, "TRUSTED_USER_POOLS", ["*"])
    token = make_token(keys["pool"], iss=OTHER_ISSUER)

    assert auth_client.post("/infer", json=INFER, headers=bearer(token)).status_code == 200
    assert fake_jwks == [OTHER_ISSUER]


@pytest.mark.parametrize("claims", [
    {"token_use": "id", "aud": "web-client", "scope": None},  # a signed-in user's ID token
    {"scope": "openid email profile"},                         # a user's access token
    {"scope": "aussie-ecoguard/infer-extra"},                  # only the exact scope counts
])
def test_user_tokens_of_the_trusted_pool_return_403(auth_client, main, keys, claims):
    token = make_token(keys["pool"], **claims)

    response = auth_client.post("/infer", json=INFER, headers=bearer(token))

    assert response.status_code == 403
    assert response.json() == {"error": "token lacks the aussie-ecoguard/infer scope"}
    assert main.pipeline.images == []


def test_keys_that_cannot_be_fetched_return_503(auth_client, main, keys, monkeypatch):
    class Unreachable:
        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientConnectionError("timed out")
    monkeypatch.setattr(main, "jwks_client", lambda issuer: Unreachable())

    response = auth_client.post("/infer", json=INFER, headers=bearer(make_token(keys["pool"])))

    assert response.status_code == 503  # the tagging Lambda retries 5xx


def test_health_needs_no_token(auth_client):
    assert auth_client.get("/health").status_code == 200


@pytest.mark.parametrize("value, pools", [
    ("us-east-1_A, us-east-1_B", ["us-east-1_A", "us-east-1_B"]),
    ("", []),  # nothing trusted: every token is refused
])
def test_trusted_pools_come_from_the_environment(monkeypatch, value, pools):
    monkeypatch.setenv("TRUSTED_USER_POOLS", value)
    sys.path.insert(0, str(INFERENCE_DIR))
    try:
        spec = importlib.util.spec_from_file_location("inference_main_env", INFERENCE_DIR / "main.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(INFERENCE_DIR))
    assert module.TRUSTED_USER_POOLS == pools


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
