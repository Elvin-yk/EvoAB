"""The ``action`` way of acting inside an agent session.

When a role acts through ``action`` together with ``tools`` or ``code``, the
action chunk is one more call in the agent's session: the same representation,
step limits and decoding as a stateless action role, executed one row per
control step through the environment's controller. The result reports what ran
and attaches the camera images measured afterwards.

The recipe compiler adds :data:`ACTION_MODULE` to every such role, so the chunk
tool is assembled, scheduled, shown and prompted like any other capability.
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

from evoab.agent.act.action.actions import REPRESENTATIONS, OutputError
from evoab.envs.composition import ModuleSelection, ToolModule
from evoab.runtime.scheduler import ToolExecutionPolicy
from evoab.tools.toolkit import ActionOutcome, ToolOutput, ToolProvider, ToolSpec, bind_tools


class ActionTools(ToolProvider):
    provider_id = "action"

    def __init__(self, episode, config, *, integration: str, output_dir: Path):
        from evoab.agent.act.action.runner import DirectPolicy
        self.episode = episode
        self.policy = DirectPolicy(episode, None, integration=integration, instruction="",
                                   output_dir=output_dir, config=config)

    @property
    def name(self) -> str:
        return self.policy.representation.tool

    def spec(self) -> ToolSpec:
        function = self.policy.representation.schema(self.policy.horizon, self.policy.arm_names)["function"]
        return ToolSpec(function["name"], function["description"] + (
            " Rows run one per control step from the current measured state; the whole chunk is checked "
            "before anything moves. Returns the executed rows, the measured state and current camera images."),
            function["parameters"])

    @cached_property
    def prompt_contributions(self):
        from evoab.agent.context.prompt import PromptContribution
        return (PromptContribution(system=self.policy.guidance(), source="act:action"),)

    def get_tools(self):
        return bind_tools([self.spec()], provider_id=self.provider_id, handlers={self.name: self._handle})

    def _handle(self, **arguments) -> ToolOutput:
        try:
            record = self.policy.execute(arguments)
        except OutputError as error:
            return ToolOutput(success=False, error=f"chunk rejected, nothing moved: {error}",
                              extra={"no_execution": True},
                              action=ActionOutcome(complete=False, executed="none"))
        from evoab.tools.scene.perception import snapshot_images
        executed = len(record["executed"])
        failure = record.get("execution_error")
        frame = self.policy.frames[-1]
        images = snapshot_images(self.episode.env, self.episode.latest_snapshot, mirror=self.episode.artifact_mirror)
        return ToolOutput(
            success=failure is None, error=failure,
            extra={"requested_rows": len(record["targets"]), "executed_rows": executed,
                   "safety_clamped": record["safety_clamped"], "step": frame["step"],
                   "measured": frame["proprio"], "image_cameras": [image.name for image in images]},
            images=images,
            action=ActionOutcome(complete=failure is None and executed == len(record["targets"]),
                                 executed="some" if executed else "none"))


def configure_action(session, selection: ModuleSelection) -> ActionTools:
    # The compiler checks that every role emitting chunks in a session shares one action setting.
    config = next(role.direct for role in session.args.compiled_recipe.roles.values() if role.emits_chunks)
    return ActionTools(session.episode, config, integration=session.args.integration,
                       output_dir=session.context.session_dir)


ACTION_MODULE = ToolModule(
    "action", configure_action,
    {representation.tool: ToolExecutionPolicy(resource_key="episode", return_mode="background",
                                              concurrency="exclusive", mutates_world=True, replan_observation=True)
     for representation in REPRESENTATIONS.values()},
    label="Action chunks", description="Emit an action chunk the controller executes row by row (act: action).")
