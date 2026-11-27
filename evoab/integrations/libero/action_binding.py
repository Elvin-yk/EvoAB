"""Bind stock LIBERO camera frames and native actions to actor_act."""

import numpy as np
from PIL import Image

from evoab.integrations.native_policy import NativePolicyBinding
from evoab.services.actor.contracts import ActorObservation
from evoab.services.actor.images import encode_image
from evoab.services.actor.libero_profile import CAMERAS, PROPRIOCEPTION, actor_profile

from .relative_action import ACTION_BINDING, native_action


class LiberoBinding(NativePolicyBinding):
    integration = "libero"
    name = ACTION_BINDING
    cameras = CAMERAS
    make_profile = staticmethod(actor_profile)
    native_action = staticmethod(native_action)

    def encode_observation(self, inputs, instruction):
        images = {}
        for name in CAMERAS:
            with Image.open(inputs.images[name]) as image:
                # The adapter has already corrected the vertical OpenGL origin.
                # Mirror the remaining axis to match the stock LIBERO policy worker.
                rgb = image.convert("RGB").transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                images[name] = encode_image(np.asarray(rgb))
        return ActorObservation(
            instruction=instruction, images=images,
            state=inputs.manifest["state"]["metadata"][PROPRIOCEPTION],
        )


def create_binding(name, args, description, actor_info):
    if name != ACTION_BINDING:
        raise ValueError("Select libero_delta_eef for the stock LIBERO actor")
    return LiberoBinding(description, actor_info)
