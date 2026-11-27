"""Configure the MetaWorld SmolVLA actor without importing native dependencies."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SmolVLAMetaWorldConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkpoint: Path
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    vlm_config: Path
    device: Literal["cpu", "cuda"] = "cuda"
    policy_rng_seed: int = Field(default=0, ge=0, le=2**63 - 1)


def resolve_config(settings) -> SmolVLAMetaWorldConfig:
    return SmolVLAMetaWorldConfig.model_validate(settings)


def service_limits(config):
    return {"max_batch_size": 1, "max_sessions": 1}
