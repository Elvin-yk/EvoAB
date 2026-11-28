"""External observation enhancement tool package for EvoAB."""
from __future__ import annotations

from .config import (
    AutoPromptConfig,
    ObservationPipelineConfig,
    PluginConfig,
    RenderConfig,
    TargetConfig,
    TargetsConfig,
    load_pipeline_config,
)
from .contracts import (
    DetectionAnnotation,
    FrameInput,
    Pose6D,
    TargetSpec,
    VisualProcessor,
)
from .pipeline import ObservationPipeline
from .proxy import ObservationProxyServer

__all__ = [
    "AutoPromptConfig",
    "DetectionAnnotation",
    "FrameInput",
    "ObservationPipeline",
    "ObservationPipelineConfig",
    "ObservationProxyServer",
    "PluginConfig",
    "Pose6D",
    "RenderConfig",
    "TargetConfig",
    "TargetSpec",
    "TargetsConfig",
    "VisualProcessor",
    "load_pipeline_config",
]
