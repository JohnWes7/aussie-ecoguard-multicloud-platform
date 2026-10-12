"""Run the deployed pipeline locally on the test videos the way the service does
(one frame every 10 s, highest count per species) and print every frame's tags.

Run from the repo root in the inference venv, after infra/fetch_models.py:
    .venv-inference/Scripts/python experiments/test-media/expected_results.py
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "inference"))
os.environ.setdefault("MODEL_DIR", os.path.join(REPO, "models"))
import main  # noqa: E402  (the service module: read_frames, merge_frames, limits)
from model import Pipeline  # noqa: E402

VIDEOS = os.path.join(HERE, "videos")
pipeline = Pipeline(main.MODEL_DIR)
print("model", pipeline.config["model_label"])
for name in sorted(os.listdir(VIDEOS)):
    if not name.endswith((".mp4", ".mov")):
        continue
    try:
        frames = main.read_frames(os.path.join(VIDEOS, name))
    except main.VideoTooLong as e:
        print(f"{name}: refused, {e.args[0]:.0f} s is over the limit\n")
        continue
    start = time.perf_counter()
    results = [pipeline.detect(frame) for frame in frames]
    for t, (tags, attributes) in zip(range(0, 10 * len(results), 10), results):
        print(f"  {name} {t:4d} s {tags} {attributes}")
    print(f"{name}: {len(frames)} frames -> {main.merge_frames(results)} "
          f"({(time.perf_counter() - start) / len(frames):.1f} s/frame)\n")
