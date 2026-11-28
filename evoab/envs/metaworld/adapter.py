"""MetaWorld MT50 native MuJoCo observations, 4-DoF Sawyer actions and task outcomes."""
from __future__ import annotations

from importlib import resources
import math
import time

import numpy as np

from evoab.servers.actor.metaworld_profile import ACTION_BINDING, POLICY_CAMERA, PROPRIOCEPTION
from evoab.envs.protocol.contracts import (
    ArmCommand,
    ArmDescription,
    ArmState,
    CameraDescription,
    EnvironmentDescription,
    EnvironmentState,
    NativeCommand,
    Pose,
    PoseCommand,
    RequestRejected,
    StepReceipt,
)
from evoab.envs.protocol.snapshots import CameraFrame, CapturedObservation, image_recording_stride

CAMERAS = ("corner2", "behindGripper", POLICY_CAMERA)
IMAGE_HEIGHT = 256
IMAGE_WIDTH = 256
POLICY_IMAGE_SIZE = 480
# The LeRobot MetaWorld wrapper uses this position for the fixed corner2 camera.
POLICY_CAMERA_POSITION = (0.75, 0.075, 0.7)


def native_action(values) -> NativeCommand:
    action = NativeCommand(values=values)
    if len(action.values) != 4 or any(abs(value) > 1 for value in action.values):
        raise RequestRejected("MetaWorld requires four normalized native action values in [-1, 1]")
    return action


def _camera_matrices(model, data, name: str, height: int, width: int):
    import mujoco

    cam_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, name)
    if cam_id < 0:
        return None, None
    fovy_rad = math.radians(float(model.cam_fovy[cam_id]))
    fy = 0.5 * height / math.tan(0.5 * fovy_rad)
    fx = fy
    intrinsic = np.array(
        [[fx, 0.0, width / 2.0], [0.0, fy, height / 2.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    # MuJoCo cameras look along -Z with +Y up; public optical frame uses +Z forward and +Y down.
    rot_world_cam = np.asarray(data.cam_xmat[cam_id], dtype=np.float64).reshape(3, 3)
    world_from_optical = np.eye(4, dtype=np.float64)
    world_from_optical[:3, :3] = rot_world_cam @ np.diag([1.0, -1.0, -1.0])
    world_from_optical[:3, 3] = np.asarray(data.cam_xpos[cam_id], dtype=np.float64)
    return intrinsic, world_from_optical


def _hand_body_id(model) -> int:
    import mujoco

    for body_name in ("hand", "right_hand"):
        bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body_name)
        if bid >= 0:
            return bid
    return -1


class MetaWorldAdapter:
    def __init__(self, env, obs, *, episode_id: str, instruction: str, metadata: dict, max_steps: int, depth_enabled: bool):
        import mujoco

        self.env = env
        self.obs = np.asarray(obs, dtype=np.float64)
        self.episode_id = episode_id
        self.instruction = instruction
        self.metadata = metadata
        self.max_steps = int(max_steps)
        self.depth_enabled = bool(depth_enabled)
        self.action_scale = float(getattr(env, "action_scale", 1.0 / 100.0))
        self.hand_body_id = _hand_body_id(env.model)
        self.renderer = mujoco.Renderer(env.model, height=IMAGE_HEIGHT, width=IMAGE_WIDTH)
        try:
            self.policy_renderer = mujoco.Renderer(
                env.model, height=POLICY_IMAGE_SIZE, width=POLICY_IMAGE_SIZE,
            )
        except BaseException:
            self.renderer.close()
            raise
        corner_id = mujoco.mj_name2id(env.model, mujoco.mjtObj.mjOBJ_CAMERA, "corner2")
        self.policy_camera_offset = np.asarray(POLICY_CAMERA_POSITION) - env.data.cam_xpos[corner_id]
        self.steps = 0
        self.success = False
        self.ended = False
        self.closed = False
        self.outcome = None
        self.command_opening = self._opening()

        control_hz = float(1.0 / (env.model.opt.timestep * getattr(env, "frame_skip", 5)))
        self.description = EnvironmentDescription(
            arms=[
                ArmDescription(
                    name="arm",
                    control_frame="world",
                    rotation_radius_m=0.0,
                    grasp_site_description=(
                        "Sawyer parallel-jaw gripper TCP in world metres; 4-DoF XYZ translation plus "
                        "normalized gripper opening (0 closed, 1 open). Wrist orientation is fixed."
                    ),
                )
            ],
            cameras=[CameraDescription(name=name, has_depth=self.depth_enabled, has_calibration=True) for name in CAMERAS],
            control_frequency_hz=control_hz,
            max_episode_steps=self.max_steps,
            action_binding=ACTION_BINDING,
            metadata={
                "image_recording_stride": image_recording_stride(control_hz),
                "control_constraints": (
                    f"Sawyer 4-DoF delta-XYZ + gripper control (action_scale={self.action_scale:.4f} m/step). "
                    "Wrist orientation is fixed and rotation targets are ignored."
                ),
                "observation_source": "native_metaworld_mujoco",
                "robot_prompt": resources.files("evoab.resources")
                .joinpath("prompts/direct/metaworld_sawyer.md")
                .read_text(encoding="utf-8"),
            },
        )

    def episode_identity(self):
        return self.instruction, self.metadata

    def _tcp_position(self) -> np.ndarray:
        return np.asarray(self.env.tcp_center, dtype=np.float64).ravel()[:3]

    def _tcp_quaternion_wxyz(self) -> np.ndarray:
        if self.hand_body_id >= 0:
            q = np.asarray(self.env.data.xquat[self.hand_body_id], dtype=np.float64).ravel()[:4]
            norm = float(np.linalg.norm(q))
            if norm > 1e-9:
                return q / norm
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)

    def _opening(self) -> float:
        if self.obs.size >= 4:
            return float(np.clip(self.obs[3], 0.0, 1.0))
        return 1.0

    def read_state(self) -> EnvironmentState:
        arm = ArmState(
            control_pose=Pose(
                position=self._tcp_position().tolist(),
                quaternion=self._tcp_quaternion_wxyz().tolist(),
            ),
            gripper=self._opening(),
            gripper_command=self.command_opening,
        )
        return EnvironmentState(
            arms={"arm": arm},
            state_version=self.steps,
            native_steps=self.steps,
            ended=self.ended,
            metadata={PROPRIOCEPTION: self.obs[:4].tolist()},
        )

    def validate_commands(self, commands, mode):
        if mode == "native":
            for command in commands:
                if not isinstance(command, NativeCommand):
                    raise RequestRejected("Supply a MetaWorld native command")
                native_action(command.values)
            return
        if mode != "pose":
            raise RequestRejected("MetaWorld MT50 accepts pose and native commands")
        for command in commands:
            if (
                not isinstance(command, PoseCommand)
                or set(command.arms) != {"arm"}
                or not isinstance(command.arms["arm"], ArmCommand)
            ):
                raise RequestRejected("Supply one pose command and gripper opening for arm")

    def _encode(self, command: PoseCommand) -> np.ndarray:
        target = command.arms["arm"]
        delta_xyz = (np.asarray(target.pose.position, dtype=np.float64) - self._tcp_position()) / self.action_scale
        # MetaWorld Sawyer gripper action: +1 closes, -1 opens; EvoAB opening: 0 closed, 1 open.
        grip = 1.0 - 2.0 * float(np.clip(target.gripper, 0.0, 1.0))
        return np.array([*np.clip(delta_xyz, -1.0, 1.0), grip], dtype=np.float32)

    def step_pose(self, command):
        self.validate_commands([command], "pose")
        return self._step(self._encode(command), float(command.arms["arm"].gripper))

    def _step(self, action, opening):
        if self.ended or self.closed:
            raise RequestRejected("Episode has ended")
        obs, _, terminated, truncated, info = self.env.step(action)
        self.obs = np.asarray(obs, dtype=np.float64)
        self.steps += 1
        self.command_opening = opening
        self.success = bool((info or {}).get("success", False))
        self.ended = bool(terminated or truncated or self.success or self.steps >= self.max_steps)
        return StepReceipt(accepted=True, state=self.read_state(), diagnostics={"executed_steps": 1})

    def step_joints(self, command):
        raise RequestRejected("Joint control is unavailable")

    def step_native(self, command):
        self.validate_commands([command], "native")
        action = np.asarray(command.values, dtype=np.float32)
        return self._step(action, 0.5 * (1.0 - command.values[-1]))

    def preview_joints(self, commands):
        raise RequestRejected("Joint previews are unavailable")

    def capture(self, cameras=None, *, high_resolution=False):
        names = list(CAMERAS) if cameras is None else cameras
        if not names or len(names) != len(set(names)) or set(names) - set(CAMERAS):
            raise RequestRejected("Select unique configured cameras")
        frames = {}
        for name in names:
            policy_view = name == POLICY_CAMERA
            renderer = self.policy_renderer if policy_view else self.renderer
            native_camera = "corner2" if policy_view else name
            renderer.disable_depth_rendering()
            renderer.update_scene(self.env.data, camera=native_camera)
            if policy_view:
                # Offset only the render camera, leaving the simulator and original view intact.
                for eye in renderer.scene.camera:
                    eye.pos[:] += self.policy_camera_offset
            rgb = np.ascontiguousarray(renderer.render(), dtype=np.uint8)
            depth = None
            if self.depth_enabled:
                renderer.enable_depth_rendering()
                depth = np.ascontiguousarray(renderer.render(), dtype=np.float32)
                renderer.disable_depth_rendering()
            intrinsic, transform = _camera_matrices(
                self.env.model, self.env.data, native_camera, rgb.shape[0], rgb.shape[1]
            )
            if policy_view:
                transform[:3, 3] += self.policy_camera_offset
            frames[name] = CameraFrame(rgb, depth, intrinsic, transform)
        state = self.read_state()
        return CapturedObservation(
            self.episode_id,
            self.steps,
            time.time(),
            state.model_dump(mode="json"),
            frames,
            terminated=self.ended,
        )

    def finish(self, reason, status=None):
        if self.outcome is None:
            self.ended = True
            self.outcome = {
                "task_success": bool(self.success),
                "native_steps": self.steps,
                "reason": reason,
                "benchmark": "metaworld",
                "task_id": self.metadata["task_id"],
                "initial_state_sha256": self.metadata["initial_state_sha256"],
            }
        return dict(self.outcome)

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                try:
                    self.policy_renderer.close()
                finally:
                    self.renderer.close()
            finally:
                self.env.close()
