"""Optional observation services, created only when a profile is selected."""

from __future__ import annotations

import importlib
from typing import Any

from evoab.envs.observation.hub import (
    InsightRecord,
    InsightStore,
    ObservationData,
    ObservationHub,
    ObservationProvider,
    ObservationSnapshot,
    ObservationStore,
)


def load_observation_profile(name: str) -> Any:
    """An integration's own observation profile, or the shared snapshot profile every environment supports."""
    module = importlib.import_module(f"evoab.envs.{name}")
    if callable(getattr(module, "create_observation_profile", None)):
        return module
    return importlib.import_module("evoab.envs.observation.episode")

__all__ = [
    "InsightRecord",
    "InsightStore",
    "ObservationData",
    "ObservationHub",
    "ObservationProvider",
    "ObservationSnapshot",
    "ObservationStore",
]
