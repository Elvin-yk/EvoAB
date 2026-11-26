"""Compose Direct gripper tools with independent scene and hybrid providers."""
from __future__ import annotations

import argparse
from typing import Any
from urllib.parse import urlsplit

from evoab.integrations.base import IntegrationSpec
from evoab.integrations.composition import ToolModule
from evoab.integrations.episode_tools import EPISODE_MODULE, SCENE_MODULE, SKILLS_MODULE
from evoab.integrations.hybrid import (
    HYBRID_MODULE, HybridOptions, add_hybrid_cli_args, validate_hybrid_cli,
)
from evoab.integrations.prepared_episode import PreparedEpisodeSession
from evoab.tools.gripper.provider import GripperTools
from evoab.tools.gripper import provider as gripper
from evoab.tools.clean import RESULT_NOTES


class GripperOptions(HybridOptions):
    run_id: str | None = None
    task: str | None = None


def add_gripper_cli_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("Gripper episode")
    group.add_argument("--run-id", help="Optional expected episode identity")
    group.add_argument("--task", help="Optional expected native task identity")
    add_hybrid_cli_args(parser)


def validate_gripper_cli(args: Any) -> None:
    validate_hybrid_cli(args)
    if args.sam3_url:
        address = urlsplit(args.sam3_url)
        if address.scheme not in {"http", "https"} or not address.hostname or address.query or address.fragment:
            raise ValueError("Perception endpoint must be an absolute HTTP(S) base URL")


GRIPPER_MODULE = ToolModule("gripper",
    lambda session, selection: GripperTools(session.episode, names=selection.tools or gripper.NAMES),
    gripper.tool_policies(), tools=gripper.NAMES, compact_prompt_contributions=RESULT_NOTES,
    label="Gripper motion", description="Move the registered grasp site, grasp, release and hold.")

#: Modules a Direct gripper environment can assemble; recipes select among them.
DIRECT_TOOL_MODULES = (SKILLS_MODULE, SCENE_MODULE, GRIPPER_MODULE, EPISODE_MODULE)
#: Gripper environments with an actor binding also offer Hybrid collaboration.
HYBRID_TOOL_MODULES = (SKILLS_MODULE, SCENE_MODULE, GRIPPER_MODULE, HYBRID_MODULE, EPISODE_MODULE)


def create_session(context, *, requirements_loader=None, binding_factory=None):
    return PreparedEpisodeSession(context, binding_factory=binding_factory,
                                  requirements_loader=requirements_loader)


def build_gripper_spec(*, name, create_session, tool_modules=DIRECT_TOOL_MODULES,
                       add_cli_args=add_gripper_cli_args,
                       validate_cli=validate_gripper_cli, config_model=GripperOptions):
    return IntegrationSpec(name=name, create_session=create_session,
        add_cli_args=add_cli_args, tool_modules=tool_modules,
        debug_modules=(HYBRID_MODULE,), validate_cli=validate_cli, config_model=config_model)
