"""Observation and native OSC action contract for stock LIBERO."""

from .contracts import ActorField, ActorProfile

REPRESENTATION = "libero.delta-eef-v1"
CAMERAS = ("agentview", "robot0_eye_in_hand")
PROPRIOCEPTION = "actor_observation_state"


def actor_profile(horizon: int) -> ActorProfile:
    return ActorProfile(
        name=REPRESENTATION, cameras=list(CAMERAS), action_horizon=horizon, control_frequency_hz=20,
        image_convention="LIBERO training orientation, RGB HWC uint8; resize in the policy runtime",
        observation_fields=[
            *(ActorField(name=f"eef_{axis}", unit="m", semantics="eef_position", frame="world") for axis in "xyz"),
            *(ActorField(name=f"eef_q{axis}", unit="unitless", semantics="eef_quaternion", frame="world")
              for axis in "xyzw"),
            *(ActorField(name=f"finger_{i}", unit="m", semantics="finger_position") for i in range(2)),
            *(ActorField(name=f"joint_{i}", unit="rad", semantics="joint_position") for i in range(7)),
        ],
        action_fields=[
            *(ActorField(name=f"position_{axis}", unit="normalized", semantics="osc_position_delta", frame="world")
              for axis in "xyz"),
            *(ActorField(name=f"rotation_{axis}", unit="normalized", semantics="osc_rotation_vector", frame="world")
              for axis in "xyz"),
            ActorField(name="gripper", unit="sign", semantics="close_positive_open_negative", minimum=-1, maximum=1),
        ],
    )
