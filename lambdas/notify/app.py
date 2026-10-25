"""notify: email subscribers when a file gains a species they subscribed to.

Trigger: DynamoDB Stream of the media table -> this function. The trigger's
event filter only passes INSERT records whose sk begins with "TAG#", so every
record means "file X gained tag Y", from automatic tagging or a manual edit.
Count changes (MODIFY) and META items never get here. The same check is
repeated below, so a trigger without the filter does no harm.

For each batch:
  1. Group the new tags by file_id: {"<sha256>": {"fox": 2, "cat": 1}}.
     The tagging Lambda writes all of a file's tags in one transaction, so
     they normally arrive in the same batch and become one email.
  2. Read the file's META item for the name, type and upload time (optional:
     the email leaves them out if META is gone).
  3. Publish one SNS message per file. Its `species` message attribute lists
     the new tags; each subscription's filter policy {"species": [...]} (set
     by lambdas/subscribe) then decides who gets the email.

If a publish fails, the function reports that file's first record as the
batch item failure and stops, so Lambda retries from there and the files
already published are not emailed again. The subject and body of the email
come from email_text.py.
"""

import json
import os

import boto3

from email_text import build_email

TABLE_NAME = os.environ["TABLE_NAME"]
TOPIC_ARN = os.environ["TOPIC_ARN"]
SITE_URL = os.environ["SITE_URL"]  # website_https_url, the links in the email point there

table = boto3.resource("dynamodb").Table(TABLE_NAME)
sns = boto3.client("sns")


def handler(event, context):
    files = new_tags_by_file(event.get("Records", []))
    for file_id, (first_sequence, tags) in files.items():
        try:
            publish(file_id, tags)
        except Exception as e:
            print(f"{file_id}: publish failed, Lambda will retry from here: {e!r}")
            return {"batchItemFailures": [{"itemIdentifier": first_sequence}]}
    return {"batchItemFailures": []}


def new_tags_by_file(records):
    """{file_id: (sequence number of its first record, {species: count})}, in stream order."""
    files = {}
    for record in records:
        if record.get("eventName") != "INSERT":
            continue
        image = record["dynamodb"].get("NewImage", {})
        sk = image.get("sk", {}).get("S", "")
        if not sk.startswith("TAG#"):
            continue
        file_id = image["file_id"]["S"]
        species = image.get("tag", {}).get("S") or sk[len("TAG#"):]
        count = int(image.get("tag_count", {}).get("N", "1"))
        sequence = record["dynamodb"]["SequenceNumber"]
        files.setdefault(file_id, (sequence, {}))[1][species] = count
    return files


def publish(file_id, tags):
    meta = table.get_item(Key={"file_id": file_id, "sk": "META"}).get("Item", {})
    species = sorted(tags)
    sns.publish(
        TopicArn=TOPIC_ARN,
        MessageAttributes={
            # Matched against each subscription's filter policy {"species": [...]}:
            # one species in common is enough.
            "species": {"DataType": "String.Array", "StringValue": json.dumps(species)},
        },
        **build_email(
            file_id, tags, SITE_URL,
            filename=meta.get("filename"),
            file_type=meta.get("file_type"),
            created_at=meta.get("created_at"),
        ),
    )
    print(f"{file_id}: notified {species}")
