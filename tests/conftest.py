"""Shared fixtures: a fake AWS (moto) with the same table and bucket as infra/template.yaml.

Nothing here talks to real AWS, so no credentials are needed.
"""

import importlib.util
import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

# Keep __pycache__ out of lambdas/, otherwise `cloudformation package` zips it.
sys.dont_write_bytecode = True

LAMBDAS_DIR = Path(__file__).resolve().parent.parent / "lambdas"
TABLE_NAME = "test-media-table"
BUCKET_NAME = "test-media-bucket"

os.environ.update(
    AWS_DEFAULT_REGION="us-east-1",
    AWS_ACCESS_KEY_ID="testing",
    AWS_SECRET_ACCESS_KEY="testing",
    TABLE_NAME=TABLE_NAME,
    BUCKET_NAME=BUCKET_NAME,
)


@pytest.fixture
def aws():
    """Start a fake AWS with the Media table (incl. GSI) and the media bucket."""
    with mock_aws():
        boto3.client("dynamodb").create_table(
            TableName=TABLE_NAME,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "file_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "tag", "AttributeType": "S"},
                {"AttributeName": "tag_count", "AttributeType": "N"},
            ],
            KeySchema=[
                {"AttributeName": "file_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "tag-count-index",
                "KeySchema": [
                    {"AttributeName": "tag", "KeyType": "HASH"},
                    {"AttributeName": "tag_count", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            }],
        )
        boto3.client("s3").create_bucket(Bucket=BUCKET_NAME)
        yield


@pytest.fixture
def table(aws):
    return boto3.resource("dynamodb").Table(TABLE_NAME)


def load_lambda(name):
    """Import lambdas/<name>/app.py as a fresh module.

    Every function's entry file is called app.py, so it is loaded by path under
    a unique name. Call this inside the `aws` fixture: the module creates its
    boto3 clients at import time and they must point at the fake AWS.
    """
    path = LAMBDAS_DIR / name / "app.py"
    spec = importlib.util.spec_from_file_location(f"{name}_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
