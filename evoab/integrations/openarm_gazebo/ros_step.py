"""One measured native operation under ROS Humble's Python 3.10 interpreter."""
import argparse
import base64
from collections import deque
import json
import math
import time
import zlib

import rclpy
from action_msgs.msg import GoalStatus
from control_msgs.action import FollowJointTrajectory
from rclpy.action import ActionClient
from rclpy.duration import Duration
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import Image, JointState
from tf2_ros import Buffer, TransformException, TransformListener
from trajectory_msgs.msg import JointTrajectoryPoint

from robot_runtime.openarm_control.trajectory import DEFAULT_ACTION_NAME, retime_joint_positions
from robot_runtime.openarm_ik import ORIGIN_FRAME, arm, step

FINGER_SPEED_RAD_S = 1.0


def sample(side, timeout, *, with_rgb=False):
    """Pair a fresh Gazebo joint message with the URDF wrist TF at its timestamp."""
    model = arm(side)
    tip = f"openarm_{side}_ee_base_link"
    node = rclpy.create_node("evoab_openarm_feedback")
    try:
        messages = deque(maxlen=256)
        node.create_subscription(JointState, "/joint_states", messages.append, qos_profile_sensor_data)
        controller = ActionClient(node, FollowJointTrajectory, DEFAULT_ACTION_NAME)
        images = deque(maxlen=1)
        if with_rgb:
            node.create_subscription(Image, "/evoab/scene/image_raw", images.append, qos_profile_sensor_data)
        buffer = Buffer()
        listener = TransformListener(buffer, node)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
            lookup_time = Time()
            if with_rgb:
                if not messages or not images:
                    continue
                image = images[-1]
                image_ns = Time.from_msg(image.header.stamp).nanoseconds
                # Rendering lags the joint stream. Look up historical TF at the
                # nearest encoder sample, bounded by samples on both sides of the image.
                def delta(message):
                    return Time.from_msg(message.header.stamp).nanoseconds - image_ns
                if delta(messages[0]) > 0 or delta(messages[-1]) < 0:
                    continue
                message = min(messages, key=lambda item: abs(delta(item)))
                if abs(delta(message)) > 50_000_000:
                    continue
                lookup_time = Time.from_msg(message.header.stamp)
            try:
                tf = buffer.lookup_transform(ORIGIN_FRAME, tip, lookup_time)
            except TransformException:
                continue
            if not with_rgb:
                message = next((m for m in reversed(messages) if m.header.stamp == tf.header.stamp), None)
                if message is None:
                    continue
            values = dict(zip(message.name, message.position))
            p, q = tf.transform.translation, tf.transform.rotation
            result = {"joints": model.order_joint_positions(values), "finger_rad": values[model.gripper_joint_name],
                      "controller_ready": controller.server_is_ready(),
                      "pose": [p.x, p.y, p.z, q.w, q.x, q.y, q.z], "sim_time_ns": Time.from_msg(tf.header.stamp).nanoseconds}
            if with_rgb:
                if image.encoding != "rgb8":
                    raise RuntimeError(f"Unexpected scene camera encoding: {image.encoding}")
                result["rgb"] = {"height": image.height, "width": image.width, "row_bytes": image.step,
                                 "sim_time_ns": image_ns,
                                 "data": base64.b64encode(zlib.compress(image.data, 1)).decode("ascii")}
            return result
        raise RuntimeError(f"Timed out pairing Gazebo feedback for {ORIGIN_FRAME} -> {tip} (RGB={with_rgb})")
    finally:
        node.destroy_node()


def send_target(side, measured, joints, opening):
    """Send one combined target to the arm and its actuated pinch finger."""
    model = arm(side)
    finger = -opening * math.pi / 4
    arm_time = retime_joint_positions([measured["joints"], joints])[-1].time_from_start
    duration = max(arm_time, abs(finger - measured["finger_rad"]) / FINGER_SPEED_RAD_S)
    goal = FollowJointTrajectory.Goal()
    goal.trajectory.joint_names = [*model.joint_names, model.gripper_joint_name]
    goal.trajectory.points = [JointTrajectoryPoint(positions=[*joints, finger],
                                                   time_from_start=Duration(seconds=duration).to_msg())]
    node = rclpy.create_node("evoab_openarm_command")
    try:
        client = ActionClient(node, FollowJointTrajectory, DEFAULT_ACTION_NAME)
        if not client.wait_for_server(timeout_sec=15):
            raise RuntimeError("Gazebo trajectory controller unavailable")
        send = client.send_goal_async(goal)
        rclpy.spin_until_future_complete(node, send)
        handle = send.result()
        if not handle.accepted:
            return {"sent": False, "success": False, "controller_status": GoalStatus.STATUS_UNKNOWN,
                    "controller_error_code": None, "error": "Trajectory rejected"}
        done = handle.get_result_async()
        rclpy.spin_until_future_complete(node, done)
        reply = done.result()
        return {"sent": True, "success": reply.status == GoalStatus.STATUS_SUCCEEDED and reply.result.error_code == 0,
                "controller_status": reply.status, "controller_error_code": reply.result.error_code,
                "error": reply.result.error_string}
    finally:
        node.destroy_node()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("side", choices=("left", "right"))
    parser.add_argument("operation", choices=("read", "capture", "home", "step", "joints"))
    parser.add_argument("target", nargs="*", type=float)
    args = parser.parse_args()
    rclpy.init(args=[])
    try:
        measured = sample(args.side, 60 if args.operation == "home" else 8, with_rgb=args.operation == "capture")
        result = {"sample": measured}
        if args.operation in ("home", "step", "joints"):
            joints = measured["joints"]
            if args.operation == "home":
                goal, opening = arm(args.side).home, 1.0
            else:
                goal, opening = args.target[:-1], args.target[-1]
                if args.operation == "step":
                    goal = step(args.side, joints, goal)
            receipt = send_target(args.side, measured, goal, opening)
            # Subscribe anew so the result only contains encoder samples from after acknowledgement.
            after = sample(args.side, 8)
            if after["sim_time_ns"] <= measured["sim_time_ns"]:
                raise RuntimeError("Gazebo clock did not advance after the trajectory")
            if args.operation == "home" and (not receipt["success"]
                    or max(abs(q - h) for q, h in zip(after["joints"], goal)) > 0.04):
                raise RuntimeError("Gazebo did not reach the initial elbow-bent configuration")
            result = {**receipt, "sample": after, "joint_target": goal}
        print(json.dumps(result, allow_nan=False), flush=True)
    finally:
        rclpy.shutdown()


if __name__ == "__main__":
    main()
