"""Run one model pipeline over data/images and save raw results.

Usage: python run.py <pipeline>
  md_clip     MegaDetector v5a boxes -> CLIP (openai/clip-vit-base-patch32) species + colour
  md_bioclip  MegaDetector v5a boxes -> BioCLIP species, CLIP colour
  md1000_clip MegaDetector v1000-redwood boxes -> CLIP species + colour
  oiv7        YOLOv8x trained on Open Images V7 (names + boxes), CLIP colour for cats/dogs
  rekognition Amazon Rekognition DetectLabels (labels, instances, dominant colours)

Writes results/<pipeline>.json with every box and its raw scores, so report.py can
try different thresholds without re-running the models.
"""

import csv
import json
import os
import sys
import time

import psutil
from PIL import Image, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGES = os.path.join(HERE, "data", "images")
LABELS = os.path.join(HERE, "data", "labels.csv")

SPECIES = ["fox", "rabbit", "feral pig", "cane toad", "cat", "dog"]
# Wrong answers on purpose: CLIP must pick one of the labels it is given, so
# without these a kangaroo would be forced into "fox" or "dog".
DISTRACTORS = ["kangaroo", "wallaby", "possum", "wombat", "koala", "bird",
               "deer", "cow", "sheep", "horse", "lizard"]
# Text used in the prompts (more specific than the tag names).
PROMPT_NAME = {"fox": "red fox", "feral pig": "wild pig", "cat": "domestic cat", "dog": "domestic dog"}
TEMPLATES = ["a camera trap photo of a {}.", "a photo of a {}.",
             "a blurry photo of a {}.", "a night infrared photo of a {}."]
COLOURS = ["orange", "black", "white", "grey", "brown"]
COLOUR_FOR = ["cat", "dog"]
MD_THRESHOLD = 0.2      # MegaDetector's recommended confidence threshold
YOLO_THRESHOLD = 0.25   # Ultralytics' default


def load_image(name):
    img = Image.open(os.path.join(IMAGES, name))
    return ImageOps.exif_transpose(img).convert("RGB")


def crop(img, box, pad=0.1):
    """box = (x, y, w, h) normalised; grow it by `pad` on each side for context."""
    W, H = img.size
    x, y, w, h = box
    x0, y0 = max(0, x - pad * w), max(0, y - pad * h)
    x1, y1 = min(1, x + w + pad * w), min(1, y + h + pad * h)
    return img.crop((x0 * W, y0 * H, x1 * W, y1 * H))


# ---------- zero-shot classifiers (CLIP / BioCLIP) ----------

class ZeroShot:
    """Score an image crop against fixed text labels.

    Each label's text embedding is the average over TEMPLATES (prompt
    ensembling, CLIP paper §3.1.4). Returns {label: probability}, summing to 1.
    """

    def __init__(self, kind):
        import torch
        self.torch = torch
        if kind == "clip":
            import open_clip
            self.model, _, self.preprocess = open_clip.create_model_and_transforms(
                "ViT-B-32", pretrained="openai")
            self.tokenizer = open_clip.get_tokenizer("ViT-B-32")
        else:  # bioclip
            import open_clip
            self.model, _, self.preprocess = open_clip.create_model_and_transforms(
                "hf-hub:imageomics/bioclip")
            self.tokenizer = open_clip.get_tokenizer("hf-hub:imageomics/bioclip")
        self.model.eval()
        self._cache = {}

    def _text_features(self, texts_per_label):
        key = json.dumps(texts_per_label)
        if key not in self._cache:
            torch = self.torch
            feats = []
            with torch.no_grad():
                for texts in texts_per_label:
                    f = self.model.encode_text(self.tokenizer(texts))
                    f = f / f.norm(dim=-1, keepdim=True)
                    f = f.mean(dim=0)
                    feats.append(f / f.norm())
            self._cache[key] = torch.stack(feats)
        return self._cache[key]

    def scores(self, image, labels, texts_per_label):
        torch = self.torch
        text = self._text_features(texts_per_label)
        with torch.no_grad():
            f = self.model.encode_image(self.preprocess(image).unsqueeze(0))
            f = f / f.norm(dim=-1, keepdim=True)
            probs = (100.0 * f @ text.T).softmax(dim=-1)[0]
        return {l: round(float(p), 4) for l, p in zip(labels, probs)}

    def species(self, image):
        labels = SPECIES + DISTRACTORS
        texts = [[t.format(PROMPT_NAME.get(l, l)) for t in TEMPLATES] for l in labels]
        return self.scores(image, labels, texts)

    def colour(self, image, species):
        texts = [[f"a photo of a {c} {species}."] for c in COLOURS]
        return self.scores(image, COLOURS, texts)


def top(scores):
    label = max(scores, key=scores.get)
    return label, scores[label]


# ---------- pipelines: each returns a list of box dicts for one image ----------

def make_megadetector_pipeline(model_name, classifier_kind):
    from megadetector.detection import run_detector
    detector = run_detector.load_detector(model_name, force_cpu=True)
    clf = ZeroShot(classifier_kind)
    colour_clf = clf if classifier_kind == "clip" else ZeroShot("clip")

    def run(img):
        result = detector.generate_detections_one_image(img, detection_threshold=MD_THRESHOLD)
        boxes = []
        for d in result["detections"]:
            if d["category"] != "1":  # 1 = animal, 2 = person, 3 = vehicle
                continue
            c = crop(img, d["bbox"])
            scores = clf.species(c)
            label, p = top(scores)
            box = {"det_conf": d["conf"], "bbox": d["bbox"], "label": label, "score": p,
                   "top3": sorted(scores.items(), key=lambda kv: -kv[1])[:3]}
            if label in COLOUR_FOR:
                box["colour"] = top(colour_clf.colour(c, label))
            boxes.append(box)
        return boxes
    return run


OIV7_MAP = {"Fox": "fox", "Rabbit": "rabbit", "Pig": "feral pig", "Frog": "cane toad",
            "Cat": "cat", "Dog": "dog"}


def make_oiv7_pipeline():
    from ultralytics import YOLO
    model = YOLO(os.path.join(HERE, "weights", "yolov8x-oiv7.pt"))
    colour_clf = ZeroShot("clip")

    def run(img):
        r = model.predict(img, conf=YOLO_THRESHOLD, device="cpu", verbose=False)[0]
        W, H = img.size
        boxes = []
        for cls, conf, xyxy in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist(), r.boxes.xyxy.tolist()):
            name = r.names[int(cls)]
            x0, y0, x1, y1 = xyxy
            bbox = [x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H]
            # Non-target classes (Kangaroo, Animal, Mammal...) are kept as "other:<name>".
            label = OIV7_MAP.get(name, "other:" + name)
            box = {"det_conf": round(conf, 4), "bbox": bbox, "label": label, "score": round(conf, 4)}
            if label in COLOUR_FOR:
                box["colour"] = top(colour_clf.colour(crop(img, bbox), label))
            boxes.append(box)
        return boxes
    return run


def make_rekognition_pipeline():
    import io
    import boto3
    client = boto3.client("rekognition", region_name="us-east-1")

    def run(img):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        resp = client.detect_labels(
            Image={"Bytes": buf.getvalue()}, MaxLabels=30, MinConfidence=50,
            Features=["GENERAL_LABELS", "IMAGE_PROPERTIES"],
            Settings={"ImageProperties": {"MaxDominantColors": 3}})
        labels = []
        for l in resp["Labels"]:
            labels.append({
                "name": l["Name"], "conf": round(l["Confidence"], 2),
                "parents": [p["Name"] for p in l.get("Parents", [])],
                "instances": [{"conf": round(i["Confidence"], 2),
                               "colours": [c["SimplifiedColor"] for c in i.get("DominantColors", [])]}
                              for i in l.get("Instances", [])],
            })
        return labels  # raw; report.py maps names to our species
    return run


PIPELINES = {
    "md_clip": lambda: make_megadetector_pipeline("MDV5A", "clip"),
    "md_bioclip": lambda: make_megadetector_pipeline("MDV5A", "bioclip"),
    "md1000_clip": lambda: make_megadetector_pipeline("MDV1000-REDWOOD", "clip"),
    "oiv7": make_oiv7_pipeline,
    "rekognition": make_rekognition_pipeline,
}


def main():
    name = sys.argv[1]
    proc = psutil.Process()
    t0 = time.perf_counter()
    run = PIPELINES[name]()
    load_s = time.perf_counter() - t0

    files = [row["file"] for row in csv.DictReader(open(LABELS, encoding="utf-8"))]
    out = {"pipeline": name, "load_s": round(load_s, 1), "images": {}}
    for f in files:
        img = load_image(f)
        t = time.perf_counter()
        boxes = run(img)
        out["images"][f] = {"time_s": round(time.perf_counter() - t, 2), "boxes": boxes}
        print(f"{f:28s} {out['images'][f]['time_s']:6.2f}s  {len(boxes)} boxes", flush=True)

    # Peak working set of this process on Windows (model + inference).
    mem = proc.memory_info()
    out["peak_mb"] = round(getattr(mem, "peak_wset", mem.rss) / 2**20)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", name + ".json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print(f"load {out['load_s']}s, peak {out['peak_mb']} MB")


if __name__ == "__main__":
    main()
