"""Unit tests for lambdas/thumbnail (S3 -> EventBridge -> thumbnail -> thumbnails/).

The images are made with Pillow in memory, except one test that uses the
smoke test's real photo. The videos are made with the ffmpeg that the
imageio-ffmpeg package installs (tests/requirements.txt), the same build the
Lambda layer uses.
"""

import hashlib
import io
import subprocess
from pathlib import Path

import imageio_ffmpeg

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


def make_video(tmp_path, *filters, extra=(), suffix=".mp4"):
    """Encode a small H.264 video from ffmpeg test sources and return its bytes.

    Each filter is one lavfi source, e.g. "color=c=black:s=640x360:d=1"; they
    are played one after another.
    """
    path = tmp_path / f"clip{suffix}"
    inputs = []
    for f in filters:
        inputs += ["-f", "lavfi", "-i", f]
    concat = "".join(f"[{i}:v]" for i in range(len(filters))) + f"concat=n={len(filters)}:v=1:a=0"
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-y", *inputs,
         "-filter_complex", concat, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "10", *extra, str(path)],
        check=True,
    )
    return path.read_bytes()


def test_video_gets_a_thumbnail(app, s3, tmp_path):
    upload(s3, make_video(tmp_path, "testsrc=s=1280x720:d=3"), content_type="video/mp4")

    result = app.handler(event(), None)

    thumb = saved_thumbnail(s3)
    assert result["thumb_key"] == THUMB_KEY
    assert thumb.format == "JPEG"
    assert thumb.size == (512, 288)


def test_video_frame_is_taken_after_the_first_second(app, s3, tmp_path):
    # Like a camera trap clip that starts dark: black at first, then orange.
    video = make_video(tmp_path, "color=c=black:s=320x240:d=0.5", "color=c=orange:s=320x240:d=2")
    upload(s3, video, content_type="video/mp4")

    app.handler(event(), None)

    r, g, b = saved_thumbnail(s3).getpixel((160, 120))
    assert r > 200 and 100 < g < 200 and b < 60  # orange, not black


def test_video_shorter_than_a_second_uses_its_first_frame(app, s3, tmp_path):
    upload(s3, make_video(tmp_path, "color=c=orange:s=320x240:d=0.5"), content_type="video/mp4")

    result = app.handler(event(), None)

    assert result["thumb_key"] == THUMB_KEY
    assert saved_thumbnail(s3).size == (320, 240)


def test_quicktime_video_from_a_phone_is_turned_upright(app, s3, tmp_path):
    # Recorded sideways (landscape pixels) with "rotate 90°" metadata, like a .mov from an iPhone held upright.
    plain = make_video(tmp_path, "testsrc=s=640x480:d=2", suffix=".mov")
    sideways = tmp_path / "sideways.mov"
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-y", "-display_rotation", "90",
         "-i", str(tmp_path / "clip.mov"), "-c", "copy", str(sideways)],
        check=True,
    )
    assert plain  # the clip itself was made
    upload(s3, sideways.read_bytes(), content_type="video/quicktime")

    app.handler(event(), None)

    assert saved_thumbnail(s3).size == (384, 512)


def test_broken_video_is_skipped_without_raising(app, s3):
    upload(s3, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100, content_type="video/mp4")

    result = app.handler(event(), None)

    assert result == {"skipped": "unreadable"}
    assert not has_thumbnail(s3)


def test_too_big_video_is_skipped(app, s3, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "MAX_VIDEO_BYTES", 100)
    upload(s3, make_video(tmp_path, "testsrc=s=320x240:d=1"), content_type="video/mp4")

    result = app.handler(event(), None)

    assert result == {"skipped": "too big"}
    assert not has_thumbnail(s3)


def test_other_files_are_skipped(app, s3):
    upload(s3, b"%PDF-1.7", content_type="application/pdf")

    result = app.handler(event(), None)

    assert result == {"skipped": "not an image or video"}
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


def test_ffmpeg_without_the_executable_bit_is_copied_to_tmp(app, monkeypatch, tmp_path):
    # If the layer zip loses the file mode (e.g. zipped on Windows), the
    # read-only layer copy cannot run, so the function runs a copy in /tmp.
    package = tmp_path / "layer" / "imageio_ffmpeg"
    (package / "binaries").mkdir(parents=True)
    bundled = package / "binaries" / "ffmpeg-linux-x86_64-v7.0.2"
    bundled.write_bytes(Path(imageio_ffmpeg.get_ffmpeg_exe()).read_bytes())
    bundled.chmod(0o644)
    monkeypatch.setattr(imageio_ffmpeg, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    monkeypatch.setattr(app, "_ffmpeg", None)

    path = app.ffmpeg_path()

    assert path == str(tmp_path / "ffmpeg")
    assert subprocess.run([path, "-version"], capture_output=True).returncode == 0
