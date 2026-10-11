"""Run Google SpeciesNet (MegaDetector v5a + camera-trap classifier) over data/images.

Usage (SpeciesNet venv): python run_speciesnet.py [AUS|none]
Uses its own venv because speciesnet pins different protobuf/yolov5 versions.

Writes results/speciesnet_raw_<country>.json (SpeciesNet's own output) and
results/speciesnet[_nogeo].json in the same format as run.py, so report.py can
score it. SpeciesNet gives ONE prediction per image, so every animal box in the
image gets that label; the count is the number of MegaDetector animal boxes.
"""

import csv
import json
import os
import sys
import time

import psutil
from speciesnet import DEFAULT_MODEL, SpeciesNet

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGES = os.path.join(HERE, "data", "images")
LABELS = os.path.join(HERE, "data", "labels.csv")
MD_THRESHOLD = 0.2

# SpeciesNet labels are "uuid;class;order;family;genus;species;common name".
# Map by scientific name (genus + species) or family to our tags.
SPECIES_MAP = {
    "vulpes vulpes": "fox",
    "oryctolagus cuniculus": "rabbit",
    "sus scrofa": "feral pig", "sus scrofa domesticus": "feral pig",
    "rhinella marina": "cane toad",
    "felis catus": "cat",
    "canis familiaris": "dog",
}


def to_tag(label):
    parts = label.split(";")
    if len(parts) < 7:
        return "other:" + label
    genus_species = f"{parts[4]} {parts[5]}".strip()
    return SPECIES_MAP.get(genus_species, "other:" + (parts[6] or parts[3] or parts[2]))


def main():
    country = sys.argv[1] if len(sys.argv) > 1 else "AUS"
    files = [r["file"] for r in csv.DictReader(open(LABELS, encoding="utf-8"))]

    t0 = time.perf_counter()
    model = SpeciesNet(DEFAULT_MODEL, geofence=country != "none")
    load_s = time.perf_counter() - t0

    raw, out = {}, {"pipeline": "speciesnet", "model": DEFAULT_MODEL, "country": country,
                    "load_s": round(load_s, 1), "images": {}}
    for f in files:
        t = time.perf_counter()
        pred = model.predict(filepaths=[os.path.join(IMAGES, f)],
                             country=None if country == "none" else country,
                             run_mode="single_thread", progress_bars=False)["predictions"][0]
        elapsed = time.perf_counter() - t
        pred["filepath"] = "data/images/" + f  # do not store the local absolute path
        raw[f] = pred
        label = to_tag(pred.get("prediction", ""))
        boxes = [{"det_conf": d["conf"], "bbox": d["bbox"], "label": label,
                  "score": pred.get("prediction_score", 0)}
                 for d in pred.get("detections", [])
                 if d["category"] == "1" and d["conf"] >= MD_THRESHOLD]
        out["images"][f] = {"time_s": round(elapsed, 2), "boxes": boxes}
        print(f"{f:24s} {elapsed:5.2f}s  {len(boxes):2d} boxes  {pred.get('prediction', '')[37:]}  "
              f"{pred.get('prediction_score', 0):.2f}  ({pred.get('prediction_source', '')})", flush=True)

    mem = psutil.Process().memory_info()
    out["peak_mb"] = round(getattr(mem, "peak_wset", mem.rss) / 2**20)
    name = "speciesnet" if country != "none" else "speciesnet_nogeo"
    json.dump(raw, open(os.path.join(HERE, "results", f"{name}_raw.json"), "w"), indent=1)
    json.dump(out, open(os.path.join(HERE, "results", f"{name}.json"), "w"), indent=1)
    print(f"load {out['load_s']}s, peak {out['peak_mb']} MB")


if __name__ == "__main__":
    main()
