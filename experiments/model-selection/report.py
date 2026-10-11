"""Score results/*.json against data/labels.csv and write results/report_<threshold>[_smooth].md.

Usage: python report.py [min_species_score] [--smooth]   (default 0.5, no smoothing)

Decision rules (the same ones the real service would use):
- MegaDetector pipelines: a box counts for a species if the classifier's top label
  is one of our species (not a distractor) with probability >= min_species_score.
- oiv7: a box counts if its class maps to one of our species (conf >= 0.25 already).
- rekognition: a species is present if a mapped image-level label has confidence
  >= REK_MIN; its count is that label's number of boxes, or 1 if it has none.
- Colour: the most common colour of the species' boxes with colour score >= MIN_COLOUR.
"""

import csv
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SPECIES = ["fox", "rabbit", "feral pig", "cane toad", "cat", "dog"]
MIN_SCORE = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
SMOOTH = "--smooth" in sys.argv
MIN_COLOUR = 0.4
REK_MIN = 80
REK_MAP = {"Fox": "fox", "Red Fox": "fox", "Rabbit": "rabbit", "Hare": "rabbit",
           "Pig": "feral pig", "Boar": "feral pig", "Hog": "feral pig",
           "Toad": "cane toad", "Cat": "cat", "Dog": "dog"}
PIPELINES = ["md_clip", "md1000_clip", "md_bioclip", "oiv7", "rekognition", "speciesnet", "speciesnet_rules", "chain", "speciesnet_perbox", "chain_perbox"]


def parse_expected(text):
    """'cat=1;dog=1' -> ({'cat': 1, 'dog': 1}, edge); 'rabbit=many' -> {'rabbit': None}."""
    edge = text.endswith("?")
    tags = {}
    for part in filter(None, text.rstrip("?").split(";")):
        species, n = part.split("=")
        tags[species] = None if n == "many" else int(n)
    return tags, edge


def parse_colours(text, expected):
    """'grey' -> {'cat': {'grey'}}; 'cat orange/white; dog white/black' -> per species."""
    if not text:
        return {}
    if len(expected) == 1 and " " not in text:
        return {next(iter(expected)): {text}}
    out = {}
    for part in text.split(";"):
        species, colours = part.strip().split(" ")
        out[species] = set(colours.split("/"))
    return out


def predict(pipeline, boxes):
    """Return ({species: count}, {species: colour})."""
    counts, colours = Counter(), {}
    if pipeline == "rekognition":
        for label in boxes:
            species = REK_MAP.get(label["name"])
            if species and label["conf"] >= REK_MIN:
                counts[species] = max(counts[species], len(label["instances"]) or 1)
                for inst in label["instances"]:
                    if species in ("cat", "dog") and inst["colours"]:
                        colours.setdefault(species, inst["colours"][0])
        return dict(counts), colours
    labels = [b["label"] if pipeline == "oiv7" or b["score"] >= MIN_SCORE else "other" for b in boxes]
    if SMOOTH:
        labels = smooth(labels)
    colour_votes = {}
    for b, label in zip(boxes, labels):
        if label not in SPECIES:
            continue
        counts[label] += 1
        if b["label"] == label and "colour" in b and b["colour"][1] >= MIN_COLOUR:
            colour_votes.setdefault(label, Counter())[b["colour"][0]] += 1
    colours = {s: v.most_common(1)[0][0] for s, v in colour_votes.items()}
    return dict(counts), colours


def smooth(labels):
    """Image-level smoothing, a simplified version of MegaDetector's
    smooth_classification_results_image_level ("700 cows and one deer is 701 cows"):
    - "other" (low-confidence) boxes take the dominant label if it has >= 2 boxes
    - a label with only 1 box takes the dominant label if that has >= 4 boxes
    """
    votes = Counter(l for l in labels if l != "other")
    if not votes:
        return labels
    dominant, n = votes.most_common(1)[0]
    out = []
    for l in labels:
        if l == "other" and n >= 2:
            l = dominant
        elif l != dominant and votes[l] <= 1 and n >= 4:
            l = dominant
        out.append(l)
    return out


def fmt(tags):
    return ", ".join(f"{s} {n}" for s, n in sorted(tags.items())) or "-"


def main():
    labels = list(csv.DictReader(open(os.path.join(HERE, "data", "labels.csv"), encoding="utf-8")))
    results = {p: json.load(open(os.path.join(HERE, "results", p + ".json"))) for p in PIPELINES
               if os.path.exists(os.path.join(HERE, "results", p + ".json"))}

    summary = {p: Counter() for p in results}
    rows = []
    for row in labels:
        expected, edge = parse_expected(row["expected"])
        want_colours = parse_colours(row["colours"], expected)
        cells = [row["file"], fmt({s: ("many" if n is None else n) for s, n in expected.items()}) + (" ?" if edge else "")]
        for p, res in results.items():
            pred, colours = predict(p, res["images"][row["file"]]["boxes"])
            cell = fmt(pred)
            if colours:
                cell += " (" + ", ".join(f"{s} {c}" for s, c in colours.items()) + ")"
            if not edge:
                s = summary[p]
                s["images"] += 1
                ok_species = set(pred) == set(expected)
                s["species_ok"] += ok_species
                if not expected:
                    s["negatives"] += 1
                    s["false_alarm"] += bool(pred)
                for sp, n in expected.items():
                    if n is not None:
                        s["count_n"] += 1
                        s["count_ok"] += pred.get(sp, 0) == n
                        s["count_err"] += abs(pred.get(sp, 0) - n)
                for sp, ok in want_colours.items():
                    s["colour_n"] += 1
                    s["colour_ok"] += colours.get(sp) in ok
                if not ok_species:
                    cell = "**" + cell + "**"
            cells.append(cell)
        rows.append(cells)

    out = [f"# Model comparison (min_species_score={MIN_SCORE}, smooth={SMOOTH}, colour>={MIN_COLOUR}, Rekognition>={REK_MIN})", ""]
    out.append("| pipeline | species exactly right | count exactly right | count abs error (total) | false alarms on negatives | colour right | avg s/image | load s | peak MB |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for p, s in summary.items():
        times = [v["time_s"] for v in results[p]["images"].values()][1:]  # skip warm-up image
        out.append(f"| {p} | {s['species_ok']}/{s['images']} | {s['count_ok']}/{s['count_n']} | {s['count_err']} | "
                   f"{s['false_alarm']}/{s['negatives']} | {s['colour_ok']}/{s['colour_n']} | "
                   f"{sum(times) / len(times):.2f} | {results[p]['load_s']} | {results[p]['peak_mb']} |")
    out += ["", "Bold = species set wrong. `?` = edge case, not scored.", ""]
    out.append("| file | expected | " + " | ".join(results) + " |")
    out.append("|---|---|" + "---|" * len(results))
    out += ["| " + " | ".join(r) + " |" for r in rows]
    text = "\n".join(out) + "\n"
    open(os.path.join(HERE, "results", f"report_{MIN_SCORE}{'_smooth' if SMOOTH else ''}.md"), "w", encoding="utf-8").write(text)
    print(text)


if __name__ == "__main__":
    main()
