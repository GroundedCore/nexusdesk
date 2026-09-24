from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from .contracts import Contract


class SensitiveWord(Contract):
    name: str = Field(min_length=1, max_length=100)
    category: str = Field(default="其他", min_length=1, max_length=100)

    @field_validator("name", "category")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class WordImport(Contract):
    words: list[SensitiveWord] = Field(min_length=1, max_length=1000)


class ReviewTest(Contract):
    text: str = Field(max_length=200000)


class AccessKey(Contract):
    name: str = Field(min_length=1, max_length=100)
    profile_ids: list[UUID] = Field(min_length=1, max_length=100)
    expires_at: datetime
    requests_per_minute: int = Field(default=60, ge=1, le=100000)
    requests_per_day: int = Field(default=10000, ge=1, le=10000000)
    concurrency: int = Field(default=4, ge=1, le=1000)

    @field_validator("expires_at")
    @classmethod
    def future_expiry(cls, value):
        if value.tzinfo is None or value <= datetime.now(UTC):
            raise ValueError("expiry must be a future timezone-aware timestamp")
        return value


class AlertRule(Contract):
    name: str = Field(min_length=1, max_length=100)
    metric: Literal["failure", "latency", "content_blocked"] = "failure"
    profile_id: UUID | None = None
    threshold_ms: int = Field(default=5000, ge=1, le=600000)
    cooldown_seconds: int = Field(default=60, ge=0, le=86400)


class Pricing(Contract):
    input_per_million: Decimal | None = Field(default=None, ge=0, le=1000000)
    output_per_million: Decimal | None = Field(default=None, ge=0, le=1000000)
    audio_per_second: Decimal | None = Field(default=None, ge=0, le=10000)
    per_thousand_characters: Decimal | None = Field(default=None, ge=0, le=1000000)
    per_page: Decimal | None = Field(default=None, ge=0, le=10000)


class ModelSettings(Contract):
    input_review: Literal["off", "sensitive_words"] = "off"
    output_review: Literal["off", "sensitive_words"] = "off"
    pricing: Pricing = Field(default_factory=Pricing)


class Quota(Contract):
    requests_per_minute: int = Field(default=1000, ge=1, le=1000000)
    requests_per_day: int = Field(default=100000, ge=1, le=100000000)
    concurrency: int = Field(default=64, ge=1, le=10000)


class QuotaUpdate(Contract):
    revision: int = Field(ge=0)
    spec: Quota


class ModelSettingsUpdate(Contract):
    revision: int = Field(ge=0)
    spec: ModelSettings
