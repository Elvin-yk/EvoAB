"""CPU validation for CALVIN Hybrid across real HTTP clients with fixture runtimes."""

from contextlib import ExitStack
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from evoab import Session, SessionConfig
from evoab.artifacts.store import ArtifactStore
from evoab.integrations.calvin.adapter import CalvinAdapter
from evoab.integrations.calvin.relative_action import BINDING_NAME
from evoab.services.actor.calvin_profile import actor_profile
from evoab.services.actor.contracts import ActorInfo, NumericChunk
from evoab.services.actor.images import decode_image
from evoab.services.actor.routes import build_router as actor_router
from evoab.services.actor.service import ActorService
from evoab.services.environment.contracts import (
    ArmCommand, EpisodeInfo, NativeCommand, Pose, PoseCommand, RequestRejected,
)
from evoab.services.environment.routes import build_router as environment_router
from evoab.services.environment.service import EnvironmentService
from evoab.services.hosting import HttpServer, create_app

CHECKPOINT = "a" * 64
ROW = [.1, .2, .3, -.4, -.5, -.6, -1.]


class NativeFixture:
    control_freq = 30

    def __init__(self):
        view = np.eye(4).flatten().tolist()
        self.cameras = [
            SimpleNamespace(name="static", viewMatrix=view, projectionMatrix=view, width=200, height=200),
            SimpleNamespace(name="gripper", view_matrix=view, projection_matrix=view, width=84, height=84),
        ]
        self.robot = SimpleNamespace(filename="robot.urdf", robot_uid=0, tcp_link_id=0, gripper_action=1,
            arm_joint_ids=list(range(7)), gripper_joint_ids=[7, 8], gripper_joint_limits=[0., .04],
            base_position=[0., 0., 0.], base_orientation=[0., 0., 0., 1.])
        self.scene = SimpleNamespace(data_path=Path("."))
        self.cid = 0
        self.p = self
        self.actions = []

    def get_info(self):
        return {"native_steps": len(self.actions)}

    def getLinkState(self, *args, **kwargs):
        return [0., 0., 0.], [0., 0., 0., 1.]

    def getJointStates(self, robot, joints, **kwargs):
        return [(0.,)] * len(joints)

    def observation(self):
        step = len(self.actions)
        return {"rgb_obs": {"rgb_static": np.full((200, 200, 3), step, np.uint8),
                            "rgb_gripper": np.full((84, 84, 3), step + 20, np.uint8)},
                "robot_obs": np.arange(15, dtype=np.float32) + step}

    def step(self, action):
        self.actions.append(action)
        return self.observation(), None, None, self.get_info()

    def close(self):
        pass


class ActorFixture:
    info = ActorInfo(model="fixture", runtime="fixture", model_id="fixture", model_config_name="fixture",
                     checkpoint_sha256=CHECKPOINT, profile=actor_profile(2))

    def __init__(self, rows=None):
        self.observations = []
        self.rows = rows if rows is not None else [ROW, ROW]

    def create_session(self):
        return None

    def predict_batch(self, requests):
        self.observations.extend(item.observation for item in requests)
        return [NumericChunk(actions=self.rows) for item in requests]

    def close_session(self, state):
        pass

    def close(self):
        pass


def adapter_fixture(native):
    def score(initial, current, tasks):
        return tasks if current["native_steps"] == 3 else set()

    with patch("evoab.integrations.robot_resources.urdf_kinematics", return_value={}), patch(
        "evoab.integrations.robot_resources.add_robot_resources"
    ):
        return CalvinAdapter(native, SimpleNamespace(get_task_info_for_set=score),
                             task="open_drawer", instruction="open drawer", episode_id="calvin-fixture",
                             metadata={"task_id": "open_drawer"}, observation=native.observation())


class CalvinActorChecks(unittest.TestCase):
    def test_hybrid_only_round_trip_and_native_terminal(self):
        for rows in (None, [ROW, [*ROW[:-1], 0.]]):
            with self.subTest(valid=rows is None), ExitStack() as stack:
                directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
                artifacts = ArtifactStore(directory / "environment")
                stack.callback(artifacts.close)
                native = NativeFixture()
                adapter = adapter_fixture(native)
                info = EpisodeInfo(episode_id=adapter.episode_id, task=adapter.instruction, integration="calvin",
                    environment=adapter.description, metadata=adapter.metadata,
                    capabilities=["pose", "native", "published_observations"])
                environment = EnvironmentService(adapter, info, artifacts)
                stack.callback(environment.close)
                environment.initialize()
                env_server = HttpServer(create_app(environment_router(environment)))
                stack.callback(env_server.stop)
                env_server.start_background()
                fixture = ActorFixture(rows)
                actor = ActorService(fixture, max_batch_size=1)
                stack.callback(actor.close)
                actor_server = HttpServer(create_app(actor_router(actor)))
                stack.callback(actor_server.stop)
                actor_server.start_background()

                cfg = SessionConfig(integration="calvin", env_url=env_server.url,
                    action_model_url=actor_server.url, recipe=str(Path(__file__).resolve().parents[2] / "examples" / "recipes" / "policy.yaml"),
                    recipe_overrides={"roles.main.capabilities": "[scene, hybrid, episode]"},
                    integration_options={"action_model_checkpoint_sha256": CHECKPOINT},
                    output_dir=directory / "agent")
                session = stack.enter_context(Session(cfg))
                tools = {spec["name"] for spec in session.tools.list()}
                self.assertTrue({"actor_act", "capture_scene", "finish", "wait_for_tool"} <= tools)
                self.assertFalse({"move", "grasp", "release", "hold"} & tools)
                job = session.tools.submit("actor_act", {"instruction": "open drawer", "active_groups": ["arm"]})
                self.assertIsNotNone(job.job_id)
                result = job.result(timeout=10)
                if rows is not None:
                    self.assertFalse(result.success)
                    self.assertEqual(native.actions, [])
                    continue

                self.assertTrue(result.success, result.error)
                self.assertEqual(result.extra["execution"]["stop_reason"], "native_terminal")
                self.assertEqual(result.extra["execution"]["executed_actions"], 3)
                self.assertEqual({image.name for image in result.images}, {"static", "gripper"})
                self.assertEqual(len(fixture.observations), 2)
                for observation, step in zip(fixture.observations, [0, 2], strict=True):
                    self.assertEqual(observation.instruction, "open drawer")
                    np.testing.assert_array_equal(observation.state, np.arange(15) + step)
                    np.testing.assert_array_equal(decode_image(observation.images["static"]),
                                                  np.full((200, 200, 3), step, np.uint8))
                    np.testing.assert_array_equal(decode_image(observation.images["gripper"]),
                                                  np.full((84, 84, 3), step + 20, np.uint8))
                for action in native.actions:
                    self.assertEqual(action.dtype, np.float32)
                    np.testing.assert_array_equal(action, np.asarray(ROW, dtype=np.float32))
                self.assertTrue(session.finish().task_success)

    def test_relative_rejections_preserve_existing_pose_execution(self):
        native = NativeFixture()
        adapter = adapter_fixture(native)
        self.assertEqual(adapter.description.action_binding, BINDING_NAME)
        for values in ([0.] * 6, [0.] * 7, [1e308, *ROW[1:]]):
            with self.assertRaises(RequestRejected):
                adapter.step_native(NativeCommand(values=values))
        self.assertEqual(native.actions, [])
        receipt = adapter.step_pose(PoseCommand(arms={"arm": ArmCommand(
            pose=Pose(position=[.1, .2, .3], quaternion=[1., 0., 0., 0.]), gripper=1.
        )}))
        self.assertTrue(receipt.accepted)
        self.assertEqual(native.actions, [([.1, .2, .3], [0., 0., 0., 1.], 1)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
