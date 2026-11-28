"""Bind the reference RMBench RGB history and joint targets to shared Hybrid tools."""

from base64 import b64encode
from pathlib import Path

from evoab.envs.native_policy import NativePolicyBinding
from evoab.servers.actor.contracts import ActorObservation, HistoryFrame, Image
from evoab.servers.actor.rmbench_profile import (
    ACTION_BINDING, ARMS, CAMERAS, EXECUTE_HORIZON, HISTORY_STRIDE, STATE_FIELD, actor_profile,
)
from evoab.envs.protocol.contracts import RequestRejected

from .dense import native_command


def png(path: str) -> Image:
    return Image(data=b64encode(Path(path).read_bytes()).decode("ascii"))


class RMBenchBinding(NativePolicyBinding):
    integration = "rmbench"
    name = ACTION_BINDING
    arms = ARMS
    cameras = CAMERAS
    default_actions = EXECUTE_HORIZON
    min_actions = EXECUTE_HORIZON
    max_actions = EXECUTE_HORIZON
    native_action = staticmethod(native_command)

    @staticmethod
    def make_profile(_horizon):
        return actor_profile()

    def __init__(self, description, actor_info):
        super().__init__(description, actor_info)
        if description.environment.metadata.get("camera_history_stride") != HISTORY_STRIDE:
            raise ValueError("RMBench requires the native head camera history cadence")
        self.required_instruction = description.task

    def validate_scope(self, active_groups, constraints):
        if (not isinstance(active_groups, list) or len(active_groups) != len(ARMS)
                or set(active_groups) != set(ARMS) or constraints != {}):
            raise RequestRejected("RMBench requires both active_groups ['left', 'right'] and empty constraints")
        return {"active_groups": list(ARMS)}

    def encode_observation(self, inputs, instruction):
        if instruction.strip() != self.required_instruction:
            raise RequestRejected("RMBench actor_act requires the original task instruction verbatim")
        tick = inputs.manifest["state_version"]
        history = inputs.history[CAMERAS[0]]
        if not history or history[-1].state_version != tick:
            raise ValueError("RMBench policy requires an observation on its native capture cadence")
        return ActorObservation(
            instruction=self.required_instruction,
            images={name: png(inputs.images[name]) for name in CAMERAS},
            state=inputs.manifest["state"]["metadata"][STATE_FIELD],
            history={CAMERAS[0]: [
                HistoryFrame(frame_id=sample.frame_id, captured_at=sample.captured_at,
                             simulation_time_s=sample.simulation_time_s, state_version=sample.state_version,
                             image=png(sample.path))
                for sample in history
            ]},
        )


def create_binding(name, args, description, actor_info):
    if name != ACTION_BINDING:
        raise ValueError(f"Select {ACTION_BINDING} for native RMBench policies")
    return RMBenchBinding(description, actor_info)
