"""
Pydantic v2 Data Contracts for Lead Processing & S3 Lead Owner Lookup.

Validates:
1. `CloseWebhookBody`: The CRM event JSON read from `s3://.../in-process/crm_event_{lead_id}.json`,
   ensuring `display_name`, `lead_id`, `date_created`, and `status_label` are present and valid.
2. `LeadOwnerLookup`: The JSON object fetched from `https://dea-lead-owner.s3.us-east-1.amazonaws.com/{lead_id}.json`,
   ensuring `lead_id`, `display_name`, `lead_email`, `lead_owner`, `funnel`, `status_label`, and `date_created`
   are present and valid.
3. `ScheduledLeadEvent`: The event payload passed by Amazon EventBridge Scheduler after the 10-minute timer.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CloseLeadData(BaseModel):
    """Validates the required CRM Event data fields inside `event.data`."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., min_length=1, description="Close Lead ID")
    display_name: str = Field(..., min_length=1, description="Lead display name")
    status_label: str = Field(..., min_length=1, description="Lead status label (e.g., Potential)")
    date_created: str = Field(..., min_length=1, description="Lead creation timestamp")


class CloseEvent(BaseModel):
    """Validates the `event` object inside `crm_event_{lead_id}.json`."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(..., min_length=1)
    lead_id: str = Field(..., min_length=1)
    data: CloseLeadData


class CloseWebhookBody(BaseModel):
    """Validates the `crm_event_{lead_id}.json` file stored in S3."""

    model_config = ConfigDict(extra="allow")

    subscription_id: str = Field(..., min_length=1)
    event: CloseEvent


class LeadOwnerLookup(BaseModel):
    """
    Validates the lead owner lookup payload returned from
    `https://dea-lead-owner.s3.us-east-1.amazonaws.com/{lead_id}.json`:
    {
      "lead_id": "lead_niuYPXlw6vnFQIhwZCKDaaKv9XMQs9KA5NgxhNRBgaA",
      "display_name": "Lowell Bast",
      "lead_email": "gatequdit@gmail.com",
      "lead_owner": "Lucija Bitunjac",
      "funnel": "DE ACADEMY Direct VSL",
      "status_label": "Potential",
      "date_created": "2025-05-20T12:14:56.409000+00:00"
    }
    """

    model_config = ConfigDict(extra="allow")

    lead_id: str = Field(..., min_length=1, description="Close Lead ID")
    display_name: str = Field(..., min_length=1, description="Lead full name")
    lead_email: str = Field(..., min_length=3, description="Lead email address")
    lead_owner: str = Field(..., min_length=1, description="Assigned Lead Owner name")
    funnel: str = Field(..., min_length=1, description="Marketing/Sales funnel name")
    status_label: Optional[str] = Field(default=None, description="Lead status label")
    date_created: Optional[str] = Field(default=None, description="Lead creation timestamp")

    @field_validator("lead_email")
    @classmethod
    def validate_email_format(cls, value: str) -> str:
        cleaned = value.strip()
        if "@" not in cleaned or "." not in cleaned.split("@")[-1]:
            raise ValueError(f"Invalid email address in lead_email: '{value}'")
        return cleaned


class ScheduledLeadEvent(BaseModel):
    """Validates the payload delivered by Amazon EventBridge Scheduler."""

    model_config = ConfigDict(extra="allow")

    bucket: Optional[str] = None
    key: str = Field(..., min_length=1, description="S3 object key under in-process/")
    schedule_name: Optional[str] = None
