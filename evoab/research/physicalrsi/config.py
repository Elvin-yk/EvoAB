"""Bounded research configuration; no simulator or upstream imports."""
from pydantic import BaseModel, ConfigDict, Field


class ResearchConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    integration: str = 'metaworld'
    task: str = 'reach-v3'
    model: str
    reasoning_effort: str = 'low'
    rounds: int = Field(default=2, ge=1, le=20)
    candidates: int = Field(default=1, ge=1, le=4)
    development_cases: int = Field(default=1, ge=1, le=20)
    validation_cases: int = Field(default=3, ge=2, le=50)
    test_cases: int = Field(default=1, ge=1, le=50)
    max_turns: int = Field(default=8, ge=1, le=100)
    episode_seconds: int = Field(default=600, ge=60, le=7200)
    seed: int = Field(default=0, ge=0, le=1000000)
    max_trials: int = Field(default=200, ge=1, le=10000)
