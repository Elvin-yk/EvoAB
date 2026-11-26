"""ARX ONE embodiment using the shared gripper tools.

The Body runtime lives in body/ and runs as its own process. Model
environments import that package through this module, so keep it free of agent
dependencies at import time.
"""
from dataclasses import replace


def validate_cli(args):
    from evoab.integrations.gripper import validate_gripper_cli
    if "hybrid" in args.tool_modules or args.action_model_url:
        raise ValueError("ARX ONE supports Direct control; no actor binding is registered")
    validate_gripper_cli(args)


def get_integration_spec():
    from evoab.integrations.gripper import build_gripper_spec
    from . import prompt  # noqa: F401 - register the prompt builder.
    from .session import create_session

    spec = build_gripper_spec(name="arx_one", create_session=create_session, validate_cli=validate_cli)
    # A failed physical action stops the queued plan until a fresh capture (replanning mode), and
    # a physical action is never replayed by retrying the episode.
    return replace(spec, execution_mode="replanning", allow_automatic_retry=False)
