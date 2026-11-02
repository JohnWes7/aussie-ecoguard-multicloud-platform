"""thumbnail: save a small JPEG of every image and video that is uploaded.

Trigger: S3 Object Created -> EventBridge rule (the same one as tagging) -> this
function. The event's object key is in event["detail"]["object"]["key"] and
looks like "uploads/<sha256>".

Steps for one file:
  1. GetObject, then check the Content-Type that upload-url set. Keys have no
     extension, so this is how images and videos are told apart.
  2. Image: read it. Video: write it to /tmp and ask ffmpeg for one frame
     VIDEO_FRAME_SECONDS in (the very first frame is often black, or a fade
     in), or the first frame if the video is shorter than that. ffmpeg turns
     phone videos upright from their rotation metadata by itself.
  3. Scale the picture down so its longest side is THUMB_SIZE px, keeping the
     aspect ratio. Phone photos are turned upright from their EXIF orientation,
     and transparent PNGs get a white background, since JPEG has no alpha.
  4. Save it as a JPEG (quality THUMB_QUALITY) to thumbnails/<sha256>.jpg.

The thumbnail key is fixed, so tagging already stores thumb_url in META
without waiting for this function, and running it twice just overwrites the
same object. The rule only matches uploads/, so writing to thumbnails/ does
not trigger it again.

Pillow and ffmpeg come from Lambda layers (aws_lambda_layer_version.pillow and
.ffmpeg in thumbnails.tf), not from this folder. ffmpeg is the static Linux
build that the imageio-ffmpeg package ships.

Retries: EventBridge invokes Lambda asynchronously, so Lambda retries an error
twice. Errors that would happen again (the object was deleted, the file is not
an image Pillow or a video ffmpeg can read, the file is too big) are logged and
the function returns normally instead. Without a thumbnail the website shows
the original image, or the video's own first frame.
"""

import io
import os
import re
import shutil
import stat
import subprocess
import tempfile

import boto3
from botocore.exceptions import ClientError
from PIL import Image, ImageOps, UnidentifiedImageError

THUMB_SIZE = int(os.environ.get("THUMB_SIZE", "512"))  # px, longest side
THUMB_QUALITY = int(os.environ.get("THUMB_QUALITY", "70"))  # JPEG quality, 1-95
MAX_BYTES = int(os.environ.get("MAX_BYTES", str(50 * 1024 * 1024)))  # bigger images are skipped
MAX_VIDEO_BYTES = int(os.environ.get("MAX_VIDEO_BYTES", str(500 * 1024 * 1024)))  # the website's upload limit
VIDEO_FRAME_SECONDS = float(os.environ.get("VIDEO_FRAME_SECONDS", "1"))
FFMPEG_TIMEOUT = 30  # seconds; one frame normally takes well under 1 s

KEY_RE = re.compile(r"^uploads/([0-9a-f]{64})$")

s3 = boto3.client("s3")


def handler(event, context):
    detail = event.get("detail") or {}
    bucket = (detail.get("bucket") or {}).get("name")
    key = (detail.get("object") or {}).get("key", "")
    match = KEY_RE.match(key)
    if not bucket or not match:
        print(f"skipped: not an upload ({bucket!r}, {key!r})")
        return {"skipped": "not an upload"}
    file_id = match.group(1)

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":  # deleted before this ran
            print(f"skipped {file_id}: object no longer exists")
            return {"skipped": "deleted"}
        raise

    body = obj["Body"]
    content_type = obj.get("ContentType", "")
    is_video = content_type.startswith("video/")
    if not (is_video or content_type.startswith("image/")):
        body.close()
        print(f"skipped {file_id}: {content_type or 'no Content-Type'} is not an image or a video")
        return {"skipped": "not an image or video"}
    if obj["ContentLength"] > (MAX_VIDEO_BYTES if is_video else MAX_BYTES):
        body.close()
        print(f"skipped {file_id}: {obj['ContentLength']} bytes is over the limit")
        return {"skipped": "too big"}

    try:
        picture = video_frame(body) if is_video else body.read()
        thumbnail = make_thumbnail(picture)
    except BadVideo as e:
        print(f"skipped {file_id}: cannot read the video ({e})")
        return {"skipped": "unreadable"}
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as e:
        # OSError: a truncated or corrupt file that Pillow starts but cannot finish.
        print(f"skipped {file_id}: cannot read the image ({e})")
        return {"skipped": "unreadable"}

    thumb_key = f"thumbnails/{file_id}.jpg"
    s3.put_object(Bucket=bucket, Key=thumb_key, Body=thumbnail, ContentType="image/jpeg")
    print(f"saved {thumb_key} ({len(thumbnail)} bytes)")
    return {"thumb_key": thumb_key, "bytes": len(thumbnail)}


def make_thumbnail(data):
    """Bytes of a JPEG or PNG -> bytes of a JPEG at most THUMB_SIZE px on its longest side."""
    image = Image.open(io.BytesIO(data))
    # For a JPEG, decode at 1/2, 1/4 or 1/8 size straight away (much faster
    # and uses less memory); thumbnail() below does the exact resize.
    image.draft("RGB", (THUMB_SIZE, THUMB_SIZE))
    image = ImageOps.exif_transpose(image)
    image.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)  # never enlarges

    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        image = Image.new("RGB", rgba.size, "white")
        image.paste(rgba, mask=rgba.getchannel("A"))
    else:
        image = image.convert("RGB")

    out = io.BytesIO()
    image.save(out, "JPEG", quality=THUMB_QUALITY, optimize=True, progressive=True)
    return out.getvalue()


class BadVideo(Exception):
    """ffmpeg could not get a frame out of the file (not a video, or broken)."""


def video_frame(body):
    """S3 body of a video -> PNG bytes of one frame, VIDEO_FRAME_SECONDS in.

    The video is written to /tmp first: ffmpeg needs to seek, and an MP4 from
    a phone often has its index (the moov atom) at the end of the file.
    """
    with tempfile.NamedTemporaryFile() as video:  # /tmp on Lambda, deleted afterwards
        shutil.copyfileobj(body, video, 1024 * 1024)
        video.flush()
        frame = grab_frame(video.name, VIDEO_FRAME_SECONDS)
        if not frame and VIDEO_FRAME_SECONDS > 0:
            # Shorter than VIDEO_FRAME_SECONDS: ffmpeg succeeds with no output.
            frame = grab_frame(video.name, 0)
    if not frame:
        raise BadVideo("no frames")
    return frame


def grab_frame(path, seconds):
    """One frame at `seconds` as PNG bytes (b"" if the video is shorter)."""
    command = [
        ffmpeg_path(), "-nostdin", "-loglevel", "error",
        "-ss", str(seconds), "-i", path,  # -ss before -i seeks quickly, by keyframe
        "-frames:v", "1", "-an",
        "-f", "image2pipe", "-c:v", "png", "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=FFMPEG_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise BadVideo(f"ffmpeg took longer than {FFMPEG_TIMEOUT} s")
    if result.returncode != 0:
        raise BadVideo(result.stderr.decode(errors="replace").strip()[-300:] or f"ffmpeg exit {result.returncode}")
    return result.stdout


_ffmpeg = None


def ffmpeg_path():
    """Path of an ffmpeg that can run here, found once per container.

    The layer is read-only. If its binary lost the executable bit on the way
    (zipped on Windows, for example), a copy in /tmp is made executable.
    """
    global _ffmpeg
    if _ffmpeg:
        return _ffmpeg
    import imageio_ffmpeg  # from the ffmpeg layer; only videos need it

    binaries = os.path.join(os.path.dirname(imageio_ffmpeg.__file__), "binaries")
    bundled = [os.path.join(binaries, n) for n in os.listdir(binaries) if n.startswith("ffmpeg-")]
    if bundled and not os.access(bundled[0], os.X_OK):
        copy = os.path.join(tempfile.gettempdir(), "ffmpeg")
        if not os.access(copy, os.X_OK):
            shutil.copyfile(bundled[0], copy)
            os.chmod(copy, os.stat(copy).st_mode | stat.S_IXUSR)
        _ffmpeg = copy
    else:
        _ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    return _ffmpeg
