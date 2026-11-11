"""
3. Processing & SES Notification Lambda Function
Triggered by EventBridge Scheduler 10 minutes after a lead lands in `in-process/`.
Reads the lead JSON from S3, sends a formatted email via Amazon SES, copies the object
to `processed/`, and deletes the original from `in-process/`.
"""

import json
import logging
from typing import Any, Dict

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Entry point for the EventBridge Scheduler-triggered Processing Lambda."""
    logger.info("Received scheduled lead event: %s", json.dumps(event))
    # Step 5 implementation will go here
    return {"statusCode": 200, "body": json.dumps({"status": "processing_stub"})}
