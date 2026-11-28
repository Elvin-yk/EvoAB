"""RMBench ALOHA-AgileX native policy contract."""

from evoab.servers.actor.contracts import ActorField, ActorProfile, HistoryRequirement

REFERENCE_REVISION = "453d6ec293e63318bbc2bce545d0b9256ab1bc51"
ACTION_BINDING = "rmbench_aloha_dense"
CAMERAS = ("head_camera", "left_camera", "right_camera")
IMAGE_KEYS = tuple("observation.images." + name for name in ("cam_high", "cam_left_wrist", "cam_right_wrist"))
STATE_FIELD = "rmbench_joint_state"
ARMS = ("left", "right")
JOINTS = 14
ACTION_HORIZON = 30
EXECUTE_HORIZON = 16
PHYSICS_SUBSTEPS = 15
NATIVE_FREQUENCY_HZ = 250 / PHYSICS_SUBSTEPS
HISTORY_STRIDE = 8
HISTORY_FRAMES = 120


def actor_profile() -> ActorProfile:
    fields = []
    for arm in ARMS:
        fields.extend(
            ActorField(name=f"{arm}_joint_{index}", unit="rad", semantics="absolute_joint_position")
            for index in range(6)
        )
        fields.append(ActorField(name=f"{arm}_gripper", unit="ratio", semantics="native_gripper_target"))
    return ActorProfile(
        name=ACTION_BINDING,
        cameras=list(CAMERAS),
        observation_fields=fields,
        action_fields=fields,
        action_horizon=ACTION_HORIZON,
        control_frequency_hz=NATIVE_FREQUENCY_HZ,
        image_convention="RGB: native head and wrist cameras at 320x240",
        history={CAMERAS[0]: HistoryRequirement(max_frames=HISTORY_FRAMES, scope="recent")},
    )
