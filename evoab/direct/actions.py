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
class Limits:
    translation_m: float = 0.02
    rotation_rad: float = 0.1


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


def _rows(arguments: dict, width: int, horizon: tuple[int, int]) -> list[list[float]]:
    if not isinstance(arguments, dict):
        raise OutputError("arguments must be a JSON object")
    extra = set(arguments) - {"actions", "note"}
    if extra:
        raise OutputError(f"unexpected fields: {sorted(extra)}")
    rows = arguments.get("actions")
    if not isinstance(rows, list):
        raise OutputError("actions must be an array of rows")
    low, high = horizon
    if not low <= len(rows) <= high:
        raise OutputError(f"actions must contain {low} to {high} rows, got {len(rows)}")
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

    def schema(self, horizon: tuple[int, int]) -> dict:
        return {"type": "function", "function": {
            "name": self.tool, "description": self.description,
            "parameters": {"type": "object", "properties": {
                "actions": {"type": "array", "minItems": horizon[0], "maxItems": horizon[1],
                            "items": {"type": "array", "items": {"type": "number"},
                                      "minItems": self.width, "maxItems": self.width}},
                "note": {"type": "string", "description": "Brief reason for this chunk and what to check next."},
            }, "required": ["actions", "note"], "additionalProperties": False}}}

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


REPRESENTATIONS: dict[str, Representation] = {r.name: r for r in (AbsoluteEEF(), DeltaEEF())}
