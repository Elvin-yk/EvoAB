"""Errors shared by model preset handling."""
from __future__ import annotations


class PresetCompatibilityError(ValueError):
    """A provider compatibility diagnostic safe to display to a user."""
