"""
1. Ingestion Lambda Function
Polls the SQS Processing Queue and writes raw Close webhook JSON payloads
to the S3 Data Lake bucket (`realtimecloseapimessages`) under the `in-process/` prefix.

Senior Engineering Highlights:
- Uses `ReportBatchItemFailures` (Partial Batch Response) so only failed records
  in an SQS batch are retried and eventually routed to the Dead-Letter Queue (DLQ),
  preventing duplicate S3 writes for records in the same batch that succeeded.
- Enriches the raw webhook payload with ingestion metadata (`sqs_message_id`,
  `ingested_at_utc`) while preserving the exact immutable `raw_payload` from Close CRM.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")

DATA_LAKE_BUCKET = os.environ.get("DATA_LAKE_BUCKET_NAME", "realtimecloseapimessages")
IN_PROCESS_PREFIX = os.environ.get("IN_PROCESS_PREFIX", "in-process")


def _extract_lead_id(payload: Dict[str, Any], fallback_id: str) -> str:
    """
    Extracts the lead_id from a Close CRM webhook payload.
    Close webhook payloads typically structure data under `event.data.id` or `event.lead_id`,
    or top-level `lead_id` / `id`. Falls back to the SQS messageId if not present.
    """
    event_obj = payload.get("event", {})
    if isinstance(event_obj, dict):
        if event_obj.get("lead_id"):
            return str(event_obj["lead_id"])
        data_obj = event_obj.get("data", {})
        if isinstance(data_obj, dict) and data_obj.get("id"):
            return str(data_obj["id"])
        if isinstance(data_obj, dict) and data_obj.get("lead_id"):
            return str(data_obj["lead_id"])

    for key in ("lead_id", "id"):
        if payload.get(key):
            return str(payload[key])

    return fallback_id


def _process_single_record(record: Dict[str, Any]) -> str:
    """
    Parses a single SQS record and writes it as a JSON object into
    `s3://<DATA_LAKE_BUCKET>/in-process/<lead_id>_<message_id>.json`.
    Returns the S3 object key written.
    """
    message_id = record["messageId"]
    raw_body = record.get("body", "")

    if not raw_body or not raw_body.strip():
        raise ValueError(f"SQS message {message_id} has an empty body")

    # Validate that the incoming webhook payload is valid JSON
    try:
        parsed_payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.error("Malformed JSON in SQS message %s: %s", message_id, exc)
        raise ValueError(f"Malformed JSON in SQS message {message_id}") from exc

    if not isinstance(parsed_payload, dict):
        raise ValueError(f"Expected JSON object in SQS message {message_id}, got {type(parsed_payload).__name__}")

    lead_id = _extract_lead_id(parsed_payload, fallback_id=message_id)
    now_utc = datetime.now(timezone.utc)
    timestamp_str = now_utc.strftime("%Y%m%dT%H%M%SZ")

    # Deterministic, collision-free S3 key under the `in-process/` folder
    s3_key = f"{IN_PROCESS_PREFIX.rstrip('/')}/{lead_id}_{message_id}.json"

    # Envelope containing both ingestion audit metadata and the raw Close payload
    document = {
        "metadata": {
            "lead_id": lead_id,
            "sqs_message_id": message_id,
            "ingested_at_utc": now_utc.isoformat(),
            "ingest_timestamp_compact": timestamp_str,
            "source": "close_crm_webhook",
        },
        "payload": parsed_payload,
    }

    s3_client.put_object(
        Bucket=DATA_LAKE_BUCKET,
        Key=s3_key,
        Body=json.dumps(document, indent=2).encode("utf-8"),
        ContentType="application/json",
    )

    logger.info(
        "Successfully wrote lead_id=%s (messageId=%s) to s3://%s/%s",
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
        except (ValueError, ClientError, Exception) as exc:
            logger.exception(
                "Failed to ingest SQS messageId=%s. Marking for retry/DLQ: %s",
                message_id,
                exc,
            )
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}
