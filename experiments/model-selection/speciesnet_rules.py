"""Re-decide SpeciesNet results with our own Australia-aware rule (no model re-run).

Usage: python speciesnet_rules.py   (reads results/speciesnet_raw.json)

SpeciesNet's ensemble rolls uncertain predictions up to "animal" / "reptile",
e.g. cane toad is its top class at only 0.16-0.2. We instead sum the top-5
classifier scores of every label that maps to one of our species, including
higher taxa that, in Australia, contain only that invasive species
(Felidae -> cat, Suidae -> feral pig, Leporidae -> rabbit, Bufonidae -> cane toad).
The best species and its summed score go on every MegaDetector animal box;
report.py then drops it if the score is below min_species_score.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MD_THRESHOLD = 0.2

# Matched against "class;order;family;genus;species" (the label without uuid
# and common name). Most specific first.
RULES = [
    ("canidae;vulpes;vulpes", "fox"),
    ("leporidae;", "rabbit"),
    ("suidae;", "feral pig"),
    ("bufonidae;", "cane toad"),
    ("felidae;", "cat"),
    ("canidae;canis;familiaris", "dog"),
    ("canidae;canis;;", "dog"),       # genus-level "canis species"
]


def to_species(label):
    taxo = ";".join(label.split(";")[1:6])
    for pattern, species in RULES:
        if pattern in taxo:
            return species
    return None


def main():
    raw = json.load(open(os.path.join(HERE, "results", "speciesnet_raw.json")))
    src = json.load(open(os.path.join(HERE, "results", "speciesnet.json")))
    out = {**src, "pipeline": "speciesnet_rules", "images": {}}
    for f, pred in raw.items():
        c = pred.get("classifications", {"classes": [], "scores": []})
        totals = {}
        for label, score in zip(c["classes"][:5], c["scores"][:5]):
            species = to_species(label)
            if species:
                totals[species] = totals.get(species, 0) + score
        best = max(totals, key=totals.get) if totals else "other"
        score = round(totals.get(best, 0), 4)
        boxes = [{"det_conf": d["conf"], "bbox": d["bbox"], "label": best, "score": score}
                 for d in pred.get("detections", [])
                 if d["category"] == "1" and d["conf"] >= MD_THRESHOLD]
        out["images"][f] = {"time_s": src["images"][f]["time_s"], "boxes": boxes}
        print(f"{f:24s} {best:10s} {score:.2f}  {len(boxes)} boxes")
    json.dump(out, open(os.path.join(HERE, "results", "speciesnet_rules.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
