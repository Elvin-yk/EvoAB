"""OpenArm 2.0 single-arm reach episodes in Gazebo Classic."""


def reach_instruction(side: str) -> str:
    return (f"Measure the starting pose of the {side} wrist. Raise it by 4 cm along "
            "arm_origin +Z while keeping its orientation. Verify and report the measured "
            "lift. The target tolerances are 5 mm and 0.05 rad.")


def task_metadata(task: str, native_root: str | None = None) -> dict:
    from .deployment import TASKS
    return {"instruction": reach_instruction(TASKS[task])} if task in TASKS else {}


def get_integration_spec():
    from dataclasses import replace
    from evoab.integrations.composition import NoOptions
    from evoab.integrations.episode_tools import EPISODE_MODULE, SCENE_MODULE, SKILLS_MODULE
    from evoab.integrations.gripper import GRIPPER_MODULE
    from evoab.integrations.simulation import build_simulation_spec
    from evoab.tools.scene.provider import SceneTools

    spec = build_simulation_spec("openarm_gazebo", include_initial_state=False, accepts_task_instruction=True)
    scene_tools = ("view_env_state", "capture_scene", "get_status")
    scene = replace(SCENE_MODULE, tools=scene_tools, options=NoOptions,
                    description="Read wrist poses, joint angles and Gazebo scene images.",
                    policies={name: SCENE_MODULE.policies["view_env_state" if name == "get_status" else name]
                              for name in scene_tools},
                    create=lambda session, selection: SceneTools(session.episode, names=selection.tools or scene_tools))
    spec.tool_modules = (SKILLS_MODULE, scene, GRIPPER_MODULE, EPISODE_MODULE)
    spec.deployment_module = "evoab.integrations.openarm_gazebo.deployment"
    return spec
