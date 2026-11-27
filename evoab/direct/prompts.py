"""Prompt packs for Direct policies.

A Direct prompt is assembled from blocks:

``robot``   the embodiment and its frame conventions (bundled per embodiment,
            or supplied by the user with the recipe's ``action.robot_prompt_file``)
``timing``  control interval and per-row step limits (never the episode step budget)
``gripper`` grasp-point geometry measured from the environment
``camera``  camera extrinsics and intrinsics from the environment's calibration
``action``  the representation's output contract (always included)

The representation always supplies its output contract. Recipes select the input blocks. Geometry is computed from what the
environment reports, so it stays correct when a benchmark changes cameras.
"""

from __future__ import annotations

from importlib import resources

import numpy as np

from evoab.direct.actions import Limits, Representation

BLOCKS = ("robot", "timing", "gripper", "camera")


def embodiment_text(description) -> str:
    """Read the environment's base prompt, falling back to general manipulation guidance."""
    declared = description.metadata.get("robot_prompt")
    if declared:
        return str(declared)
    return resources.files("evoab.resources").joinpath("prompts/direct/generic.md").read_text(encoding="utf-8")


def timing_text(control_hz: float, limits: Limits, space: str = "pose") -> str:
    """Control timing and step limits. The episode's step budget is evaluation-side and never shown."""
    head = (
        "EXECUTION TIMING\n"
        f"Each action row runs for one control interval: {1 / control_hz:.8g} seconds ({control_hz:g} Hz) in this "
        "environment. A row specifies a target; it does not wait until that target is reached. API response "
        "time does not advance simulation.\n\n")
    if space == "joint":
        return head + (
            f"Each joint target may change by at most {limits.joint_rad:g} rad (or metres) per row. Actual "
            "movement may be smaller. The gripper command still executes when joint changes are shortened. "
            "Use measured joints to assess arrival.")
    return head + (
        f"Translation targets are limited to {limits.translation_m:g} m per row and rotation changes to "
        f"{limits.rotation_rad:g} rad per row. Actual movement may be smaller. The gripper command still "
        "executes even when the requested arm movement is shortened. Use measured ee_pos/ee_quat to assess "
        "arrival.")


def _axes(quaternion_wxyz) -> np.ndarray:
    from scipy.spatial.transform import Rotation
    w, x, y, z = quaternion_wxyz
    return Rotation.from_quat([x, y, z, w]).as_matrix()


def _vector(values) -> str:
    return "[" + ", ".join(f"{v:+.3f}" for v in values) + "]"


def gripper_text(quaternion_wxyz, max_opening_m: float | None = 0.08) -> str:
    """Name the gripper's local axes in world coordinates at the initial orientation."""
    axes = _axes(quaternion_wxyz)
    # The approach axis points from the wrist toward the fingertips; at reset it points most nearly downward.
    approach = int(np.argmin(axes[2]))
    names = "xyz"
    lines = [
        "GRIPPER GEOMETRY",
        "The action pose and ee_pos/ee_quat locate the grasp point between the finger pads, so a target "
        "position is where an object should sit when the gripper closes on it; no flange offset is needed.",
        f"At the initial measured orientation ee_quat={_vector(quaternion_wxyz)} the gripper's local axes "
        "point along these world directions:",
    ]
    for i in range(3):
        role = " (approach: wrist toward fingertips)" if i == approach else ""
        lines.append(f"  local +{names[i]} = world {_vector(axes[:, i])}{role}")
    lines.append("These local directions rotate with ee_quat, while action x/y/z remain world axes. Keeping the "
                 "initial orientation keeps the gripper pointing the same way; rotate about the approach axis to "
                 "align the jaws with an object.")
    if max_opening_m:
        lines.append(f"The jaws open to about {max_opening_m:.3f} m between the pads; measured gripper_closure "
                     "is 0 when fully open and 1 when fully closed. A closure that stays well below 1 after "
                     "closing usually means an object is held.")
    return "\n".join(lines)


def camera_text(cameras: dict) -> str:
    """Extrinsics and normalized intrinsics for every calibrated camera in a snapshot."""
    lines = ["CAMERA GEOMETRY",
             "Each camera's optical frame has +z forward along the viewing direction, +x to the image right and "
             "+y to the image bottom. Project a world point p with p_c = R^T (p - t), then "
             "u = fx * p_c.x / p_c.z + cx and v = fy * p_c.y / p_c.z + cy, where u and v are fractions of the "
             "image width and height measured from the top-left corner."]
    found = False
    for name, frame in cameras.items():
        K, T = frame.get("intrinsic_K"), frame.get("T_world_from_optical")
        if K is None or T is None:
            lines.append(f"{name}: uncalibrated in this episode.")
            continue
        found = True
        K, T = np.asarray(K, dtype=float), np.asarray(T, dtype=float)
        height, width = frame.get("image_size") or (1, 1)
        R, t = T[:3, :3], T[:3, 3]
        lines.append(
            f"{name}: position t={_vector(t)} m; forward(+z)={_vector(R[:, 2])}, right(+x)={_vector(R[:, 0])}, "
            f"down(+y)={_vector(R[:, 1])}; fx={K[0, 0] / width:.6f}, fy={K[1, 1] / height:.6f}, "
            f"cx={K[0, 2] / width:.6f}, cy={K[1, 2] / height:.6f}; image {width}x{height}.")
    return "\n".join(lines) if found else ""


def assemble(*, description, representation: Representation, horizon, limits: Limits,
             blocks, initial_state: dict, cameras: dict, robot_text: str | None = None,
             custom_prompt: str = "") -> tuple[str, dict]:
    """The system prompt and the text of each block, keyed by block name."""
    unknown = set(blocks) - set(BLOCKS)
    if unknown:
        raise ValueError(f"unknown Direct prompt blocks: {sorted(unknown)}")
    parts = {}
    if "robot" in blocks:
        parts["robot"] = (robot_text or embodiment_text(description)).strip() + "\n\nControlled arms:\n" + "\n".join(
            f"- {arm.name}: {arm.control_frame}; {arm.grasp_site_description}" for arm in description.arms)
    if "timing" in blocks:
        parts["timing"] = timing_text(description.control_frequency_hz, limits, representation.space)
    if "gripper" in blocks:
        if "arms" in initial_state:
            parts["gripper"] = "\n".join(
                f"{name}: initial grasp-site orientation wxyz={_vector(state['ee_quat'])}."
                for name, state in initial_state["arms"].items()
            )
        else:
            parts["gripper"] = gripper_text(initial_state["ee_quat"])
    if "camera" in blocks and (text := camera_text(cameras)):
        parts["camera"] = text
    parts["action"] = representation.instruction(horizon)
    if len(description.arms) > 1:
        names = ", ".join(arm.name for arm in description.arms)
        parts["action"] += (
            "\n\nSynchronized control: actions is an array of time steps. Each step is a JSON object "
            f"with exactly these arm names as keys: {names}. Each value is that arm's numeric action "
            "in the format above. All arms execute together in one control interval. Each arm starts "
            "from its own observed pose. To keep an arm still, repeat its target (zero motion for a delta) "
            "and retain its gripper command."
        )
    if custom_prompt.strip():
        parts["custom"] = custom_prompt
    return "\n\n".join(parts.values()), parts
