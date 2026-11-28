"""Expose recipe roles as tools: ``delegate_<role>(instruction)``.

The caller hands a sub-goal to another role. That role runs with its own brain,
ways of acting and capabilities against the same episode and returns a report when it
stops. The tool owns only the schema and the report format; running the role
belongs to the session, which supplies ``run_role``.

Placement: a role that acts only through action chunks (stateless) bypasses the
executor, so its delegation occupies the exclusive ``episode`` resource for the
whole sub-run. Agent roles call tools through the shared executor, so
their delegation must not hold any exclusive queue those calls would wait on;
it runs with parallel placement (and recipes cannot combine it with
``global-serial`` scheduling, which serializes every call).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from evoab.runtime.scheduler import ToolExecutionPolicy
from evoab.tools.toolkit import ActionOutcome, ToolOutput, ToolProvider, ToolSpec, bind_tools, schema


@dataclass(frozen=True)
class DelegateTarget:
    name: str
    description: str
    act: tuple[str, ...]
    agent: str


def tool_name(role: str) -> str:
    return f"delegate_{role}"


def tool_policies(targets: Mapping[str, DelegateTarget]) -> dict[str, ToolExecutionPolicy]:
    return {
        tool_name(name): (ToolExecutionPolicy(resource_key="episode", concurrency="exclusive", mutates_world=True)
                          if target.act == ("action",) else
                          ToolExecutionPolicy(resource_key="delegation", concurrency="parallel"))
        for name, target in targets.items()
    }


def tool_specs(targets: Mapping[str, DelegateTarget]) -> list[ToolSpec]:
    specs = []
    for name, target in targets.items():
        ways = {"tools": "calls its own robot tools", "code": "writes and runs programs against the robot API",
                "action": "emits action chunks"}
        how = ("emits action chunks directly from camera frames" if target.act == ("action",)
               else " and ".join(ways[way] for way in target.act))
        description = (f"Hand a sub-goal to the {name!r} role ({target.agent}), which {how} against the "
                       f"current episode and returns a report when it stops. "
                       + (target.description.strip() + " " if target.description.strip() else "")
                       + "Give a self-contained instruction. Observe the scene afterwards; the report "
                         "does not prove that the sub-goal succeeded.")
        specs.append(ToolSpec(tool_name(name), description,
                              schema({"instruction": {"type": "string", "minLength": 1}}, ("instruction",))))
    return specs


class DelegationTools(ToolProvider):
    provider_id = "delegation"

    def __init__(self, targets: Mapping[str, DelegateTarget], run_role: Callable[[str, str], dict]):
        self.targets = dict(targets)
        self.run_role = run_role

    def get_tools(self):
        handlers = {tool_name(name): self._handler(name) for name in self.targets}
        return bind_tools(tool_specs(self.targets), provider_id=self.provider_id, handlers=handlers)

    def _handler(self, role: str):
        def delegate(instruction: str) -> ToolOutput:
            report = self.run_role(role, instruction)
            action = None
            if self.targets[role].act == ("action",):
                # The sub-run stepped the environment itself; report what it executed for replanning.
                steps = report.get("steps")
                executed = "unknown" if steps is None else ("some" if steps > 0 else "none")
                action = ActionOutcome(complete=bool(report.get("success")) and not report.get("error"),
                                       executed=executed)
            return ToolOutput(success=not report.get("error"), error=report.get("error"),
                              extra={"role": role, "report": report}, action=action)
        return delegate
