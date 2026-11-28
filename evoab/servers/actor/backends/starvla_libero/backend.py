"""Serve a stock LIBERO StarVLA checkpoint over the RoboScope transport."""

from evoab.servers.actor.backends.roboscope_starvla import RoboScopeStarVLABackend
from evoab.servers.actor.libero_profile import CAMERAS, REPRESENTATION, actor_profile


class StarVLALiberoBackend(RoboScopeStarVLABackend):
    benchmark = "libero"
    suite = "libero_spatial"
    model = "StarVLA-QwenOFT-LIBERO-4in1"
    representation = REPRESENTATION
    camera_features = dict(zip(CAMERAS, ("images.primary", "images.wrist"), strict=True))
    make_profile = staticmethod(actor_profile)
