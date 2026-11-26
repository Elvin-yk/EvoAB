"""Example user tools.

Select this file in a recipe:
    capabilities: [skills, scene, gripper, episode, {id: user_tools, options: {file: examples/tools/lift.py}}]
or for one run:
    --set 'roles.main.capabilities=[skills, scene, gripper, episode, {id: user_tools, options: {file: examples/tools/lift.py}}]'
"""
from evoab import tool


@tool(readonly=True)
def grasp_site_poses(robot) -> dict:
    """Report every arm's grasp-site pose [x, y, z, roll, pitch, yaw] in world metres and radians."""
    state = robot.view_env_state()["state"]
    return {name: arm["control_pose"] for name, arm in state["arms"].items()}


@tool
def lift_by(robot, arm: str, height_m: float = 0.05) -> str:
    """Raise one arm's grasp site straight up by height_m metres, keeping its orientation."""
    pose = grasp_site_poses(robot)[arm]
    target = [pose[0], pose[1], pose[2] + height_m, *pose[3:]]
    robot.move(targets=[{"arm": arm, "pose": target}])
    return f"{arm} grasp site moved from z={pose[2]:.3f} to z={grasp_site_poses(robot)[arm][2]:.3f}"
