"""Species detection: MegaDetector -> SpeciesNet -> CLIP fallback.

The pipeline was chosen in experiments/model-selection (see its README):
  1. MegaDetector finds the animals (boxes with conf >= detection_threshold).
  2. The SpeciesNet classifier names every box. Its labels (2000+ taxa) are
     mapped to our tag list with species_rules (see to_species).
  3. Image-level smoothing: boxes it is unsure about follow the image's
     dominant species ("700 cows and one deer is really 701 cows").
  4. If no box reaches species_threshold, CLIP zero-shot names the boxes
     instead (model chaining, Spec 4.1).
  5. Animals that are still unnamed are counted as unknown_tag ("other animal").
  6. CLIP gives the colour of the species in colour_for (cats and dogs).

Model files, thresholds and labels all come from <MODEL_DIR>/config.json,
so a new model or species list needs no code change (Spec 4.1).

The functions below are plain Python (no torch), so the decision rules can be
unit-tested without the models.
"""

import json
import os
from collections import Counter

OTHER = "other"  # a box we could not name with enough confidence


def load_config(model_dir):
    with open(os.path.join(model_dir, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def taxonomy_key(label):
    """SpeciesNet label -> "class;order;family;genus;species".

    Labels look like "<uuid>;mammalia;carnivora;canidae;vulpes;vulpes;red fox".
    Higher-level labels leave the lower fields empty, e.g. the family-level
    "<uuid>;mammalia;lagomorpha;leporidae;;;rabbit and hare family", and
    "blank" (no animal) has every field empty: ";;;;".
    """
    parts = label.lower().split(";")
    return ";".join(parts[1:6]) if len(parts) >= 7 else ""


def to_species(label, rules):
    """Map a SpeciesNet label to one of our tags, or None.

    rules is a list of [prefix, tag], first match wins, so specific rules
    come before general ones (cane toad before frog, emu before bird).
    A family prefix such as "mammalia;carnivora;felidae;" matches every genus
    and species in that family; in Australia the felids are all cats.
    """
    key = taxonomy_key(label)
    for prefix, species in rules:
        if key.startswith(prefix):
            return species
    return None


def best_species(classes, scores, rules):
    """Pick the species for one box from SpeciesNet's top-5 classes.

    Scores of all classes that map to the same species are added up, e.g.
    "european rabbit" 0.42 + "rabbit and hare family" 0.49 -> rabbit 0.91.
    Returns (species, score), or (OTHER, 0.0) if none of the classes map.
    """
    totals = Counter()
    for label, score in zip(classes[:5], scores[:5]):
        species = to_species(label, rules)
        if species:
            totals[species] += score
    if not totals:
        return OTHER, 0.0
    species, score = totals.most_common(1)[0]
    return species, score


def apply_threshold(named, threshold):
    """[(label, score)] -> [label], with OTHER for scores below threshold."""
    return [label if score >= threshold else OTHER for label, score in named]


def smooth(labels):
    """Image-level smoothing, a simplified version of MegaDetector's
    smooth_classification_results_image_level:
      - OTHER boxes take the dominant label if it has >= 2 boxes
      - a label with a single box takes the dominant label if that has >= 4
    """
    votes = Counter(label for label in labels if label != OTHER)
    if not votes:
        return list(labels)
    dominant, n = votes.most_common(1)[0]
    smoothed = []
    for label in labels:
        if label == OTHER and n >= 2:
            label = dominant
        elif label != dominant and votes[label] == 1 and n >= 4:
            label = dominant
        smoothed.append(label)
    return smoothed


def needs_fallback(named, threshold):
    """True if SpeciesNet named no box with confidence >= threshold.

    Then the image goes to CLIP instead. An image with no boxes at all has
    nothing to classify, so it never falls back.
    """
    return bool(named) and max(score for _, score in named) < threshold


def count_species(labels, species, unknown_tag):
    """{"fox": 2, "other animal": 1} from the final box labels.

    Boxes nobody could name (OTHER) are animals MegaDetector found but no
    model recognised, so they count as unknown_tag. Labels that are not in
    the species list, such as "not an animal" (blank, vehicle, human), are
    dropped.
    """
    counts = Counter()
    for label in labels:
        if label == OTHER:
            counts[unknown_tag] += 1
        elif label in species:
            counts[label] += 1
    return dict(counts)


def file_colours(box_colours, threshold):
    """Colour attributes for the file from [(colour, score)] of each pet box.

    Low-confidence colours are ignored; each colour is listed once, most
    common first, e.g. ["orange", "black"] for an orange cat and a black dog.
    """
    votes = Counter(colour for colour, score in box_colours if score >= threshold)
    return [colour for colour, _ in votes.most_common()]


def crop(img, bbox, pad=0.1):
    """Cut a box out of a PIL image for CLIP.

    bbox is MegaDetector's [x, y, width, height], relative to the image size
    (0-1). The box is grown by `pad` on each side so CLIP sees some context.
    """
    W, H = img.size
    x, y, w, h = bbox
    x0, y0 = max(0, x - pad * w), max(0, y - pad * h)
    x1, y1 = min(1, x + w + pad * w), min(1, y + h + pad * h)
    return img.crop((x0 * W, y0 * H, x1 * W, y1 * H))


class Pipeline:
    """Loads the three models once and runs them on one image at a time."""

    def __init__(self, model_dir):
        # Heavy imports live here so the rules above can be tested without torch.
        import open_clip
        import torch
        from speciesnet import BBox, SpeciesNetClassifier, SpeciesNetDetector

        self.torch = torch
        self.BBox = BBox
        self.config = c = load_config(model_dir)

        # Both SpeciesNet parts load from the same local folder (MegaDetector
        # weights included), so nothing is downloaded at startup.
        speciesnet_dir = os.path.join(model_dir, c["speciesnet_dir"])
        self.detector = SpeciesNetDetector(speciesnet_dir)
        self.classifier = SpeciesNetClassifier(speciesnet_dir, device="cpu")

        self.clip, _, self.clip_preprocess = open_clip.create_model_and_transforms(
            c["clip_arch"], pretrained=os.path.join(model_dir, c["clip_weights"]))
        self.clip.eval()
        tokenizer = open_clip.get_tokenizer(c["clip_arch"])

        # The text side of CLIP never changes, so encode all prompts once.
        names = c["clip_prompt_names"]
        self.species_text = self._encode_texts(
            [[t.format(names.get(s, s)) for t in c["clip_templates"]] for s in c["species"]], tokenizer)
        self.colour_text = {
            s: self._encode_texts([[f"a photo of a {colour} {s}."] for colour in c["colours"]], tokenizer)
            for s in c["colour_for"]
        }

    def detect(self, img):
        """PIL RGB image -> ({"fox": 2, "other animal": 1}, ["orange"])."""
        c = self.config
        boxes = self.find_animals(img)
        named = self.name_with_speciesnet(img, boxes)
        if needs_fallback(named, c["species_threshold"]):
            named = [self.clip_top(crop(img, b), self.species_text, c["species"]) for b in boxes]
        labels = smooth(apply_threshold(named, c["species_threshold"]))
        tags = count_species(labels, c["species"], c["unknown_tag"])

        pet_colours = [self.clip_top(crop(img, b), self.colour_text[label], c["colours"])
                       for b, label in zip(boxes, labels) if label in self.colour_text]
        return tags, file_colours(pet_colours, c["colour_threshold"])

    def find_animals(self, img):
        """MegaDetector: [x, y, w, h] of every animal box above the threshold."""
        result = self.detector.predict("image", self.detector.preprocess(img))
        return [d["bbox"] for d in result.get("detections", [])
                if d["category"] == "1" and d["conf"] >= self.config["detection_threshold"]]

    def name_with_speciesnet(self, img, boxes):
        """SpeciesNet classifier on every box -> [(tag, score)]."""
        crops = [self.classifier.preprocess(img, bboxes=[self.BBox(*b)]) for b in boxes]
        if not crops:
            return []
        # batch_predict matches results back by name, so every box needs its own.
        results = self.classifier.batch_predict([f"box{i}" for i in range(len(crops))], crops)
        named = []
        for r in results:
            top5 = r.get("classifications", {"classes": [], "scores": []})
            named.append(best_species(top5["classes"], top5["scores"], self.config["species_rules"]))
        return named

    def clip_top(self, image, text_features, labels):
        """CLIP zero-shot: the best of `labels` for this image -> (label, probability)."""
        torch = self.torch
        with torch.no_grad():
            f = self.clip.encode_image(self.clip_preprocess(image).unsqueeze(0))
            f = f / f.norm(dim=-1, keepdim=True)
            probs = (100.0 * f @ text_features.T).softmax(dim=-1)[0]
        best = int(probs.argmax())
        return labels[best], float(probs[best])

    def _encode_texts(self, texts_per_label, tokenizer):
        """One embedding per label: the average over its prompts (prompt ensembling)."""
        torch = self.torch
        features = []
        with torch.no_grad():
            for texts in texts_per_label:
                f = self.clip.encode_text(tokenizer(texts))
                f = f / f.norm(dim=-1, keepdim=True)
                f = f.mean(dim=0)
                features.append(f / f.norm())
        return torch.stack(features)
