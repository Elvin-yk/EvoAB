"""Turn the Studio agent cards into role definitions for a recipe override."""
from graphlib import CycleError, TopologicalSorter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evoab.recipe.schema import Brain, Capability, PromptSpec, Role
from evoab.envs import load_integration
from evoab.tools.selection import TOOL_GROUPS
from evoab.apps.workbench import benchmarks

DIRECT_TOOL = "actor_act"


class AgentCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["planner", "actor"]
    model_preset: str | None = Field(default=None, min_length=1)
    instructions: str = Field(default="", max_length=8192)
    tools: list[str]
    delegate_to: list[int] | None = None  # One-based card numbers; absent on legacy cards.


def team_catalog(integration: str) -> dict[str, list[str]]:
    if integration not in {info.integration for info in benchmarks.REGISTRY}:
        raise ValueError("Unknown benchmark")
    spec = load_integration(integration).get_integration_spec()
    groups = {module.name: [name for name in module.tools if name != "write_skill"]
              for module in spec.tool_modules if module.tools and module.name != "hybrid"}
    groups["jobs"] = list(TOOL_GROUPS["@jobs"])
    groups["action_policy"] = [DIRECT_TOOL] if spec.direct_policy_binding else []
    return groups


def team_roles(integration: str, cards: list[dict]) -> dict:
    if not isinstance(cards, list) or not cards:
        raise ValueError("Select at least one agent")
    agents = [AgentCard.model_validate(card) for card in cards]
    groups = team_catalog(integration)
    allowed = {name for tools in groups.values() for name in tools}
    names = ["main", *(f"{card.role}_{index}" for index, card in enumerate(agents[1:], 2))]
    roles = {}
    delegation = TopologicalSorter()
    for index, card in enumerate(agents):
        selected = set(card.tools)
        if unknown := selected - allowed:
            raise ValueError(f"Unavailable tools: {sorted(unknown)}")
        if card.role == "planner" and (motion := selected.intersection(groups.get("gripper", ()))):
            raise ValueError(f"Planner cannot use execution tools: {sorted(motion)}")
        capabilities = []
        for group, tools in groups.items():
            subset = [name for name in tools if name in selected]
            if subset and group == "action_policy":
                binding = load_integration(integration).get_integration_spec().direct_policy_binding
                capabilities.append(Capability(id="hybrid", tools=subset, options={"binding": binding}))
            elif subset and group != "jobs":
                capabilities.append(Capability(id=group, tools=subset))
        numbers = card.delegate_to
        if numbers is None:
            numbers = list(range(2, len(agents) + 1)) if index == 0 else []
        if any(n < 2 or n > len(agents) or n == index + 1 for n in numbers):
            raise ValueError(f"Invalid delegation target for Agent {index + 1}")
        targets = [names[n - 1] for n in dict.fromkeys(numbers)]
        delegation.add(names[index], *targets)
        if targets:
            capabilities.append(Capability(id="delegate", options={"roles": targets}))
        duty = ("Plan the task and verify results." if card.role == "planner"
                else "Execute the assigned task using the available tools and report the result.")
        instructions = "\n".join(filter(None, (duty, card.instructions.strip())))
        role = Role(description=instructions, prompt=PromptSpec(append=instructions),
                    job_tools=[name for name in groups["jobs"] if name in selected],
                    capabilities=capabilities)
        if card.model_preset:
            role.brain = Brain(model_preset=card.model_preset)
        roles[names[index]] = role
    try:
        delegation.prepare()
    except CycleError as exc:
        raise ValueError("Agent delegation must not form a cycle") from exc
    return {name: role.model_dump(mode="json", exclude_unset=True) for name, role in roles.items()}
