"""Validate the MetaWorld policy observation and shared native/pose step contract."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image

from evoab.envs.metaworld.action_binding import MetaWorldBinding
from evoab.envs.metaworld.adapter import CAMERAS, MetaWorldAdapter, native_action
from evoab.servers.actor.contracts import NumericChunk, validate_observation
from evoab.servers.actor.images import decode_image
from evoab.servers.actor.metaworld_profile import ACTION_BINDING, POLICY_CAMERA, PROPRIOCEPTION, actor_profile
from evoab.envs.protocol.contracts import ArmCommand, Pose, PoseCommand, RequestRejected


class MetaWorldActorChecks(unittest.TestCase):
    def test_binding_uses_training_camera_and_preserves_native_actions(self):
        environment = SimpleNamespace(
            action_binding=ACTION_BINDING, arms=[SimpleNamespace(name="arm")],
            cameras=[SimpleNamespace(name=name) for name in CAMERAS], control_frequency_hz=80,
        )
        episode = SimpleNamespace(integration="metaworld", capabilities=["pose", "native"],
                                  environment=environment)
        binding = MetaWorldBinding(episode, SimpleNamespace(profile=actor_profile(2)))
        raw = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
        state = [0.1, 0.2, 0.3, 0.4]
        manifest = {"state": {"metadata": {PROPRIOCEPTION: state}}, "state_version": 3}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corner.png"
            Image.fromarray(raw).save(path)
            inputs = SimpleNamespace(images={POLICY_CAMERA: path}, manifest=manifest)
            observation = binding.encode_observation(inputs, "press the button")
        validate_observation(observation, binding.profile)
        self.assertEqual(observation.state, state)
        self.assertEqual(observation.instruction, "press the button")
        np.testing.assert_array_equal(decode_image(observation.images[POLICY_CAMERA]), raw[::-1, ::-1])

        actions = [[0.1, 0.2, 0.3, -0.5], [0.3, 0.2, 0.1, 1.0]]
        proposal = binding.decode_prediction(NumericChunk(actions=actions), manifest, {}, {})
        commands = binding.plan(proposal, {"mode": "student", "steps": 2}, {})
        self.assertEqual([command.values for command in commands], actions)
        with self.assertRaises(RequestRejected):
            native_action([0, 0, 0, 1.1])

    def test_native_and_pose_steps_share_episode_and_gripper_intent(self):
        adapter = MetaWorldAdapter.__new__(MetaWorldAdapter)
        adapter.steps, adapter.max_steps = 0, 3
        adapter.ended = adapter.closed = adapter.success = False
        adapter.action_scale, adapter.hand_body_id = 0.01, -1
        adapter.env = SimpleNamespace(
            tcp_center=[0.0, 0.0, 0.0],
            step=Mock(side_effect=[
                ([0.0, 0.0, 0.0, 0.6], 0, False, False, {"success": 0}),
                ([0.0, 0.0, 0.0, 0.8], 0, False, False, {"success": 1}),
            ]),
        )
        first = adapter.step_native(native_action([0.0, 0.0, 0.3, 0.6]))
        self.assertEqual(first.state.state_version, 1)
        self.assertAlmostEqual(first.state.arms["arm"].gripper_command, 0.2)
        self.assertEqual(first.state.metadata[PROPRIOCEPTION], [0, 0, 0, 0.6])
        pose = Pose(position=[0.01, 0, 0], quaternion=[1, 0, 0, 0])
        second = adapter.step_pose(PoseCommand(arms={"arm": ArmCommand(pose=pose, gripper=0.8)}))
        self.assertEqual(second.state.state_version, 2)
        self.assertTrue(second.state.ended)
        np.testing.assert_allclose(adapter.env.step.call_args.args[0], [1, 0, 0, -0.6])
        with self.assertRaises(RequestRejected):
            adapter.step_native(native_action([0.0] * 4))
        self.assertEqual(adapter.env.step.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
