"""Bind CALVIN observations and relative actions to the shared policy tool."""

import numpy as np
from PIL import Image

from evoab.envs.native_policy import NativePolicyBinding
from evoab.servers.actor.calvin_profile import CAMERAS, actor_profile
from evoab.servers.actor.contracts import ActorObservation
from evoab.servers.actor.images import encode_image

from .relative_action import BINDING_NAME, PROPRIOCEPTION, relative_command


class CalvinBinding(NativePolicyBinding):
    integration = "calvin"
    name = BINDING_NAME
    cameras = CAMERAS
    make_profile = staticmethod(actor_profile)
    native_action = staticmethod(relative_command)

    def encode_observation(self, inputs, instruction):
        images = {}
        for name in CAMERAS:
            with Image.open(inputs.images[name]) as image:
                images[name] = encode_image(np.asarray(image.convert("RGB")))
        return ActorObservation(
            instruction=instruction, images=images,
            state=inputs.manifest["state"]["metadata"][PROPRIOCEPTION],
        )

def create_binding(name, args, description, actor_info):
    if name != BINDING_NAME:
        raise ValueError("Select calvin_relative for the CALVIN actor")
    return CalvinBinding(description, actor_info)
