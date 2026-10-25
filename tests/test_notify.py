"""Unit tests for lambdas/notify (DynamoDB Stream -> one SNS message per file).

SNS is moto's fake. Subscribers are SQS queues with the same filter policy the
subscribe Lambda sets on email subscriptions, so the tests can see who would
have received an email.
"""

import json
import sys

import boto3
import pytest

from conftest import LAMBDAS_DIR, load_lambda

SHA_A = "a" * 64
SHA_B = "b" * 64
SITE = "https://aussie-ecoguard-web-example.s3.amazonaws.com/index.html"

# Lambda puts the function's folder on sys.path, load_lambda() does not.
sys.path.insert(0, str(LAMBDAS_DIR / "notify"))


@pytest.fixture
def topic_arn(aws, monkeypatch):
    arn = boto3.client("sns").create_topic(Name="test-notifications")["TopicArn"]
    monkeypatch.setenv("TOPIC_ARN", arn)
    monkeypatch.setenv("SITE_URL", SITE)
    return arn


@pytest.fixture
def app(topic_arn):
    return load_lambda("notify")


@pytest.fixture
def subscriber(topic_arn):
    """subscriber(["fox"]) -> function returning the messages that subscriber got."""
    sns, sqs = boto3.client("sns"), boto3.client("sqs")

    def make(species):
        url = sqs.create_queue(QueueName="sub-" + "-".join(s.replace(" ", "_") for s in species))["QueueUrl"]
        arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        sns.subscribe(
            TopicArn=topic_arn, Protocol="sqs", Endpoint=arn,
            Attributes={"FilterPolicy": json.dumps({"species": species}),
                        "FilterPolicyScope": "MessageAttributes"},
        )

        def received():
            messages = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10).get("Messages", [])
            return [json.loads(m["Body"]) for m in messages]
        return received
    return make


def tag_record(file_id, species, count=1, event="INSERT", sequence="100"):
    """A stream record as Lambda delivers it (NEW_IMAGE view)."""
    return {
        "eventName": event,
        "dynamodb": {
            "Keys": {"file_id": {"S": file_id}, "sk": {"S": f"TAG#{species}"}},
            "NewImage": {
                "file_id": {"S": file_id},
                "sk": {"S": f"TAG#{species}"},
                "tag": {"S": species},
                "tag_count": {"N": str(count)},
            },
            "SequenceNumber": sequence,
        },
    }


def meta_record(file_id, event="MODIFY"):
    return {
        "eventName": event,
        "dynamodb": {
            "NewImage": {"file_id": {"S": file_id}, "sk": {"S": "META"}, "status": {"S": "READY"}},
            "SequenceNumber": "1",
        },
    }


def put_meta(table, file_id, **fields):
    table.put_item(Item={"file_id": file_id, "sk": "META", "status": "READY", **fields})


def test_two_new_tags_on_one_file_send_one_message(app, subscriber, table):
    fox_fan = subscriber(["fox"])
    put_meta(table, SHA_A, filename="gate.jpg", file_type="image", created_at="2026-10-14T09:30:00Z")

    result = app.handler({"Records": [tag_record(SHA_A, "fox", 2, sequence="1"),
                                      tag_record(SHA_A, "cat", 1, sequence="2")]}, None)

    assert result == {"batchItemFailures": []}
    [message] = fox_fan()
    assert message["Subject"] == "Aussie EcoGuard: 2 foxes and 1 cat tagged"
    assert "gate.jpg" in message["Message"]
    assert "14 Oct 2026, 09:30 UTC" in message["Message"]
    assert f"index.html#/file/{SHA_A}" in message["Message"]


def test_species_attribute_is_a_string_array(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app.sns, "publish", lambda **kw: calls.append(kw))

    app.handler({"Records": [tag_record(SHA_A, "fox"), tag_record(SHA_A, "cane toad")]}, None)

    [call] = calls
    assert call["MessageAttributes"] == {
        "species": {"DataType": "String.Array", "StringValue": '["cane toad", "fox"]'},
    }


def test_only_matching_subscribers_get_the_email(app, subscriber):
    fox_fan = subscriber(["fox", "rabbit"])
    cat_fan = subscriber(["cat"])
    toad_fan = subscriber(["cane toad"])

    app.handler({"Records": [tag_record(SHA_A, "fox"), tag_record(SHA_A, "cat")]}, None)

    assert len(fox_fan()) == 1
    assert len(cat_fan()) == 1
    assert toad_fan() == []


def test_one_message_per_file(app, subscriber):
    fox_fan = subscriber(["fox"])

    app.handler({"Records": [tag_record(SHA_A, "fox", sequence="1"),
                             tag_record(SHA_B, "fox", 3, sequence="2")]}, None)

    links = sorted(m["Message"].split("#/file/")[1][:64] for m in fox_fan())
    assert links == [SHA_A, SHA_B]


def test_other_records_are_ignored(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app.sns, "publish", lambda **kw: calls.append(kw))

    app.handler({"Records": [
        tag_record(SHA_A, "fox", event="MODIFY"),   # count changed, not a new tag
        tag_record(SHA_A, "cat", event="REMOVE"),
        meta_record(SHA_A),
        meta_record(SHA_B, event="INSERT"),        # upload-url reserving a file
    ]}, None)

    assert calls == []


def test_missing_meta_still_sends_without_details(app, subscriber):
    fox_fan = subscriber(["fox"])

    app.handler({"Records": [tag_record(SHA_A, "fox")]}, None)

    [message] = fox_fan()
    assert "File:" not in message["Message"]
    assert "A file in Aussie EcoGuard" in message["Message"]


def test_failed_publish_retries_from_that_file(app, monkeypatch):
    published = []

    def publish(**kw):
        if SHA_B in kw["Message"]:
            raise RuntimeError("SNS is down")
        published.append(kw)
    monkeypatch.setattr(app.sns, "publish", publish)

    result = app.handler({"Records": [
        tag_record(SHA_A, "fox", sequence="1"),
        tag_record(SHA_B, "cat", sequence="2"),
        tag_record(SHA_B, "dog", sequence="3"),
        tag_record("c" * 64, "emu", sequence="4"),
    ]}, None)

    assert result == {"batchItemFailures": [{"itemIdentifier": "2"}]}
    assert len(published) == 1  # A only; C waits for the retry so it is not sent twice


def test_empty_batch(app):
    assert app.handler({"Records": []}, None) == {"batchItemFailures": []}


def test_records_from_the_real_stream(app, subscriber, table):
    """Records as the table's stream gives them, after tagging-style writes."""
    fox_fan = subscriber(["fox"])
    client = boto3.client("dynamodb")
    stream_arn = client.update_table(
        TableName=table.name,
        StreamSpecification={"StreamEnabled": True, "StreamViewType": "NEW_IMAGE"},
    )["TableDescription"]["LatestStreamArn"]
    put_meta(table, SHA_A, filename="gate.jpg", file_type="image")
    client.transact_write_items(TransactItems=[
        {"Put": {"TableName": table.name, "Item": {
            "file_id": {"S": SHA_A}, "sk": {"S": f"TAG#{species}"},
            "tag": {"S": species}, "tag_count": {"N": str(count)}}}}
        for species, count in [("fox", 2), ("cat", 1)]
    ])

    streams = boto3.client("dynamodbstreams")
    [shard] = streams.describe_stream(StreamArn=stream_arn)["StreamDescription"]["Shards"]
    iterator = streams.get_shard_iterator(
        StreamArn=stream_arn, ShardId=shard["ShardId"], ShardIteratorType="TRIM_HORIZON",
    )["ShardIterator"]
    records = streams.get_records(ShardIterator=iterator)["Records"]
    assert len(records) == 3  # the META insert is skipped by the handler

    app.handler({"Records": records}, None)

    [message] = fox_fan()
    assert message["Subject"] == "Aussie EcoGuard: 2 foxes and 1 cat tagged"
