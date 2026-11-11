"""
Unit tests for the Scheduler Lambda (`scripts/scheduler/handler.py`).
"""

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    """Unit test suite for the S3 Event Notification -> EventBridge Scheduler Lambda."""

    def test_build_schedule_name_is_deterministic_and_under_64_chars(self) -> None:
        key = "in-process/crm_event_lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA.json"
        name1 = scheduler_handler._build_schedule_name(key)
        name2 = scheduler_handler._build_schedule_name(key)
        self.assertEqual(name1, name2)
        self.assertLessEqual(len(name1), 64)
        self.assertTrue(name1.startswith("lead-crm_event_lead_"))

    @patch.object(scheduler_handler, "scheduler_client")
    def test_lambda_handler_creates_10_minute_one_time_schedule(
        self, mock_scheduler: MagicMock
    ) -> None:
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
        kwargs = mock_scheduler.create_schedule.call_args.kwargs
        self.assertEqual(kwargs["GroupName"], "close-lead-pipeline-prod-lead-timers")
        self.assertEqual(kwargs["ActionAfterCompletion"], "DELETE")
        self.assertEqual(kwargs["FlexibleTimeWindow"], {"Mode": "OFF"})
        self.assertTrue(kwargs["ScheduleExpression"].startswith("at("))

        target_input = json.loads(kwargs["Target"]["Input"])
        self.assertEqual(target_input["bucket"], "real-time-close-api-messages")
        self.assertEqual(
            target_input["key"],
            "in-process/crm_event_lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA.json",
        )
        self.assertEqual(
            kwargs["Target"]["DeadLetterConfig"]["Arn"],
            "arn:aws:sqs:us-east-1:440107864885:close-lead-pipeline-prod-dlq",
        )

    @patch.object(scheduler_handler, "scheduler_client")
    def test_lambda_handler_skips_folder_marker_objects(
        self, mock_scheduler: MagicMock
    ) -> None:
        s3_event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": "real-time-close-api-messages"},
                        "object": {"key": "in-process/"},
                    }
                }
            ]
        }

        response = scheduler_handler.lambda_handler(s3_event, None)
        self.assertEqual(response["statusCode"], 200)
        mock_scheduler.create_schedule.assert_not_called()


if __name__ == "__main__":
    unittest.main()
