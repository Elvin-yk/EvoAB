"""Pluggable FoundationPose 6D object pose processor over HTTP."""
from __future__ import annotations

import base64
import io
import math
import os
from typing import Any

import httpx
import numpy as np
from PIL import Image

from evoab.telemetry.logging import get_logger

from ..contracts import DetectionAnnotation, FrameInput, Pose6D, TargetSpec

logger = get_logger("observation.toolpackage.foundationpose")


def _rotation_to_wxyz(rotation: np.ndarray) -> list[float]:
    """Convert a 3x3 proper rotation matrix to unit [w, x, y, z] without SciPy."""
    m = np.asarray(rotation, dtype=np.float64)
    trace = float(m[0, 0] + m[1, 1] + m[2, 2])
    if trace > 0.0:
        s = 2.0 * math.sqrt(trace + 1.0)
        q = np.array([0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s])
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = 2.0 * math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2])
        q = np.array([(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s])
    elif m[1, 1] > m[2, 2]:
        s = 2.0 * math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2])
        q = np.array([(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s])
    else:
        s = 2.0 * math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1])
        q = np.array([(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s])
    q /= np.linalg.norm(q)
    return [round(float(v), 6) for v in q]


def _validate_rigid_transform(matrix: Any) -> np.ndarray:
    arr = np.asarray(matrix, dtype=np.float64)
    if arr.shape != (4, 4) or not np.isfinite(arr).all():
        raise ValueError("Pose matrix must be finite 4x4")
    if not np.allclose(arr[3], [0.0, 0.0, 0.0, 1.0], atol=1e-4):
        raise ValueError("Pose matrix bottom row must be [0, 0, 0, 1]")
    rot = arr[:3, :3]
    if not np.allclose(rot.T @ rot, np.eye(3), atol=1e-2) or np.linalg.det(rot) < 0.95:
        raise ValueError("Pose matrix rotation block must be a proper rigid rotation")
    return arr


def _encode_mask_png(mask: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray((np.asarray(mask, dtype=bool).astype(np.uint8) * 255), mode="L").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _encode_depth_npy(depth_m: np.ndarray) -> str:
    buf = io.BytesIO()
    np.save(buf, np.asarray(depth_m, dtype=np.float32), allow_pickle=False)
    return base64.b64encode(buf.getvalue()).decode("ascii")


class FoundationPoseProcessor:
    """Enrich detections with 6D poses from an external FoundationPose HTTP service.

    Expected HTTP contract (POST ``<endpoint>/estimate_pose``):
      Request JSON:
        {
          "camera": str,
          "label": str,
          "prompt": str,
          "mesh_path": str | None,
          "mesh_id": str | None,
          "rgb_png_base64": str,
          "depth_npy_base64": str,
          "intrinsic_K": [[...], [...], [...]],
          "mask_png_base64": str | None,
          "box_xyxy": [x0, y0, x1, y1] | None,
        }
      Response JSON:
        {
          "found": bool,
          "T_camera_from_object": [[...], ...],  # 4x4 optical frame -> object
          "bbox_3d_extents_m": [dx, dy, dz],     # optional
          "confidence": float,                   # optional
        }
    """

    name = "foundationpose"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        endpoint_env: str = "EVOAB_FOUNDATIONPOSE_URL",
        route: str = "/estimate_pose",
        timeout_s: float = 60.0,
        require_mask: bool = True,
        require_mesh: bool = False,
        http_client: Any = None,
    ) -> None:
        self._endpoint = endpoint or os.environ.get(endpoint_env)
        self._route = route if route.startswith("/") else "/" + route
        self._timeout_s = float(timeout_s)
        self._require_mask = bool(require_mask)
        self._require_mesh = bool(require_mesh)
        self._http = http_client
        self._owns_http = http_client is None

    def _ensure_http(self) -> Any:
        if self._http is None:
            if not self._endpoint:
                return None
            self._http = httpx.Client(
                base_url=self._endpoint.rstrip("/"),
                timeout=self._timeout_s,
                trust_env=False,
            )
        return self._http

    def process(
        self,
        frame: FrameInput,
        targets: tuple[TargetSpec, ...],
        existing: list[DetectionAnnotation],
    ) -> list[DetectionAnnotation]:
        if not targets:
            return []
        if frame.depth_m is None or frame.intrinsic_K is None:
            logger.debug("FoundationPose skipped on camera %s: missing depth or intrinsic_K", frame.camera)
            return []
        client = self._ensure_http()
        if client is None:
            logger.debug("FoundationPose skipped: no endpoint configured")
            return []

        by_label = {item.label: item for item in existing}
        rgb_b64 = base64.b64encode(frame.rgb_png).decode("ascii")
        depth_b64 = _encode_depth_npy(frame.depth_m)
        intrinsic_list = np.asarray(frame.intrinsic_K, dtype=np.float64).tolist()

        produced: list[DetectionAnnotation] = []
        for target in targets:
            if self._require_mesh and not (target.mesh_path or target.mesh_id):
                continue
            matched = by_label.get(target.label)
            mask = matched.mask if matched is not None else None
            if self._require_mask and mask is None:
                continue

            payload = {
                "camera": frame.camera,
                "label": target.label,
                "prompt": target.prompt,
                "mesh_path": target.mesh_path,
                "mesh_id": target.mesh_id,
                "rgb_png_base64": rgb_b64,
                "depth_npy_base64": depth_b64,
                "intrinsic_K": intrinsic_list,
                "mask_png_base64": None if mask is None else _encode_mask_png(mask),
                "box_xyxy": None if matched is None or matched.box_xyxy is None else list(matched.box_xyxy),
            }
            response = client.post(self._route, json=payload)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not data.get("found"):
                continue

            t_cam_obj = _validate_rigid_transform(data["T_camera_from_object"])
            t_world_obj = None
            pos_world = None
            quat_world = None
            if frame.T_world_from_optical is not None:
                t_world_cam = _validate_rigid_transform(frame.T_world_from_optical)
                t_world_obj_arr = t_world_cam @ t_cam_obj
                t_world_obj = np.round(t_world_obj_arr, 6).tolist()
                pos_world = [round(float(v), 5) for v in t_world_obj_arr[:3, 3]]
                quat_world = _rotation_to_wxyz(t_world_obj_arr[:3, :3])

            extents = data.get("bbox_3d_extents_m")
            if extents is not None:
                extents_arr = np.asarray(extents, dtype=np.float64)
                if extents_arr.shape != (3,) or not np.isfinite(extents_arr).all() or (extents_arr <= 0).any():
                    extents = None
                else:
                    extents = [round(float(v), 5) for v in extents_arr]

            pose = Pose6D(
                T_camera_from_object=np.round(t_cam_obj, 6).tolist(),
                T_world_from_object=t_world_obj,
                position_world_m=pos_world,
                quaternion_world_wxyz=quat_world,
                bbox_3d_extents_m=extents,
                confidence=None if data.get("confidence") is None else round(float(data["confidence"]), 4),
            )
            if matched is not None:
                matched.pose_6d = pose
                if pos_world is not None and matched.centroid_world_xyz is None:
                    matched.centroid_world_xyz = pos_world
            else:
                produced.append(
                    DetectionAnnotation(
                        label=target.label,
                        prompt=target.prompt,
                        source=self.name,
                        score=pose.confidence,
                        centroid_world_xyz=pos_world,
                        pose_6d=pose,
                        color=target.color,
                    )
                )
        return produced

    def close(self) -> None:
        if self._owns_http and self._http is not None:
            try:
                self._http.close()
            finally:
                self._http = None
