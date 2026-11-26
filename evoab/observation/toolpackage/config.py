"""Configuration schema and loader for the observation enhancement tool package."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
import yaml


class TargetConfig(BaseModel):
    """Explicit target prompt and optional 3D asset reference."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    prompt: str | None = None
    mesh_path: str | None = None
    mesh_id: str | None = None
    color: str | None = None
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)

    def effective_prompt(self) -> str:
        return (self.prompt or self.label).strip()


class TargetsConfig(BaseModel):
    """Task-scoped target overrides plus optional global defaults."""

    model_config = ConfigDict(extra="forbid")

    default: list[TargetConfig] = Field(default_factory=list)
    by_task: dict[str, list[TargetConfig]] = Field(default_factory=dict)


class AutoPromptConfig(BaseModel):
    """Automatic prompt extraction when no explicit task target list is configured."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    strategy: Literal["hybrid", "rule", "vlm"] = "hybrid"
    max_targets: int = Field(default=6, ge=1, le=24)
    vlm_base_url: str | None = None
    vlm_model: str | None = None
    vlm_api_key_env: str = "EVOAB_API_KEY"
    vlm_timeout_s: float = Field(default=30.0, gt=0)
    include_first_frame: bool = True


class PluginConfig(BaseModel):
    """One ordered processor step in the visual observation pipeline."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    enabled: bool = True
    cameras: list[str] | None = None
    fail_open: bool = True
    options: dict[str, Any] = Field(default_factory=dict)


class RenderConfig(BaseModel):
    """Non-destructive visual overlay settings (always preserves source HxW)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    draw_mask_fill: bool = True
    mask_fill_alpha: float = Field(default=0.18, ge=0.0, le=0.8)
    draw_mask_contour: bool = True
    contour_width: int = Field(default=2, ge=1, le=8)
    draw_bbox: bool = True
    bbox_width: int = Field(default=2, ge=1, le=8)
    draw_labels: bool = True
    draw_centroid_3d: bool = True
    draw_pose_axes: bool = True
    pose_axis_length_m: float = Field(default=0.06, gt=0.0, le=0.5)
    draw_bbox_3d: bool = True
    draw_hud: bool = True
    hud_position: Literal["top", "bottom"] = "bottom"
    hud_max_items: int = Field(default=6, ge=1, le=16)


class ObservationPipelineConfig(BaseModel):
    """Top-level configuration for one observation enhancement tool package."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    cameras: list[str] | None = None
    targets: TargetsConfig = Field(default_factory=TargetsConfig)
    auto_prompt: AutoPromptConfig = Field(default_factory=AutoPromptConfig)
    plugins: list[PluginConfig] = Field(default_factory=list)
    render: RenderConfig = Field(default_factory=RenderConfig)
    inject_metadata: bool = True
    dedup_iou_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    enhance_step_snapshots: bool = False
    archive_images: bool = True

    @field_validator("cameras")
    @classmethod
    def _distinct_cameras(cls, value: list[str] | None) -> list[str] | None:
        if value is not None:
            if not value or len(set(value)) != len(value):
                raise ValueError("cameras must be a non-empty list of distinct camera names")
        return value


def load_pipeline_config(path: str | Path) -> ObservationPipelineConfig:
    """Read and validate a YAML or JSON observation pipeline configuration offline."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Observation pipeline config not found: {source}")
    text = source.read_text(encoding="utf-8")
    try:
        raw = json.loads(text) if source.suffix.lower() == ".json" else yaml.safe_load(text)
    except Exception as error:
        raise ValueError(f"Could not parse observation pipeline config {source}: {error}") from error
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("Observation pipeline config must be a YAML/JSON mapping")
    config = ObservationPipelineConfig.model_validate(raw)
    # Resolve relative mesh paths against the config file directory.
    base = source.parent
    resolved_default = [_resolve_target_paths(item, base) for item in config.targets.default]
    resolved_by_task = {
        task: [_resolve_target_paths(item, base) for item in items]
        for task, items in config.targets.by_task.items()
    }
    return config.model_copy(update={
        "targets": TargetsConfig(default=resolved_default, by_task=resolved_by_task),
    })


def _resolve_target_paths(target: TargetConfig, base: Path) -> TargetConfig:
    if not target.mesh_path:
        return target
    candidate = Path(target.mesh_path).expanduser()
    if not candidate.is_absolute():
        candidate = (base / candidate).resolve()
    return target.model_copy(update={"mesh_path": str(candidate)})
