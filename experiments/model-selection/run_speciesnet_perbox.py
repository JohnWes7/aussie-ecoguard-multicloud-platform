"""Classify EVERY MegaDetector animal box with the SpeciesNet classifier.

Usage (SpeciesNet venv): python run_speciesnet_perbox.py

SpeciesNet itself classifies only the most confident box (classifier.py crops
bboxes[0]) and uses that label for the whole image. Here each box gets its own
top-5, mapped with the same Australia rules as speciesnet_rules.py. Boxes come
from results/speciesnet_raw.json (the same MDv5a run), so only the classifier runs.
Writes results/speciesnet_perbox.json for report.py.
"""

import json
import os
import time

from speciesnet import DEFAULT_MODEL, BBox, SpeciesNetClassifier, load_rgb_image

from speciesnet_rules import MD_THRESHOLD, to_species

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGES = os.path.join(HERE, "data", "images")


def main():
    raw = json.load(open(os.path.join(HERE, "results", "speciesnet_raw.json")))
    src = json.load(open(os.path.join(HERE, "results", "speciesnet.json")))
    clf = SpeciesNetClassifier(DEFAULT_MODEL, device="cpu")
    out = {**src, "pipeline": "speciesnet_perbox", "images": {}}

    for f, pred in raw.items():
        dets = [d for d in pred.get("detections", []) if d["category"] == "1" and d["conf"] >= MD_THRESHOLD]
        img = load_rgb_image(os.path.join(IMAGES, f))
        t = time.perf_counter()
        crops = [clf.preprocess(img, bboxes=[BBox(*d["bbox"])]) for d in dets]
        # Unique names: results are matched back by filepath, so a repeated name
        # would give every box the same result.
        results = clf.batch_predict([f"{f}#{i}" for i in range(len(crops))], crops) if crops else []
        elapsed = time.perf_counter() - t

        boxes = []
        for d, r in zip(dets, results):
            c = r["classifications"]
            totals = {}
            for label, score in zip(c["classes"][:5], c["scores"][:5]):
                species = to_species(label)
                if species:
                    totals[species] = totals.get(species, 0) + score
            best = max(totals, key=totals.get) if totals else "other"
            boxes.append({"det_conf": d["conf"], "bbox": d["bbox"], "label": best,
                          "score": round(totals.get(best, 0), 4),
                          "top1": c["classes"][0].split(";")[-1]})
        # MD time is the same as before; add the per-box classification time.
        md_time = src["images"][f]["time_s"]
        out["images"][f] = {"time_s": round(md_time + elapsed, 2), "classify_s": round(elapsed, 2), "boxes": boxes}
        print(f"{f:24s} {len(boxes):2d} boxes  classify {elapsed:5.2f}s  "
              + ", ".join(f"{b['label']} {b['score']:.2f}" for b in boxes[:6]), flush=True)

    json.dump(out, open(os.path.join(HERE, "results", "speciesnet_perbox.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
