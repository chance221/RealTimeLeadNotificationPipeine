"""
Unit tests for the Processing & SES Lambda (`scripts/processing/handler.py` & `scripts/processing/models.py`).
Covers explicit Happy Path and Sad Path test cases for every function and Pydantic model.
"""

import importlib.util
import io
import json
import sys
import unittest
import urllib.error
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
    """Happy and Sad path unit tests for every function in the Processing & SES Lambda."""

    # =========================================================================
    # 1. Pydantic Models (`LeadOwnerLookup`, `CloseWebhookBody`, `ScheduledLeadEvent`)
    # =========================================================================
    def test_lead_owner_lookup_happy_path_validates_sample_payload(self) -> None:
        """Happy Path: Validates a well-formed S3 lead owner lookup payload."""
        lookup = processing_models.LeadOwnerLookup.model_validate(SAMPLE_LOOKUP_PAYLOAD)
        self.assertEqual(lookup.lead_email, "gatequdit@gmail.com")
        self.assertEqual(lookup.lead_owner, "Lucija Bitunjac")
        self.assertEqual(lookup.funnel, "DE ACADEMY Direct VSL")

    def test_lead_owner_lookup_sad_path_rejects_invalid_email_or_missing_owner(self) -> None:
        """Sad Path: Rejects lookup payload with malformed email or missing lead_owner."""
        bad_email = dict(SAMPLE_LOOKUP_PAYLOAD, lead_email="invalid-email-address")
        with self.assertRaises(ValidationError):
            processing_models.LeadOwnerLookup.model_validate(bad_email)

        missing_owner = dict(SAMPLE_LOOKUP_PAYLOAD)
        del missing_owner["lead_owner"]
        with self.assertRaises(ValidationError):
            processing_models.LeadOwnerLookup.model_validate(missing_owner)

    # =========================================================================
    # 2. `_fetch_and_validate_lead_owner_lookup` (Happy & Sad Paths)
    # =========================================================================
    @patch.object(processing_handler, "urllib")
    def test_fetch_and_validate_lead_owner_lookup_happy_path(
        self, mock_urllib: MagicMock
    ) -> None:
        """Happy Path: Fetches JSON from dea-lead-owner S3 URL and returns validated LeadOwnerLookup."""
        mock_http_resp = MagicMock()
        mock_http_resp.read.return_value = json.dumps(SAMPLE_LOOKUP_PAYLOAD).encode("utf-8")
        mock_http_resp.__enter__.return_value = mock_http_resp
        mock_urllib.request.urlopen.return_value = mock_http_resp

        result = processing_handler._fetch_and_validate_lead_owner_lookup(
            "lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA"
        )
        self.assertEqual(result.lead_owner, "Lucija Bitunjac")
        self.assertEqual(result.lead_email, "gatequdit@gmail.com")

    @patch.object(processing_handler, "urllib")
    def test_fetch_and_validate_lead_owner_lookup_sad_path_http_404_raises_runtime_error(
        self, mock_urllib: MagicMock
    ) -> None:
        """Sad Path: Raises RuntimeError when public S3 lookup returns HTTP 404/403."""
        mock_urllib.error = urllib.error
        mock_urllib.request.urlopen.side_effect = urllib.error.HTTPError(
            url="https://dea-lead-owner.s3.us-east-1.amazonaws.com/lead_missing.json",
            code=404,
            msg="Not Found",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )
        with self.assertRaises(RuntimeError):
            processing_handler._fetch_and_validate_lead_owner_lookup("lead_missing")

    @patch.object(processing_handler, "urllib")
    def test_fetch_and_validate_lead_owner_lookup_sad_path_invalid_schema_raises_validation_error(
        self, mock_urllib: MagicMock
    ) -> None:
        """Sad Path: Raises Pydantic ValidationError if lookup S3 bucket returns incomplete JSON."""
        mock_urllib.error = urllib.error
        mock_http_resp = MagicMock()
        mock_http_resp.read.return_value = json.dumps({"lead_id": "lead_123"}).encode("utf-8")
        mock_http_resp.__enter__.return_value = mock_http_resp
        mock_urllib.request.urlopen.return_value = mock_http_resp

        with self.assertRaises(ValidationError):
            processing_handler._fetch_and_validate_lead_owner_lookup("lead_123")

    # =========================================================================
    # 3. `_build_prescribed_email` & `_send_ses_email` (Happy & Sad Paths)
    # =========================================================================
    def test_build_prescribed_email_happy_path_matches_exact_template(self) -> None:
        """Happy Path: Formats the exact prescribed email text and HTML bodies."""
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

    @patch.object(processing_handler, "ses_client")
    def test_send_ses_email_happy_path_returns_message_id(self, mock_ses: MagicMock) -> None:
        """Happy Path: Calls SES send_email and returns the SES MessageId."""
        mock_ses.send_email.return_value = {"MessageId": "ses-ok-999"}
        msg_id = processing_handler._send_ses_email(
            "Subject", "Text Body", "<p>HTML Body</p>", "lead_123"
        )
        self.assertEqual(msg_id, "ses-ok-999")
        mock_ses.send_email.assert_called_once()

    @patch.object(processing_handler, "ses_client")
    def test_send_ses_email_sad_path_ses_rejection_raises_client_error(
        self, mock_ses: MagicMock
    ) -> None:
        """Sad Path: Propagates ClientError when SES rejects an unverified sender/recipient."""
        mock_ses.send_email.side_effect = ClientError(
            {"Error": {"Code": "MessageRejected", "Message": "Email address is not verified"}},
            "SendEmail",
        )
        with self.assertRaises(ClientError):
            processing_handler._send_ses_email(
                "Subject", "Text Body", "<p>HTML Body</p>", "lead_123"
            )

    # =========================================================================
    # 4. `_object_exists` (Happy & Sad Paths)
    # =========================================================================
    @patch.object(processing_handler, "s3_client")
    def test_object_exists_happy_path_returns_true_or_false_on_404(
        self, mock_s3: MagicMock
    ) -> None:
        """Happy Path: Returns True when object exists and False on 404 Not Found."""
        mock_s3.head_object.return_value = {"ContentLength": 123}
        self.assertTrue(processing_handler._object_exists("bucket", "key"))

        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        self.assertFalse(processing_handler._object_exists("bucket", "key"))

    @patch.object(processing_handler, "s3_client")
    def test_object_exists_sad_path_re_raises_unexpected_s3_error(
        self, mock_s3: MagicMock
    ) -> None:
        """Sad Path: Re-raises non-404 ClientError (such as 403 Forbidden)."""
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject"
        )
        with self.assertRaises(ClientError):
            processing_handler._object_exists("bucket", "key")

    # =========================================================================
    # 5. `lambda_handler` (Happy & Sad Paths)
    # =========================================================================
    @patch.object(processing_handler, "urllib")
    @patch.object(processing_handler, "ses_client")
    @patch.object(processing_handler, "s3_client")
    def test_lambda_handler_happy_path_processes_lead_emails_and_moves_file(
        self,
        mock_s3: MagicMock,
        mock_ses: MagicMock,
        mock_urllib: MagicMock,
    ) -> None:
        """Happy Path: Reads in-process JSON, fetches lookup, sends SES email, moves to processed/."""
        mock_s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        mock_s3.get_object.return_value = {
            "Body": io.BytesIO(json.dumps(SAMPLE_CRM_BODY).encode("utf-8"))
        }

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
        mock_ses.send_email.assert_called_once()
        mock_s3.put_object.assert_called_once()
        mock_s3.delete_object.assert_called_once()

    @patch.object(processing_handler, "ses_client")
    @patch.object(processing_handler, "s3_client")
    def test_lambda_handler_sad_path_missing_event_key_raises_validation_error(
        self,
        mock_s3: MagicMock,
        mock_ses: MagicMock,
    ) -> None:
        """Sad Path: Rejects scheduled event missing the required 'key' field via Pydantic."""
        with self.assertRaises(ValidationError):
            processing_handler.lambda_handler({"bucket": "real-time-close-api-messages"}, None)
        mock_s3.get_object.assert_not_called()
        mock_ses.send_email.assert_not_called()


if __name__ == "__main__":
    unittest.main()
