"""R1Pro mobile manipulation through the shared Hybrid tool family."""
from evoab.integrations.base import IntegrationSpec
from evoab.integrations.hybrid import HybridOptions, add_hybrid_cli_args, validate_hybrid_cli


class BehaviorOptions(HybridOptions):
    task: str | None = None
    run_id: str | None = None
    seed: int | None = None


def add_cli_args(parser):
    group = parser.add_argument_group("BEHAVIOR episode identity")
    group.add_argument("--task", help="Expected prepared BEHAVIOR task")
    group.add_argument("--run-id", help="Expected prepared episode identity")
    group.add_argument("--seed", type=int, help="Expected native reset seed")
    add_hybrid_cli_args(parser)


def validate_cli(args):
    if "hybrid" not in args.tool_modules:
        raise ValueError("BEHAVIOR R1Pro requires the Hybrid tool profile")
    validate_hybrid_cli(args)
    if args.action_model_binding not in (None, "behavior_r1pro_joint"):
        raise ValueError("BEHAVIOR R1Pro requires behavior_r1pro_joint")
    if args.sam3_url:
        raise ValueError("This BEHAVIOR profile exposes RGB without geometric localization")


def get_integration_spec():
    from evoab.integrations.episode_tools import EPISODE_MODULE, SCENE_MODULE, SKILLS_MODULE
    from evoab.integrations.hybrid import HYBRID_MODULE
    from . import prompt  # noqa: F401 - register the integration prompt.
    from .session import create_session
    return IntegrationSpec(name="behavior", create_session=create_session, config_model=BehaviorOptions,
        add_cli_args=add_cli_args, validate_cli=validate_cli,
        tool_modules=(SKILLS_MODULE, SCENE_MODULE, HYBRID_MODULE, EPISODE_MODULE),
        default_recipe="agent_policy", deployment_module="evoab.integrations.behavior.deployment",
        allow_automatic_retry=False)
