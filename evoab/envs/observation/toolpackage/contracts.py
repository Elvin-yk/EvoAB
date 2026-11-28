"""Typed contracts for external visual-observation enhancement pipelines."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np


@dataclass(frozen=True)
class TargetSpec:
    """One semantic object target to detect, localize, or track across observations."""

    label: str
    prompt: str
    mesh_path: str | None = None
    mesh_id: str | None = None
    color: str | None = None
    min_score: float | None = None
    source: str = "config"

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "prompt": self.prompt,
            "mesh_path": self.mesh_path,
            "mesh_id": self.mesh_id,
            "color": self.color,
            "min_score": self.min_score,
            "source": self.source,
        }


@dataclass(frozen=True)
class Pose6D:
    """Rigid 6D object pose in camera optical and optional world frames."""

    T_camera_from_object: list[list[float]]
    T_world_from_object: list[list[float]] | None = None
    position_world_m: list[float] | None = None
    quaternion_world_wxyz: list[float] | None = None
    bbox_3d_extents_m: list[float] | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "T_camera_from_object": self.T_camera_from_object,
            "T_world_from_object": self.T_world_from_object,
            "position_world_m": self.position_world_m,
            "quaternion_world_wxyz": self.quaternion_world_wxyz,
            "bbox_3d_extents_m": self.bbox_3d_extents_m,
            "confidence": self.confidence,
        }


@dataclass
class DetectionAnnotation:
    """One visual perception annotation produced for a single camera frame."""

    label: str
    prompt: str
    source: str
    score: float | None = None
    box_xyxy: tuple[float, float, float, float] | None = None
    mask: np.ndarray | None = field(default=None, repr=False)
    centroid_world_xyz: list[float] | None = None
    bounds_world_xyz: dict[str, list[float]] | None = None
    geometry_valid: bool | None = None
    pose_6d: Pose6D | None = None
    color: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self, *, index: int | None = None) -> dict[str, Any]:
        """JSON-safe summary (never includes raw pixel masks)."""
        result: dict[str, Any] = {
            **({"id": index} if index is not None else {}),
            "label": self.label,
            "prompt": self.prompt,
            "source": self.source,
        }
        if self.score is not None:
            result["score"] = round(float(self.score), 4)
        if self.box_xyxy is not None:
            result["box_xyxy"] = [round(float(v), 2) for v in self.box_xyxy]
        if self.centroid_world_xyz is not None:
            result["centroid_world_xyz"] = [round(float(v), 4) for v in self.centroid_world_xyz]
        if self.bounds_world_xyz is not None:
            result["bounds_world_xyz"] = self.bounds_world_xyz
        if self.geometry_valid is not None:
            result["geometry_valid"] = self.geometry_valid
        if self.pose_6d is not None:
            result["pose_6d"] = self.pose_6d.to_dict()
        if self.extra:
            result["extra"] = self.extra
        return result


@dataclass(frozen=True)
class FrameInput:
    """Immutable raw sensor frame passed to visual processors."""

    camera: str
    snapshot_id: str
    state_version: int
    rgb: np.ndarray
    rgb_png: bytes
    depth_m: np.ndarray | None = None
    intrinsic_K: np.ndarray | None = None
    T_world_from_optical: np.ndarray | None = None
    calibration: dict[str, Any] | None = None
    task: str = ""
    instruction: str = ""
    workspace_bounds: list[list[float]] | None = None


class VisualProcessor(Protocol):
    """Pluggable visual perception processor invoked before images reach an agent."""

    name: str

    def process(
        self,
        frame: FrameInput,
        targets: tuple[TargetSpec, ...],
        existing: list[DetectionAnnotation],
    ) -> list[DetectionAnnotation]: ...

    def close(self) -> None: ...
