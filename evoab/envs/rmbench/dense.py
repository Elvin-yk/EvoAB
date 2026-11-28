"""Expose RMBench's reference dense joint controller and real camera history."""

from collections import deque
from dataclasses import replace
import hashlib
import json
from math import ceil
from pathlib import Path
import time

import numpy as np

from evoab.envs.rmbench.robot import RoboTwinAdapter, native_ee_action
from evoab.servers.actor.rmbench_profile import (
    ACTION_BINDING, ARMS, CAMERAS, HISTORY_FRAMES, HISTORY_STRIDE, JOINTS, NATIVE_FREQUENCY_HZ,
    PHYSICS_SUBSTEPS, REFERENCE_REVISION, STATE_FIELD,
)
from evoab.envs.protocol.contracts import NativeCommand, RequestRejected, StepReceipt
from evoab.envs.protocol.snapshots import CameraFrame, CapturedHistoryFrame


def native_command(values) -> NativeCommand:
    command = NativeCommand(values=values)
    if len(command.values) != JOINTS:
        raise RequestRejected(f"RMBench native control requires {JOINTS} joint and gripper targets")
    return command


def fixture(args):
    manifest = args.baseline_manifest.expanduser().resolve(strict=True)
    data = json.loads(manifest.read_text())
    rows = [row for row in data["selected"] if row["task"] == args.task and row["seed"] == args.seed]
    if (data["benchmark_revision"] != REFERENCE_REVISION or data["task_config"] != args.task_config
            or data["instruction_type"] != "seen" or len(rows) != 1):
        raise ValueError("Select one matching case from the reference RMBench baseline manifest")
    case = rows[0]
    if not (case["reset_ok"] and case["done"] and case["success"] and case["instruction"]):
        raise ValueError("The case lacks a successful native outcome or original instruction")
    inputs = (manifest.parent / data.get("snapshot_directory", "cases")).resolve(strict=True)
    if not inputs.is_relative_to(manifest.parent):
        raise ValueError("Baseline snapshots must be stored under the manifest directory")
    snapshot = (inputs / case["initial_state"]).resolve(strict=True)
    if not snapshot.is_relative_to(inputs) or hashlib.sha256(snapshot.read_bytes()).hexdigest() != case["initial_state_sha256"]:
        raise ValueError("The baseline initial observation hash differs from the manifest")
    return case, snapshot


def prepare_dense(args):
    """Reset the exact seed and prompt with the simulator used by the reference evaluator."""
    import subprocess

    from rmbench_sim.rmbench_env import RMBenchSimEnv, VENDORED_SIM_DIR, encode_obs, obs_state

    root = args.native_root.expanduser().resolve(strict=True)
    if Path(VENDORED_SIM_DIR).resolve() != root:
        raise ValueError("Use the upstream evaluator's vendored RMBench simulator")
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if revision != REFERENCE_REVISION:
        raise ValueError(f"The reference simulator requires SimpleMemVLA revision {REFERENCE_REVISION}")
    case, snapshot = fixture(args)
    simulator = RMBenchSimEnv(args.task, args.task_config, str(root))
    try:
        observation = simulator.reset(args.seed, instruction=case["instruction"])
        if simulator.step_lim != case["step_limit"] or simulator.take_action_cnt != 0:
            raise ValueError("The native episode budget or initial tick differs from the baseline")
        with np.load(snapshot, allow_pickle=False) as expected:
            np.testing.assert_array_equal(obs_state(observation), expected["state"],
                                          err_msg="RMBench reset joint state differs from baseline")
            checks = {name: bool(np.array_equal(image, expected[name]))
                      for name, image in encode_obs(observation).items()}
        if not all(checks.values()):
            raise ValueError("RMBench reset camera images differ from the baseline")
        metadata = {"benchmark": "rmbench", "native_revision": revision, "task_id": args.task,
                    "seed": args.seed, "task_config": args.task_config, "instruction_type": "seen",
                    "embodiment": "aloha-agilex", "initial_state_sha256": case["initial_state_sha256"],
                    "initial_joint_state_matches": True, "initial_rgb_matches": checks,
                    "mixed_control": args.mixed_control}
        return DenseRMBenchAdapter(simulator, observation, episode_id=args.run_id,
                                   instruction=case["instruction"], metadata=metadata, mixed_control=args.mixed_control)
    except BaseException:
        simulator.close()
        raise


class DenseRMBenchAdapter(RoboTwinAdapter):
    """Own physics ticks and camera frames while preserving the reference native evaluator."""

    def __init__(self, simulator, observation, *, episode_id, instruction, metadata, mixed_control=False):
        self.simulator = simulator
        self.mixed_control = mixed_control
        self.history = deque(maxlen=HISTORY_FRAMES)
        super().__init__(simulator.TASK_ENV, episode_id=episode_id, instruction=instruction,
                         metadata=metadata, depth_enabled=False, cameras=CAMERAS, observation=observation)
        self.observed_step = 0
        self.description.action_binding = ACTION_BINDING
        self.description.control_frequency_hz = NATIVE_FREQUENCY_HZ
        self.description.metadata.update({
            "image_recording_stride": HISTORY_STRIDE, "action_snapshot_interval_steps": HISTORY_STRIDE,
            "step_unit": "dense_joint_target", "native_action_type": "joint",
            "control_timing": "One dense joint target advances 15 native physics ticks.",
            "control_constraints": "Use both arms and pass the original task instruction verbatim to actor_act.",
            "camera_history_stride": HISTORY_STRIDE,
        })
        if mixed_control:
            self.description.metadata["control_timing"] = (
                "One dense joint target advances 15 native physics ticks. "
                "A Cartesian or gripper primitive follows a joint trajectory and ends at an eight-target camera boundary."
            )
        self.remember_head()

    def remember_head(self):
        tick = self.observed_step
        head = CameraFrame(np.asarray(self.obs["observation"][CAMERAS[0]]["rgb"]))
        self.history.append(CapturedHistoryFrame(f"{self.episode_id}:head:{tick}",
            time.time(), tick / NATIVE_FREQUENCY_HZ, tick, head.freeze()))

    def read_state(self):
        state = super().read_state()
        robot = self.env.robot
        joints = robot.get_left_arm_jointState() + robot.get_right_arm_jointState()
        state.metadata[STATE_FIELD] = np.asarray(joints, dtype=np.float32).tolist()
        return state

    def capture(self, cameras=None, *, high_resolution=False):
        if self.observed_step != self.simulator.take_action_cnt:
            self.obs = self.observe()
            self.observed_step = self.simulator.take_action_cnt
        captured = super().capture(cameras, high_resolution=high_resolution)
        return replace(captured, history={CAMERAS[0]: tuple(self.history)} if CAMERAS[0] in captured.cameras else {})

    def step_pose(self, command):
        if not self.mixed_control:
            raise RequestRejected("The reference RMBench dense profile accepts native joint targets")
        self.validate_commands([command], "pose")
        state = self.read_state()
        if self.closed or state.ended:
            raise RequestRejected("Episode has ended")

        # Preflight both arms before the first physics tick. The native planner
        # supplies the joint-space waypoints of each Cartesian command.
        target = native_ee_action(command.arms).reshape(len(ARMS), 8)
        paths = []
        for index, arm in enumerate(ARMS):
            measured = state.arms[arm]
            if command.arms[arm].pose == measured.control_pose:
                path = np.asarray([measured.joints])
            else:
                plan = getattr(self.env.robot, f"{arm}_plan_path")(target[index, :7])
                status = str(plan.get("status", "missing"))
                path = np.asarray(plan.get("position", []), dtype=float)
                if (status != "Success" or path.ndim != 2 or not len(path)
                        or path.shape[1] != len(measured.joints) or not np.isfinite(path).all()):
                    return StepReceipt(accepted=False, state=state, execution_outcome="none",
                        diagnostics={"reason": "ik_failed", "arm": arm, "planner_status": status})
            paths.append(path)

        before = self.simulator.take_action_cnt
        trajectory_steps = ceil(max(map(len, paths)) / PHYSICS_SUBSTEPS)
        final_tick = ceil((before + trajectory_steps) / HISTORY_STRIDE) * HISTORY_STRIDE
        for index in range(final_tick - before):
            if self.simulator.done:
                break
            sample = (index + 1) * PHYSICS_SUBSTEPS - 1
            values = [value for arm, path in enumerate(paths)
                      for value in (*path[min(sample, len(path) - 1)], target[arm, 7])]
            self._advance_dense(values)
        executed = self.simulator.take_action_cnt - before
        return StepReceipt(accepted=True, state=self.read_state(), diagnostics={"executed_steps": executed})

    def step_native(self, command):
        values = native_command(command.values).values
        if self.closed or self.read_state().ended:
            raise RequestRejected("Episode has ended")
        self._advance_dense(values)
        return StepReceipt(accepted=True, state=self.read_state(), diagnostics={"executed_steps": 1})

    def _advance_dense(self, values):
        before = self.simulator.take_action_cnt
        self.simulator.take_action_dense(np.asarray(values), substeps=PHYSICS_SUBSTEPS)
        after = self.simulator.take_action_cnt
        if after != before + 1:
            raise RuntimeError("The RMBench controller did not account for one joint target")
        if after % HISTORY_STRIDE == 0 or self.simulator.done:
            self.obs = self.observe()
            self.observed_step = after
            if after % HISTORY_STRIDE == 0:
                self.remember_head()

    def close(self):
        if not self.closed:
            self.closed = True
            self.simulator.close()
