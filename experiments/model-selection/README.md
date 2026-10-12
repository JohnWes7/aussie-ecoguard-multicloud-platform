# Model selection experiment

This folder records how we chose the inference pipeline for the Cloud Run service. We compared ten pipelines on the same hand-labelled test set. This page covers the method, the results, how we reached the decision, and how to reproduce it.

**Result:** MegaDetector v5a finds and counts the animals. The SpeciesNet classifier names the species in each box, using Australia-specific mapping rules and image-level smoothing. When SpeciesNet is unsure about a whole image, the image is forwarded to CLIP. CLIP also gives the colour of cats and dogs. This chain got the species exactly right on 23 of 26 test images, with a total count error of 9. The best single-classifier pipeline (MegaDetector + CLIP) scored 21–22 of 26, with a count error of 31–37. Amazon Rekognition scored 17 of 26 and could not count reliably.

## 1. Goal and constraints

- **Spec §4.1:** the first (entry) model must be MegaDetector, Ultralytics YOLO or a managed service such as Rekognition. Other models may be chained after it.
- **Output:** per-species counts for `fox`, `rabbit`, `feral pig`, `cane toad`, `cat` and `dog`, e.g. `{"fox": 2}`, so that queries such as `{"feral pig": 1, "cane toad": 3}` work. Cats and dogs also get a colour (lost-pet search).
- **No training or fine-tuning.** Only pretrained models are used.
- **CPU only:** Cloud Run without a GPU, so speed and memory matter.

## 2. Test set

27 images from Wikimedia Commons (26 scored; the dingo is an unscored edge case), labelled by hand in [`data/labels.csv`](data/labels.csv). Licences and authors are in [`data/sources.csv`](data/sources.csv). The images are not committed (see §7).

| Group | Images | What they test |
|---|---|---|
| Fox | 4 | night infrared camera trap, partly hidden, small and distant adult + cub |
| Rabbit | 4 | close-up, group of 12, 100+ rabbits (species only), camera trap with a barely visible rabbit |
| Feral pig | 3 | single, 17 distant pigs, camera trap with adults and piglets |
| Cane toad | 3 | single, pair, night flash |
| Cat | 3 | camera-trap tabby, orange, black |
| Dog | 2 | brown poodle, black-and-white photo |
| Cat + dog | 1 | two species in one image |
| Negatives | 6 | 2 kangaroo, possum, kookaburra holding a cane toad (only the toad counts), camera-trap deer, cane toad **statue**. The kookaburra image is scored as a cane toad image; the other 5 must get no tags |
| Edge case | 1 | dingo (dog or not?), not scored |

About two-thirds of the photos were taken in Australia. The species look the same elsewhere: for example, European wild boar and Australian feral pigs are both *Sus scrofa*. The main limitation is the size of the set, and especially that only 5 images are camera-trap photos. Counts for distant groups are approximate.

## 3. Pipelines compared

| Pipeline | Finds and counts animals | Names the species | Colour (cats/dogs) |
|---|---|---|---|
| `md_clip` | MegaDetector v5a | CLIP ViT-B/32 (OpenAI weights), zero-shot | CLIP |
| `md1000_clip` | MegaDetector v1000-redwood | CLIP | CLIP |
| `md_bioclip` | MegaDetector v5a | BioCLIP, zero-shot | CLIP |
| `oiv7` | YOLOv8x trained on Open Images V7 (has Fox, Rabbit, Pig, Frog, Cat, Dog) | same model | CLIP |
| `rekognition` | Amazon Rekognition `DetectLabels` | same service | Rekognition dominant colours |
| `speciesnet` | MegaDetector v5a | Google SpeciesNet v4.0.3a, its own ensemble with `country=AUS` | not computed |
| `speciesnet_rules` | MegaDetector v5a | SpeciesNet classifier + our Australia rules, one label per image | not computed |
| `chain` | MegaDetector v5a | `speciesnet_rules`; images it is unsure about go to CLIP | not computed |
| `speciesnet_perbox` | MegaDetector v5a | SpeciesNet classifier on **every** box + Australia rules | not computed |
| **`chain_perbox`** | MegaDetector v5a | `speciesnet_perbox`; images it is unsure about go to CLIP | not computed |

Colour was only computed for the CLIP-based pipelines. In the chosen pipeline, cat and dog colour comes from CLIP, so its colour accuracy matches theirs.

### Decision rules

- **MegaDetector:** keep `animal` boxes with confidence ≥ 0.2 (MegaDetector's recommended threshold). The count is the number of boxes.
- **CLIP / BioCLIP zero-shot:** each box crop, padded by 10 %, is scored against our 6 species plus 11 **distractors** (kangaroo, wallaby, possum, wombat, koala, bird, deer, cow, sheep, horse, lizard). Zero-shot classification always picks one of the labels it is given, so without distractors a kangaroo would be forced into `fox` or `dog`. Each label's text embedding is the average over 4 prompt templates ("a camera trap photo of a {}", …), the prompt ensembling described in the CLIP paper. A box counts only if its top label is one of our species with probability ≥ the threshold.
- **YOLOv8-OIV7:** Fox→fox, Rabbit→rabbit, Pig→feral pig, Frog→cane toad (there is no Toad class), Cat→cat, Dog→dog, at confidence ≥ 0.25.
- **Rekognition:** a species is present if a matching image-level label (Fox, Red Fox, Rabbit, Hare, Pig, Boar, Hog, Toad, Cat, Dog) has confidence ≥ 80. The count is that label's number of boxes, or 1 if it has none.
- **Australia rules (SpeciesNet):** SpeciesNet's own ensemble rolls uncertain results up to a higher taxon, e.g. "rabbit and hare family" or "animal". We instead sum the top-5 classifier scores of all labels that map to one species. The mapping includes higher taxa that, **in Australia, contain only that invasive species**: Felidae → cat, Suidae → feral pig, Leporidae → rabbit, Bufonidae → cane toad, *Canis* → dog. Red fox → fox.
- **Image-level smoothing:** a simplified version of MegaDetector's `smooth_classification_results_image_level`, which says "an image with 700 cows and one deer is really just 701 cows". Low-confidence boxes take the image's dominant species if that species has at least 2 confident boxes. A species with a single box is overwritten if the dominant species has at least 4.
- **Chain fallback:** if the highest SpeciesNet score in an image is < 0.3, the whole image is forwarded to the MegaDetector + CLIP pipeline. This is the model chaining that Spec §4.1 describes.

### Metrics

- **Species exactly right:** the predicted set of species equals the expected set. Negatives must produce no tags.
- **Count exactly right / count error:** compared per species, on the 21 expectations with a number (the "100+ rabbits" image is excluded).
- **False alarms:** negatives that received any tag.
- **Colour right:** on the 6 cat/dog expectations that have a colour.
- Timing and memory were measured on a laptop CPU, per image after warm-up. Peak memory is the process's peak working set. Load time includes the first download of the weights, so it is not comparable between pipelines.

## 4. Results

Species threshold 0.3, with image-level smoothing ([`results/report_0.3_smooth.md`](results/report_0.3_smooth.md), which also lists every image):

| Pipeline | Species exactly right | Count exactly right | Count error (total) | False alarms | Colour right | s / image | Peak memory |
|---|---|---|---|---|---|---|---|
| md_clip | 21/26 | 14/21 | 31 | 0/5 | 2/6 | 1.76 | 2.3 GB |
| md1000_clip | 21/26 | 13/21 | 31 | 0/5 | 3/6 | 2.02 | 2.3 GB |
| md_bioclip | 19/26 | 13/21 | 24 | 1/5 | 2/6 | 1.97 | 2.9 GB |
| oiv7 | 18/26 | 13/21 | 39 | 0/5 | 3/6 | 0.64 | 1.9 GB |
| rekognition | 17/26 | 12/21 | 40 | 0/5 | 1/6 | 0.74 (network) | – |
| speciesnet | 15/26 | 9/21 | 28 | 0/5 | – | 1.96 | 1.7 GB |
| speciesnet_rules | 20/26 | 13/21 | 13 | 0/5 | – | 1.96 | 1.7 GB |
| chain | 23/26 | 16/21 | 9 | 0/5 | – | 2.62 | ≈ 2.3 GB* |
| speciesnet_perbox | 21/26 | 15/21 | 11 | 0/5 | – | 2.50 | 1.7 GB |
| **chain_perbox** | **23/26** | **17/21** | **9** | **0/5** | (CLIP) | 3.09 | ≈ 2.3 GB* |

\* The chains need SpeciesNet and CLIP loaded together. This value is estimated from the two separate runs and still has to be measured on Cloud Run.

At threshold 0.5 without smoothing ([`results/report_0.5.md`](results/report_0.5.md)) the ranking is the same: `md1000_clip` 22, `chain` 22, `md_clip` 20, `rekognition` 17. With only 26 images, a difference of 1–2 images is within noise. The count error is the clearer signal.

### What we learned

1. **MegaDetector is the strongest detector and counter.** It found exactly 12 rabbits and 17 distant feral pigs, matching the hand counts, and even boxed the barely visible rabbit. YOLOv8-OIV7 found 10 and 5 boxes on the same two images.
2. **With CLIP, the bottleneck is naming the species, not finding the animal.** On small camera-trap crops CLIP guessed `cow` / `horse` for 15×60-pixel pigs, `kangaroo` / `wallaby` for rabbits, `sheep` for pale wild boar and `cat` for a night-IR fox. BioCLIP fixed some of these but added false positives (cane toad statue → `cane toad`, poodle → `rabbit`) and used 600 MB more memory.
3. **Rekognition was not suitable.** Its image-level labels are good on clear photos (Fox, Toad, Boar at 95–100 %), but many labels have no bounding box, and the boxes it does draw often carry the wrong label. In a fox photo, `Fox` had no box while `Dog` had one around the fox. Rabbits were boxed as `Kangaroo` and toads as `Lizard` / `Turtle`. So it cannot count, it adds wrong species, and its dominant colours were poor (orange cat → grey).
4. **SpeciesNet is strong on camera-trap images but too conservative as shipped.** It is trained on more than 65 million camera-trap images, and it got the night-IR fox, all feral-pig images and the camera-trap cat right. It also handled the negatives correctly: the kangaroos came out as blank or mammal, and the statue as vehicle. Its ensemble, however, rolls up uncertain results ("rabbit and hare family", "canis species", "animal"). Cane toad, which is rare in camera-trap data, scored only 0.04–0.2. Our Australia rules raised it from 15 to 20 of 26 and cut the count error from 28 to 13.
5. **CLIP and SpeciesNet fail in different places**, so chaining them works. SpeciesNet's weak spot is cane toads, where CLIP got all three right. CLIP's weak spot is camera-trap crops, where SpeciesNet is strong. The fallback fired on 9–10 of 27 images, mostly negatives (still correctly untagged) and toads.
6. **Classify every box, then smooth.** SpeciesNet's own ensemble classifies only the most confident box (`classifier.py` crops `bboxes[0]`), so a cat and a dog in one image became "cat 2". Classifying every box separates them, as MegaDetector's own `run_md_and_speciesnet` does ("runs SpeciesNet on every above-threshold crop"). On its own, though, per-box classification recognised only 4 of the 17 tiny pigs. Smoothing restores all 17. The cost is about 0.24 s per extra box.
7. **Colour is weak for every method** (CLIP 2–3 of 6), and the sample is small. Colour is only stored for cats and dogs, because `attributes` is a file-level list: colours of other animals would make a search for "orange cat" also match a photo of an orange fox next to a grey cat.

### Remaining errors of the chosen pipeline

- The barely visible camera-trap rabbit: MegaDetector boxed it, but neither classifier could name it.
- The cane toad held in a kookaburra's beak: only the bird is boxed.
- The pair of cane toads: with per-box classification, one toad scores 0.16 and the other is called `cat` at 0.32. The image's top score sits just above the 0.3 fallback threshold, so it is not forwarded to CLIP.

## 5. Decision

```
image (or a video frame every 10 s)
  → MegaDetector v5a: animal boxes, conf ≥ 0.2          (entry model, Spec §4.1)
  → SpeciesNet classifier on every box + Australia rules
  → image-level smoothing
  → if the image's best score < 0.3: CLIP zero-shot on the boxes instead (model chaining)
  → cats / dogs: CLIP colour
  → {"fox": 2}, attributes ["orange"]
```

Model names, thresholds, the species mapping and the colour list will go in the service's `config.json`, so they can be changed without code changes (Spec §4.1).

After deployment one rule was added: CLIP checks every box and overrules SpeciesNet when it is at least 0.98 sure of another species (section 8).

### Limitations and follow-ups

- The test set is small and has few camera-trap images. The thresholds (0.2 / 0.3) are round numbers, not tuned, but we will re-check them on more Australian camera-trap images while building the service.
- Per-box classification makes images and video frames with many animals slower, about 0.24 s per box on a laptop CPU. We need to measure this on Cloud Run, and cap the number of boxes per frame if needed.
- SpeciesNet and the `megadetector` package need conflicting dependencies (protobuf, yolov5). The service will use the `speciesnet` package, which includes MegaDetector, plus `open_clip`.

## 6. Reproducing

Python 3.10, CPU only. Two virtual environments are needed because of the dependency conflict above.

```bash
cd experiments/model-selection
python data/download_images.py                 # 27 images → data/images/ (same files, 1280 px)

python -m venv .venv && source .venv/Scripts/activate   # Git Bash; Linux/macOS: .venv/bin/activate
pip install -r requirements.txt
python run.py md_clip                          # also: md1000_clip, md_bioclip, oiv7
python run.py rekognition                      # needs AWS credentials (us-east-1)

python -m venv .venv-speciesnet && source .venv-speciesnet/Scripts/activate
pip install -r requirements-speciesnet.txt
python run_speciesnet.py AUS
python run_speciesnet_perbox.py

# back in .venv: derived results (no models) and the tables
python speciesnet_rules.py
python chain.py 0.3                            # → results/chain.json
python chain.py 0.3 speciesnet_perbox          # → results/chain_perbox.json
python report.py 0.3 --smooth                  # → results/report_0.3_smooth.md
```

Model weights are downloaded on first use: MegaDetector and SpeciesNet into their caches, YOLOv8-OIV7 into `weights/`. `results/` already contains the raw outputs of our run, so `report.py` can be run without any models.

## 7. Files

| File | Purpose |
|---|---|
| `data/labels.csv` | Ground truth: expected `species=count`, colours, scene, notes |
| `data/sources.csv` | Commons title, licence, author and URL of each image |
| `data/download_images.py` | Downloads the images. They are not committed: 11 MB, mostly CC BY-SA, so we link to the sources instead of redistributing them |
| `run.py` | Runs the MegaDetector + CLIP/BioCLIP, OIV7 and Rekognition pipelines and saves every box with its raw scores |
| `run_speciesnet.py`, `run_speciesnet_perbox.py` | SpeciesNet as shipped, and per-box classification |
| `speciesnet_rules.py`, `chain.py` | Australia rules and the CLIP fallback, applied to saved results |
| `report.py` | Scores everything against `labels.csv` for a given threshold, with or without smoothing |
| `results/` | Raw outputs (`*.json`) and generated tables (`report_*.md`) |
| `data/pet_photos.csv` | Section 8: 16 close-up pet photos (Commons title, licence, author, URL, expected species) |
| `clip_override.py` | Section 8: SpeciesNet vs CLIP on every box, and the override at several thresholds. Runs the deployed service code |
| `results/clip_override.txt` | Its output |

Images: Wikimedia Commons contributors. Per-image credits and licences are in `data/sources.csv`.

## 8. Follow-up: close-up pet photos and the CLIP override (2026-10-13)

After deployment, a studio photo of a pet rabbit uploaded through the website was tagged `cat`. SpeciesNet said "domestic cat" at 0.86 (rabbit family 0.008), above the threshold, so CLIP was never asked; CLIP on the same box said `rabbit` at 0.998. SpeciesNet is trained on camera-trap images, and close-up pet photos are outside what it has seen.

To check whether this was a one-off, we added 16 close-up pet photos from Commons (`data/pet_photos.csv`: 8 rabbits of different breeds and colours, 3 cats, 3 dogs, 2 foxes) and compared SpeciesNet and CLIP on every box (`clip_override.py`, output in `results/clip_override.txt`):

- It was not a one-off: SpeciesNet called 5 of the 8 pet rabbits `cat` (0.35–0.93) or `goat` (0.75), while CLIP said `rabbit` at 0.99 or more. Cats, dogs and foxes were all right.
- CLIP is not always better: on the 27 test images, most boxes where the two disagree are CLIP mistakes, and some are confident. Tiny distant pigs are `cattle` at 0.96.

Rule tested: if SpeciesNet and CLIP both name a species, they differ, and CLIP is at least T sure, use CLIP's tag.

| T | Test images (26 scored) | Pet photos (16) |
|---|---|---|
| off | species 23/26, count error 15 | 11/16 |
| 0.97, 0.98, 0.99 | unchanged | 15/16 |
| 0.95, 0.9 | the pig group breaks (count error 24) | 15/16 |

We deployed T = 0.98 (model version v2, `clip_override_threshold` in `config.json`): it sits between CLIP's most confident mistake on the test images (0.96) and the pet rabbits (0.99+). The remaining error is an old sepia photo of a French Lop (still `cat`; CLIP is unsure, `rodent` 0.36). The threshold rests on few images. Checking every box with CLIP costs about 0.1 s per box.

