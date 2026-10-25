"""Tests for lambdas/notify/email_text.py (wording of the notification email).

The module makes no AWS calls, so no fake AWS is needed.
"""

import importlib.util
from decimal import Decimal

import pytest

from conftest import LAMBDAS_DIR

_spec = importlib.util.spec_from_file_location("notify_email_text", LAMBDAS_DIR / "notify" / "email_text.py")
email_text = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(email_text)

SHA = "a" * 64
SITE = "https://aussie-ecoguard-web-example.s3.amazonaws.com/index.html"


def test_subject_one_species():
    assert email_text.build_subject({"fox": 1}) == "Aussie EcoGuard: 1 fox tagged"


def test_subject_plurals_and_order():
    # most animals first, then by name
    subject = email_text.build_subject({"cat": 1, "fox": 2, "deer": 2})
    assert subject == "Aussie EcoGuard: 2 deer, 2 foxes and 1 cat tagged"


@pytest.mark.parametrize("species, expected", [
    ("fox", "foxes"), ("deer", "deer"), ("cattle", "cattle"),
    ("cane toad", "cane toads"), ("feral pig", "feral pigs"), ("other animal", "other animals"),
])
def test_plural(species, expected):
    assert email_text.plural(species) == expected


def test_subject_many_species_is_shortened():
    tags = {"fox": 3, "rabbit": 2, "cat": 1, "dog": 1, "emu": 1}
    subject = email_text.build_subject(tags)
    assert subject == "Aussie EcoGuard: 3 foxes, 2 rabbits and 3 more species tagged"
    assert len(subject) < 100


def test_subject_never_reaches_100_characters_or_has_line_breaks():
    subject = email_text.build_subject({"x" * 80 + "\nline": 1, "y" * 80: 2})
    assert len(subject) < 100
    assert "\n" not in subject and "\r" not in subject


def test_counts_from_a_stream_record():
    # DynamoDB numbers arrive as Decimal (boto3) or text (raw stream record)
    assert email_text.build_subject({"fox": Decimal("2"), "cat": "1"}) == "Aussie EcoGuard: 2 foxes and 1 cat tagged"


def test_body_with_all_details():
    msg = email_text.build_email(SHA, {"fox": 2, "cane toad": 1}, SITE,
                                 filename="trailcam_01.jpg", file_type="image",
                                 created_at="2026-10-04T09:30:12.345678+00:00")
    body = msg["Message"]
    assert body.startswith("Hello,\n\nAn image in Aussie EcoGuard has new tags, including at least one species\nyou subscribed to:\n")
    assert "  fox        2\n  cane toad  1" in body
    assert "File:      trailcam_01.jpg" in body
    assert "Uploaded:  4 Oct 2026, 09:30 UTC" in body
    assert f"{SITE}#/file/{SHA}" in body
    assert "unsubscribe link below." in body
    assert max(len(line) for line in body.splitlines() if "http" not in line) <= 72


def test_body_without_meta_details():
    body = email_text.build_email(SHA, {"koala": 1}, SITE)["Message"]
    assert "A file in Aussie EcoGuard has a new tag that you subscribed to:" in body
    assert "File:" not in body and "Uploaded:" not in body
    assert "View the file and all of its tags:" in body


def test_video_wording():
    body = email_text.build_email(SHA, {"kangaroo": 4}, SITE, file_type="video")["Message"]
    assert body.startswith("Hello,\n\nA video in Aussie EcoGuard")
    assert "View the video and all of its tags:" in body


def test_bad_date_is_left_out():
    body = email_text.build_email(SHA, {"fox": 1}, SITE, created_at="yesterday")["Message"]
    assert "Uploaded:" not in body


@pytest.mark.parametrize("site", [SITE, SITE + "#/library", SITE + "#"])
def test_file_link_replaces_any_fragment(site):
    assert email_text.file_link(site, SHA) == f"{SITE}#/file/{SHA}"


def test_no_tags_is_an_error():
    with pytest.raises(ValueError):
        email_text.build_email(SHA, {}, SITE)
