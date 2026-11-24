"""Action representations a Direct policy may emit, and how each becomes control targets.

A representation fixes three things: the single tool the model must call, the
prompt paragraph that defines its rows, and the decoding of those rows into one
absolute grasp-site target per control step. Decoding happens completely before
the environment advances; a malformed chunk is never partially executed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


class OutputError(ValueError):
    """The model's reply cannot be decoded into a complete action chunk."""


@dataclass(frozen=True)
class Target:
    position: tuple[float, float, float]
    quaternion: tuple[float, float, float, float]  # wxyz
    closure: float  # 0 = open, 1 = closed
    clamped: bool = False

    def row(self) -> list[float]:
        return [*self.position, *self.quaternion, self.closure]


@dataclass(frozen=True)
class JointRow:
    """One control step of absolute joint targets for one arm."""

    joints: tuple[float, ...]
    closure: float  # 0 = open, 1 = closed
    clamped: bool = False

    def row(self) -> list[float]:
        return [*self.joints, self.closure]


@dataclass(frozen=True)
class Limits:
    translation_m: float = 0.02
    rotation_rad: float = 0.1
    joint_rad: float = 0.05


def _quat_wxyz(rotation) -> tuple[float, float, float, float]:
    x, y, z, w = rotation.as_quat()
    if w < 0:
        w, x, y, z = -w, -x, -y, -z
    return (float(w), float(x), float(y), float(z))


def _rotation(wxyz):
    from scipy.spatial.transform import Rotation
    w, x, y, z = wxyz
    return Rotation.from_quat([x, y, z, w])


def _limit(previous: Target, position, rotation, closure: float, limits: Limits) -> Target:
    """Shorten one step toward its requested pose; the gripper command is never shortened."""
    from scipy.spatial.transform import Rotation, Slerp
    start = np.asarray(previous.position)
    step = np.asarray(position) - start
    distance = float(np.linalg.norm(step))
    clamped = False
    if distance > limits.translation_m:
        step *= limits.translation_m / distance
        clamped = True
    begin = _rotation(previous.quaternion)
    angle = float((begin.inv() * rotation).magnitude())
    if angle > limits.rotation_rad:
        rotation = Slerp([0, 1], Rotation.concatenate([begin, rotation]))(limits.rotation_rad / angle)
        clamped = True
    return Target(tuple(float(v) for v in start + step), _quat_wxyz(rotation), closure, clamped)


def _action_rows(arguments: dict, horizon: tuple[int, int]) -> list:
    if not isinstance(arguments, dict):
        raise OutputError("arguments must be a JSON object")
    extra = set(arguments) - {"actions", "note"}
    if extra:
        raise OutputError(f"unexpected fields: {sorted(extra)}")
    if "note" in arguments and not isinstance(arguments["note"], str):
        raise OutputError("note must be a string")
    rows = arguments.get("actions")
    if not isinstance(rows, list):
        raise OutputError("actions must be an array of rows")
    low, high = horizon
    if not low <= len(rows) <= high:
        raise OutputError(f"actions must contain {low} to {high} rows, got {len(rows)}")
    return rows


def _rows(arguments: dict, width: int, horizon: tuple[int, int]) -> list[list[float]]:
    rows = _action_rows(arguments, horizon)
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != width:
            raise OutputError(f"row {index + 1} must have exactly {width} numbers")
        for value in row:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise OutputError(f"row {index + 1} contains a non-finite or non-numeric value")
        if not 0 <= row[-1] <= 1:
            raise OutputError(f"row {index + 1} gripper must be between 0 (open) and 1 (closed)")
    return rows


class Representation:
    name: str
    label: str
    tool: str
    width: int
    columns: tuple[str, ...]
    #: "pose" targets the registered grasp site through the environment's IK; "joint" bypasses it.
    space: str = "pose"

    def bind(self, description) -> "Representation":
        """Specialize to one environment description (joint counts, names); pose rows need nothing."""
        return self

    def schema(self, horizon: tuple[int, int], arms: tuple[str, ...] = ()) -> dict:
        row = {"type": "array", "items": {"type": "number"}, "minItems": self.width, "maxItems": self.width}
        if len(arms) > 1:
            row = {"type": "object", "properties": {name: row for name in arms},
                   "required": list(arms), "additionalProperties": False}
        return {"type": "function", "function": {
            "name": self.tool, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "actions": {"type": "array", "minItems": horizon[0], "maxItems": horizon[1],
                            "items": row},
                "note": {"type": "string", "description": "Brief reason for this chunk and what to check next."},
            }, "required": ["actions", "note"], "additionalProperties": False}}}

    def decode_chunk(self, arguments: dict, measured: dict[str, Target], horizon, limits: Limits
                     ) -> list[dict[str, Target]]:
        """Validate the entire synchronized chunk before any arm can move."""
        if len(measured) == 1:
            name, start = next(iter(measured.items()))
            return [{name: target} for target in self.decode(arguments, start, horizon, limits)]
        rows = _action_rows(arguments, horizon)
        for i, row in enumerate(rows, 1):
            if not isinstance(row, dict) or row.keys() != measured.keys():
                raise OutputError(f"row {i} must provide exactly the arms {list(measured)}")
        tracks = {}
        for name, start in measured.items():
            try:
                tracks[name] = self.decode({"actions": [row[name] for row in rows]}, start, horizon, limits)
            except OutputError as error:
                raise OutputError(f"{name}: {error}") from error
        return [{name: track[i] for name, track in tracks.items()} for i in range(len(rows))]

    def decode(self, arguments: dict, measured: Target, horizon, limits: Limits) -> list[Target]:
        raise NotImplementedError

    def instruction(self, horizon: tuple[int, int]) -> str:
        raise NotImplementedError


class AbsoluteEEF(Representation):
    name, label, tool, width = "abs_eef", "Abs. EEF", "emit_eef_actions", 8
    columns = ("x", "y", "z", "qw", "qx", "qy", "qz", "gripper")
    description = "Emit a variable-length chunk of absolute grasp-point EEF targets."

    def instruction(self, horizon):
        return (
            "Each EEF action is an ABSOLUTE grasp-point pose in the world frame: "
            "[x, y, z, qw, qx, qy, qz, gripper]. Positions are metres, quaternions use unit wxyz order, "
            "and gripper is 0=open or 1=closed. The client normalizes quaternions and enforces sequential "
            "Cartesian step limits, but those checks do not replace careful planning.\n\n"
            f"Respond with exactly one {self.tool} tool call containing between {horizon[0]} and {horizon[1]} "
            "consecutive absolute 8D EEF actions. Specify every executed control target explicitly; the client "
            "does not interpolate between model-emitted actions. Choose the number of actions needed before "
            "the next visual replan.")

    def decode(self, arguments, measured, horizon, limits):
        targets, previous = [], measured
        for row in _rows(arguments, self.width, horizon):
            quaternion = np.asarray(row[3:7], dtype=float)
            if np.linalg.norm(quaternion) < 1e-9:
                raise OutputError("quaternion cannot be zero")
            rotation = _rotation(quaternion / np.linalg.norm(quaternion))
            previous = _limit(previous, row[:3], rotation, float(row[7]), limits)
            targets.append(previous)
        return targets


class DeltaEEF(Representation):
    name, label, tool, width = "delta_eef", "Delta EEF", "emit_delta_eef_actions", 7
    columns = ("dx", "dy", "dz", "drx", "dry", "drz", "gripper")
    description = "Emit a variable-length chunk of stepwise world-frame EEF deltas."

    def instruction(self, horizon):
        return (
            "Each EEF action is a STEPWISE delta of the grasp point in the fixed world frame: "
            "[dx, dy, dz, drx, dry, drz, gripper]. Row k moves from the previous row's target "
            "(row 1 starts from the newest measured ee_pos/ee_quat): position adds [dx, dy, dz] metres, "
            "orientation applies the world-frame rotation vector [drx, dry, drz] radians on the left. "
            "Gripper is absolute: 0=open or 1=closed. The client enforces sequential Cartesian step limits.\n\n"
            f"Respond with exactly one {self.tool} tool call containing between {horizon[0]} and {horizon[1]} "
            "consecutive 7D delta actions. The client does not interpolate between rows. Choose the number of "
            "actions needed before the next visual replan.")

    def decode(self, arguments, measured, horizon, limits):
        from scipy.spatial.transform import Rotation
        targets, previous = [], measured
        for row in _rows(arguments, self.width, horizon):
            position = np.asarray(previous.position) + np.asarray(row[:3])
            rotation = Rotation.from_rotvec(row[3:6]) * _rotation(previous.quaternion)
            previous = _limit(previous, position, rotation, float(row[6]), limits)
            targets.append(previous)
        return targets


class AbsoluteJoints(Representation):
    """Absolute joint positions per control step; the environment applies them without IK."""

    name, label, tool = "joint_abs", "Abs. joints", "emit_joint_actions"
    description = "Emit a variable-length chunk of absolute joint-position targets."
    space = "joint"
    width = 0
    columns: tuple[str, ...] = ()

    def __init__(self, joint_names: dict[str, tuple[str, ...]] | None = None):
        self.joint_names = dict(joint_names or {})

    def bind(self, description) -> "AbsoluteJoints":
        names = {}
        for arm in description.arms:
            control = arm.control
            if control is not None and "joints" not in control.commands:
                raise ValueError(f"arm {arm.name!r} does not accept joint commands; use a pose representation")
            if not arm.joint_names:
                raise ValueError(f"arm {arm.name!r} declares no joint_names; joint control is unavailable")
            names[arm.name] = tuple(arm.joint_names)
        return AbsoluteJoints(names)

    def _width(self, arm: str) -> int:
        return len(self.joint_names[arm]) + 1

    def schema(self, horizon, arms=()):
        arms = tuple(arms) or tuple(self.joint_names)

        def row(arm):
            width = self._width(arm)
            return {"type": "array", "items": {"type": "number"}, "minItems": width, "maxItems": width}
        items = row(arms[0]) if len(arms) == 1 else {
            "type": "object", "properties": {arm: row(arm) for arm in arms},
            "required": list(arms), "additionalProperties": False}
        return {"type": "function", "function": {
            "name": self.tool, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "actions": {"type": "array", "minItems": horizon[0], "maxItems": horizon[1], "items": items},
                "note": {"type": "string", "description": "Brief reason for this chunk and what to check next."},
            }, "required": ["actions", "note"], "additionalProperties": False}}}

    def instruction(self, horizon):
        layout = "; ".join(f"{arm}: [{', '.join(names)}, gripper]" for arm, names in self.joint_names.items())
        return (
            "Each action row gives ABSOLUTE joint positions in radians (prismatic joints in metres) in the "
            f"declared joint order, followed by gripper (0=open, 1=closed). Row layout per arm: {layout}. "
            "The client limits each joint's change per row and never interpolates between rows. Joint targets "
            "bypass inverse kinematics: check measured joints and the camera views after each chunk.\n\n"
            f"Respond with exactly one {self.tool} tool call containing between {horizon[0]} and {horizon[1]} "
            "consecutive rows. Choose the number of rows needed before the next visual replan.")

    def decode_chunk(self, arguments, measured, horizon, limits):
        rows = _action_rows(arguments, horizon)
        arms = list(measured)
        if len(arms) == 1:
            rows = [{arms[0]: row} for row in rows]
        result, previous = [], dict(measured)
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict) or set(row) != set(arms):
                raise OutputError(f"row {index} must provide exactly the arms {arms}")
            step = {}
            for arm in arms:
                values = row[arm]
                width = self._width(arm)
                if not isinstance(values, list) or len(values) != width:
                    raise OutputError(f"row {index} ({arm}) must have exactly {width} numbers")
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                    raise OutputError(f"row {index} ({arm}) contains a non-finite or non-numeric value")
                if not 0 <= values[-1] <= 1:
                    raise OutputError(f"row {index} ({arm}) gripper must be between 0 (open) and 1 (closed)")
                start = np.asarray(previous[arm].joints, dtype=float)
                target = np.asarray(values[:-1], dtype=float)
                if start.shape != target.shape:
                    raise OutputError(f"{arm}: measured {start.size} joints but the row has {target.size}")
                delta = np.clip(target - start, -limits.joint_rad, limits.joint_rad)
                clamped = bool(np.any(np.abs(target - start) > limits.joint_rad + 1e-12))
                step[arm] = JointRow(tuple(float(v) for v in start + delta), float(values[-1]), clamped)
            previous = step
            result.append(step)
        return result


REPRESENTATIONS: dict[str, Representation] = {r.name: r for r in (AbsoluteEEF(), DeltaEEF(), AbsoluteJoints())}
