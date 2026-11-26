"""Orchestration pipeline for external visual-observation enhancement."""
from __future__ import annotations

import io
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from evoab.telemetry.logging import get_logger

from .config import ObservationPipelineConfig, PluginConfig, TargetConfig
from .contracts import DetectionAnnotation, FrameInput, TargetSpec, VisualProcessor
from .plugins import create_processor
from .prompts import resolve_targets
from .renderer import render_annotations

logger = get_logger("observation.toolpackage.pipeline")


@dataclass(frozen=True)
class FrameProcessingResult:
    """Output produced for one camera frame after running all plugins and rendering."""

    camera: str
    enhanced_rgb: np.ndarray
    enhanced_png: bytes
    annotations: list[DetectionAnnotation] = field(default_factory=list)
    modified: bool = False
    plugin_errors: dict[str, str] = field(default_factory=dict)


class ObservationPipeline:
    """Stateful per-episode visual perception and non-destructive rendering pipeline."""

    def __init__(
        self,
        config: ObservationPipelineConfig,
        *,
        archive_dir: str | Path | None = None,
        processors: list[VisualProcessor] | None = None,
        default_sam3_url: str | None = None,
        default_foundationpose_url: str | None = None,
    ) -> None:
        self.config = config
        self.archive_dir = Path(archive_dir).expanduser().resolve() if archive_dir is not None else None
        self._lock = threading.RLock()
        self._targets: tuple[TargetSpec, ...] | None = None
        self._target_task = ""
        self._target_instruction = ""
        self._frames_processed = 0
        self._closed = False

        if self.archive_dir is not None:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            if self.config.archive_images:
                (self.archive_dir / "raw_images").mkdir(exist_ok=True)
                (self.archive_dir / "annotated_images").mkdir(exist_ok=True)

        self._plugins: list[tuple[PluginConfig, VisualProcessor]] = []
        self._owns_processors = processors is None
        if processors is not None:
            for proc in processors:
                plugin_cfg = PluginConfig(
                    type=getattr(proc, "name", type(proc).__name__),
                    enabled=True,
                    fail_open=True,
                )
                self._plugins.append((plugin_cfg, proc))
        else:
            try:
                for plugin_cfg in config.plugins:
                    if not plugin_cfg.enabled:
                        continue
                    effective_cfg = _apply_endpoint_defaults(
                        plugin_cfg,
                        default_sam3_url=default_sam3_url,
                        default_foundationpose_url=default_foundationpose_url,
                    )
                    self._plugins.append((effective_cfg, create_processor(effective_cfg)))
            except BaseException:
                self.close()
                raise

    @property
    def plugin_names(self) -> tuple[str, ...]:
        return tuple(cfg.type for cfg, _ in self._plugins)

    def ensure_targets(
        self,
        task: str,
        instruction: str,
        *,
        first_frame_png: bytes | None = None,
    ) -> tuple[TargetSpec, ...]:
        """Resolve and cache target prompts once per episode unless overridden."""
        with self._lock:
            if self._targets is not None:
                return self._targets
            resolved = resolve_targets(
                task=task,
                instruction=instruction,
                targets_config=self.config.targets,
                auto_prompt=self.config.auto_prompt,
                first_frame_png=first_frame_png,
            )
            self._targets = resolved
            self._target_task = task
            self._target_instruction = instruction
            self._persist_targets(resolved, task=task, instruction=instruction)
            logger.info(
                "Resolved %d observation targets for task=%r: %s",
                len(resolved),
                task,
                [item.prompt for item in resolved],
            )
            return resolved

    def get_targets(self) -> list[dict[str, Any]]:
        with self._lock:
            if self._targets is None:
                return []
            return [item.to_dict() for item in self._targets]

    def set_targets(self, targets: list[TargetSpec | TargetConfig | dict[str, Any]]) -> tuple[TargetSpec, ...]:
        """Override active target prompts at runtime (for example via POST /ob/targets)."""
        converted: list[TargetSpec] = []
        for item in targets:
            if isinstance(item, TargetSpec):
                converted.append(item)
            elif isinstance(item, TargetConfig):
                converted.append(
                    TargetSpec(
                        label=item.label,
                        prompt=item.effective_prompt(),
                        mesh_path=item.mesh_path,
                        mesh_id=item.mesh_id,
                        color=item.color,
                        min_score=item.min_score,
                        source="runtime_override",
                    )
                )
            elif isinstance(item, dict):
                cfg = TargetConfig.model_validate(item)
                converted.append(
                    TargetSpec(
                        label=cfg.label,
                        prompt=cfg.effective_prompt(),
                        mesh_path=cfg.mesh_path,
                        mesh_id=cfg.mesh_id,
                        color=cfg.color,
                        min_score=cfg.min_score,
                        source="runtime_override",
                    )
                )
            else:
                raise ValueError(f"Unsupported target specification: {type(item).__name__}")
        result = tuple(converted)
        with self._lock:
            self._targets = result
            self._persist_targets(result, task=self._target_task, instruction=self._target_instruction)
        return result

    def process_frame(
        self,
        frame: FrameInput,
        *,
        targets: tuple[TargetSpec, ...] | None = None,
    ) -> FrameProcessingResult:
        """Run enabled visual plugins and render annotations onto one camera frame."""
        if self.config.cameras is not None and frame.camera not in self.config.cameras:
            return FrameProcessingResult(
                camera=frame.camera,
                enhanced_rgb=frame.rgb.copy(),
                enhanced_png=frame.rgb_png,
                annotations=[],
                modified=False,
            )

        active_targets = (
            targets
            if targets is not None
            else self.ensure_targets(frame.task, frame.instruction, first_frame_png=frame.rgb_png)
        )

        annotations: list[DetectionAnnotation] = []
        plugin_errors: dict[str, str] = {}

        for plugin_cfg, processor in self._plugins:
            if plugin_cfg.cameras is not None and frame.camera not in plugin_cfg.cameras:
                continue
            try:
                produced = processor.process(frame, active_targets, annotations)
                if produced:
                    annotations.extend(produced)
                    if self.config.dedup_iou_threshold > 0.0:
                        annotations = deduplicate_annotations(
                            annotations, self.config.dedup_iou_threshold
                        )
            except Exception as error:
                if not plugin_cfg.fail_open:
                    raise
                message = f"{type(error).__name__}: {error}"
                plugin_errors[plugin_cfg.type] = message
                logger.warning(
                    "Observation plugin %s failed on camera %s (fail_open=True): %s",
                    plugin_cfg.type,
                    frame.camera,
                    message,
                )

        if self.config.render.enabled and annotations:
            enhanced_rgb = render_annotations(
                frame.rgb,
                annotations,
                self.config.render,
                intrinsic_K=frame.intrinsic_K,
            )
            buf = io.BytesIO()
            Image.fromarray(enhanced_rgb, mode="RGB").save(buf, format="PNG")
            enhanced_png = buf.getvalue()
            modified = True
        else:
            enhanced_rgb = frame.rgb.copy()
            enhanced_png = frame.rgb_png
            modified = False

        with self._lock:
            self._frames_processed += 1
            self._archive_frame(frame, enhanced_png, annotations, plugin_errors, modified=modified)

        return FrameProcessingResult(
            camera=frame.camera,
            enhanced_rgb=enhanced_rgb,
            enhanced_png=enhanced_png,
            annotations=annotations,
            modified=modified,
            plugin_errors=plugin_errors,
        )

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "plugins": list(self.plugin_names),
                "cameras": self.config.cameras,
                "targets_resolved": self._targets is not None,
                "targets": [item.to_dict() for item in (self._targets or ())],
                "frames_processed": self._frames_processed,
                "render_enabled": self.config.render.enabled,
                "dedup_iou_threshold": self.config.dedup_iou_threshold,
                "inject_metadata": self.config.inject_metadata,
                "enhance_step_snapshots": self.config.enhance_step_snapshots,
                "archive_dir": str(self.archive_dir) if self.archive_dir is not None else None,
            }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_processors:
            for plugin_cfg, processor in self._plugins:
                close_fn = getattr(processor, "close", None)
                if callable(close_fn):
                    try:
                        close_fn()
                    except Exception as error:
                        logger.warning("Failed to close observation plugin %s: %s", plugin_cfg.type, error)

    def _persist_targets(self, targets: tuple[TargetSpec, ...], *, task: str, instruction: str) -> None:
        if self.archive_dir is None:
            return
        payload = {
            "task": task,
            "instruction": instruction,
            "updated_at": time.time(),
            "targets": [item.to_dict() for item in targets],
        }
        (self.archive_dir / "targets.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _archive_frame(
        self,
        frame: FrameInput,
        enhanced_png: bytes,
        annotations: list[DetectionAnnotation],
        plugin_errors: dict[str, str],
        *,
        modified: bool,
    ) -> None:
        if self.archive_dir is None:
            return
        stem = f"{frame.snapshot_id[:16]}_{frame.camera}"
        raw_rel = None
        annotated_rel = None
        if self.config.archive_images:
            raw_path = self.archive_dir / "raw_images" / f"{stem}.png"
            if not raw_path.exists():
                raw_path.write_bytes(frame.rgb_png)
            raw_rel = f"raw_images/{stem}.png"
            if modified:
                ann_path = self.archive_dir / "annotated_images" / f"{stem}.png"
                ann_path.write_bytes(enhanced_png)
                annotated_rel = f"annotated_images/{stem}.png"
        record = {
            "timestamp": time.time(),
            "snapshot_id": frame.snapshot_id,
            "state_version": frame.state_version,
            "camera": frame.camera,
            "modified": modified,
            "raw_image": raw_rel,
            "annotated_image": annotated_rel,
            "annotations": [ann.to_public_dict(index=i + 1) for i, ann in enumerate(annotations)],
            **({"plugin_errors": plugin_errors} if plugin_errors else {}),
        }
        with (self.archive_dir / "annotations.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


def _box_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    x0 = max(float(a[0]), float(b[0]))
    y0 = max(float(a[1]), float(b[1]))
    x1 = min(float(a[2]), float(b[2]))
    y1 = min(float(a[3]), float(b[3]))
    inter_w = max(0.0, x1 - x0)
    inter_h = max(0.0, y1 - y0)
    inter = inter_w * inter_h
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, float(a[2]) - float(a[0])) * max(0.0, float(a[3]) - float(a[1]))
    area_b = max(0.0, float(b[2]) - float(b[0])) * max(0.0, float(b[3]) - float(b[1]))
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def annotation_iou(a: DetectionAnnotation, b: DetectionAnnotation) -> float:
    """Compute exact boolean mask IoU when available, falling back to 2D box IoU."""
    if a.mask is not None and b.mask is not None and a.mask.shape == b.mask.shape:
        ma = np.asarray(a.mask, dtype=bool)
        mb = np.asarray(b.mask, dtype=bool)
        union = int(np.logical_or(ma, mb).sum())
        if union == 0:
            return 0.0
        inter = int(np.logical_and(ma, mb).sum())
        return inter / union
    if a.box_xyxy is not None and b.box_xyxy is not None:
        return _box_iou(a.box_xyxy, b.box_xyxy)
    return 0.0


def deduplicate_annotations(
    annotations: list[DetectionAnnotation],
    iou_threshold: float,
) -> list[DetectionAnnotation]:
    """Suppress lower-scoring overlapping detections using mask (or 2D box) IoU NMS."""
    if iou_threshold <= 0.0 or len(annotations) <= 1:
        return list(annotations)
    ranked = sorted(
        enumerate(annotations),
        key=lambda pair: (
            1 if pair[1].pose_6d is not None else 0,
            float(pair[1].score) if pair[1].score is not None else -1.0,
            -pair[0],
        ),
        reverse=True,
    )
    kept_indices: list[int] = []
    kept_items: list[DetectionAnnotation] = []
    for idx, candidate in ranked:
        if any(annotation_iou(candidate, existing) >= iou_threshold for existing in kept_items):
            continue
        kept_indices.append(idx)
        kept_items.append(candidate)
    kept_indices.sort()
    return [annotations[idx] for idx in kept_indices]


def _apply_endpoint_defaults(
    plugin_cfg: PluginConfig,
    *,
    default_sam3_url: str | None,
    default_foundationpose_url: str | None,
) -> PluginConfig:
    options = dict(plugin_cfg.options)
    if plugin_cfg.type == "sam3" and default_sam3_url and not options.get("endpoint"):
        options["endpoint"] = default_sam3_url
    elif plugin_cfg.type == "foundationpose" and default_foundationpose_url and not options.get("endpoint"):
        options["endpoint"] = default_foundationpose_url
    else:
        return plugin_cfg
    return plugin_cfg.model_copy(update={"options": options})
