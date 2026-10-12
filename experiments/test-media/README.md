# Test videos

Videos for testing video tagging (one frame every 10 seconds, highest count per species, at most 3 minutes). The videos themselves are not committed (CC BY / CC BY-SA, about 120 MB with the re-encoded copies); `videos.csv` records where each one comes from, and the scripts rebuild everything into `videos/` (git-ignored).

## Make them

```bash
python experiments/test-media/download_videos.py          # 4 WebM videos from Wikimedia Commons

python -m venv .venv-video                                 # first time only (git-ignored)
.venv-video/Scripts/pip install -r experiments/test-media/requirements.txt
.venv-video/Scripts/python experiments/test-media/make_test_videos.py
```

The synthetic videos use images from `experiments/model-selection/data/images/` (run its `download_images.py` first).

Commons only hosts WebM, and the website accepts only `.mp4` and `.mov`, so `make_test_videos.py` re-encodes them as H.264. It also makes an HEVC `.mov` whose pixels are stored sideways with a 90° display matrix, which is how iPhones store videos filmed in portrait. FFmpeg 7+ no longer writes the old `rotate` tag, so the script patches the matrix in the `tkhd` box itself.

## Expected results

From the deployed model (v2, `md5a+speciesnet-4.0.3a+clip-b32-v2`), checked locally with `expected_results.py` and on Cloud Run.

| File (upload these) | Length | What is in it | Result |
|---|---|---|---|
| `synthetic_40s.mp4` | 40 s | 10 s each: 1 fox, 2 foxes, a cat and a dog, nothing | `fox 1, cat 1, dog 1` (the 2-fox photo is shrunk so much that only 1 fox is found) |
| `fox_red_bulgaria.mp4` | 10 s | red fox | `fox 1` |
| `rabbit_browsing.mp4` | 18.7 s | wild rabbit | `rabbit 1` |
| `cat_lotti_smartphone.mp4` | 74 s | black-and-white cat, dim room | `cat 1, cattle 1`: one dark frame is called cattle, and a mistake in any frame stays in the result |
| `cat_lotti_hevc_rotated.mov` | 74 s | the same, as an iPhone-style portrait HEVC | the same; OpenCV reads the rotation (90°) and turns the frames upright |
| `fox_gray_trap.mp4` | 31 s | gray fox at night, camera trap | wrong (`other animal`, `kangaroo`, `wombat`, ...): gray foxes are not Australian and have no mapping rule, and the night frames are poor |
| `synthetic_190s.mp4` | 190 s | a fox photo | refused: `FAILED`, "video is 190 s long; the limit is 3 minutes" |

On Cloud Run each frame takes about 4–5 s, so the 74-second cat video (8 frames) is tagged in about 35 s once an instance is running.

## Files

| File | Purpose |
|---|---|
| `videos.csv` | Commons title, licence, author, URL, length, animals and notes for each video |
| `download_videos.py` | Downloads the original uploads from Commons |
| `make_test_videos.py` | Re-encodes them for upload and builds the synthetic videos |
| `expected_results.py` | Runs the service's own `read_frames` / `merge_frames` and the model on every video (inference venv) |
| `requirements.txt` | PyAV for `make_test_videos.py` |

Videos: Wikimedia Commons contributors; per-video credits and licences are in `videos.csv`.
