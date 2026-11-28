"""Configuration and identity for the CALVIN StarVLA actor."""

from evoab.servers.actor.backends.roboscope_config import resolve_config, service_limits

__all__ = ["MODEL", "resolve_config", "service_limits"]
MODEL = "StarVLA-QwenGR00T-CALVIN-D-D"
