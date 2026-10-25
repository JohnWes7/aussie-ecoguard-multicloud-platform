"""Unit tests for lambdas/thumbnail (S3 -> EventBridge -> thumbnail -> thumbnails/).

The images are made with Pillow in memory, except one test that uses the
smoke test's real photo.
"""

import hashlib
import io
from pathlib import Path

import boto3
import pytest
from PIL import Image

from conftest import BUCKET_NAME, load_lambda

SHA = hashlib.sha256(b"fox").hexdigest()
KEY = f"uploads/{SHA}"
THUMB_KEY = f"thumbnails/{SHA}.jpg"
SMOKE_CAT = Path(__file__).resolve().parent / "fixtures" / "smoke_cat.jpg"


@pytest.fixture
def app(aws):
    return load_lambda("thumbnail")


@pytest.fixture
def s3(aws):
    return boto3.client("s3")


def event(key=KEY, bucket=BUCKET_NAME):
    """An S3 Object Created event as EventBridge passes it to the function (trimmed)."""
    return {
        "source": "aws.s3",
        "detail-type": "Object Created",
        "detail": {"bucket": {"name": bucket}, "object": {"key": key, "size": 1}},
    }


def image_bytes(size, mode="RGB", fmt="JPEG", colour="orange", exif=None):
    out = io.BytesIO()
    image = Image.new(mode, size, colour)
    kwargs = {"exif": exif} if exif is not None else {}
    image.save(out, fmt, **kwargs)
    return out.getvalue()


def upload(s3, body, content_type="image/jpeg", key=KEY):
    """Put an object the way the browser does with upload-url's presigned PUT."""
    s3.put_object(Bucket=BUCKET_NAME, Key=key, Body=body, ContentType=content_type)


def saved_thumbnail(s3):
    obj = s3.get_object(Bucket=BUCKET_NAME, Key=THUMB_KEY)
    assert obj["ContentType"] == "image/jpeg"
    return Image.open(io.BytesIO(obj["Body"].read()))


def has_thumbnail(s3):
    return s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix="thumbnails/")["KeyCount"] > 0


def test_landscape_jpeg_keeps_its_aspect_ratio(app, s3):
    upload(s3, image_bytes((4000, 3000)))

    result = app.handler(event(), None)

    thumb = saved_thumbnail(s3)
    assert result["thumb_key"] == THUMB_KEY
    assert thumb.format == "JPEG"
    assert thumb.size == (512, 384)


def test_portrait_png_keeps_its_aspect_ratio(app, s3):
    upload(s3, image_bytes((1000, 2000), fmt="PNG"), content_type="image/png")

    app.handler(event(), None)

    assert saved_thumbnail(s3).size == (256, 512)


def test_small_image_is_not_enlarged(app, s3):
    upload(s3, image_bytes((300, 200)))

    app.handler(event(), None)

    assert saved_thumbnail(s3).size == (300, 200)


def test_thumbnail_is_much_smaller_than_the_original(app, s3):
    # Noise compresses badly, so the original is large like a real photo.
    noise = Image.effect_noise((3000, 2000), 64).convert("RGB")
    out = io.BytesIO()
    noise.save(out, "JPEG", quality=95)
    upload(s3, out.getvalue())

    result = app.handler(event(), None)

    assert result["bytes"] < len(out.getvalue()) / 20


def test_real_photo(app, s3):
    upload(s3, SMOKE_CAT.read_bytes())

    app.handler(event(), None)

    original = Image.open(SMOKE_CAT)
    thumb = saved_thumbnail(s3)
    assert max(thumb.size) == min(512, max(original.size))
    assert abs(thumb.width / thumb.height - original.width / original.height) < 0.01


def test_phone_photo_is_turned_upright(app, s3):
    # Stored sideways (landscape pixels) with EXIF orientation 6, "rotate 90° clockwise to view".
    exif = Image.Exif()
    exif[0x0112] = 6
    upload(s3, image_bytes((4000, 3000), exif=exif))

    app.handler(event(), None)

    assert saved_thumbnail(s3).size == (384, 512)


def test_transparent_png_gets_a_white_background(app, s3):
    upload(s3, image_bytes((800, 600), mode="RGBA", fmt="PNG", colour=(255, 0, 0, 0)), content_type="image/png")

    app.handler(event(), None)

    thumb = saved_thumbnail(s3)
    assert thumb.mode == "RGB"
    r, g, b = thumb.getpixel((10, 10))
    assert min(r, g, b) > 240  # white, not black or red


def test_video_is_skipped(app, s3):
    upload(s3, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100, content_type="video/mp4")

    result = app.handler(event(), None)

    assert result == {"skipped": "not an image"}
    assert not has_thumbnail(s3)


def test_unreadable_image_is_skipped_without_raising(app, s3):
    # No retry would help, so the function returns normally.
    upload(s3, b"this is not a jpeg")

    result = app.handler(event(), None)

    assert result == {"skipped": "unreadable"}
    assert not has_thumbnail(s3)


def test_truncated_jpeg_is_skipped_without_raising(app, s3):
    data = image_bytes((2000, 1500))
    upload(s3, data[: len(data) // 3])

    result = app.handler(event(), None)

    # Pillow either fails (skipped) or fills in the missing part; it must not raise.
    assert result.get("skipped") == "unreadable" or has_thumbnail(s3)


def test_too_big_image_is_skipped(app, s3, monkeypatch):
    monkeypatch.setattr(app, "MAX_BYTES", 100)
    upload(s3, image_bytes((400, 300)))

    result = app.handler(event(), None)

    assert result == {"skipped": "too big"}
    assert not has_thumbnail(s3)


def test_deleted_object_is_skipped(app, s3):
    # delete-files removed it before this function ran.
    assert app.handler(event(), None) == {"skipped": "deleted"}


@pytest.mark.parametrize("key", [
    THUMB_KEY,                 # the function's own output
    f"uploads/{SHA}.jpg",      # not a bare SHA-256
    "uploads/abc",
    "",
])
def test_keys_that_are_not_uploads_are_skipped(app, s3, key):
    assert app.handler(event(key=key), None) == {"skipped": "not an upload"}


def test_running_twice_overwrites_the_same_thumbnail(app, s3):
    # Lambda can deliver an EventBridge event more than once.
    upload(s3, image_bytes((1000, 1000)))

    app.handler(event(), None)
    app.handler(event(), None)

    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix="thumbnails/")["Contents"]]
    assert keys == [THUMB_KEY]
