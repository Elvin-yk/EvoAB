"""Expose stock LIBERO's native OSC step alongside the shared motion tools."""

import numpy as np

from evoab.envs.libero_plus.adapter import LiberoAdapter
from evoab.servers.actor.libero_profile import PROPRIOCEPTION
from evoab.envs.protocol.contracts import RequestRejected

from .relative_action import ACTION_BINDING, native_action


class LiberoPolicyAdapter(LiberoAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.description.action_binding = ACTION_BINDING

    def read_state(self):
        state = super().read_state()
        names = ("robot0_eef_pos", "robot0_eef_quat", "robot0_gripper_qpos", "robot0_joint_pos")
        state.metadata[PROPRIOCEPTION] = np.concatenate(
            [np.asarray(self.obs[name], dtype=np.float32).reshape(-1) for name in names]
        ).tolist()
        return state

    def step_native(self, command):
        action = native_action(command.values)
        with np.errstate(over="ignore"):
            values = np.asarray(action.values, dtype=np.float32)
        if not np.isfinite(values).all():
            raise RequestRejected("LIBERO OSC deltas must fit into finite float32 values")
        return self._step(values, float(values[-1] == -1))
