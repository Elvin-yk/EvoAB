"""Bind a training-aligned MetaWorld view to native Sawyer actions."""

import numpy as np
from PIL import Image

from evoab.integrations.native_policy import NativePolicyBinding
from evoab.services.actor.contracts import ActorObservation
from evoab.services.actor.images import encode_image
from evoab.services.actor.metaworld_profile import ACTION_BINDING, POLICY_CAMERA, PROPRIOCEPTION, actor_profile

from .adapter import CAMERAS, native_action


class MetaWorldBinding(NativePolicyBinding):
    integration = "metaworld"
    name = ACTION_BINDING
    cameras = CAMERAS
    make_profile = staticmethod(actor_profile)
    native_action = staticmethod(native_action)

    def encode_observation(self, inputs, instruction):
        with Image.open(inputs.images[POLICY_CAMERA]) as image:
            rgb = image.convert("RGB").transpose(Image.Transpose.ROTATE_180)
            encoded = encode_image(np.asarray(rgb))
        return ActorObservation(
            instruction=instruction,
            images={POLICY_CAMERA: encoded},
            state=inputs.manifest["state"]["metadata"][PROPRIOCEPTION],
        )


def create_binding(name, args, description, actor_info):
    if name != ACTION_BINDING:
        raise ValueError(f"Select {ACTION_BINDING} for native MetaWorld policies")
    return MetaWorldBinding(description, actor_info)
