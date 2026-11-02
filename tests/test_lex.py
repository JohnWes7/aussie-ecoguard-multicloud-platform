"""Unit tests for the Lex chatbot: fulfilment in lambdas/search, and lambdas/chat."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from conftest import load_lambda

FOX_NEW = hashlib.sha256(b"fox new").hexdigest()
FOX_OLD = hashlib.sha256(b"fox old").hexdigest()
TWO_FOXES = hashlib.sha256(b"two foxes").hexdigest()
GINGER_CAT = hashlib.sha256(b"ginger cat").hexdigest()
BLACK_CAT = hashlib.sha256(b"black cat").hexdigest()
PIG = hashlib.sha256(b"pig").hexdigest()


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def put_file(table, sha, tags, attributes=(), created_at=None):
    table.put_item(Item={
        "file_id": sha, "sk": "META", "status": "READY", "file_url": f"uploads/{sha}",
        "thumb_url": f"thumbnails/{sha}.jpg", "file_type": "image", "filename": f"{sha[:4]}.jpg",
        "attributes": list(attributes), "created_at": created_at or ago(0),
    })
    for species, count in tags.items():
        table.put_item(Item={"file_id": sha, "sk": f"TAG#{species}", "tag": species, "tag_count": count})


@pytest.fixture
def search(aws):
    return load_lambda("search")


@pytest.fixture
def media(table):
    put_file(table, FOX_NEW, {"fox": 1}, created_at=ago(0))
    put_file(table, FOX_OLD, {"fox": 1}, created_at=ago(40))
    put_file(table, TWO_FOXES, {"fox": 2}, created_at=ago(3))
    put_file(table, GINGER_CAT, {"cat": 1}, ["orange"], ago(2))
    put_file(table, BLACK_CAT, {"cat": 1}, ["black"], ago(1))
    put_file(table, PIG, {"feral pig": 1}, created_at=ago(5))


def slot(value, interpreted=True):
    v = {"originalValue": value}
    if interpreted:
        v["interpretedValue"] = value
    return {"value": v}


def lex(search, slots=None, intent="FindSightings", transcript=""):
    event = {
        "inputTranscript": transcript,
        "invocationSource": "FulfillmentCodeHook",
        "sessionState": {"intent": {"name": intent, "slots": slots, "state": "InProgress"},
                         "sessionAttributes": {"results": "[stale]", "other": "kept"}},
    }
    reply = search.handler(event, None)
    state = reply["sessionState"]
    assert state["dialogAction"]["type"] == "Close"
    assert state["intent"]["state"] == "Fulfilled"
    assert state["intent"]["name"] == intent
    assert state["sessionAttributes"]["other"] == "kept"
    message = reply["messages"][0]
    assert message["contentType"] == "PlainText" and "<" not in message["content"]
    results = json.loads(state["sessionAttributes"].get("results", "[]"))
    return message["content"], results


def ids(results):
    return [r["file_id"] for r in results]


# ---------- species from messy slot values ----------

@pytest.mark.parametrize("text, species", [
    ("fox photos", "fox"), ("Foxes", "fox"), ("FOX", "fox"), ("all photos of foxes", "fox"),
    ("feral pigs", "feral pig"), ("pig", "feral pig"), ("cane toads", "cane toad"),
    ("wallaby photos", "kangaroo"), ("kittens", "cat"), ("ginger cat", "cat"), ("bunnies", "rabbit"),
    ("unicorn", None), ("photos", None), ("", None),
])
def test_species_from_text(search, text, species):
    assert search.species_from_text(text) == species


def test_fox_photos_slot_finds_foxes_newest_first(search, media):
    text, results = lex(search, {"Species": slot("fox photos")})
    assert ids(results) == [FOX_NEW, TWO_FOXES, FOX_OLD]
    assert "3 files with fox" in text


def test_results_are_s3_keys_for_the_chat_lambda(search, media):
    _, results = lex(search, {"Species": slot("fox")})
    assert results[0] == {
        "file_id": FOX_NEW, "filename": f"{FOX_NEW[:4]}.jpg", "file_type": "image",
        "created_at": results[0]["created_at"],
        "thumb_key": f"thumbnails/{FOX_NEW}.jpg", "file_key": f"uploads/{FOX_NEW}",
    }


def test_count_slot(search, media):
    _, results = lex(search, {"Species": slot("fox"), "Count": slot("2")})
    assert ids(results) == [TWO_FOXES]


def test_slot_without_interpreted_value_does_not_crash(search, media):
    text, results = lex(search, {"Species": slot("foxes", interpreted=False)})
    assert len(results) == 3


def test_unknown_animal_gets_a_friendly_answer(search, media):
    text, results = lex(search, {"Species": slot("unicorn")})
    assert results == [] and "unicorn" in text and "fox" in text


def test_no_slots_uses_the_sentence(search, media):
    text, results = lex(search, None, transcript="show me feral pigs")
    assert ids(results) == [PIG]


def test_no_match(search, media):
    text, results = lex(search, {"Species": slot("wombat")})
    assert results == [] and "didn't find any photos of wombat" in text


def test_previous_results_are_cleared(search, media):
    text, results = lex(search, {"Species": slot("wombat")})
    assert results == []


def test_database_error_gives_a_friendly_answer(search, media):
    with mock.patch.object(search.table, "query", side_effect=RuntimeError("boom")):
        event = {"sessionState": {"intent": {"name": "FindSightings", "slots": {"Species": slot("fox")}}}}
        reply = search.handler(event, None)
    assert "went wrong" in reply["messages"][0]["content"]
    assert reply["sessionState"]["intent"]["state"] == "Fulfilled"


# ---------- dates ----------

def test_date_slot_day(search, media):
    today = datetime.now(search.LOCAL_TZ).strftime("%Y-%m-%d")
    text, results = lex(search, {"Species": slot("fox"), "Date": slot(today)})
    assert ids(results) == [FOX_NEW] and " on " in text


def test_yesterday_in_the_sentence(search, table):
    yesterday = datetime.now(search.LOCAL_TZ).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)
    put_file(table, FOX_NEW, {"fox": 1}, created_at=yesterday.isoformat())
    put_file(table, FOX_OLD, {"fox": 1}, created_at=(yesterday - timedelta(days=1)).isoformat())
    text, results = lex(search, {"Species": slot("foxes")}, transcript="show me all photos of foxes spotted yesterday")
    assert ids(results) == [FOX_NEW] and "yesterday" in text


def test_last_n_days(search, media):
    _, results = lex(search, {"Species": slot("fox")}, transcript="foxes in the last 7 days")
    assert ids(results) == [FOX_NEW, TWO_FOXES]


@pytest.mark.parametrize("value, days", [("2026-11-01", 1), ("2026-W44", 7), ("2026-W44-WE", 2), ("2026-10", 31)])
def test_amazon_date_formats(search, value, days):
    start, end, label = search.date_range(value)
    assert (end - start).days == days and label


def test_bad_date_is_ignored(search, media):
    _, results = lex(search, {"Species": slot("fox"), "Date": slot("someday")})
    assert len(results) == 3


# ---------- lost pet ----------

def test_lost_ginger_cat_from_the_sentence(search, media):
    text, results = lex(search, {"Species": slot("lost ginger cat")}, transcript="help me find a lost ginger cat")
    assert ids(results) == [GINGER_CAT] and "orange cat" in text and "your pet" in text


def test_lost_pet_intent_with_colour_slot(search, media):
    _, results = lex(search, {"Species": slot("cat"), "Colour": slot("black")}, intent="FindLostPet")
    assert ids(results) == [BLACK_CAT]


def test_lost_pet_without_colour_shows_every_cat(search, media):
    _, results = lex(search, {"Species": slot("cat")}, intent="FindLostPet")
    assert ids(results) == [BLACK_CAT, GINGER_CAT]


def test_lost_pet_none_found(search, media):
    text, results = lex(search, {"Species": slot("dog")}, intent="FindLostPet")
    assert results == [] and "Notifications" in text


def test_results_are_capped(search, table):
    for i in range(15):
        put_file(table, hashlib.sha256(str(i).encode()).hexdigest(), {"fox": 1}, created_at=ago(i))
    text, results = lex(search, {"Species": slot("fox")})
    assert len(results) == search.LEX_MAX_RESULTS and "15 files" in text and "newest 12" in text


# ---------- chat Lambda (POST /chat) ----------

@pytest.fixture
def chat(aws, monkeypatch):
    monkeypatch.setenv("BOT_ID", "BOT")
    monkeypatch.setenv("BOT_ALIAS_ID", "ALIAS")
    return load_lambda("chat")


def post(chat, body, raw=False, sub="user-1"):
    event = {"body": body if raw else json.dumps(body),
             "requestContext": {"authorizer": {"claims": {"sub": sub}}}}
    response = chat.handler(event, None)
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"
    return response["statusCode"], json.loads(response["body"])


@pytest.mark.parametrize("body, raw", [(None, True), ("{", True), ("[]", True), ({}, False),
                                       ({"text": "  "}, False), ({"text": 5}, False), ({"text": "x" * 2000}, False)])
def test_chat_bad_requests(chat, body, raw):
    status, reply = post(chat, body, raw)
    assert status == 400 and reply["error"]


def test_chat_signs_the_results(chat):
    lex_reply = {
        "messages": [{"content": "I found 1 file with fox."}],
        "sessionState": {"intent": {"name": "FindSightings"}, "sessionAttributes": {"results": json.dumps([{
            "file_id": FOX_NEW, "filename": "f.jpg", "file_type": "image", "created_at": "2026-11-01",
            "thumb_key": f"thumbnails/{FOX_NEW}.jpg", "file_key": f"uploads/{FOX_NEW}",
        }])}},
    }
    with mock.patch.object(chat.lex_client, "recognize_text", return_value=lex_reply) as call:
        status, reply = post(chat, {"text": "show me foxes", "sessionId": "tab1"})
    assert status == 200 and reply["message"] == "I found 1 file with fox." and reply["intent"] == "FindSightings"
    [result] = reply["results"]
    assert f"thumbnails/{FOX_NEW}.jpg" in result["thumbnail_url"] and "Signature" in result["thumbnail_url"]
    assert f"uploads/{FOX_NEW}" in result["file_url"]
    kwargs = call.call_args.kwargs
    assert kwargs["sessionId"] == "user-1:tab1" and kwargs["botId"] == "BOT" and kwargs["botAliasId"] == "ALIAS"
    assert kwargs["requestAttributes"]["x-amz-lex:time-zone"] == "Australia/Melbourne"


def test_chat_joins_messages_and_handles_no_results(chat):
    lex_reply = {"messages": [{"content": "Hi."}, {"content": "What animal?"}], "sessionState": {}}
    with mock.patch.object(chat.lex_client, "recognize_text", return_value=lex_reply):
        status, reply = post(chat, {"text": "hello"})
    assert reply["message"] == "Hi.\nWhat animal?" and reply["results"] == []


def test_chat_fallback_when_lex_says_nothing(chat):
    with mock.patch.object(chat.lex_client, "recognize_text", return_value={"sessionState": {}}):
        _, reply = post(chat, {"text": "asdf"})
    assert "didn't understand" in reply["message"]


def test_chat_bad_results_attribute_is_ignored(chat):
    lex_reply = {"messages": [{"content": "x"}], "sessionState": {"sessionAttributes": {"results": "[stale]"}}}
    with mock.patch.object(chat.lex_client, "recognize_text", return_value=lex_reply):
        _, reply = post(chat, {"text": "fox"})
    assert reply["results"] == []


def test_chat_lex_error_hides_details(chat):
    with mock.patch.object(chat.lex_client, "recognize_text", side_effect=Exception("AccessDenied arn:aws:lex:secret")):
        status, reply = post(chat, {"text": "fox"})
    assert status == 502 and "arn" not in json.dumps(reply)


def test_chat_bad_session_id_is_cleaned(chat):
    with mock.patch.object(chat.lex_client, "recognize_text", return_value={"sessionState": {}}) as call:
        post(chat, {"text": "fox", "sessionId": "a b/c"}, sub="")
    assert call.call_args.kwargs["sessionId"] == "abc"
