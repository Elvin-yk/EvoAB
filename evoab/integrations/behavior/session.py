"""Reuse the prepared episode and one supplied R1Pro binding."""
from evoab.integrations.prepared_episode import PreparedEpisodeSession
from evoab.services.environment.device_contracts import DeviceEpisodeInfo


def validate_episode(session):
    info = session.episode.description
    if not isinstance(info, DeviceEpisodeInfo) or info.integration != "behavior":
        raise ValueError("Expected a prepared BEHAVIOR device episode")


def create_session(context):
    from .action_binding import create_binding
    return PreparedEpisodeSession(context, binding_factory=create_binding, validate_episode=validate_episode)
