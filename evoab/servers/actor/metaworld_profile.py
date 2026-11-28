"""MetaWorld Sawyer observation and native command contract."""

from .contracts import ActorField, ActorProfile

ACTION_BINDING = "metaworld_sawyer_xyz"
POLICY_CAMERA = "corner2_policy"
PROPRIOCEPTION = "actor_observation_state"


def actor_profile(horizon: int) -> ActorProfile:
    return ActorProfile(
        name="metaworld.delta-xyz-v1",
        cameras=[POLICY_CAMERA],
        observation_fields=[
            *(ActorField(name=f"hand_{axis}", unit="m", semantics="hand_position", frame="world")
              for axis in "xyz"),
            ActorField(name="jaw", unit="normalized", semantics="native_gripper_observation"),
        ],
        action_fields=[
            *(ActorField(name=f"delta_{axis}", unit="normalized", semantics="native_xyz_delta",
                         frame="world", minimum=-1, maximum=1) for axis in "xyz"),
            ActorField(name="gripper", unit="normalized", semantics="close_positive_open_negative",
                       minimum=-1, maximum=1),
        ],
        action_horizon=horizon,
        control_frequency_hz=80,
        image_convention="480x480 RGB, LeRobot MetaWorld corner2 pose and 180-degree rotation",
    )
