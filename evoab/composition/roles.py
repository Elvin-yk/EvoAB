"""Settings for running a non-entry role inside the entry role's session."""

from __future__ import annotations

import copy
from typing import Any

from evoab.composition.compiler import CompiledRole


def role_namespace(args: Any, role: CompiledRole) -> Any:
    """A copy of the run namespace with one role's brain and ways of acting applied.

    A role without its own model preset uses the run's model.
    """
    brain = role.role.brain
    namespace = copy.copy(args)
    namespace.agent_backend = "native"
    namespace.model = brain.model or brain.model_preset or args.model
    if brain.reasoning_effort is not None:
        namespace.reasoning_effort = brain.reasoning_effort
    namespace.act = role.act
    namespace.direct_config = role.direct
    namespace.max_turns = role.role.max_turns
    namespace.skill_writing = role.skill_writing
    namespace.tool_style = role.role.style
    return namespace


def validate_roles(args: Any) -> None:
    """Check every non-entry role offline, as the CLI checks the entry role."""
    from evoab.agents.registry import get_agent_backend

    compiled = args.compiled_recipe
    for name, role in compiled.roles.items():
        if name == compiled.recipe.loop.entry:
            continue
        namespace = role_namespace(args, role)
        get_agent_backend("native").validate_config(namespace, set())
