"""World-frame pose control for the LIBERO Panda gripper."""
import numpy as np
from scipy.spatial.transform import Rotation

from evoab.envs.protocol.contracts import ArmState, Pose, RequestRejected


class LiberoOSC:
    constraint = "Full world-frame pose control at the midpoint of the finger pads."

    def __init__(self, env):
        self.robot, = env.robots
        self.model, self.data = env.sim.model._model, env.sim.data._data
        self.controller, self.gripper = self.robot.controller, self.robot.gripper
        if type(self.gripper).__name__ != "PandaGripper" or self.controller.name != "OSC_POSE":
            raise ValueError("LIBERO requires PandaGripper with OSC_POSE")
        if getattr(self.controller, "input_ref_frame", "world") != "world":
            raise ValueError("OSC controller must accept world-frame deltas")
        self.site = int(self.robot.eef_site_id)
        self.pads = [[self.model.geom(n).id for n in self.gripper.important_geoms[pad]]
                     for pad in ("left_fingerpad", "right_fingerpad")]

    def _pose(self):
        centre = np.mean([self.data.geom_xpos[ids].mean(axis=0) for ids in self.pads], axis=0)
        orientation = Rotation.from_matrix(self.data.site_xmat[self.site].reshape(3, 3))
        return centre, orientation

    def read(self, commands):
        centre, orientation = self._pose()
        indices = self.robot._ref_gripper_joint_pos_indexes
        if isinstance(indices, dict):
            indices = indices[self.robot.arms[0]]
        width = np.abs(self.data.qpos[indices]).sum()
        pose = Pose(position=centre.tolist(), quaternion=orientation.as_quat()[[3, 0, 1, 2]].tolist())
        return {"arm": ArmState(control_pose=pose, gripper=float(np.clip(width / .08, 0, 1)),
                                gripper_command=commands["arm"])}

    def encode(self, targets):
        target = targets["arm"]
        centre, current = self._pose()
        desired = Rotation.from_quat(np.asarray(target.pose.quaternion)[[1, 2, 3, 0]])
        site = self.data.site_xpos[self.site]
        offset = current.inv().apply(centre - site)
        delta = np.r_[np.asarray(target.pose.position) - desired.apply(offset) - site,
                      (desired * current.inv()).as_rotvec()]
        low, high = np.asarray(self.controller.output_min), np.asarray(self.controller.output_max)
        ilow, ihigh = np.asarray(self.controller.input_min), np.asarray(self.controller.input_max)
        if np.any(high <= low):
            raise RequestRejected("OSC controller has invalid scaling")
        arm = np.clip(ilow + (delta - low) * (ihigh - ilow) / (high - low), ilow, ihigh)
        opening = (1. + float(np.asarray(self.gripper.current_action).ravel()[0])) / 2
        error = opening - target.gripper
        grip = 0. if abs(error) < float(self.gripper.speed) / 4 + 1e-8 else np.sign(error)
        return [*arm.tolist(), float(grip)]
