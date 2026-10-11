"""Unit tests for the decision rules in inference/model.py. No models or torch needed.

The rules are read from the real infra/model/config.json, so these tests also
check the config the service is deployed with. The SpeciesNet labels below are
real ones seen in experiments/model-selection.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "inference" / "model.py"


@pytest.fixture(scope="module")
def model():
    spec = importlib.util.spec_from_file_location("inference_model", MODEL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def config(model):
    return model.load_config(ROOT / "infra" / "model")


UUID = "00000000-0000-0000-0000-000000000000"
RED_FOX = f"{UUID};mammalia;carnivora;canidae;vulpes;vulpes;red fox"
EURO_RABBIT = f"{UUID};mammalia;lagomorpha;leporidae;oryctolagus;cuniculus;european rabbit"
RABBIT_FAMILY = f"{UUID};mammalia;lagomorpha;leporidae;;;rabbit and hare family"
WILD_BOAR = f"{UUID};mammalia;artiodactyla;suidae;sus;scrofa;wild boar"
CANE_TOAD = f"{UUID};amphibia;anura;bufonidae;rhinella;marina;cane toad"
DOMESTIC_CAT = f"{UUID};mammalia;carnivora;felidae;felis;catus;domestic cat"
CAT_FAMILY = f"{UUID};mammalia;carnivora;felidae;;;cat family"
DOMESTIC_DOG = f"{UUID};mammalia;carnivora;canidae;canis;familiaris;domestic dog"
CANIS_GENUS = f"{UUID};mammalia;carnivora;canidae;canis;;canis species"
DOG_FAMILY = f"{UUID};mammalia;carnivora;canidae;;;canidae family"
KANGAROO = f"{UUID};mammalia;diprotodontia;macropodidae;macropus;giganteus;eastern grey kangaroo"
REPTILE = f"{UUID};reptilia;;;;;reptile"
BLANK = f"{UUID};;;;;;blank"


def test_taxonomy_key(model):
    assert model.taxonomy_key(RED_FOX) == "canidae;vulpes;vulpes"
    assert model.taxonomy_key(RABBIT_FAMILY) == "leporidae;;"
    assert model.taxonomy_key(BLANK) == ";;"
    assert model.taxonomy_key("not a speciesnet label") == ""


@pytest.mark.parametrize("label, species", [
    (RED_FOX, "fox"),
    (EURO_RABBIT, "rabbit"),
    (RABBIT_FAMILY, "rabbit"),      # family level is enough in Australia
    (WILD_BOAR, "feral pig"),
    (CANE_TOAD, "cane toad"),
    (DOMESTIC_CAT, "cat"),
    (CAT_FAMILY, "cat"),
    (DOMESTIC_DOG, "dog"),
    (CANIS_GENUS, "dog"),
    (DOG_FAMILY, None),             # canidae also includes the fox: too vague
    (KANGAROO, None),
    (REPTILE, None),
    (BLANK, None),
])
def test_to_species_with_the_deployed_rules(model, config, label, species):
    assert model.to_species(label, config["species_rules"]) == species


def test_every_rule_maps_to_one_of_our_species(config):
    assert {species for _, species in config["species_rules"]} <= set(config["species"])
    assert set(config["colour_for"]) <= set(config["species"])


def test_best_species_adds_up_scores_of_the_same_species(model, config):
    species, score = model.best_species(
        [RABBIT_FAMILY, EURO_RABBIT, KANGAROO], [0.49, 0.42, 0.05], config["species_rules"])
    assert species == "rabbit"
    assert score == pytest.approx(0.91)


def test_best_species_picks_the_highest_total(model, config):
    # cat 0.42 + 0.16 = 0.58 beats dog 0.14
    species, score = model.best_species(
        [DOMESTIC_CAT, CAT_FAMILY, DOMESTIC_DOG], [0.42, 0.16, 0.14], config["species_rules"])
    assert (species, score) == ("cat", pytest.approx(0.58))


def test_best_species_only_uses_the_top_5(model, config):
    classes = [KANGAROO] * 5 + [RED_FOX]
    scores = [0.1] * 5 + [0.5]
    assert model.best_species(classes, scores, config["species_rules"]) == (model.OTHER, 0.0)


def test_best_species_with_no_match_is_other(model, config):
    assert model.best_species([REPTILE, BLANK], [0.68, 0.2], config["species_rules"]) == (model.OTHER, 0.0)


def test_apply_threshold(model):
    named = [("fox", 0.9), ("fox", 0.3), ("cat", 0.29), (model.OTHER, 0.0)]
    assert model.apply_threshold(named, 0.3) == ["fox", "fox", model.OTHER, model.OTHER]


def test_smooth_unsure_boxes_follow_a_dominant_species(model):
    O = model.OTHER
    # 17 distant feral pigs: SpeciesNet was only sure about 4 of them
    labels = ["feral pig"] * 4 + [O] * 13
    assert model.smooth(labels) == ["feral pig"] * 17


def test_smooth_needs_two_sure_boxes_to_fill_in_others(model):
    O = model.OTHER
    assert model.smooth(["fox", O]) == ["fox", O]
    assert model.smooth(["fox", "fox", O]) == ["fox", "fox", "fox"]


def test_smooth_overwrites_a_single_odd_box_only_when_dominant_has_four(model):
    assert model.smooth(["rabbit"] * 4 + ["cat"]) == ["rabbit"] * 5
    assert model.smooth(["rabbit"] * 3 + ["cat"]) == ["rabbit"] * 3 + ["cat"]


def test_smooth_keeps_two_species_that_each_have_boxes(model):
    # a cat and a dog in one photo stay a cat and a dog
    assert model.smooth(["cat", "dog"]) == ["cat", "dog"]
    assert model.smooth(["rabbit"] * 5 + ["cat", "cat"]) == ["rabbit"] * 5 + ["cat", "cat"]


def test_smooth_with_nothing_named(model):
    O = model.OTHER
    assert model.smooth([O, O]) == [O, O]
    assert model.smooth([]) == []


def test_needs_fallback(model):
    assert model.needs_fallback([("cane toad", 0.16), ("cat", 0.29)], 0.3)
    assert not model.needs_fallback([("cane toad", 0.16), ("cat", 0.32)], 0.3)
    assert not model.needs_fallback([], 0.3)  # no animals: nothing to classify


def test_count_species_drops_other_and_distractors(model, config):
    labels = ["fox", "fox", "kangaroo", model.OTHER, "cat"]
    assert model.count_species(labels, config["species"]) == {"fox": 2, "cat": 1}
    assert model.count_species([], config["species"]) == {}


def test_file_colours(model):
    colours = [("orange", 0.8), ("black", 0.5), ("orange", 0.6), ("white", 0.2)]
    assert model.file_colours(colours, 0.4) == ["orange", "black"]
    assert model.file_colours([], 0.4) == []
