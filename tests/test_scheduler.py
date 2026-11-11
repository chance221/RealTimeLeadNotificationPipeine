"""
Unit tests for the Scheduler Lambda (`scripts/scheduler/handler.py`).
Covers explicit Happy Path and Sad Path test cases for every function in the module.
"""

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

# Set required environment variables before importing the scheduler handler
os.environ.setdefault(
    "PROCESSING_LAMBDA_ARN",
    "arn:aws:lambda:us-east-1:440107864885:function:close-lead-pipeline-prod-processing",
)
os.environ.setdefault(
    "SCHEDULER_ROLE_ARN",
    "arn:aws:iam::440107864885:role/close-lead-pipeline-prod-eb-scheduler-role",
)
os.environ.setdefault(
    "DLQ_ARN",
    "arn:aws:sqs:us-east-1:440107864885:close-lead-pipeline-prod-dlq",
)
os.environ.setdefault("SCHEDULE_GROUP_NAME", "close-lead-pipeline-prod-lead-timers")
os.environ.setdefault("DELAY_MINUTES", "10")

SCHEDULER_DIR = Path(__file__).resolve().parents[1] / "scripts" / "scheduler"
_spec = importlib.util.spec_from_file_location("scheduler_handler", SCHEDULER_DIR / "handler.py")
assert _spec and _spec.loader
scheduler_handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scheduler_handler)


class TestSchedulerLambda(unittest.TestCase):
    """Happy and Sad path unit tests for every function in the Scheduler Lambda."""

    # =========================================================================
    # 1. `_build_schedule_name` (Happy & Sad/Edge Paths)
    # =========================================================================
    def test_build_schedule_name_happy_path_deterministic_and_under_64_chars(self) -> None:
        """Happy Path: Produces a deterministic schedule name <= 64 characters."""
        key = "in-process/crm_event_lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA.json"
        name1 = scheduler_handler._build_schedule_name(key)
        name2 = scheduler_handler._build_schedule_name(key)
        self.assertEqual(name1, name2)
        self.assertLessEqual(len(name1), 64)
        self.assertTrue(name1.startswith("lead-crm_event_lead_"))

    def test_build_schedule_name_sad_path_sanitizes_special_characters_and_long_keys(self) -> None:
        """Sad/Edge Path: Sanitizes unsupported special characters (`@`, spaces, `$`) and truncates >100 char keys."""
        dirty_key = "in-process/crm event$lead@with!invalid#chars_" + ("x" * 100) + ".json"
        schedule_name = scheduler_handler._build_schedule_name(dirty_key)
        self.assertLessEqual(len(schedule_name), 64)
        for ch in schedule_name:
            self.assertTrue(ch.isalnum() or ch in ("-", "_"))

    # =========================================================================
    # 2. `_schedule_single_s3_record` (Happy & Sad Paths)
    # =========================================================================
    @patch.object(scheduler_handler, "scheduler_client")
    def test_schedule_single_s3_record_happy_path_creates_schedule(
        self, mock_scheduler: MagicMock
    ) -> None:
        """Happy Path: Parses S3 record and creates a one-time 10-minute EventBridge schedule."""
        record = {
            "s3": {
                "bucket": {"name": "real-time-close-api-messages"},
                "object": {
                    "key": "in-process/crm_event_lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA.json"
                },
            }
        }
        result = scheduler_handler._schedule_single_s3_record(record)
        self.assertEqual(result["status"], "scheduled")
        mock_scheduler.create_schedule.assert_called_once()

    @patch.object(scheduler_handler, "scheduler_client")
    def test_schedule_single_s3_record_sad_path_missing_bucket_or_key_raises_value_error(
        self, mock_scheduler: MagicMock
    ) -> None:
        """Sad Path: Raises ValueError when S3 event record is missing bucket or object key."""
        with self.assertRaises(ValueError):
            scheduler_handler._schedule_single_s3_record({"s3": {"bucket": {}, "object": {}}})
        mock_scheduler.create_schedule.assert_not_called()

    @patch.object(scheduler_handler, "scheduler_client")
    def test_schedule_single_s3_record_sad_path_skips_folder_marker_or_non_json(
        self, mock_scheduler: MagicMock
    ) -> None:
        """Sad Path: Skips S3 folder markers (`in-process/`) and non-JSON files without calling EventBridge."""
        folder_record = {
            "s3": {
                "bucket": {"name": "real-time-close-api-messages"},
                "object": {"key": "in-process/"},
            }
        }
        result = scheduler_handler._schedule_single_s3_record(folder_record)
        self.assertEqual(result["status"], "skipped")
        mock_scheduler.create_schedule.assert_not_called()

    # =========================================================================
    # 3. `lambda_handler` (Happy & Sad Paths)
    # =========================================================================
    @patch.object(scheduler_handler, "scheduler_client")
    def test_lambda_handler_happy_path_schedules_all_records(
        self, mock_scheduler: MagicMock
    ) -> None:
        """Happy Path: Processes S3 event notification batch and returns 200 OK."""
        s3_event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "real-time-close-api-messages"},
                        "object": {
                            "key": "in-process/crm_event_lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA.json"
                        },
                    }
                }
            ]
        }

        response = scheduler_handler.lambda_handler(s3_event, None)
        self.assertEqual(response["statusCode"], 200)
        mock_scheduler.create_schedule.assert_called_once()

    @patch.object(scheduler_handler, "scheduler_client")
    def test_lambda_handler_sad_path_propagates_client_error_for_dlq_routing(
        self, mock_scheduler: MagicMock
    ) -> None:
        """Sad Path: Re-raises EventBridge ClientError so Lambda retries and sends failed event to DLQ."""
        mock_scheduler.exceptions.ConflictException = type("ConflictException", (Exception,), {})
        mock_scheduler.create_schedule.side_effect = ClientError(
            {"Error": {"Code": "InternalServerException", "Message": "Service failure"}},
            "CreateSchedule",
        )
        s3_event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "real-time-close-api-messages"},
                        "object": {
                            "key": "in-process/crm_event_lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA.json"
                        },
                    }
                }
            ]
        }

        with self.assertRaises(ClientError):
            scheduler_handler.lambda_handler(s3_event, None)


if __name__ == "__main__":
    unittest.main()
