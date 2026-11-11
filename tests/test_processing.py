"""
Unit tests for the Processing & SES Lambda (`scripts/processing/handler.py` & `scripts/processing/models.py`).
"""

import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from pydantic import ValidationError

PROCESSING_DIR = Path(__file__).resolve().parents[1] / "scripts" / "processing"


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load `processing/models.py` as `models` during `processing/handler.py` import
import sys  # noqa: E402

sys.path.insert(0, str(PROCESSING_DIR))
processing_models = _load_module("models", PROCESSING_DIR / "models.py")
sys.modules["models"] = processing_models
processing_handler = _load_module("processing_handler", PROCESSING_DIR / "handler.py")
sys.path.remove(str(PROCESSING_DIR))


SAMPLE_CRM_BODY = {
    "subscription_id": "whsub_1zwvQVQOBPFqrH6xXJEvFJ",
    "event": {
        "id": "ev_1ntH1vAE4G7DNjNZYkMeck",
        "date_created": "2025-05-20T12:14:56.446000",
        "lead_id": "lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA",
        "object_type": "lead",
        "action": "created",
        "data": {
            "id": "lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA",
            "display_name": "Lowell Bast",
            "status_label": "Potential",
            "date_created": "2025-05-20T12:14:56.409000+00:00",
            "custom.cf_am3UgCUhyM5iNDtAPL84enDjUrZx1JsyVZ9uD9TbYwG": "DE ACADEMY Direct VSL",
        },
    },
}

SAMPLE_LOOKUP_PAYLOAD = {
    "lead_id": "lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA",
    "display_name": "Lowell Bast",
    "lead_email": "gatequdit@gmail.com",
    "lead_owner": "Lucija Bitunjac",
    "funnel": "DE ACADEMY Direct VSL",
    "status_label": "Potential",
    "date_created": "2025-05-20T12:14:56.409000+00:00",
}


class TestProcessingLambda(unittest.TestCase):
    """Unit test suite for the Processing & SES Lambda handler and Pydantic models."""

    def test_lead_owner_lookup_pydantic_validation_success(self) -> None:
        lookup = processing_models.LeadOwnerLookup.model_validate(SAMPLE_LOOKUP_PAYLOAD)
        self.assertEqual(lookup.lead_email, "gatequdit@gmail.com")
        self.assertEqual(lookup.lead_owner, "Lucija Bitunjac")
        self.assertEqual(lookup.funnel, "DE ACADEMY Direct VSL")

    def test_lead_owner_lookup_pydantic_rejects_invalid_email(self) -> None:
        bad_lookup = dict(SAMPLE_LOOKUP_PAYLOAD, lead_email="not-an-email")
        with self.assertRaises(ValidationError):
            processing_models.LeadOwnerLookup.model_validate(bad_lookup)

    def test_build_prescribed_email_matches_exact_template(self) -> None:
        crm_model = processing_models.CloseWebhookBody.model_validate(SAMPLE_CRM_BODY)
        lookup_model = processing_models.LeadOwnerLookup.model_validate(SAMPLE_LOOKUP_PAYLOAD)

        subject, text_body, _ = processing_handler._build_prescribed_email(crm_model, lookup_model)
        self.assertIn("Lowell Bast", subject)

        expected_text = (
            "Name:  Lowell Bast\n"
            "Lead ID : lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA\n"
            "Created Date: 2025-05-20T12:14:56.409000+00:00\n"
            "Label: Potential\n"
            "Email: gatequdit@gmail.com\n"
            "Lead Owner: Lucija Bitunjac\n"
            "Funnel: DE ACADEMY Direct VSL\n"
        )
        self.assertEqual(text_body, expected_text)

    @patch.object(processing_handler, "urllib")
    @patch.object(processing_handler, "ses_client")
    @patch.object(processing_handler, "s3_client")
    def test_lambda_handler_processes_lead_sends_email_and_moves_to_processed(
        self,
        mock_s3: MagicMock,
        mock_ses: MagicMock,
        mock_urllib: MagicMock,
    ) -> None:
        # Simulate that `processed/crm_event_...json` does not yet exist (404 on head_object)
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        mock_s3.get_object.return_value = {
            "Body": io.BytesIO(json.dumps(SAMPLE_CRM_BODY).encode("utf-8"))
        }

        # Mock HTTP response from https://dea-lead-owner.s3.us-east-1.amazonaws.com/{lead_id}.json
        mock_http_resp = MagicMock()
        mock_http_resp.read.return_value = json.dumps(SAMPLE_LOOKUP_PAYLOAD).encode("utf-8")
        mock_http_resp.__enter__.return_value = mock_http_resp
        mock_urllib.request.urlopen.return_value = mock_http_resp

        mock_ses.send_email.return_value = {"MessageId": "ses-msg-123"}

        event = {
            "bucket": "real-time-close-api-messages",
            "key": "in-process/crm_event_lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA.json",
            "schedule_name": "lead-test-schedule",
        }

        result = processing_handler.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 200)
        body = json.loads(result["body"])
        self.assertEqual(body["status"], "processed")
        self.assertEqual(body["ses_message_id"], "ses-msg-123")
        self.assertEqual(
            body["processed_key"],
            "processed/crm_event_lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA.json",
        )

        # Verify SES email was sent to intelectual221@gmail.com
        mock_ses.send_email.assert_called_once()
        ses_kwargs = mock_ses.send_email.call_args.kwargs
        self.assertEqual(ses_kwargs["Destination"]["ToAddresses"], ["intelectual221@gmail.com"])

        # Verify object was copied to `processed/` and deleted from `in-process/`
        mock_s3.put_object.assert_called_once()
        self.assertEqual(
            mock_s3.put_object.call_args.kwargs["Key"],
            "processed/crm_event_lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA.json",
        )
        mock_s3.delete_object.assert_called_once_with(
            Bucket="real-time-close-api-messages",
            Key="in-process/crm_event_lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA.json",
        )

    @patch.object(processing_handler, "ses_client")
    @patch.object(processing_handler, "s3_client")
    def test_lambda_handler_idempotency_skips_duplicate_email_if_already_processed(
        self,
        mock_s3: MagicMock,
        mock_ses: MagicMock,
    ) -> None:
        # Simulate that `processed/crm_event_...json` ALREADY exists
        mock_s3.head_object.return_value = {"ContentLength": 500}

        event = {
            "bucket": "real-time-close-api-messages",
            "key": "in-process/crm_event_lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA.json",
        }

        result = processing_handler.lambda_handler(event, None)
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(json.loads(result["body"])["status"], "already_processed")
        mock_ses.send_email.assert_not_called()


if __name__ == "__main__":
    unittest.main()
