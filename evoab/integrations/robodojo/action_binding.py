"""RoboDojo semantic policy over reusable hybrid arm capabilities."""
from evoab.direct.actor import absolute_actor_profile
from evoab.services.actor.contracts import ActorProfile
from evoab.tools.hybrid.adapters.arm_gripper import (
    ArmActionLayout,
    ArmGripperBinding,
)

from .actor_profiles import profile_for, validate_x5_joint_profile

X5_JOINT_NAMES = tuple(f"joint{index}" for index in range(1, 7))


class ActionBinding(ArmGripperBinding):
    def __init__(self, args, description, actor_info, *, name="robodojo_pi05"):
        if name not in {"robodojo_pi05", "robodojo_x5_joint", "robodojo_direct_eef"}:
            raise ValueError(f"Unsupported RoboDojo action binding: {name}")
        environment = description.environment
        if (description.integration != "robodojo" or tuple(arm.name for arm in environment.arms) != ("left", "right")
                or environment.control_frequency_hz != 25):
            raise ValueError("RoboDojo binding requires the dual X5 environment at 25 Hz")
        for arm in environment.arms:
            control = arm.control
            if (tuple(arm.joint_names) != X5_JOINT_NAMES or arm.control_frame != "x5_grasp_site"
                    or control is None or set(control.commands) != {"joints", "pose"}
                    or control.pose_solver != "dls" or not control.joint_preview
                    or control.rotation != "full" or control.rotation_reference != "measured"
                    or control.gripper != "continuous"):
                raise ValueError("RoboDojo must publish the ordered X5 joints and grasp-site control contract")
        if name == "robodojo_direct_eef":
            self._configure_direct(description, actor_info)
            return
        legacy = name == "robodojo_pi05"
        if legacy:
            if (actor_info.profile != profile_for(name) or actor_info.model != "pi05"
                    or actor_info.runtime != "openpi_jax" or actor_info.policy_rng_seed != 0
                    or actor_info.model_config_name != "pi05_base_aloha_full_sim_arx-x5_seed_0"):
                raise ValueError("Legacy RoboDojo Pi0.5 requires its identified profile and seed-zero runtime")
        else:
            # A compatible actor is independent of its serving backend.
            validate_x5_joint_profile(actor_info.profile)
        layout = {arm: ArmActionLayout(joints=tuple(f"{arm}_joint_{i}" for i in range(6)),
                                     gripper=f"{arm}_gripper") for arm in ("left", "right")}
        cameras = {"cam_high": "head", "cam_left_wrist": "left_wrist", "cam_right_wrist": "right_wrist"} if legacy else None
        super().__init__(name=name, description=description, actor_info=actor_info, action_layout=layout,
            observation_encoder=lambda state: [number for arm in ("left", "right")
                for number in (*state["arms"][arm]["joints"], state["arms"][arm]["gripper"])],
            camera_map=cameras, clip_grippers=legacy)

    def _configure_direct(self, description, actor_info):
        profile = actor_info.profile
        arms = tuple(arm.name for arm in description.environment.arms)
        if (not isinstance(profile, ActorProfile) or actor_info.runtime != "direct"
                or not 1 <= profile.action_horizon <= 15
                or not profile.cameras
                or profile != absolute_actor_profile(arms, profile.cameras, 25, profile.action_horizon)):
            raise ValueError("Direct actor must declare the RoboDojo absolute EEF observation and action profile")
        layout = {arm: ArmActionLayout(
            position=(f"{arm}.x", f"{arm}.y", f"{arm}.z"),
            quaternion=(f"{arm}.qw", f"{arm}.qx", f"{arm}.qy", f"{arm}.qz"),
            gripper=f"{arm}.opening") for arm in arms}

        def observation(state):
            values = []
            for name in arms:
                arm = state["arms"][name]
                pose = arm["control_pose"]
                values.extend((*pose["position"], *pose["quaternion"], arm["gripper"]))
            return values

        self.reviewer_supported = False
        super().__init__(name="robodojo_direct_eef", description=description, actor_info=actor_info,
                         action_layout=layout, observation_encoder=observation,
                         action_kind="cartesian_absolute", default_actions=profile.action_horizon,
                         max_actions=profile.action_horizon, default_max_chunks=1)

    def encode_observation(self, inputs, instruction):
        if self.name == "robodojo_direct_eef" and inputs.active_groups:
            groups = ", ".join(inputs.active_groups)
            instruction = f"Active arms: {groups}. Keep all other arms at their measured pose and opening.\n{instruction}"
        return super().encode_observation(inputs, instruction)


def create_binding(name, args, description, actor_info):
    return ActionBinding(args, description, actor_info, name=name)
