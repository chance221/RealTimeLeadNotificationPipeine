"""
1. Ingestion Lambda Function
Polls the SQS Processing Queue, validates the Close CRM webhook payload using
Pydantic (`CloseWebhookEnvelope` / `CloseWebhookBody`), and writes ONLY the
validated `body` JSON to the S3 Data Lake bucket (`real-time-close-api-messages`)
under `in-process/crm_event_{lead_id}.json`.

Senior Engineering Highlights:
- Strict schema validation at the ingestion boundary via Pydantic v2 (`models.py`).
- Preserves all extra Close CRM fields (`extra="allow"`) while guaranteeing required
  fields (`subscription_id`, `event.id`, `event.lead_id`, `event.data.display_name`,
  `event.data.status_label`, `event.data.date_created`) exist and are non-empty.
- Uses `ReportBatchItemFailures` (Partial Batch Response) so any record failing
  Pydantic `ValidationError` is retried and routed to the Dead-Letter Queue (DLQ).
"""

import json
import logging
import os
from typing import Any, Dict, List

import boto3
from botocore.exceptions import ClientError
from pydantic import ValidationError

from models import CloseWebhookBody, validate_incoming_webhook

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")

DATA_LAKE_BUCKET = os.environ.get("DATA_LAKE_BUCKET_NAME", "real-time-close-api-messages")
IN_PROCESS_PREFIX = os.environ.get("IN_PROCESS_PREFIX", "in-process")


def _process_single_record(record: Dict[str, Any]) -> str:
    """
    Parses a single SQS record, validates the Close CRM webhook payload via Pydantic,
    and writes ONLY the `body` portion to `s3://<DATA_LAKE_BUCKET>/in-process/crm_event_{lead_id}.json`.
    Returns the S3 object key written.
    """
    message_id = record["messageId"]
    raw_sqs_body = record.get("body", "")

    if not raw_sqs_body or not raw_sqs_body.strip():
        raise ValueError(f"SQS message {message_id} has an empty body")

    # Validate incoming JSON (either API Gateway envelope with `body` or direct Close body)
    validated_body: CloseWebhookBody = validate_incoming_webhook(raw_sqs_body)
    lead_id = validated_body.event.lead_id

    # Save strictly as `in-process/crm_event_{lead_id}.json` containing only the validated CRM body
    s3_key = f"{IN_PROCESS_PREFIX.rstrip('/')}/crm_event_{lead_id}.json"

    s3_client.put_object(
        Bucket=DATA_LAKE_BUCKET,
        Key=s3_key,
        Body=json.dumps(validated_body.model_dump(mode="json"), indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    logger.info(
        "Validated and wrote CRM body for lead_id=%s (messageId=%s) to s3://%s/%s",
        lead_id,
        message_id,
        DATA_LAKE_BUCKET,
        s3_key,
    )
    return s3_key


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, List[Dict[str, str]]]:
    """
    Entry point for the SQS-triggered Ingestion Lambda.
    Returns `batchItemFailures` so SQS only retries failed messages in the batch,
    and moves repeatedly failing messages to the Dead-Letter Queue after maxReceiveCount (3).
    """
    records = event.get("Records", [])
    logger.info("Received SQS batch with %d record(s)", len(records))

    batch_item_failures: List[Dict[str, str]] = []

    for record in records:
        message_id = record.get("messageId", "unknown")
        try:
            _process_single_record(record)
        except ValidationError as exc:
            logger.error(
                "Pydantic schema validation failed for SQS messageId=%s: %s",
                message_id,
                exc.json(),
            )
            batch_item_failures.append({"itemIdentifier": message_id})
        except (ValueError, ClientError, Exception) as exc:
            logger.exception(
                "Failed to ingest SQS messageId=%s. Marking for retry/DLQ: %s",
                message_id,
                exc,
            )
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}
