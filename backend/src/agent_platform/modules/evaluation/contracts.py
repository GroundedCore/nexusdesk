"""Evaluation request contracts.

Lives apart from :mod:`.service` so the spreadsheet reader can validate rows without
importing the service (which itself imports the reader).
"""

import json

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    input: str = Field(min_length=1, max_length=8000)
    expected_contains: list[str] = Field(default_factory=list, max_length=20)
    forbidden_contains: list[str] = Field(default_factory=list, max_length=20)
    expected_tools: list[str] | None = None
    mock_tools: dict = Field(default_factory=dict)
    reference_answers: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def valid(self):
        if (
            not self.expected_contains
            and not self.forbidden_contains
            and self.expected_tools is None
        ):
            raise ValueError("At least one assertion is required")
        if len(json.dumps(self.mock_tools)) > 50000:
            raise ValueError("Mock responses too large")
        if any(not s.strip() or len(s) > 8000 for s in self.reference_answers):
            raise ValueError("Reference answers must be nonempty and at most 8000 characters")
        return self
