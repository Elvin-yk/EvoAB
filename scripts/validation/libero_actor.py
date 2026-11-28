"""Validate LIBERO policy camera orientation, native actions, and receipts."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from evoab.envs.libero.action_binding import LiberoBinding
from evoab.envs.libero.adapter import LiberoPolicyAdapter
from evoab.envs.libero.relative_action import ACTION_BINDING, native_action
from evoab.envs.libero_pro.action_binding import LiberoProBinding, create_binding as create_pro_binding
from evoab.envs.libero_pro.adapter import LiberoProPolicyAdapter
from evoab.envs.libero_pro.deployment import plan as plan_pro_episode
from evoab.servers.actor.contracts import NumericChunk
from evoab.servers.actor.images import decode_image
from evoab.servers.actor.libero_profile import CAMERAS, PROPRIOCEPTION, actor_profile
from evoab.envs.protocol.contracts import RequestRejected


class LiberoActorChecks(unittest.TestCase):
    def describe_episode(self, integration):
        environment = SimpleNamespace(
            action_binding=ACTION_BINDING, arms=[SimpleNamespace(name="arm")],
            cameras=[SimpleNamespace(name=name) for name in CAMERAS], control_frequency_hz=20,
        )
        return SimpleNamespace(integration=integration, capabilities=["pose", "native"],
                               environment=environment)

    def make_binding(self):
        return LiberoBinding(self.describe_episode("libero"), SimpleNamespace(profile=actor_profile(2)))

    def test_pro_accepts_the_shared_policy_profile_for_native_episodes(self):
        episode = self.describe_episode("libero_pro")
        actor = SimpleNamespace(profile=actor_profile(2))
        binding = create_pro_binding(ACTION_BINDING, None, episode, actor)
        self.assertIsInstance(binding, LiberoProBinding)
        self.assertEqual(binding.profile, actor.profile)
        with self.assertRaisesRegex(ValueError, "prepared episode"):
            create_pro_binding(ACTION_BINDING, None, self.describe_episode("libero"), actor)
        episode.capabilities = ["pose"]
        with self.assertRaisesRegex(ValueError, "native actions"):
            create_pro_binding(ACTION_BINDING, None, episode, actor)

    def test_pro_uses_the_bddl_instruction_for_perturbed_tasks(self):
        env = SimpleNamespace(language_instruction="lift the bowl and set it on the plate")
        observation = {}
        with patch.object(LiberoPolicyAdapter, "__init__", return_value=None) as init:
            LiberoProPolicyAdapter(env, observation, instruction="pick up the bowl")
        init.assert_called_once_with(env, observation, instruction=env.language_instruction)

    def test_pro_launch_preserves_nonzero_initial_state_index(self):
        args = SimpleNamespace(runtime_profile="libero_spatial_lan", task="task", seed=7,
                               init_state_index=2, host="127.0.0.1", port=None, depth=False)
        runtime = SimpleNamespace(python="python", environment={},
                                  data={"native_root": "/libero-pro", "tasks": {"libero_spatial_lan/task": {}}})
        launch = plan_pro_episode(args, runtime, Path("/output"), "episode")
        index = launch.command.index("--init-state-index")
        self.assertEqual(launch.command[index + 1], "2")
        self.assertEqual(launch.expected["metadata.init_state_index"], 2)

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
