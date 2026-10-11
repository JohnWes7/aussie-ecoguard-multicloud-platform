"""Species detection: MegaDetector -> SpeciesNet -> CLIP fallback.

The pipeline was chosen in experiments/model-selection (see its README):
  1. MegaDetector finds the animals (boxes with conf >= detection_threshold).
  2. The SpeciesNet classifier names every box. Its labels are mapped to our
     species with species_rules (Australia-specific, see below).
  3. Image-level smoothing: boxes it is unsure about follow the image's
     dominant species ("700 cows and one deer is really 701 cows").
  4. If no box reaches species_threshold, CLIP zero-shot names the boxes
     instead (model chaining, Spec 4.1).
  5. CLIP gives the colour of the species in colour_for (cats and dogs).

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
    """SpeciesNet label -> "family;genus;species".

    Labels look like "<uuid>;mammalia;carnivora;canidae;vulpes;vulpes;red fox".
    Higher-level labels leave the lower fields empty, e.g. the family-level
    "<uuid>;mammalia;lagomorpha;leporidae;;;rabbit and hare family".
    """
    parts = label.lower().split(";")
    return ";".join(parts[3:6]) if len(parts) >= 7 else ""


def to_species(label, rules):
    """Map a SpeciesNet label to one of our species, or None.

    rules is a list of [prefix, species], first match wins. A family prefix
    such as "felidae;" also matches every genus and species in that family.
    That is safe because, in Australia, each of these families contains only
    the one invasive species we track.
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


def count_species(labels, species):
    """{"fox": 2} from the final box labels, keeping only our species
    (OTHER and CLIP distractors such as "kangaroo" are dropped)."""
    return dict(Counter(label for label in labels if label in species))


def file_colours(box_colours, threshold):
    """Colour attributes for the file from [(colour, score)] of each pet box.

    Low-confidence colours are ignored; each colour is listed once, most
    common first, e.g. ["orange", "black"] for an orange cat and a black dog.
    """
    votes = Counter(colour for colour, score in box_colours if score >= threshold)
    return [colour for colour, _ in votes.most_common()]
