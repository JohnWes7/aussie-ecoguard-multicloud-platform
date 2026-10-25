"""POST /subscriptions: subscribe the signed-in user to email notifications.

Request body:  {"tags": ["fox", "cane toad"]}
200 response:  {"email", "tags", "pending"}
               pending true:  SNS has sent a confirmation email; nothing is
                              delivered until the user clicks the link in it.
               pending false: the user had already confirmed, so only their
                              species changed and no email is sent.
409 response:  the user subscribed before with other species and has not
               confirmed yet. SNS cannot change a subscription that is still
               pending, so they must confirm (or let it expire) first.

The email comes from the Cognito ID token (the API's authorizer has already
checked it), never from the request body, so a user can only subscribe their
own address. Each user has one subscription on the topic; subscribing again
replaces its species. The filter policy {"species": [...]} matches the
`species` message attribute that the notify Lambda publishes, so SNS only
emails users who chose at least one of a file's new tags.
"""

import json
import os

import boto3
from botocore.exceptions import ClientError

TOPIC_ARN = os.environ["TOPIC_ARN"]

# The tags the model can give: infra/model/config.json "species" plus
# "unknown_tag". Copied here because only lambdas/subscribe is in the zip;
# tests/test_subscribe.py fails if the two lists drift apart.
SPECIES = {
    "fox", "rabbit", "feral pig", "cane toad", "cat", "dog",
    "deer", "goat", "horse", "camel", "cattle",
    "kangaroo", "possum", "wombat", "koala", "bandicoot", "echidna", "emu",
    "bird", "reptile", "frog", "rodent", "other animal",
}

sns = boto3.client("sns")


def handler(event, context):
    # API Gateway's Cognito authorizer has already checked the ID token and
    # passes its claims on. They are missing only when the function is invoked
    # some other way, e.g. a test event in the Lambda console.
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims", {})
    email = claims.get("email")
    if not email:
        return response(401, {"error": "not signed in"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"error": "body must be JSON"})

    tags = body.get("tags") if isinstance(body, dict) else None
    if not isinstance(tags, list) or not tags or not all(isinstance(t, str) for t in tags):
        return response(400, {"error": "tags must be a non-empty list of species"})
    tags = sorted({t.strip().lower() for t in tags})
    unknown = [t for t in tags if t not in SPECIES]
    if unknown:
        return response(400, {"error": f"unknown species: {', '.join(unknown)}"})

    filter_policy = json.dumps({"species": tags})
    existing = find_subscription(email)

    if existing and existing != "PendingConfirmation":
        # Confirmed: change the filter in place, without another confirmation email.
        sns.set_subscription_attributes(
            SubscriptionArn=existing, AttributeName="FilterPolicy", AttributeValue=filter_policy,
        )
        return response(200, {"email": email, "tags": tags, "pending": False})

    # New, or pending with the same species: SNS (re)sends the confirmation email.
    try:
        sns.subscribe(
            TopicArn=TOPIC_ARN,
            Protocol="email",
            Endpoint=email,
            Attributes={"FilterPolicy": filter_policy, "FilterPolicyScope": "MessageAttributes"},
            ReturnSubscriptionArn=True,
        )
    except ClientError as e:
        # Pending with other species: "Subscription already exists with different attributes".
        if e.response["Error"]["Code"] != "InvalidParameter":
            raise
        return response(409, {
            "error": "You already have a subscription waiting for confirmation. Click "
                     '"Confirm subscription" in the email from Aussie EcoGuard first, then '
                     "choose your species again.",
            "email": email,
        })
    return response(200, {"email": email, "tags": tags, "pending": True})


def find_subscription(email):
    """This email's subscription ARN on the topic, "PendingConfirmation", or None."""
    pages = sns.get_paginator("list_subscriptions_by_topic").paginate(TopicArn=TOPIC_ARN)
    for page in pages:
        for sub in page["Subscriptions"]:
            if sub["Protocol"] == "email" and sub["Endpoint"] == email:
                return sub["SubscriptionArn"]
    return None


def response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
