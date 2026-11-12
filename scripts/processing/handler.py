"""
3. Processing & SES Notification Lambda Function
Triggered by Amazon EventBridge Scheduler 10 minutes after `crm_event_{lead_id}.json`
lands in the `in-process/` folder of the S3 Data Lake bucket.

Responsibilities:
1. Validates the incoming EventBridge Scheduler payload using `ScheduledLeadEvent` (Pydantic).
2. Reads `s3://<bucket>/in-process/crm_event_{lead_id}.json` and validates it using
   `CloseWebhookBody` (Pydantic) to ensure `display_name`, `lead_id`, `date_created`,
   and `status_label` are present.
3. Fetches the Lead Owner lookup JSON from `https://dea-lead-owner.s3.us-east-1.amazonaws.com/{lead_id}.json`
   and validates it using `LeadOwnerLookup` (Pydantic) to ensure `lead_email`, `lead_owner`,
   and `funnel` are present and valid.
4. Sends the prescribed notification email via Amazon SES to `intelectual221@gmail.com`.
5. Moves `crm_event_{lead_id}.json` from `in-process/` to `processed/`.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

import boto3
from botocore.exceptions import ClientError
from pydantic import ValidationError

from models import CloseWebhookBody, LeadOwnerLookup, ScheduledLeadEvent

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")
ses_client = boto3.client("ses")

DATA_LAKE_BUCKET = os.environ.get("DATA_LAKE_BUCKET_NAME", "real-time-close-api-messages")
IN_PROCESS_PREFIX = os.environ.get("IN_PROCESS_PREFIX", "in-process")
PROCESSED_PREFIX = os.environ.get("PROCESSED_PREFIX", "processed")
LEAD_OWNER_BASE_URL = os.environ.get(
    "LEAD_OWNER_BASE_URL", "https://dea-lead-owner.s3.us-east-1.amazonaws.com"
).rstrip("/")
SES_SENDER_EMAIL = os.environ.get("SES_SENDER_EMAIL", "intelectual221@gmail.com").strip()
SES_RECIPIENT_EMAIL = os.environ.get("SES_RECIPIENT_EMAIL", "intelectual221@gmail.com").strip()


def _fetch_and_validate_lead_owner_lookup(lead_id: str) -> LeadOwnerLookup:
    """
    Fetches the lead owner lookup JSON from the public S3 bucket:
      1. `https://dea-lead-owner.s3.us-east-1.amazonaws.com/{lead_id}.json`
    and validates the returned JSON against the `LeadOwnerLookup` Pydantic model.
    """
    candidate_urls = [
        f"{LEAD_OWNER_BASE_URL}/{lead_id}.json"
    ]

    last_error: Optional[Exception] = None
    for url in candidate_urls:
        try:
            logger.info("Fetching lead owner lookup data from %s", url)
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as response:
                raw_lookup = response.read().decode("utf-8")
                validated_lookup = LeadOwnerLookup.model_validate_json(raw_lookup)
                logger.info(
                    "Successfully fetched and validated LeadOwnerLookup for lead_id=%s from %s",
                    lead_id,
                    url,
                )
                return validated_lookup
        except ValidationError as exc:
            logger.error("Pydantic validation failed for lookup payload from %s: %s", url, exc.json())
            raise
        except urllib.error.HTTPError as exc:
            logger.warning("HTTP %s when fetching %s: %s", exc.code, url, exc.reason)
            last_error = exc
        except (urllib.error.URLError, Exception) as exc:
            logger.warning("Error fetching %s: %s", url, exc)
            last_error = exc

    raise RuntimeError(
        f"Failed to retrieve lead_owner lookup data for lead_id={lead_id} from {candidate_urls}: {last_error}"
    )


def _build_prescribed_email(
    crm_body: CloseWebhookBody, lookup: LeadOwnerLookup
) -> Tuple[str, str, str]:
    """
    Constructs the exact prescribed email format from the validated Pydantic models:

    Name:  {display_name}
    Lead ID : {lead_id}
    Created Date: {date_created}
    Label: {status_label}
    Email: {lead_email}
    Lead Owner: {lead_owner}
    Funnel: {funnel}
    """
    display_name = crm_body.event.data.display_name
    lead_id = crm_body.event.lead_id
    date_created = crm_body.event.data.date_created
    status_label = crm_body.event.data.status_label

    lead_email = lookup.lead_email
    lead_owner = lookup.lead_owner
    funnel = lookup.funnel

    subject = f"New Lead Notification: {display_name} ({lead_id})"

    text_body = (
        f"Name:  {display_name}\n"
        f"Lead ID : {lead_id}\n"
        f"Created Date: {date_created}\n"
        f"Label: {status_label}\n"
        f"Email: {lead_email}\n"
        f"Lead Owner: {lead_owner}\n"
        f"Funnel: {funnel}\n"
    )

    html_body = (
        "<html><body style=\"font-family: Arial, sans-serif; line-height: 1.6; color: #111827;\">"
        f"<p><strong>Name:</strong>  {display_name}<br>"
        f"<strong>Lead ID :</strong> {lead_id}<br>"
        f"<strong>Created Date:</strong> {date_created}<br>"
        f"<strong>Label:</strong> {status_label}<br>"
        f"<strong>Email:</strong> {lead_email}<br>"
        f"<strong>Lead Owner:</strong> {lead_owner}<br>"
        f"<strong>Funnel:</strong> {funnel}</p>"
        "</body></html>"
    )

    return subject, text_body, html_body


def _send_ses_email(subject: str, text_body: str, html_body: str, lead_id: str) -> Optional[str]:
    """Sends the prescribed notification email via Amazon SES."""
    if not SES_SENDER_EMAIL or not SES_RECIPIENT_EMAIL:
        raise ValueError("SES_SENDER_EMAIL and SES_RECIPIENT_EMAIL must be configured.")

    recipients = [addr.strip() for addr in SES_RECIPIENT_EMAIL.split(",") if addr.strip()]

    response = ses_client.send_email(
        Source=SES_SENDER_EMAIL,
        Destination={"ToAddresses": recipients},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": text_body, "Charset": "UTF-8"},
                "Html": {"Data": html_body, "Charset": "UTF-8"},
            },
        },
    )
    ses_message_id = response.get("MessageId")
    logger.info(
        "Sent SES lead notification email (MessageId=%s) for lead_id=%s to %s",
        ses_message_id,
        lead_id,
        recipients,
    )
    return ses_message_id


def _object_exists(bucket: str, key: str) -> bool:
    """Checks whether an S3 object exists (used for idempotency checks)."""
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return False
        raise


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Entry point for the EventBridge Scheduler-triggered Processing & SES Lambda.
    """
    logger.info("Received scheduled lead event: %s", json.dumps(event))

    # 1. Validate the EventBridge Scheduler event payload via Pydantic
    scheduled_event = ScheduledLeadEvent.model_validate(event)
    bucket = scheduled_event.bucket or DATA_LAKE_BUCKET
    in_process_key = scheduled_event.key

    filename = in_process_key.rsplit("/", 1)[-1]
    processed_key = f"{PROCESSED_PREFIX.rstrip('/')}/{filename}"

    # 2. Idempotency check
    if _object_exists(bucket, processed_key):
        logger.warning(
            "Idempotency check: s3://%s/%s already exists. Skipping duplicate SES email.",
            bucket,
            processed_key,
        )
        if _object_exists(bucket, in_process_key):
            s3_client.delete_object(Bucket=bucket, Key=in_process_key)
        return {
            "statusCode": 200,
            "body": json.dumps({"status": "already_processed", "processed_key": processed_key}),
        }

    # 3. Read `crm_event_{lead_id}.json` from `in-process/` and validate with Pydantic
    response = s3_client.get_object(Bucket=bucket, Key=in_process_key)
    raw_content = response["Body"].read().decode("utf-8")
    validated_crm_body = CloseWebhookBody.model_validate_json(raw_content)
    lead_id = validated_crm_body.event.lead_id

    # 4. Fetch and validate Lead Owner lookup (`LeadOwnerLookup` Pydantic model)
    validated_lookup = _fetch_and_validate_lead_owner_lookup(lead_id)

    # 5. Build and send the prescribed email via Amazon SES
    subject, text_body, html_body = _build_prescribed_email(validated_crm_body, validated_lookup)
    ses_message_id = _send_ses_email(subject, text_body, html_body, lead_id)

    # 6. Copy the validated `crm_event_{lead_id}.json` to `processed/` and delete from `in-process/`
    s3_client.put_object(
        Bucket=bucket,
        Key=processed_key,
        Body=raw_content.encode("utf-8"),
        ContentType="application/json",
    )
    logger.info("Moved lead file to s3://%s/%s", bucket, processed_key)

    s3_client.delete_object(Bucket=bucket, Key=in_process_key)
    logger.info("Removed lead file from s3://%s/%s", bucket, in_process_key)

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "status": "processed",
                "lead_id": lead_id,
                "ses_message_id": ses_message_id,
                "processed_key": processed_key,
            }
        ),
    }