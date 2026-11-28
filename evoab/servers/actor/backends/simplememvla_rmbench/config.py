"""Configure the pinned upstream SimpleMemVLA evaluator."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

class SimpleMemVLARMBenchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    native_root: Path
    checkpoint: Path
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_rng_seed: int = Field(default=0, ge=0, le=2**63 - 1)


def resolve_config(settings) -> SimpleMemVLARMBenchConfig:
    return SimpleMemVLARMBenchConfig.model_validate(settings)


def service_limits(config):
    return {"max_batch_size": 1, "max_sessions": 1}
