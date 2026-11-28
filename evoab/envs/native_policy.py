"""Execute a bounded prefix of native single-arm policy actions."""

from evoab.servers.actor.contracts import ActorProfile, NumericChunk, validate_prediction
from evoab.envs.protocol.contracts import StepRequest
from evoab.tools.hybrid.contracts import HybridCapabilities, Proposal, StepOutcome
from evoab.tools.hybrid.decisions import validate_decision
from evoab.tools.toolkit import schema


class NativePolicyBinding:
    reviewer_supported = False
    reviewer_prompt = None
    validate_decision = staticmethod(validate_decision)

    def __init__(self, description, actor_info):
        environment = description.environment
        profile = actor_info.profile
        if (description.integration != self.integration or "native" not in description.capabilities
                or environment.action_binding != self.name or [arm.name for arm in environment.arms] != ["arm"]
                or {camera.name for camera in environment.cameras} != set(self.cameras)):
            raise ValueError(f"{self.integration} Hybrid requires a prepared episode with native actions")
        if (not isinstance(profile, ActorProfile) or profile != self.make_profile(profile.action_horizon)
                or environment.control_frequency_hz != profile.control_frequency_hz):
            raise ValueError(f"The actor must declare the {self.integration} native control profile")
        self.profile = profile
        self.action_horizon = profile.action_horizon
        self.capabilities = HybridCapabilities(
            groups=("arm",), constraint_schema=schema({}),
            default_actions=self.action_horizon, max_actions=self.action_horizon,
        )

    def validate_scope(self, active_groups, constraints):
        if active_groups != ["arm"] or constraints != {}:
            raise ValueError(f"{self.integration} requires active_groups=['arm'] and empty constraints")
        return {"active_groups": active_groups}

    def decode_prediction(self, prediction, manifest, images, scope):
        if not isinstance(prediction, NumericChunk):
            raise ValueError(f"{self.integration} requires a numeric actor prediction")
        validate_prediction(prediction, self.profile)
        rows = [self.native_action(values) for values in prediction.actions]
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
            raise ValueError(f"{self.integration} admits a bounded prefix of native actor actions")
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
            raise ValueError(f"{self.integration} receipt does not acknowledge exactly one action")
        return StepOutcome(
            status=status, completed_samples=int(status == "completed"),
            execution_started=status in {"completed", "partial"},
            duration_s=1 / self.profile.control_frequency_hz if status == "completed" else None,
            stop_reason="native_terminal" if reply.state_after.ended else None,
        )
