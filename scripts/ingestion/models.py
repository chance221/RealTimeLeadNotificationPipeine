"""
Pydantic v2 Data Contracts for Close CRM Webhook Ingestion.

Validates:
1. `CloseLeadData`: Required fields inside `event.data` (`id`, `display_name`, `status_label`, `date_created`).
2. `CloseEvent`: Required fields inside `event` (`id`, `lead_id`, `object_type`, `action`, `data`).
3. `CloseWebhookBody`: The `body` object stored in S3 as `crm_event_{lead_id}.json` (`subscription_id`, `event`).
4. `CloseWebhookEnvelope`: Optional outer API Gateway envelope (`resource`, `headers`, `requestContext`, `body`)
   matching `sample_wh_response.json`.
"""

import json
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CloseLeadData(BaseModel):
    """Validates the required Close CRM lead attributes inside `event.data`."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., min_length=1, description="Unique Close Lead ID (e.g., lead_...)")
    display_name: str = Field(..., min_length=1, description="Lead display name")
    status_label: str = Field(..., min_length=1, description="Lead status label (e.g., Potential)")
    date_created: str = Field(..., min_length=1, description="ISO-8601 lead creation timestamp")

    @field_validator("id")
    @classmethod
    def validate_lead_id_prefix(cls, value: str) -> str:
        if not value.startswith("lead_"):
            raise ValueError(f"Expected Close lead id to start with 'lead_', got '{value}'")
        return value


class CloseEvent(BaseModel):
    """Validates the `event` object inside the Close CRM webhook body."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., min_length=1, description="Unique Close Event ID (e.g., ev_...)")
    lead_id: str = Field(..., min_length=1, description="Close Lead ID associated with the event")
    object_type: str = Field(..., min_length=1, description="Close object type (e.g., lead)")
    action: str = Field(..., min_length=1, description="Webhook action (e.g., created)")
    data: CloseLeadData

    @field_validator("lead_id")
    @classmethod
    def validate_event_lead_id(cls, value: str) -> str:
        if not value.startswith("lead_"):
            raise ValueError(f"Expected event.lead_id to start with 'lead_', got '{value}'")
        return value


class CloseWebhookBody(BaseModel):
    """
    Validates the Close CRM webhook `body` that is persisted to S3
    as `in-process/crm_event_{lead_id}.json`.
    Preserves all extra fields (`extra="allow"`) so the exact raw CRM body is saved.
    """

    model_config = ConfigDict(extra="allow")

    subscription_id: str = Field(..., min_length=1, description="Close webhook subscription ID")
    event: CloseEvent


class CloseWebhookEnvelope(BaseModel):
    """
    Validates an API Gateway proxy envelope (like `sample_wh_response`) where the
    Close webhook payload is nested inside a top-level `"body"` field.
    """

    model_config = ConfigDict(extra="allow")

    body: CloseWebhookBody

    @field_validator("body", mode="before")
    @classmethod
    def parse_json_string_body(cls, value: Union[str, Dict[str, Any]]) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON string in envelope 'body': {exc}") from exc
        return value


def validate_incoming_webhook(raw_payload: Union[str, Dict[str, Any]]) -> CloseWebhookBody:
    """
    Validates an incoming webhook payload (either an API Gateway envelope with `"body"`
    or a direct Close webhook body) and returns the validated `CloseWebhookBody` model.
    """
    parsed: Any = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object for webhook payload, got {type(parsed).__name__}")

    if "body" in parsed and "event" not in parsed:
        envelope = CloseWebhookEnvelope.model_validate(parsed)
        return envelope.body

    return CloseWebhookBody.model_validate(parsed)
