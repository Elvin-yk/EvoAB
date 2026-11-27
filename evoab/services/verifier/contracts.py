"""Path-only verification requests and completion receipts."""
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class VerificationJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    native_outcome_path: Path
    trajectory_path: Path
    result_path: Path

    @classmethod
    def for_session(cls, directory: Path, request_id: UUID) -> "VerificationJob":
        directory = directory.resolve()
        return cls(request_id=request_id, native_outcome_path=directory / "native_outcome.json",
                   trajectory_path=directory / "trajectory.jsonl", result_path=directory / "result.json")


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    status: Literal["completed"]
