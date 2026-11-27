"""Bind the CALVIN observation and relative action profile to shared Hybrid tools."""

import numpy as np
from PIL import Image

from evoab.services.actor.calvin_profile import CAMERAS, actor_profile
from evoab.services.actor.contracts import ActorObservation, ActorProfile, NumericChunk, validate_prediction
from evoab.services.actor.images import encode_image
from evoab.services.environment.contracts import StepRequest
from evoab.tools.hybrid.contracts import HybridCapabilities, Proposal, StepOutcome
from evoab.tools.hybrid.decisions import validate_decision
from evoab.tools.toolkit import schema

from .relative_action import BINDING_NAME, PROPRIOCEPTION, relative_command


class CalvinBinding:
    name = BINDING_NAME
    reviewer_supported = False
    reviewer_prompt = None
    validate_decision = staticmethod(validate_decision)

    def __init__(self, description, actor_info):
        environment = description.environment
        profile = actor_info.profile
        if (description.integration != "calvin" or "native" not in description.capabilities
                or environment.action_binding != self.name or [arm.name for arm in environment.arms] != ["arm"]
                or {camera.name for camera in environment.cameras} != set(CAMERAS)):
            raise ValueError("CALVIN Hybrid requires a prepared CALVIN episode with native relative actions")
        if (not isinstance(profile, ActorProfile) or profile != actor_profile(profile.action_horizon)
                or environment.control_frequency_hz != profile.control_frequency_hz):
            raise ValueError("The actor must declare the CALVIN relative control profile")
        self.profile = profile
        self.action_horizon = profile.action_horizon
        self.capabilities = HybridCapabilities(
            groups=("arm",), constraint_schema=schema({}),
            default_actions=self.action_horizon, max_actions=self.action_horizon,
        )

    def validate_scope(self, active_groups, constraints):
        if active_groups != ["arm"] or constraints != {}:
            raise ValueError("CALVIN requires active_groups=['arm'] and empty constraints")
        return {"active_groups": active_groups}

    def encode_observation(self, inputs, instruction):
        images = {}
        for name in CAMERAS:
            with Image.open(inputs.images[name]) as image:
                images[name] = encode_image(np.asarray(image.convert("RGB")))
        return ActorObservation(
            instruction=instruction, images=images,
            state=inputs.manifest["state"]["metadata"][PROPRIOCEPTION],
        )

    def decode_prediction(self, prediction, manifest, images, scope):
        if not isinstance(prediction, NumericChunk):
            raise ValueError("CALVIN requires a numeric actor prediction")
        validate_prediction(prediction, self.profile)
        rows = [relative_command(values) for values in prediction.actions]
        return Proposal(
            rows=rows, state_version=manifest["state_version"], current_state=manifest["state"],
            images=images, raw_actions=prediction.actions, scoped_actions=prediction.actions,
        )

    def preview(self, proposal, environment):
        return {"action_samples": len(proposal.rows), "representation": self.profile.name}

    def review_context(self, proposal, scope):
        return {"preview": proposal.preview, "scope": scope}

    def plan(self, proposal, decision, scope):
        if decision["mode"] != "student" or not 1 <= decision["steps"] <= len(proposal.rows):
            raise ValueError("CALVIN admits a bounded prefix of native actor actions")
        return proposal.rows[:decision["steps"]]

    def compile_row(self, row, state):
        return row

    def dispatch(self, environment, command, expected_state_version):
        return environment.step(StepRequest(command=command, expected_state_version=expected_state_version))

    def outcome(self, reply, command):
        status = reply.execution_status
        if status == "completed" and (not reply.feedback["accepted"]
                or reply.feedback["diagnostics"]["executed_steps"] != 1
                or reply.state_after.state_version != reply.state_before.state_version + 1):
            raise ValueError("CALVIN receipt does not acknowledge exactly one action")
        return StepOutcome(
            status=status, completed_samples=int(status == "completed"),
            execution_started=status in {"completed", "partial"},
            duration_s=1 / self.profile.control_frequency_hz if status == "completed" else None,
            stop_reason="native_terminal" if reply.state_after.ended else None,
        )


def create_binding(name, args, description, actor_info):
    if name != BINDING_NAME:
        raise ValueError("Select calvin_relative for the CALVIN actor")
    return CalvinBinding(description, actor_info)
