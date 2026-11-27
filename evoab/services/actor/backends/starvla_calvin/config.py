"""Configuration and semantic profile for the CALVIN StarVLA actor."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

MODEL = "StarVLA-QwenGR00T-CALVIN-D-D"
RUNTIME = "roboscope_starvla"

class StarVLACalvinConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    socket_path: Path
    checkpoint: Path
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_timeout_s: float = Field(default=120, gt=0, le=900)


def resolve_config(settings) -> StarVLACalvinConfig:
    return StarVLACalvinConfig.model_validate(settings)


def service_limits(config):
    return {"max_batch_size": 1, "max_sessions": 1}
