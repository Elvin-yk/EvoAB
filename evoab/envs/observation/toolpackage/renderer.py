"""Non-destructive visual annotation renderer preserving exact source (H, W)."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from .config import RenderConfig
from .contracts import DetectionAnnotation

_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 229, 255),    # cyan
    (255, 196, 0),    # amber
    (118, 255, 3),    # lime
    (255, 64, 129),   # pink
    (179, 136, 255),  # lavender
    (255, 110, 64),   # coral
)


def _parse_color(value: str | None, index: int) -> tuple[int, int, int]:
    if value and isinstance(value, str):
        text = value.strip().lstrip("#")
        if len(text) == 6:
            try:
                return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
            except ValueError:
                pass
    return _PALETTE[index % len(_PALETTE)]


def _mask_contour(mask: np.ndarray, width: int) -> np.ndarray:
    mask = np.asarray(mask, dtype=bool)
    padded = np.pad(mask, 1, mode="constant", constant_values=False)
    interior = mask & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
    edge = mask & ~interior
    for _ in range(max(0, width - 1)):
        p = np.pad(edge, 1, mode="constant", constant_values=False)
        edge = p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:] | edge
    return edge


def _project_point(point_cam: np.ndarray, intrinsic_K: np.ndarray) -> tuple[float, float] | None:
    z = float(point_cam[2])
    if not np.isfinite(point_cam).all() or z <= 1e-4:
        return None
    u = float(intrinsic_K[0, 0] * point_cam[0] / z + intrinsic_K[0, 2])
    v = float(intrinsic_K[1, 1] * point_cam[1] / z + intrinsic_K[1, 2])
    if not (np.isfinite(u) and np.isfinite(v)):
        return None
    return u, v


def _draw_pose_axes(
    draw: ImageDraw.ImageDraw,
    t_cam_obj: np.ndarray,
    intrinsic_K: np.ndarray,
    axis_length_m: float,
) -> tuple[float, float] | None:
    origin_cam = t_cam_obj[:3, 3]
    origin_px = _project_point(origin_cam, intrinsic_K)
    if origin_px is None:
        return None
    axis_colors = ((255, 60, 60), (60, 235, 90), (70, 150, 255))
    axis_names = ("X", "Y", "Z")
    for idx, (color, name) in enumerate(zip(axis_colors, axis_names)):
        tip_obj = np.zeros(4, dtype=np.float64)
        tip_obj[idx] = axis_length_m
        tip_obj[3] = 1.0
        tip_cam = (t_cam_obj @ tip_obj)[:3]
        tip_px = _project_point(tip_cam, intrinsic_K)
        if tip_px is not None:
            draw.line([origin_px, tip_px], fill=color, width=2)
            draw.text((tip_px[0] + 2, tip_px[1] - 6), name, fill=color, stroke_width=1, stroke_fill=(0, 0, 0))
    draw.ellipse((origin_px[0] - 3, origin_px[1] - 3, origin_px[0] + 3, origin_px[1] + 3),
                 fill=(255, 255, 255), outline=(0, 0, 0))
    return origin_px


def _draw_bbox_3d(
    draw: ImageDraw.ImageDraw,
    t_cam_obj: np.ndarray,
    extents_m: list[float],
    intrinsic_K: np.ndarray,
    color: tuple[int, int, int],
) -> None:
    hx, hy, hz = (float(extents_m[0]) / 2.0, float(extents_m[1]) / 2.0, float(extents_m[2]) / 2.0)
    corners_obj = np.array([
        [-hx, -hy, -hz, 1.0], [hx, -hy, -hz, 1.0], [hx, hy, -hz, 1.0], [-hx, hy, -hz, 1.0],
        [-hx, -hy, hz, 1.0], [hx, -hy, hz, 1.0], [hx, hy, hz, 1.0], [-hx, hy, hz, 1.0],
    ], dtype=np.float64)
    corners_cam = (corners_obj @ t_cam_obj.T)[:, :3]
    projected = [_project_point(pt, intrinsic_K) for pt in corners_cam]
    if any(pt is None for pt in projected):
        return
    edges = (
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    )
    for start, end in edges:
        draw.line([projected[start], projected[end]], fill=color, width=1)


def _anchor_point(
    annotation: DetectionAnnotation,
    pose_origin_px: tuple[float, float] | None,
    width: int,
    height: int,
) -> tuple[int, int]:
    if annotation.box_xyxy is not None:
        x0, y0, _, _ = annotation.box_xyxy
        return max(2, min(width - 80, int(x0))), max(2, min(height - 16, int(y0) - 14))
    if pose_origin_px is not None:
        return max(2, min(width - 80, int(pose_origin_px[0]) + 4)), max(2, min(height - 16, int(pose_origin_px[1]) - 14))
    if annotation.mask is not None and annotation.mask.any():
        ys, xs = np.nonzero(annotation.mask)
        return max(2, min(width - 80, int(xs.min()))), max(2, min(height - 16, int(ys.min()) - 14))
    return 6, 6


def _hud_line(index: int, annotation: DetectionAnnotation) -> str:
    parts = [f"[{index}] {annotation.label}"]
    pos = (annotation.pose_6d.position_world_m if annotation.pose_6d and annotation.pose_6d.position_world_m
           else annotation.centroid_world_xyz)
    if pos is not None:
        parts.append(f"xyz=({pos[0]:.3f},{pos[1]:.3f},{pos[2]:.3f})m")
    if annotation.pose_6d is not None and annotation.pose_6d.quaternion_world_wxyz:
        q = annotation.pose_6d.quaternion_world_wxyz
        parts.append(f"q=({q[0]:.2f},{q[1]:.2f},{q[2]:.2f},{q[3]:.2f})")
    elif annotation.score is not None:
        parts.append(f"s={annotation.score:.2f}")
    return " ".join(parts)


def render_annotations(
    rgb: np.ndarray,
    annotations: list[DetectionAnnotation],
    config: RenderConfig,
    *,
    intrinsic_K: np.ndarray | None = None,
) -> np.ndarray:
    """Render visual annotations onto a copy of ``rgb`` while preserving exact (H, W, 3)."""
    base = np.asarray(rgb, dtype=np.uint8)
    if not config.enabled or not annotations:
        return base.copy()

    height, width = base.shape[:2]
    canvas = base.astype(np.float32)

    for idx, ann in enumerate(annotations):
        color = _parse_color(ann.color, idx)
        if ann.mask is not None and ann.mask.shape == (height, width):
            mask = np.asarray(ann.mask, dtype=bool)
            if config.draw_mask_fill and config.mask_fill_alpha > 0:
                alpha = float(config.mask_fill_alpha)
                canvas[mask] = canvas[mask] * (1.0 - alpha) + np.asarray(color, dtype=np.float32) * alpha
            if config.draw_mask_contour:
                contour = _mask_contour(mask, config.contour_width)
                canvas[contour] = np.asarray(color, dtype=np.float32)

    image = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), mode="RGB").convert("RGBA")
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    K = None if intrinsic_K is None else np.asarray(intrinsic_K, dtype=np.float64)

    for idx, ann in enumerate(annotations):
        number = idx + 1
        color = _parse_color(ann.color, idx)
        rgba = (*color, 235)
        if config.draw_bbox and ann.box_xyxy is not None:
            x0, y0, x1, y1 = ann.box_xyxy
            draw.rectangle([x0, y0, x1, y1], outline=rgba, width=config.bbox_width)

        pose_origin_px = None
        if ann.pose_6d is not None and K is not None:
            t_cam = np.asarray(ann.pose_6d.T_camera_from_object, dtype=np.float64)
            if config.draw_bbox_3d and ann.pose_6d.bbox_3d_extents_m is not None:
                _draw_bbox_3d(draw, t_cam, ann.pose_6d.bbox_3d_extents_m, K, color)
            if config.draw_pose_axes:
                pose_origin_px = _draw_pose_axes(draw, t_cam, K, config.pose_axis_length_m)

        if config.draw_labels:
            ax, ay = _anchor_point(ann, pose_origin_px, width, height)
            text = f"[{number}] {ann.label}"
            if config.draw_centroid_3d and ann.centroid_world_xyz is not None and not config.draw_hud:
                x, y, z = ann.centroid_world_xyz
                text += f" ({x:.2f},{y:.2f},{z:.2f})"
            bbox = draw.textbbox((ax, ay), text)
            pad = 2
            draw.rectangle([bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad], fill=(0, 0, 0, 175))
            draw.text((ax, ay), text, fill=rgba)

    if config.draw_hud:
        lines = [_hud_line(i + 1, ann) for i, ann in enumerate(annotations[:config.hud_max_items])]
        if lines:
            line_h = 13
            strip_h = min(height // 3, len(lines) * line_h + 6)
            y_start = 0 if config.hud_position == "top" else max(0, height - strip_h)
            draw.rectangle([0, y_start, width, y_start + strip_h], fill=(0, 0, 0, 165))
            for i, line in enumerate(lines):
                y_pos = y_start + 3 + i * line_h
                if y_pos + 10 > y_start + strip_h:
                    break
                color = _parse_color(annotations[i].color, i)
                draw.text((6, y_pos), line, fill=(*color, 245))

    composited = Image.alpha_composite(image, overlay).convert("RGB")
    result = np.asarray(composited, dtype=np.uint8)
    assert result.shape == base.shape, "Renderer must preserve exact (H, W, 3) image dimensions"
    return result
