"""
2. Scheduler Lambda Function
Triggered by S3 Event Notification (`s3:ObjectCreated:*`) when a new `.json` file
lands in the `in-process/` prefix of the Data Lake bucket (`real-time-close-api-messages`).

Creates a one-time schedule in Amazon EventBridge Scheduler set to fire at
`current_time + 10 minutes` (`at(yyyy-mm-ddThh:mm:ss)`), passing the S3 bucket and
object key to the Processing & SES Lambda function, and automatically deletes the
schedule upon completion (`ActionAfterCompletion="DELETE"`).
"""

import hashlib
import json
import logging
import os
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

scheduler_client = boto3.client("scheduler")

PROCESSING_LAMBDA_ARN = os.environ["PROCESSING_LAMBDA_ARN"]
SCHEDULER_ROLE_ARN = os.environ["SCHEDULER_ROLE_ARN"]
DLQ_ARN = os.environ["DLQ_ARN"]
SCHEDULE_GROUP_NAME = os.environ.get("SCHEDULE_GROUP_NAME", "default")
DELAY_MINUTES = int(os.environ.get("DELAY_MINUTES", "10"))


def _build_schedule_name(object_key: str) -> str:
    """
    Constructs a deterministic, valid EventBridge Scheduler name (<= 64 chars,
    alphanumeric, hyphens, underscores) from the S3 object key.
    """
    filename = object_key.rsplit("/", 1)[-1].removesuffix(".json")
    safe_prefix = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in filename)[:36]
    key_hash = hashlib.sha256(object_key.encode("utf-8")).hexdigest()[:12]
    return f"lead-{safe_prefix}-{key_hash}"


def _schedule_single_s3_record(record: Dict[str, Any]) -> Dict[str, str]:
    """
    Parses a single S3 event record and creates a one-time EventBridge schedule
    for `now + DELAY_MINUTES` minutes.
    """
    s3_info = record.get("s3", {})
    bucket_name = s3_info.get("bucket", {}).get("name")
    raw_key = s3_info.get("object", {}).get("key", "")
    object_key = urllib.parse.unquote_plus(raw_key)

    if not bucket_name or not object_key:
        raise ValueError(f"Missing S3 bucket or object key in event record: {json.dumps(record)}")

    # Ignore folder marker objects or non-JSON files
    if object_key.endswith("/") or not object_key.endswith(".json"):
        logger.info("Skipping non-lead S3 object: s3://%s/%s", bucket_name, object_key)
        return {"status": "skipped", "key": object_key}

    now_utc = datetime.now(timezone.utc)
    fire_at_utc = now_utc + timedelta(minutes=DELAY_MINUTES)
    # EventBridge Scheduler `at()` expression requires format: at(yyyy-mm-ddThh:mm:ss)
    schedule_expression = f"at({fire_at_utc.strftime('%Y-%m-%dT%H:%M:%S')})"
    schedule_name = _build_schedule_name(object_key)

    target_payload = {
        "bucket": bucket_name,
        "key": object_key,
        "scheduled_at_utc": now_utc.isoformat(),
        "expected_execution_utc": fire_at_utc.isoformat(),
        "schedule_name": schedule_name,
    }

    try:
        scheduler_client.create_schedule(
            Name=schedule_name,
            GroupName=SCHEDULE_GROUP_NAME,
            ScheduleExpression=schedule_expression,
            ScheduleExpressionTimezone="UTC",
            FlexibleTimeWindow={"Mode": "OFF"},
            ActionAfterCompletion="DELETE",
            Description=f"10-minute delayed lead notification for s3://{bucket_name}/{object_key}",
            Target={
                "Arn": PROCESSING_LAMBDA_ARN,
                "RoleArn": SCHEDULER_ROLE_ARN,
                "Input": json.dumps(target_payload),
                "DeadLetterConfig": {
                    "Arn": DLQ_ARN,
                },
                "RetryPolicy": {
                    "MaximumEventAgeInSeconds": 3600,
                    "MaximumRetryAttempts": 3,
                },
            },
        )
        logger.info(
            "Created schedule '%s' (%s UTC) for s3://%s/%s",
            schedule_name,
            schedule_expression,
            bucket_name,
            object_key,
        )
    except scheduler_client.exceptions.ConflictException:
        # Idempotency: if S3 delivers a duplicate ObjectCreated event for the same key,
        # update the existing schedule or log and succeed rather than failing.
        logger.warning(
            "Schedule '%s' already exists for s3://%s/%s; skipping duplicate creation.",
            schedule_name,
            bucket_name,
            object_key,
        )

    return {
        "status": "scheduled",
        "schedule_name": schedule_name,
        "fire_at_utc": fire_at_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "key": object_key,
    }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Entry point for the S3 Event Notification-triggered Scheduler Lambda.
    If any exception is raised, Lambda retries the asynchronous invocation twice
    and then sends the failed event to the configured Dead-Letter Queue (DLQ).
    """
    logger.info("Received S3 event notification: %s", json.dumps(event))
    records: List[Dict[str, Any]] = event.get("Records", [])
    results: List[Dict[str, str]] = []

    for record in records:
        try:
            result = _schedule_single_s3_record(record)
            results.append(result)
        except (ValueError, ClientError, Exception) as exc:
            logger.exception("Failed to schedule S3 event record: %s", exc)
            raise

    return {
        "statusCode": 200,
        "body": json.dumps({"scheduled_records": results}),
    }
