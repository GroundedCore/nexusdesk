from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

RunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
TERMINAL = frozenset({"completed", "failed", "cancelled"})


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    conversation_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=8000)


class RunView(BaseModel):
    id: UUID
    conversation_id: UUID
    status: RunStatus
    cancel_requested: bool
    output: str | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RuntimeFault(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class BusyError(Exception):
    pass


class CapacityError(Exception):
    pass
