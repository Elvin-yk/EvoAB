"""The ``action`` way of acting inside an agent session.

When a role acts through ``action`` together with ``tools`` or ``code``, the
action chunk is one more call in the agent's session: the same representation,
step limits and decoding as a stateless action role, executed one row per
control step through the environment's controller. The result reports what ran
and attaches the camera images measured afterwards.
"""

from __future__ import annotations

from pathlib import Path

from evoab.direct.actions import OutputError
from evoab.execution.scheduler import ToolExecutionPolicy
from evoab.tools.toolkit import ActionOutcome, ImageAttachment, ToolOutput, ToolProvider, ToolSpec, bind_tools


class ActionTools(ToolProvider):
    provider_id = "action"

    def __init__(self, episode, config, *, integration: str, output_dir: Path):
        from evoab.direct.runner import DirectPolicy
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

    def policies(self) -> dict[str, ToolExecutionPolicy]:
        return {self.name: ToolExecutionPolicy(resource_key="episode", return_mode="background",
                                               concurrency="exclusive", mutates_world=True,
                                               replan_observation=True)}

    def guidance(self) -> str:
        return self.policy.guidance()

    def get_tools(self):
        return bind_tools([self.spec()], provider_id=self.provider_id, handlers={self.name: self._handle})

    def _handle(self, **arguments) -> ToolOutput:
        try:
            record = self.policy.execute(arguments)
        except OutputError as error:
            return ToolOutput(success=False, error=f"chunk rejected, nothing moved: {error}",
                              extra={"no_execution": True},
                              action=ActionOutcome(complete=False, executed="none"))
        executed = len(record["executed"])
        failure = record.get("execution_error")
        frame = self.policy.frames[-1]
        images = [ImageAttachment(data, "image/jpeg", camera) for camera, data in self.policy.latest_images().items()]
        return ToolOutput(
            success=failure is None, error=failure,
            extra={"requested_rows": len(record["targets"]), "executed_rows": executed,
                   "safety_clamped": record["safety_clamped"], "step": frame["step"],
                   "measured": frame["proprio"], "image_cameras": [image.name for image in images]},
            images=images,
            action=ActionOutcome(complete=failure is None and executed == len(record["targets"]),
                                 executed="some" if executed else "none"))
