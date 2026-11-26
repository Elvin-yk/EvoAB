"""RoboDojo entrypoints and environment-specific task requirements."""

from evoab.integrations.robodojo.tasks import task_metadata  # noqa: F401 - Studio task catalog


def create_session(context):
    from evoab.integrations.gripper import create_session as build
    from evoab.integrations.robodojo.prompt import load_task_requirements

    from evoab.integrations.robodojo.action_binding import create_binding

    loader = load_task_requirements if getattr(context.args, "task_supplements", False) else None
    return build(context, requirements_loader=loader, binding_factory=create_binding)


def get_integration_spec():
    from evoab.integrations.gripper import HYBRID_TOOL_MODULES, build_gripper_spec

    spec = build_gripper_spec(name="robodojo", create_session=create_session,
                              tool_modules=HYBRID_TOOL_MODULES)
    spec.task_supplements = True
    spec.deployment_module = "evoab.integrations.robodojo.deployment"
    return spec


def create_observation_profile(episode, session_dir):
    from evoab.context.prompt import get_prompts_dir
    from evoab.observation.episode import create_observation_profile as build

    return build(episode, session_dir,
                 extra_guides=(get_prompts_dir() / "observer/robodojo_main.md",))
