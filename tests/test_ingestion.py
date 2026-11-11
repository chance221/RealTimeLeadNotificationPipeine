"""
Unit tests for the Ingestion Lambda (`scripts/ingestion/handler.py` & `scripts/ingestion/models.py`).
Covers explicit Happy Path and Sad Path test cases for every function and validator.
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from pydantic import ValidationError

INGESTION_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ingestion"


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(INGESTION_DIR))
ingestion_models = _load_module("models", INGESTION_DIR / "models.py")
sys.modules["models"] = ingestion_models
ingestion_handler = _load_module("ingestion_handler", INGESTION_DIR / "handler.py")
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
    """Happy and Sad path unit tests for every function in the Ingestion Lambda."""

    # =========================================================================
    # 1. `validate_incoming_webhook` & Pydantic Models (Happy & Sad Paths)
    # =========================================================================
    def test_validate_incoming_webhook_happy_path_apigw_envelope(self) -> None:
        """Happy Path: Unwraps API Gateway envelope (with dict or JSON-string body) and validates."""
        validated = ingestion_models.validate_incoming_webhook(SAMPLE_APIGW_ENVELOPE)
        self.assertEqual(
            validated.event.lead_id, "lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA"
        )
        self.assertEqual(validated.event.data.display_name, "Lowell Bast")
        self.assertEqual(validated.subscription_id, "whsub_1zwvQVQOBPFqrH6xXJEvFJ")

    def test_validate_incoming_webhook_happy_path_direct_crm_body(self) -> None:
        """Happy Path: Validates a direct Close CRM body JSON string."""
        validated = ingestion_models.validate_incoming_webhook(json.dumps(SAMPLE_CRM_BODY))
        self.assertEqual(
            validated.event.lead_id, "lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA"
        )

    def test_validate_incoming_webhook_sad_path_invalid_lead_id_prefix(self) -> None:
        """Sad Path: Rejects payload where lead_id does not start with 'lead_'."""
        bad_payload = json.loads(json.dumps(SAMPLE_CRM_BODY))
        bad_payload["event"]["lead_id"] = "invalid_prefix_123"
        with self.assertRaises(ValidationError):
            ingestion_models.validate_incoming_webhook(bad_payload)

    def test_validate_incoming_webhook_sad_path_missing_required_fields(self) -> None:
        """Sad Path: Rejects payload missing required fields like display_name or status_label."""
        bad_payload = json.loads(json.dumps(SAMPLE_CRM_BODY))
        del bad_payload["event"]["data"]["display_name"]
        with self.assertRaises(ValidationError):
            ingestion_models.validate_incoming_webhook(bad_payload)

    def test_validate_incoming_webhook_sad_path_non_dict_json(self) -> None:
        """Sad Path: Rejects a JSON list or primitive instead of a JSON object."""
        with self.assertRaises(ValueError):
            ingestion_models.validate_incoming_webhook('["not", "an", "object"]')

    def test_validate_incoming_webhook_sad_path_corrupt_inner_body_string(self) -> None:
        """Sad Path: Rejects an API Gateway envelope whose inner 'body' string is invalid JSON."""
        bad_envelope = {"resource": "/crm", "body": "{corrupt-json"}
        with self.assertRaises(ValidationError):
            ingestion_models.validate_incoming_webhook(bad_envelope)

    # =========================================================================
    # 2. `_process_single_record` (Happy & Sad Paths)
    # =========================================================================
    @patch.object(ingestion_handler, "s3_client")
    def test_process_single_record_happy_path_writes_only_body_to_s3(
        self, mock_s3: MagicMock
    ) -> None:
        """Happy Path: Extracts CRM body and writes `in-process/crm_event_{lead_id}.json` to S3."""
        record = {"messageId": "msg-100", "body": json.dumps(SAMPLE_APIGW_ENVELOPE)}
        s3_key = ingestion_handler._process_single_record(record)

        self.assertEqual(
            s3_key,
            "in-process/crm_event_lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA.json",
        )
        mock_s3.put_object.assert_called_once()
        saved_body = json.loads(mock_s3.put_object.call_args.kwargs["Body"].decode("utf-8"))
        self.assertIn("subscription_id", saved_body)
        self.assertIn("event", saved_body)
        self.assertNotIn("headers", saved_body)

    @patch.object(ingestion_handler, "s3_client")
    def test_process_single_record_sad_path_empty_body_raises_value_error(
        self, mock_s3: MagicMock
    ) -> None:
        """Sad Path: Raises ValueError when SQS record body is empty or whitespace."""
        with self.assertRaises(ValueError):
            ingestion_handler._process_single_record({"messageId": "msg-empty", "body": "   "})
        mock_s3.put_object.assert_not_called()

    @patch.object(ingestion_handler, "s3_client")
    def test_process_single_record_sad_path_s3_client_error_propagates(
        self, mock_s3: MagicMock
    ) -> None:
        """Sad Path: Propagates boto3 ClientError when S3 PutObject fails."""
        mock_s3.put_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "PutObject"
        )
        record = {"messageId": "msg-s3-fail", "body": json.dumps(SAMPLE_CRM_BODY)}
        with self.assertRaises(ClientError):
            ingestion_handler._process_single_record(record)

    # =========================================================================
    # 3. `lambda_handler` (Happy & Sad Paths)
    # =========================================================================
    @patch.object(ingestion_handler, "s3_client")
    def test_lambda_handler_happy_path_returns_empty_batch_failures(
        self, mock_s3: MagicMock
    ) -> None:
        """Happy Path: Processes all valid SQS records and returns empty batchItemFailures."""
        sqs_event = {
            "Records": [
                {"messageId": "msg-001", "body": json.dumps(SAMPLE_APIGW_ENVELOPE)},
            ]
        }
        response = ingestion_handler.lambda_handler(sqs_event, None)
        self.assertEqual(response, {"batchItemFailures": []})
        mock_s3.put_object.assert_called_once()

    @patch.object(ingestion_handler, "s3_client")
    def test_lambda_handler_sad_path_partial_batch_failures_for_dlq(
        self, mock_s3: MagicMock
    ) -> None:
        """Sad Path: Captures malformed JSON and Pydantic ValidationError records in batchItemFailures."""
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
        mock_s3.put_object.assert_called_once()


if __name__ == "__main__":
    unittest.main()
