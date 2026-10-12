"""Make the uploadable test videos in videos/ from the downloaded ones (run download_videos.py first).

The website only accepts .mp4 and .mov, and Commons only hosts WebM, so:
  <name>.mp4                  each Commons video re-encoded as H.264 (like most Android phones)
  cat_lotti_hevc_rotated.mov  the cat video as HEVC with its pixels stored sideways and a
                              "rotate 90" display matrix, like an iPhone video filmed in portrait
  synthetic_40s.mp4           10 s each: fox_otway, fox_play, cat_dog_sleeping (from the model
                              selection test set), then an empty grey frame
  synthetic_190s.mp4          longer than the 3-minute limit, so it must be refused

Needs its own venv (PyAV ships FFmpeg with the x264 and x265 encoders):
    python -m venv .venv-video && .venv-video/Scripts/pip install -r experiments/test-media/requirements.txt
    .venv-video/Scripts/python experiments/test-media/make_test_videos.py
"""

import os
import struct

import av
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEOS = os.path.join(HERE, "videos")
IMAGES = os.path.join(HERE, "..", "model-selection", "data", "images")


def write(path, frames, fps, codec, size):
    """Encode RGB numpy frames (all of one size) into path."""
    with av.open(path, "w") as out:
        stream = out.add_stream(codec, rate=fps)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        for array in frames:
            for packet in stream.encode(av.VideoFrame.from_ndarray(array, format="rgb24")):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)


def transcode(src, dst, codec, sideways=False):
    """Re-encode a video; with sideways=True store the pixels turned 90 degrees anticlockwise."""
    with av.open(src) as inp:
        v = inp.streams.video[0]
        frames = (f.to_ndarray(format="rgb24") for f in inp.decode(v))
        size = (v.width, v.height)
        if sideways:
            frames = (np.ascontiguousarray(np.rot90(a)) for a in frames)
            size = (v.height, v.width)
        write(dst, frames, round(float(v.average_rate or 25)), codec, size)


def set_rotation(path):
    """Write a 90-degree clockwise display matrix into the video track's tkhd box.

    That is how phones mark portrait videos. FFmpeg 7+ (and so PyAV) ignores the
    old "rotate" metadata tag when writing, so the bytes are patched directly.
    """
    data = bytearray(open(path, "rb").read())
    i = data.find(b"tkhd")
    version = data[i + 4]
    # After "tkhd": version/flags, times + track id + duration, reserved 8, layer, group,
    # volume, reserved, then the 3x3 matrix of 32-bit values (16.16 fixed point, w 2.30).
    m = i + 4 + 4 + (32 if version == 1 else 20) + 8 + 8
    data[m:m + 36] = struct.pack(">9I", 0, 0x00010000, 0, 0xFFFF0000, 0, 0, 0, 0, 0x40000000)
    open(path, "wb").write(data)


def still(name, size=(1280, 720)):
    """A test image letterboxed onto a grey canvas, as an RGB array."""
    img = Image.open(os.path.join(IMAGES, name)).convert("RGB")
    img.thumbnail(size)
    canvas = Image.new("RGB", size, (90, 90, 90))
    canvas.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
    return np.asarray(canvas)


if __name__ == "__main__":
    for name in ["fox_red_bulgaria", "fox_gray_trap", "rabbit_browsing", "cat_lotti_smartphone"]:
        transcode(os.path.join(VIDEOS, name + ".webm"), os.path.join(VIDEOS, name + ".mp4"), "libx264")
    mov = os.path.join(VIDEOS, "cat_lotti_hevc_rotated.mov")
    transcode(os.path.join(VIDEOS, "cat_lotti_smartphone.webm"), mov, "libx265", sideways=True)
    set_rotation(mov)

    fps = 5
    grey = np.full((720, 1280, 3), 128, np.uint8)
    segments = [still("fox_otway.jpg"), still("fox_play.jpg"), still("cat_dog_sleeping.jpg"), grey]
    write(os.path.join(VIDEOS, "synthetic_40s.mp4"),
          (seg for seg in segments for _ in range(10 * fps)), fps, "libx264", (1280, 720))
    fox = still("fox_otway.jpg")
    write(os.path.join(VIDEOS, "synthetic_190s.mp4"), (fox for _ in range(190)), 1, "libx264", (1280, 720))
    for f in sorted(os.listdir(VIDEOS)):
        print(f"{f:32s} {os.path.getsize(os.path.join(VIDEOS, f)) / 1e6:6.1f} MB")
