"""Section 8: should CLIP overrule SpeciesNet when CLIP is almost certain?

For every box of the 27 test images and the 16 pet photos, records SpeciesNet's
tag/score and CLIP's tag/probability, then re-runs the rest of the deployed
pipeline with "CLIP >= T and a different species -> use CLIP" for several T.
The 27 test images are scored like report.py; the pet photos on species only.

Uses the deployed service code (inference/model.py) and its model files, so run
it from the repo root in the inference venv after infra/fetch_models.py:
    .venv-inference/Scripts/python experiments/model-selection/clip_override.py
Images: python experiments/model-selection/data/download_images.py (both lists).
"""

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "inference"))
sys.path.insert(0, HERE)
sys.argv = sys.argv[:1]  # report.py reads a threshold from argv on import
import model as m  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402
from report import parse_expected  # noqa: E402

TARGETS = ["fox", "rabbit", "feral pig", "cane toad", "cat", "dog"]
DATA = os.path.join(HERE, "data")

p = m.Pipeline(os.path.join(REPO, "models"))
c = p.config
items = [(os.path.join(DATA, "images", r["file"]), r["file"], r["expected"], "test")
         for r in csv.DictReader(open(os.path.join(DATA, "labels.csv"), encoding="utf-8"))]
items += [(os.path.join(DATA, "pet_photos", r["file"]), r["file"], r["expected"] + "=many", "pets")
          for r in csv.DictReader(open(os.path.join(DATA, "pet_photos.csv"), encoding="utf-8"))]


def clip_box(img, box):
    torch = p.torch
    with torch.no_grad():
        f = p.clip.encode_image(p.clip_preprocess(m.crop(img, box)).unsqueeze(0))
        f = f / f.norm(dim=-1, keepdim=True)
        probs = (100.0 * f @ p.species_text.T).softmax(dim=-1)[0].tolist()
    return m.best_label(p.clip_labels, probs)


def run(sn, cl, override):
    """The rest of Pipeline.detect, with the override at threshold `override` (None = off)."""
    labels = m.apply_threshold(sn, c["species_threshold"], {m.NOT_ANIMAL: c["not_animal_threshold"]})
    labels = m.clip_override(labels, cl, override, c["species"])
    if m.needs_fallback(labels):
        labels = m.apply_threshold(cl, c["species_threshold"], {m.NOT_ANIMAL: 0.0})
    return m.count_species(m.smooth(labels), c["species"], c["unknown_tag"])


data = []
for path, name, expected, group in items:
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    boxes = p.find_animals(img)
    data.append((name, expected, group, p.name_with_speciesnet(img, boxes), [clip_box(img, b) for b in boxes]))

print("Boxes where SpeciesNet names a species and CLIP disagrees:")
for name, expected, group, sn, cl in data:
    labels = m.apply_threshold(sn, c["species_threshold"], {m.NOT_ANIMAL: c["not_animal_threshold"]})
    for i, (lab, (s_lab, s_p), (c_lab, c_p)) in enumerate(zip(labels, sn, cl)):
        if lab in c["species"] and c_lab != lab:
            print(f"  {name:30s} box{i:<2d} expected {expected:16s} SN {s_lab} {s_p:.2f} | CLIP {c_lab} {c_p:.2f}")

for override in [None, 0.99, 0.98, 0.97, 0.95, 0.9]:
    right = {"test": 0, "pets": 0}
    scored = {"test": 0, "pets": 0}
    count_ok = count_n = error = 0
    changed = []
    for name, expected, group, sn, cl in data:
        tags = run(sn, cl, override)
        if tags != run(sn, cl, None):
            changed.append(f"{name}: {run(sn, cl, None)} -> {tags}")
        exp, edge = parse_expected(expected)
        if edge:
            continue
        pred = {s: n for s, n in tags.items() if s in TARGETS}
        scored[group] += 1
        right[group] += set(pred) == set(exp)
        if group == "test":
            for s, n in exp.items():
                if n is not None:
                    count_n += 1
                    count_ok += pred.get(s, 0) == n
                    error += abs(pred.get(s, 0) - n)
    print(f"\nCLIP override >= {override}: test species {right['test']}/{scored['test']}, "
          f"count right {count_ok}/{count_n}, count error {error} | pets species {right['pets']}/{scored['pets']}")
    for line in changed:
        print("   changed:", line)
