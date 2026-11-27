"""Serve the CALVIN StarVLA policy over the shared RoboScope transport."""

from evoab.services.actor.backends.roboscope_starvla import RoboScopeStarVLABackend
from evoab.services.actor.calvin_profile import CAMERAS, REPRESENTATION, actor_profile

from .config import MODEL


class StarVLACalvinBackend(RoboScopeStarVLABackend):
    benchmark = "calvin"
    suite = "task_D_D"
    model = MODEL
    representation = REPRESENTATION
    camera_features = {name: f"images.{name}" for name in CAMERAS}
    make_profile = staticmethod(actor_profile)
