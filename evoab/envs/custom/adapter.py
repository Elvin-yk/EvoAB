"""Environment backend over a user's Gym-style environment.

Runs inside the benchmark's own interpreter; depends only on numpy and the
EvoAB runtime contracts (no SciPy), so it fits most simulator environments.
"""

from __future__ import annotations

import importlib
import sys
import time
from typing import Any

import numpy as np

from evoab.envs.protocol.contracts import (
    ArmDescription, ArmState, CameraDescription, EnvironmentDescription, EnvironmentState, Pose, PoseCommand,
    RequestRejected, StepReceipt,
)
from evoab.envs.protocol.snapshots import CameraFrame, CapturedObservation, image_recording_stride


def resolve(path: str | None, target: str):
    if path and path not in sys.path:
        sys.path.insert(0, path)
    module, _, attribute = target.partition(":")
    return getattr(importlib.import_module(module), attribute)


def list_tasks(manifest: dict) -> list[dict]:
    if manifest.get("tasks"):
        return manifest["tasks"]
    source = resolve(manifest.get("path"), manifest["tasks_from"])
    items = source() if callable(source) else source
    return [{"id": str(t["id"]), "instruction": str(t["instruction"]), "max_steps": t.get("max_steps")} for t in items]


# -- quaternions (wxyz) -------------------------------------------------------

def _to_wxyz(q, order: str) -> np.ndarray:
    q = np.asarray(q, dtype=float).ravel()[:4]
    q = q if order == "wxyz" else q[[3, 0, 1, 2]]
    return q / np.linalg.norm(q)


def _from_wxyz(q, order: str) -> list[float]:
    q = np.asarray(q, dtype=float)
    return (q if order == "wxyz" else q[[1, 2, 3, 0]]).tolist()


def _multiply(a, b) -> np.ndarray:
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2])


def _rotvec(q) -> np.ndarray:
    q = q if q[0] >= 0 else -q
    angle = 2 * np.arccos(np.clip(q[0], -1, 1))
    s = np.sqrt(max(1 - q[0] ** 2, 0))
    return np.zeros(3) if s < 1e-9 else q[1:] / s * angle


class GymAdapter:
    def __init__(self, manifest: dict, task_id: str, seed: int):
        self.manifest, self.task_id, self.seed = manifest, task_id, seed
        tasks = {t["id"]: t for t in list_tasks(manifest)}
        if task_id not in tasks:
            raise RequestRejected(f"unknown task {task_id!r}")
        self.task = tasks[task_id]
        self.obs_map, self.action_map = manifest["observation"], manifest["action"]
        self.env = resolve(manifest.get("path"), manifest["make"])(task_id, seed)
        reset = self.env.reset()
        self.obs = reset[0] if isinstance(reset, tuple) else reset
        self.info: dict[str, Any] = reset[1] if isinstance(reset, tuple) and len(reset) > 1 else {}
        self.max_steps = int(self.task.get("max_steps") or manifest["max_steps"])
        self.steps, self.success, self.ended, self.closed, self.outcome = 0, False, False, False, None
        self.command_opening = self._opening()
        cameras = list(self.obs_map["cameras"])
        self.description = EnvironmentDescription(
            arms=[ArmDescription(name="arm", control_frame="world", rotation_radius_m=0.1,
                                 grasp_site_description=self.obs_map.get("pose_description") or "end effector")],
            cameras=[CameraDescription(name=name, has_depth=False, has_calibration=False) for name in cameras],
            control_frequency_hz=float(manifest["control_hz"]), max_episode_steps=self.max_steps,
            metadata={"image_recording_stride": image_recording_stride(manifest["control_hz"]),
                      "observation_source": "custom_gym", "benchmark": manifest["name"],
                      **({"robot_prompt": manifest["prompt"]} if manifest.get("prompt") else {})})

    # -- identity and state ---------------------------------------------------

    def episode_identity(self):
        return self.task["instruction"], {"benchmark": self.manifest["name"], "task_id": self.task_id,
                                           "seed": self.seed, "instruction": self.task["instruction"]}

    def _pose(self) -> tuple[np.ndarray, np.ndarray]:
        position = np.asarray(self.obs[self.obs_map["ee_pos"]], dtype=float).ravel()[:3]
        return position, _to_wxyz(self.obs[self.obs_map["ee_quat"]], self.obs_map.get("quat_order", "wxyz"))

    def _opening(self) -> float:
        key = self.obs_map.get("gripper")
        if not key:
            return 1.0
        values = np.asarray(self.obs[key], dtype=float).ravel()
        reduce = self.obs_map.get("gripper_reduce", "abs_sum")
        value = {"first": values[0], "sum": values.sum(), "mean": values.mean(), "abs_sum": np.abs(values).sum()}[reduce]
        opened, closed = float(self.obs_map.get("gripper_open", 0.08)), float(self.obs_map.get("gripper_closed", 0.0))
        return float(np.clip((value - closed) / (opened - closed), 0, 1)) if opened != closed else 1.0

    def read_state(self) -> EnvironmentState:
        position, quaternion = self._pose()
        arm = ArmState(control_pose=Pose(position=position.tolist(), quaternion=quaternion.tolist()),
                       gripper=self._opening(), gripper_command=self.command_opening)
        return EnvironmentState(arms={"arm": arm}, state_version=self.steps, native_steps=self.steps, ended=self.ended)

    # -- actions --------------------------------------------------------------

    def validate_commands(self, commands, mode):
        if mode != "pose":
            raise RequestRejected("Only pose control is available")
        for command in commands:
            if not isinstance(command, PoseCommand) or set(command.arms) != {"arm"}:
                raise RequestRejected("Supply one pose command for arm")

    def _encode(self, command: PoseCommand) -> np.ndarray:
        target = command.arms["arm"]
        a = self.action_map
        grip = a["gripper_closed"] + (a["gripper_open"] - a["gripper_closed"]) * float(target.gripper)
        if a["type"] == "absolute_eef":
            return np.array([*target.pose.position, *_from_wxyz(target.pose.quaternion, a.get("quat_order", "wxyz")), grip])
        position, quaternion = self._pose()
        desired = np.asarray(target.pose.quaternion, dtype=float)
        delta_rotation = _rotvec(_multiply(desired, quaternion * np.array([1, -1, -1, -1])))
        arm = np.r_[(np.asarray(target.pose.position) - position) / a["position_scale"],
                    delta_rotation / a["rotation_scale"]]
        return np.r_[np.clip(arm, -1, 1), grip]

    def step_pose(self, command):
        self.validate_commands([command], "pose")
        if self.ended or self.closed:
            raise RequestRejected("Episode has ended")
        result = self.env.step(self._encode(command))
        if len(result) == 5:
            self.obs, _, terminated, truncated, self.info = result
            done = bool(terminated or truncated)
        else:
            self.obs, _, done, self.info = result
        self.steps += 1
        self.command_opening = float(command.arms["arm"].gripper)
        success = self.manifest.get("success") or {}
        if success.get("method"):
            self.success = bool(getattr(self.env, success["method"])())
        else:
            self.success = bool((self.info or {}).get(success.get("info_key") or "success", False))
        self.ended = bool(done or self.success or self.steps >= self.max_steps)
        return StepReceipt(accepted=True, state=self.read_state(), diagnostics={"executed_steps": 1})

    def step_joints(self, command):
        raise RequestRejected("Joint control is unavailable")

    def step_native(self, command):
        raise RequestRejected("Raw action control is unavailable")

    def preview_joints(self, commands):
        raise RequestRejected("Joint previews are unavailable")

    # -- observations and lifecycle -------------------------------------------

    def capture(self, cameras=None, *, high_resolution=False):
        mapping = self.obs_map["cameras"]
        names = list(mapping) if cameras is None else cameras
        if not names or set(names) - set(mapping):
            raise RequestRejected("Select configured cameras")
        frames = {}
        for name in names:
            rgb = np.asarray(self.obs[mapping[name]])
            if rgb.dtype != np.uint8:
                rgb = np.clip(rgb * (255 if rgb.max() <= 1.0 else 1), 0, 255).astype(np.uint8)
            if self.obs_map.get("flip_vertical"):
                rgb = rgb[::-1]
            frames[name] = CameraFrame(np.ascontiguousarray(rgb[..., :3]), None, None, None)
        state = self.read_state()
        return CapturedObservation(self.manifest["name"], self.steps, time.time(), state.model_dump(mode="json"),
                                   frames, terminated=self.ended)

    def finish(self, reason, status=None):
        if self.outcome is None:
            self.ended = True
            self.outcome = {"task_success": bool(self.success), "native_steps": self.steps, "reason": reason,
                            "benchmark": self.manifest["name"], "task_id": self.task_id}
        return dict(self.outcome)

    def close(self):
        if not self.closed:
            self.closed = True
            close = getattr(self.env, "close", None)
            if callable(close):
                close()
