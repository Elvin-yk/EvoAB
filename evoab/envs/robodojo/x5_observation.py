"""Map a single calibrated X5 snapshot to the public XPolicyLab observation."""

from hashlib import sha256
from io import BytesIO

import numpy as np
from PIL import Image

from evoab.envs.protocol.contracts import JointCommand, JointTarget

from .actor_profiles import X5_CAMERAS
from .environment.camera_projection import PIXEL_COORDINATES
from .environment.kinematics import native_pose


def x5_observation(snapshot, artifact_bytes):
    state = snapshot["state"]
    robot = {}
    for side in ("left", "right"):
        arm = state["arms"][side]
        pose = arm["control_pose"]
        joints = np.asarray(arm["joints"], dtype=float)
        gripper = float(arm["metadata"]["gripper_applied_command"])
        if joints.shape != (6,) or not np.isfinite(joints).all() or not 0 <= gripper <= 1:
            raise ValueError(f"Invalid native X5 joint state for {side}")
        robot[f"{side}_arm_joint_state"] = joints
        robot[f"{side}_ee_joint_state"] = np.array([gripper])
        robot[f"{side}_ee_pose"] = native_pose(pose["position"], pose["quaternion"])

    vision = {}
    for name in X5_CAMERAS:
        frame = snapshot["cameras"][name]
        k = np.asarray(frame["intrinsic_K"], dtype=float)
        optical = np.asarray(frame["T_world_from_optical"], dtype=float)
        if (k.shape != (3, 3) or optical.shape != (4, 4) or not np.isfinite(k).all()
                or not np.isfinite(optical).all() or k[0, 0] <= 0 or k[1, 1] <= 0):
            raise ValueError(f"Missing same-frame camera calibration for {name}")
        if (frame.get("calibration") or {}).get("pixel_coordinates") == PIXEL_COORDINATES:
            # Native CameraManager centers the image at (width/2, height/2).
            k = k.copy()
            k[:2, 2] += 0.5
        ref = frame["rgb"]
        data = artifact_bytes(ref["artifact_id"])
        if sha256(data).hexdigest() != ref["sha256"]:
            raise ValueError(f"Snapshot RGB checksum mismatch for {name}")
        with Image.open(BytesIO(data)) as image:
            if image.mode != "RGB" or [image.height, image.width] != frame["image_size"]:
                raise ValueError(f"Snapshot RGB format or calibration resolution mismatch for {name}")
            rgb = np.array(image)
        vision[f"cam_{name}"] = dict(color=rgb, intrinsic_matrix=k,
                                     extrinsic_matrix=optical @ np.diag([1., -1., -1., 1.]))
    return dict(instruction=state["metadata"]["instruction"], state=robot, vision=vision,
                additional_info={"frequency": 25}, data_format_version="v1.0")


def x5_joint_command(actions):
    if not isinstance(actions, list) or len(actions) != 1:
        raise ValueError("The single-step X5 policy must return exactly one action.")
    action = actions[0]
    expected = {f"{side}_{field}_joint_state" for side in ("left", "right") for field in ("arm", "ee")}
    if not isinstance(action, dict) or action.keys() != expected:
        raise ValueError("The policy must command both complete X5 arms.")
    targets = {}
    for side in ("left", "right"):
        q = np.asarray(action[f"{side}_arm_joint_state"])
        g = np.asarray(action[f"{side}_ee_joint_state"])
        if q.shape != (6,) or g.shape != (1,) or q.dtype.kind not in "fiu" or g.dtype.kind not in "fiu":
            raise ValueError(f"Invalid X5 joint action for {side}")
        targets[side] = JointTarget(joints=q.tolist(), gripper=g.item())
    return JointCommand(arms=targets)
