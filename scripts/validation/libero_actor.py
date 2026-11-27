"""Validate LIBERO policy camera orientation, native actions, and receipts."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from evoab.integrations.libero.action_binding import LiberoBinding
from evoab.integrations.libero.adapter import LiberoPolicyAdapter
from evoab.integrations.libero.relative_action import ACTION_BINDING, native_action
from evoab.services.actor.contracts import NumericChunk
from evoab.services.actor.images import decode_image
from evoab.services.actor.libero_profile import CAMERAS, PROPRIOCEPTION, actor_profile
from evoab.services.environment.contracts import RequestRejected


class LiberoActorChecks(unittest.TestCase):
    def make_binding(self):
        environment = SimpleNamespace(
            action_binding=ACTION_BINDING, arms=[SimpleNamespace(name="arm")],
            cameras=[SimpleNamespace(name=name) for name in CAMERAS], control_frequency_hz=20,
        )
        episode = SimpleNamespace(integration="libero", capabilities=["pose", "native"],
                                  environment=environment)
        return LiberoBinding(episode, SimpleNamespace(profile=actor_profile(2)))

    def test_actor_inputs_match_the_native_worker_rotation(self):
        binding = self.make_binding()
        # The environment publishes an upright image; the model uses both OpenGL axes flipped.
        raw = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "upright.png"
            Image.fromarray(raw[::-1]).save(path)
            state = list(range(16))
            inputs = SimpleNamespace(
                images=dict.fromkeys(CAMERAS, path),
                manifest={"state": {"metadata": {PROPRIOCEPTION: state}}},
            )
            observation = binding.encode_observation(inputs, "pick up the bowl")
        self.assertEqual(observation.instruction, "pick up the bowl")
        self.assertEqual(observation.state, state)
        for image in observation.images.values():
            np.testing.assert_array_equal(decode_image(image), raw[::-1, ::-1])

    def test_policy_actions_preserve_order_and_native_gripper_sign(self):
        binding = self.make_binding()
        rows = [[.1, .2, .3, -.4, -.5, -.6, sign] for sign in (-1, 1)]
        manifest = {"state_version": 0, "state": {}}
        proposal = binding.decode_prediction(NumericChunk(actions=rows), manifest, {}, {})
        plan = binding.plan(proposal, {"mode": "student", "steps": 2}, {})
        self.assertEqual([command.values for command in plan], rows)
        client = Mock()
        binding.dispatch(client, plan[0], 0)
        request = client.step.call_args.args[0]
        self.assertEqual(request.command.values, rows[0])
        self.assertEqual(request.expected_state_version, 0)
        with self.assertRaises(RequestRejected):
            native_action([0.] * 7)

    def test_adapter_passes_osc_actions_and_normalizes_command_for_tools(self):
        adapter = LiberoPolicyAdapter.__new__(LiberoPolicyAdapter)
        with patch.object(adapter, "_step") as step:
            adapter.step_native(native_action([.1] * 6 + [-1]))
            values, opening = step.call_args.args
            self.assertEqual(opening, 1.)
            np.testing.assert_array_equal(values, np.array([.1] * 6 + [-1], dtype=np.float32))
            adapter.step_native(native_action([.1] * 6 + [1]))
            self.assertEqual(step.call_args.args[1], 0.)
            with self.assertRaises(RequestRejected):
                adapter.step_native(native_action([1e308] * 6 + [1]))
            self.assertEqual(step.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
