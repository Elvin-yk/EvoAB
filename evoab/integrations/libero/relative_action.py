"""Translate the seven native LIBERO action values into an episode command."""

from evoab.services.environment.contracts import NativeCommand, RequestRejected

ACTION_BINDING = "libero_delta_eef"


def native_action(values) -> NativeCommand:
    command = NativeCommand(values=values)
    if len(command.values) != 7 or command.values[-1] not in (-1, 1):
        raise RequestRejected("LIBERO requires six OSC deltas and a gripper command of -1 or 1")
    return command
