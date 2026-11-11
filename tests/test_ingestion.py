"""
Unit tests for the Ingestion Lambda (`scripts/ingestion/handler.py` & `scripts/ingestion/models.py`).
"""

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

# Load the ingestion module and its models in isolation
INGESTION_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ingestion"
sys.path.insert(0, str(INGESTION_DIR))

import models as ingestion_models  # noqa: E402
import handler as ingestion_handler  # noqa: E402

sys.path.remove(str(INGESTION_DIR))


SAMPLE_CRM_BODY = {
    "subscription_id": "whsub_1zwvQVQOBPFqrH6xXJEvFJ",
    "event": {
        "id": "ev_1ntH1vAE4G7DNjNZYkMeck",
        "date_created": "2025-05-20T12:14:56.446000",
        "date_updated": "2025-05-20T12:14:56.446000",
        "organization_id": "orga_rq7EW7TTwnrAshSzIQ49jJUWzDxtTQUBBrA4VoRWElD",
        "user_id": "user_RoWq89U2oZOlTTfJScR9pTREnX3AAeNdKnw4YOOcYwG",
        "request_id": "req_1B0t9wF68LfveKOdLNsbVE",
        "api_key_id": None,
        "oauth_client_id": "oa2client_0RZ3Soibz8n7hqA2D6bmlV",
        "oauth_scope": "all.full_access offline_access",
        "object_type": "lead",
        "object_id": "lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA",
        "lead_id": "lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA",
        "action": "created",
        "changed_fields": [],
        "meta": {"request_path": "/api/v1/lead/", "request_method": "POST"},
        "data": {
            "status_id": "stat_w883GIAbRCWhns1gvURwAaImLfsmA4SwN5HYLsToMOY",
            "display_name": "Lowell Bast",
            "created_by": "user_RoWq89U2oZOlTTfJScR9pTREnX3AAeNdKnw4YOOcYwG",
            "created_by_name": "Christopher Garzon",
            "addresses": [],
            "name": "",
            "description": "",
            "url": None,
            "contact_ids": ["cont_xASn6XuY8qQuUq6FN919ia9jjg3QvkKXDXx90pytjAE"],
            "date_updated": "2025-05-20T12:14:56.444000+00:00",
            "organization_id": "orga_rq7EW7TTwnrAshSzIQ49jJUWzDxtTQUBBrA4VoRWElD",
            "updated_by": "user_RoWq89U2oZOlTTfJScR9pTREnX3AAeNdKnw4YOOcYwG",
            "id": "lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA",
            "updated_by_name": "Christopher Garzon",
            "status_label": "Potential",
            "date_created": "2025-05-20T12:14:56.409000+00:00",
            "custom.cf_am3UgCUhyM5iNDtAPL84enDjUrZx1JsyVZ9uD9TbYwG": "DE ACADEMY Direct VSL",
        },
        "previous_data": {},
    },
}

SAMPLE_APIGW_ENVELOPE = {
    "resource": "/crm",
    "path": "/crm",
    "httpMethod": "POST",
    "headers": {"User-Agent": "Close Webhooks 2.0"},
    "body": SAMPLE_CRM_BODY,
    "isBase64Encoded": False,
}


class TestIngestionLambda(unittest.TestCase):
    """Unit test suite for the Ingestion Lambda handler and Pydantic models."""

    def test_validate_incoming_webhook_unwraps_apigw_envelope(self) -> None:
        validated = ingestion_models.validate_incoming_webhook(SAMPLE_APIGW_ENVELOPE)
        self.assertEqual(
            validated.event.lead_id, "lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA"
        )
        self.assertEqual(validated.event.data.display_name, "Lowell Bast")
        self.assertEqual(validated.subscription_id, "whsub_1zwvQVQOBPFqrH6xXJEvFJ")

    def test_validate_incoming_webhook_accepts_direct_crm_body(self) -> None:
        validated = ingestion_models.validate_incoming_webhook(json.dumps(SAMPLE_CRM_BODY))
        self.assertEqual(
            validated.event.lead_id, "lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA"
        )

    def test_pydantic_rejects_invalid_lead_id_prefix(self) -> None:
        bad_payload = json.loads(json.dumps(SAMPLE_CRM_BODY))
        bad_payload["event"]["lead_id"] = "invalid_prefix_123"
        with self.assertRaises(ValidationError):
            ingestion_models.validate_incoming_webhook(bad_payload)

    def test_pydantic_rejects_missing_required_fields(self) -> None:
        bad_payload = json.loads(json.dumps(SAMPLE_CRM_BODY))
        del bad_payload["event"]["data"]["display_name"]
        with self.assertRaises(ValidationError):
            ingestion_models.validate_incoming_webhook(bad_payload)

    @patch.object(ingestion_handler, "s3_client")
    def test_lambda_handler_writes_crm_event_file_to_s3(self, mock_s3: MagicMock) -> None:
        sqs_event = {
            "Records": [
                {
                    "messageId": "msg-001",
                    "body": json.dumps(SAMPLE_APIGW_ENVELOPE),
                }
            ]
        }

        response = ingestion_handler.lambda_handler(sqs_event, None)
        self.assertEqual(response, {"batchItemFailures": []})

        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args.kwargs
        self.assertEqual(
            call_kwargs["Key"],
            "in-process/crm_event_lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA.json",
        )
        saved_json = json.loads(call_kwargs["Body"].decode("utf-8"))
        # Verify ONLY the body (subscription_id + event) is saved, not the outer API Gateway headers
        self.assertIn("subscription_id", saved_json)
        self.assertIn("event", saved_json)
        self.assertNotIn("headers", saved_json)
        self.assertEqual(saved_json["event"]["data"]["display_name"], "Lowell Bast")

    @patch.object(ingestion_handler, "s3_client")
    def test_lambda_handler_reports_partial_batch_failures_for_dlq(
        self, mock_s3: MagicMock
    ) -> None:
        sqs_event = {
            "Records": [
                {"messageId": "msg-valid", "body": json.dumps(SAMPLE_CRM_BODY)},
                {"messageId": "msg-malformed-json", "body": "{not-valid-json"},
                {
                    "messageId": "msg-missing-fields",
                    "body": json.dumps({"subscription_id": "whsub_123", "event": {}}),
                },
            ]
        }

        response = ingestion_handler.lambda_handler(sqs_event, None)
        self.assertEqual(
            response,
            {
                "batchItemFailures": [
                    {"itemIdentifier": "msg-malformed-json"},
                    {"itemIdentifier": "msg-missing-fields"},
                ]
            },
        )
        # Valid message was still written to S3
        mock_s3.put_object.assert_called_once()


if __name__ == "__main__":
    unittest.main()
