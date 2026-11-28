"""Native CALVIN relative actions shared by its binding and adapter."""

from evoab.envs.protocol.contracts import NativeCommand, RequestRejected

BINDING_NAME = "calvin_relative"
PROPRIOCEPTION = "actor_observation_state"


def relative_command(values: list[float]) -> NativeCommand:
    command = NativeCommand(values=values)
    if len(command.values) != 7 or command.values[-1] not in (-1, 1):
        raise RequestRejected("CALVIN requires six relative control values and a gripper command of -1 or 1")
    return command
