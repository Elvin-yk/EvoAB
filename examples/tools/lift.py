"""Example user tools.

Select this file in a recipe:
    capabilities: [skills, scene, gripper, episode, {id: user_tools, options: {file: examples/tools/lift.py}}]
or for one run:
    --set 'roles.main.capabilities=[skills, scene, gripper, episode, {id: user_tools, options: {file: examples/tools/lift.py}}]'
"""
from evoab import tool
from evoab.tools.gripper.pose import quaternion_to_rpy


@tool(readonly=True)
def grasp_site_poses(robot) -> dict:
    """Report every arm's grasp-site position (world metres) and orientation as [roll, pitch, yaw] radians."""
    state = robot.view_env_state()["state"]
    return {name: {"position": arm["control_pose"]["position"],
                   "rpy": [round(float(v), 5) for v in quaternion_to_rpy(arm["control_pose"]["quaternion"])]}
            for name, arm in state["arms"].items()}


@tool
def lift_by(robot, arm: str, height_m: float = 0.05) -> str:
    """Raise one arm's grasp site straight up by height_m metres, keeping its orientation."""
    pose = grasp_site_poses(robot)[arm]
    x, y, z = pose["position"]
    robot.move(targets=[{"arm": arm, "pose": [x, y, z + height_m, *pose["rpy"]]}])
    return f"{arm} grasp site moved from z={z:.3f} to z={grasp_site_poses(robot)[arm]['position'][2]:.3f}"
