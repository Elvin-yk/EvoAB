"""Actor wire profile for Direct absolute grasp-site targets."""

from evoab.direct.actions import AbsoluteEEF
from evoab.services.actor.contracts import ActorField, ActorProfile


def absolute_actor_profile(arms, cameras, control_frequency_hz, horizon):
    fields = []
    for arm in arms:
        for column in AbsoluteEEF.columns[:3]:
            fields.append(ActorField(name=f"{arm}.{column}", unit="m", semantics="position",
                                     frame="world", control_point="grasp_site"))
        for column in AbsoluteEEF.columns[3:7]:
            fields.append(ActorField(name=f"{arm}.{column}", unit="unitless", semantics="quaternion_wxyz",
                                     frame="world", control_point="grasp_site"))
        fields.append(ActorField(name=f"{arm}.opening", unit="normalized",
                                 semantics="normalized_opening", minimum=0, maximum=1))
    return ActorProfile(name="direct_abs_eef", cameras=list(cameras), observation_fields=fields,
                        action_fields=fields, action_horizon=horizon,
                        control_frequency_hz=control_frequency_hz, image_convention="RGB HWC")
