"""thumbnail: save a small JPEG of every image that is uploaded.

Trigger: S3 Object Created -> EventBridge rule (the same one as tagging) -> this
function. The event's object key is in event["detail"]["object"]["key"] and
looks like "uploads/<sha256>".

Steps for one file:
  1. GetObject, then check the Content-Type that upload-url set. Keys have no
     extension, so this is how videos are told apart; they are skipped before
     their body is read.
  2. Scale the image down so its longest side is THUMB_SIZE px, keeping the
     aspect ratio. Phone photos are turned upright from their EXIF orientation,
     and transparent PNGs get a white background, since JPEG has no alpha.
  3. Save it as a JPEG (quality THUMB_QUALITY) to thumbnails/<sha256>.jpg.

The thumbnail key is fixed, so tagging already stores thumb_url in META
without waiting for this function, and running it twice just overwrites the
same object. The rule only matches uploads/, so writing to thumbnails/ does
not trigger it again.

Pillow comes from a Lambda layer (aws_lambda_layer_version.pillow in
thumbnails.tf), not from this folder.

Retries: EventBridge invokes Lambda asynchronously, so Lambda retries an error
twice. Errors that would happen again (the object was deleted, the file is not
an image Pillow can read, the image is too big) are logged and the function
returns normally instead. Without a thumbnail the website shows the original.
"""

import io
import os
import re

import boto3
from botocore.exceptions import ClientError
from PIL import Image, ImageOps, UnidentifiedImageError

THUMB_SIZE = int(os.environ.get("THUMB_SIZE", "512"))  # px, longest side
THUMB_QUALITY = int(os.environ.get("THUMB_QUALITY", "70"))  # JPEG quality, 1-95
MAX_BYTES = int(os.environ.get("MAX_BYTES", str(50 * 1024 * 1024)))  # bigger images are skipped

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
    if not content_type.startswith("image/"):
        body.close()
        print(f"skipped {file_id}: {content_type or 'no Content-Type'} is not an image")
        return {"skipped": "not an image"}
    if obj["ContentLength"] > MAX_BYTES:
        body.close()
        print(f"skipped {file_id}: {obj['ContentLength']} bytes is over MAX_BYTES")
        return {"skipped": "too big"}

    try:
        thumbnail = make_thumbnail(body.read())
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
