"""Ordered CALVIN observations and native relative control semantics."""

from .contracts import ActorField, ActorProfile

REPRESENTATION = "calvin.relative-v1"
CAMERAS = ("static", "gripper")


def actor_profile(horizon: int) -> ActorProfile:
    observation_fields = [
        *(ActorField(name=f"tcp_{axis}", unit="m", semantics="tcp_position", frame="world") for axis in "xyz"),
        *(ActorField(name=f"tcp_euler_{axis}", unit="rad", semantics="tcp_euler_angle", frame="world") for axis in "xyz"),
        ActorField(name="gripper_width", unit="m", semantics="gripper_width"),
        *(ActorField(name=f"joint_{index}", unit="rad", semantics="joint_position") for index in range(7)),
        ActorField(name="gripper_action", unit="sign", semantics="native_gripper_command"),
    ]
    action_fields = [
        *(ActorField(name=f"position_{axis}", unit="normalized", semantics="calvin_position_delta",
                     frame="calvin_native") for axis in "xyz"),
        *(ActorField(name=f"orientation_{axis}", unit="normalized", semantics="calvin_euler_delta",
                     frame="calvin_native") for axis in "xyz"),
        ActorField(name="gripper", unit="sign", semantics="open_positive_close_negative", minimum=-1, maximum=1),
    ]
    return ActorProfile(
        name=REPRESENTATION, cameras=list(CAMERAS), observation_fields=observation_fields,
        action_fields=action_fields, action_horizon=horizon, control_frequency_hz=30,
        image_convention="Native CALVIN RGB HWC uint8; StarVLA preprocessing in the policy runtime",
    )
