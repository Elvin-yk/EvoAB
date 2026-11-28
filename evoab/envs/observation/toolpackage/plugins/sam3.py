"""SAM3 segmentation + optional calibrated depth-geometry processor."""
from __future__ import annotations

import os
from typing import Any

import numpy as np

from evoab.envs.protocol.snapshots import CameraFrame
from evoab.servers.perception.client import PerceptionClient
from evoab.servers.perception.contracts import TextQuery
from evoab.records.telemetry.logging import get_logger
from evoab.tools.scene.geometry import mask_geometry_quality, project_mask

from ..contracts import DetectionAnnotation, FrameInput, TargetSpec

logger = get_logger("observation.toolpackage.sam3")


class Sam3Processor:
    """Segment configured targets via EvoAB's SAM3 HTTP perception service."""

    name = "sam3"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        endpoint_env: str = "EVOAB_SAM3_URL",
        min_score: float = 0.25,
        timeout_s: float = 60.0,
        compute_depth_geometry: bool = True,
        client: Any = None,
    ) -> None:
        self._endpoint = endpoint or os.environ.get(endpoint_env)
        self._min_score = float(min_score)
        self._timeout_s = float(timeout_s)
        self._compute_depth_geometry = bool(compute_depth_geometry)
        self._client = client
        self._owns_client = client is None

    def _ensure_client(self) -> Any:
        if self._client is None:
            if not self._endpoint:
                return None
            self._client = PerceptionClient(self._endpoint, timeout_s=self._timeout_s)
        return self._client

    def process(
        self,
        frame: FrameInput,
        targets: tuple[TargetSpec, ...],
        existing: list[DetectionAnnotation],
    ) -> list[DetectionAnnotation]:
        del existing
        if not targets:
            return []
        client = self._ensure_client()
        if client is None:
            logger.debug("SAM3 processor skipped: no endpoint configured")
            return []

        has_depth = (
            self._compute_depth_geometry
            and frame.depth_m is not None
            and frame.intrinsic_K is not None
            and frame.T_world_from_optical is not None
        )
        cam_frame = (
            CameraFrame(
                rgb=frame.rgb,
                depth_m=frame.depth_m,
                intrinsic_K=frame.intrinsic_K,
                T_world_from_optical=frame.T_world_from_optical,
                calibration=frame.calibration,
            )
            if has_depth
            else None
        )

        produced: list[DetectionAnnotation] = []
        for target in targets:
            threshold = self._min_score if target.min_score is None else target.min_score
            result = client.segment(frame.rgb_png, query=TextQuery(text=target.prompt), min_score=threshold)
            if result.status != "matched" or result.mask is None:
                continue
            mask = np.asarray(result.mask, dtype=bool)
            if mask.shape != frame.rgb.shape[:2] or not mask.any():
                continue

            centroid_world = None
            bounds_world = None
            geometry_valid = None
            extra: dict[str, Any] = {}
            if cam_frame is not None:
                try:
                    geometry = project_mask(cam_frame, mask)
                    quality = mask_geometry_quality(geometry, workspace_bounds=frame.workspace_bounds)
                    centroid_world = geometry["median_xyz"]
                    bounds_world = geometry["bounds_xyz"]
                    geometry_valid = bool(quality["valid"])
                    if quality["reasons"]:
                        extra["geometry_warnings"] = quality["reasons"]
                except ValueError as error:
                    geometry_valid = False
                    extra["geometry_error"] = str(error)

            produced.append(
                DetectionAnnotation(
                    label=target.label,
                    prompt=target.prompt,
                    source=self.name,
                    score=result.score,
                    box_xyxy=result.box_xyxy,
                    mask=mask,
                    centroid_world_xyz=centroid_world,
                    bounds_world_xyz=bounds_world,
                    geometry_valid=geometry_valid,
                    color=target.color,
                    extra=extra,
                )
            )
        return produced

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None
