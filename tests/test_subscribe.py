"""Unit tests for lambdas/subscribe (POST /subscriptions)."""

import json
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError

from conftest import load_lambda

CONFIG = Path(__file__).resolve().parent.parent / "infra" / "model" / "config.json"
EMAIL = "ranger@example.com"


@pytest.fixture
def sns(aws):
    return boto3.client("sns")


@pytest.fixture
def topic_arn(sns, monkeypatch):
    arn = sns.create_topic(Name="test-notifications")["TopicArn"]
    monkeypatch.setenv("TOPIC_ARN", arn)
    return arn


@pytest.fixture
def app(topic_arn):
    return load_lambda("subscribe")


def call(app, body, email=EMAIL):
    """Invoke the handler the way API Gateway's proxy integration does after
    the Cognito authorizer has accepted the token."""
    claims = {"sub": "1234-abcd", "email": email} if email else {}
    event = {
        "body": body if isinstance(body, str) else json.dumps(body),
        "requestContext": {"authorizer": {"claims": claims}},
    }
    response = app.handler(event, None)
    return response["statusCode"], json.loads(response["body"])


def subscriptions(sns, topic_arn):
    return sns.list_subscriptions_by_topic(TopicArn=topic_arn)["Subscriptions"]


def filter_policy(sns, arn):
    attributes = sns.get_subscription_attributes(SubscriptionArn=arn)["Attributes"]
    return json.loads(attributes["FilterPolicy"])


def test_new_subscription_uses_email_from_token(app, sns, topic_arn):
    status, body = call(app, {"tags": ["fox", "cane toad"]})

    assert status == 200
    assert body == {"email": EMAIL, "tags": ["cane toad", "fox"], "pending": True}
    [sub] = subscriptions(sns, topic_arn)
    assert sub["Protocol"] == "email"
    assert sub["Endpoint"] == EMAIL


def test_filter_policy_matches_species_attribute(app, sns, topic_arn):
    call(app, {"tags": ["fox", "cane toad"]})

    [sub] = subscriptions(sns, topic_arn)
    assert filter_policy(sns, sub["SubscriptionArn"]) == {"species": ["cane toad", "fox"]}
    attributes = sns.get_subscription_attributes(SubscriptionArn=sub["SubscriptionArn"])["Attributes"]
    assert attributes["FilterPolicyScope"] == "MessageAttributes"


def test_email_in_body_is_ignored(app, sns, topic_arn):
    call(app, {"tags": ["fox"], "email": "someone-else@example.com"})

    assert [s["Endpoint"] for s in subscriptions(sns, topic_arn)] == [EMAIL]


def test_tags_are_cleaned_and_deduplicated(app):
    _, body = call(app, {"tags": [" Fox", "fox", "CANE TOAD"]})

    assert body["tags"] == ["cane toad", "fox"]


def test_confirmed_subscriber_changes_species_without_new_subscription(app, sns, topic_arn):
    # moto confirms email subscriptions at once, like a user who clicked the link.
    call(app, {"tags": ["fox"]})

    status, body = call(app, {"tags": ["cat", "dog"]})

    assert status == 200
    assert body["pending"] is False
    [sub] = subscriptions(sns, topic_arn)
    assert filter_policy(sns, sub["SubscriptionArn"]) == {"species": ["cat", "dog"]}


def test_other_users_keep_their_own_subscription(app, sns, topic_arn):
    call(app, {"tags": ["fox"]})
    call(app, {"tags": ["cat"]}, email="owner@example.com")

    policies = {s["Endpoint"]: filter_policy(sns, s["SubscriptionArn"]) for s in subscriptions(sns, topic_arn)}
    assert policies == {EMAIL: {"species": ["fox"]}, "owner@example.com": {"species": ["cat"]}}


def pending_topic(app, monkeypatch, subscribe):
    """Make the topic report EMAIL as pending, as real SNS does before the user confirms."""
    pages = [{"Subscriptions": [{
        "SubscriptionArn": "PendingConfirmation", "Protocol": "email", "Endpoint": EMAIL,
    }]}]
    paginator = type("Paginator", (), {"paginate": lambda self, **kw: pages})()
    monkeypatch.setattr(app.sns, "get_paginator", lambda name: paginator)
    monkeypatch.setattr(app.sns, "subscribe", subscribe)


def test_pending_subscriber_with_same_species_gets_email_again(app, monkeypatch):
    calls = []
    pending_topic(app, monkeypatch, lambda **kw: calls.append(kw) or {"SubscriptionArn": "arn"})

    status, body = call(app, {"tags": ["fox"]})

    assert status == 200
    assert body["pending"] is True
    assert calls[0]["Endpoint"] == EMAIL


def test_pending_subscriber_with_other_species_gets_409(app, monkeypatch):
    def subscribe(**kw):
        raise ClientError({"Error": {"Code": "InvalidParameter", "Message":
                          "Invalid parameter: Attributes Reason: Subscription already exists with different attributes"}},
                          "Subscribe")
    pending_topic(app, monkeypatch, subscribe)

    status, body = call(app, {"tags": ["cat"]})

    assert status == 409
    assert "confirm" in body["error"].lower()


def test_other_sns_errors_are_not_hidden(app, monkeypatch):
    def subscribe(**kw):
        raise ClientError({"Error": {"Code": "AuthorizationError", "Message": "no"}}, "Subscribe")
    monkeypatch.setattr(app.sns, "subscribe", subscribe)

    with pytest.raises(ClientError):
        call(app, {"tags": ["fox"]})


@pytest.mark.parametrize("body", [
    {},                               # no tags
    {"tags": []},                     # empty
    {"tags": "fox"},                  # not a list
    {"tags": ["fox", 3]},             # not all text
    {"tags": ["fox", "dragon"]},      # unknown species
    ["fox"],                          # not an object
])
def test_invalid_request_returns_400(app, sns, topic_arn, body):
    status, _ = call(app, body)

    assert status == 400
    assert subscriptions(sns, topic_arn) == []


def test_body_that_is_not_json_returns_400(app):
    status, _ = call(app, "{")
    assert status == 400


def test_request_without_email_claim_returns_401(app, sns, topic_arn):
    status, _ = call(app, {"tags": ["fox"]}, email=None)

    assert status == 401
    assert subscriptions(sns, topic_arn) == []


def test_species_match_model_config(app):
    config = json.loads(CONFIG.read_text())
    assert app.SPECIES == set(config["species"]) | {config["unknown_tag"]}


def test_responses_allow_cors(app):
    response = app.handler({"body": "{}", "requestContext": {"authorizer": {"claims": {"email": EMAIL}}}}, None)
    assert response["headers"]["Access-Control-Allow-Origin"] == "*"
