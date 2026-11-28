"""A reach episode driven by RuboClaw IK and measured Gazebo wrist poses."""
from __future__ import annotations

import base64
import math
import time
import zlib

import numpy as np

from evoab.integrations.openarm_gazebo import reach_instruction
from evoab.services.environment.contracts import (
    ArmCommand, ArmControlCapabilities, ArmDescription, ArmState, CameraDescription, EnvironmentDescription,
    EnvironmentState, JointTarget, Pose, PoseCommand, RequestRejected, StepReceipt,
)
from evoab.services.environment.snapshots import CameraFrame, CapturedObservation

LIFT_M = 0.04
POSITION_TOL_M = 0.005
ORIENTATION_TOL_RAD = 0.05


class OpenArmAdapter:
    def __init__(self, native, side, run_id):
        self.native, self.side, self.run_id = native, side, run_id
        home = native("home")
        self.sample = home["sample"]
        self.joint_target = home["joint_target"]
        self.controller_fault = None
        self.start = tuple(self.sample["pose"])
        self.goal = (*self.start[:2], self.start[2] + LIFT_M, *self.start[3:])
        self.opening_command = 1.0
        self.steps = 0
        self.ended = False
        self.description = EnvironmentDescription(
            arms=[ArmDescription(name=side, control_frame="arm_origin", rotation_radius_m=0.1,
                                 grasp_site_description=f"openarm_{side}_ee_base_link (wrist)",
                                 joint_names=[f"openarm_{side}_joint{i}" for i in range(1, 8)],
                                 control=ArmControlCapabilities(commands=["pose"]))],
            cameras=[CameraDescription(name="scene", has_depth=False, has_calibration=False)],
            control_frequency_hz=20, max_episode_steps=40, observation_interval_steps=1,
            metadata={"observation_source": "gazebo_joint_states_and_tf", "normalized_opening_tolerance": 0.05,
                      "robot_prompt": "The task world is arm_origin on the torso. Only the selected wrist moves. "
                                      "Use the precision profile and preserve the initial wrist orientation."})

    def episode_identity(self):
        return reach_instruction(self.side), {"benchmark": "openarm_gazebo", "task_id": f"reach_{self.side}", "seed": 0}

    def _opening(self):
        return min(1.0, max(0.0, -self.sample["finger_rad"] / (math.pi / 4)))

    def read_state(self):
        pose = self.sample["pose"]
        arm = ArmState(control_pose=Pose(position=pose[:3], quaternion=pose[3:]), joints=self.sample["joints"],
                       gripper=self._opening(), gripper_command=self.opening_command,
                       metadata={"gripper_opening_measured": True, "joint_position_command": self.joint_target})
        return EnvironmentState(arms={self.side: arm}, state_version=self.steps, native_steps=self.steps,
                                ended=self.ended, metadata={"sim_time_ns": self.sample["sim_time_ns"],
                                    "controller": {"ready": self.sample["controller_ready"], "fault": self.controller_fault}})

    def validate_commands(self, commands, mode):
        for command in commands:
            if mode != "pose" or not isinstance(command, PoseCommand) or command.solver != "native":
                raise RequestRejected("OpenArm reach accepts native pose control")
            if set(command.arms) != {self.side}:
                raise RequestRejected(f"Only the {self.side} arm is active")
            target = command.arms[self.side]
            if isinstance(target, JointTarget) and target.joints != self.joint_target:
                raise RequestRejected("A held arm retains its last joint target")

    def step_pose(self, command):
        self.validate_commands([command], "pose")
        if self.ended:
            raise RequestRejected("OpenArm episode has ended")
        target = command.arms[self.side]
        if isinstance(target, ArmCommand):
            result = self.native("step", *target.pose.position, *target.pose.quaternion, target.gripper)
        else:
            result = self.native("joints", *target.joints, target.gripper)
        self.sample = result["sample"]
        self.steps += int(result["sent"])
        diagnostics = {"controller_status": result["controller_status"], "controller_error_code": result["controller_error_code"]}
        if not result["success"]:
            self.controller_fault = {**diagnostics, "message": result["error"]}
            self.ended = result["sent"]
            return StepReceipt(accepted=False, state=self.read_state(), diagnostics=self.controller_fault,
                               execution_outcome="partial" if result["sent"] else "none")
        self.controller_fault = None
        self.joint_target, self.opening_command = result["joint_target"], target.gripper
        self.ended = self.steps >= self.description.max_episode_steps
        return StepReceipt(accepted=True, state=self.read_state(), diagnostics=diagnostics)

    def step_joints(self, command):
        raise RequestRejected("Joint control is unavailable")

    def step_native(self, command):
        raise RequestRejected("Raw actions are unavailable")

    def preview_joints(self, commands):
        raise RequestRejected("Joint previews are unavailable")

    def capture(self, cameras=None, *, high_resolution=False):
        if cameras is not None and set(cameras) - {"scene"}:
            raise RequestRejected("The OpenArm reach camera is named scene")
        frames = {}
        capture_rgb = cameras is None or "scene" in cameras
        measured = self.native("capture" if capture_rgb else "read")["sample"]
        if capture_rgb:
            rgb = measured.pop("rgb")
            rows = np.frombuffer(zlib.decompress(base64.b64decode(rgb["data"])), dtype=np.uint8)
            pixels = rows.reshape(rgb["height"], rgb["row_bytes"])[:, :3 * rgb["width"]]
            frames["scene"] = CameraFrame(pixels.reshape(rgb["height"], rgb["width"], 3))
        self.sample = measured
        state = self.read_state()
        if capture_rgb:
            state.metadata["camera_sim_time_ns"] = rgb["sim_time_ns"]
        return CapturedObservation(self.run_id, self.steps, time.time(), state.model_dump(mode="json"), frames,
                                   terminated=self.ended)

    def finish(self, reason, status=None):
        self.sample = self.native("read")["sample"]
        self.ended = True
        pose = self.sample["pose"]
        position_error = math.dist(pose[:3], self.goal[:3])
        dot = abs(sum(a * b for a, b in zip(pose[3:], self.goal[3:])))
        orientation_error = 2 * math.acos(min(1.0, dot))
        return {"task_success": self.steps > 0 and position_error <= POSITION_TOL_M
                                and orientation_error <= ORIENTATION_TOL_RAD,
                "native_steps": self.steps, "reason": reason, "frame": "arm_origin",
                "start_pose": self.start, "target_pose": self.goal, "final_pose": pose,
                "position_error_m": position_error, "orientation_error_rad": orientation_error,
                "sim_time_ns": self.sample["sim_time_ns"]}

    def close(self):
        pass
