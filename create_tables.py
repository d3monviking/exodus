"""
Create the four DynamoDB tables in LocalStack. Delete-then-create, not
create-if-absent — re-run this from scratch whenever the stack is reset.

Run from the host (not inside a SAM container), so it defaults to
localhost:4566. Override with AWS_ENDPOINT_URL if needed.
"""

from __future__ import annotations

import os
import sys

import boto3

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
REGION = os.environ.get("AWS_REGION", "us-east-1")

TABLES = [
    {
        "TableName": "Requests",
        "KeySchema": [{"AttributeName": "student_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "student_id", "AttributeType": "S"},
            {"AttributeName": "route", "AttributeType": "S"},
            {"AttributeName": "status", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "route-status-index",
                "KeySchema": [
                    {"AttributeName": "route", "KeyType": "HASH"},
                    {"AttributeName": "status", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "Groups",
        "KeySchema": [{"AttributeName": "group_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "group_id", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "Blocks",
        "KeySchema": [{"AttributeName": "pair_key", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "pair_key", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "Releases",
        "KeySchema": [{"AttributeName": "release_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "release_id", "AttributeType": "S"},
            {"AttributeName": "route", "AttributeType": "S"},
            {"AttributeName": "ran_at", "AttributeType": "N"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "route-ran_at-index",
                "KeySchema": [
                    {"AttributeName": "route", "KeyType": "HASH"},
                    {"AttributeName": "ran_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        "BillingMode": "PAY_PER_REQUEST",
    },
]


def main() -> None:
    ddb = boto3.client(
        "dynamodb",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )

    existing = set(ddb.list_tables().get("TableNames", []))
    for table in TABLES:
        name = table["TableName"]
        if name in existing:
            print(f"deleting existing table {name}")
            ddb.delete_table(TableName=name)
            ddb.get_waiter("table_not_exists").wait(TableName=name)

    for table in TABLES:
        name = table["TableName"]
        print(f"creating table {name}")
        ddb.create_table(**table)
        ddb.get_waiter("table_exists").wait(TableName=name)

    print("all tables ready:", sorted(t["TableName"] for t in TABLES))
    reset_bucket()


def reset_bucket() -> None:
    from botocore.config import Config

    bucket = os.environ.get("RELEASE_LOG_BUCKET", "exodus-release-log")
    s3 = boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(s3={"addressing_style": "path"}),
    )
    if bucket in {b["Name"] for b in s3.list_buckets().get("Buckets", [])}:
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                s3.delete_object(Bucket=bucket, Key=obj["Key"])
        s3.delete_bucket(Bucket=bucket)
    s3.create_bucket(Bucket=bucket)
    print("audit bucket ready:", bucket)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # surface a clear message instead of a boto traceback
        print(f"failed against endpoint {ENDPOINT}: {exc}", file=sys.stderr)
        sys.exit(1)
