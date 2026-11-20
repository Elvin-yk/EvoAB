"""Own one prepared Arena episode through the shared session lifecycle."""
from evoab.integrations.prepared_episode import PreparedEpisodeSession
from evoab.services.environment.device_contracts import DeviceEpisodeInfo


def validate_episode(session):
    info = session.episode.description
    if not isinstance(info, DeviceEpisodeInfo) or info.integration != "isaaclab_arena":
        raise ValueError("Arena requires a prepared isaaclab_arena device episode")


def create_session(context):
    from .action_binding import create_binding
    return PreparedEpisodeSession(context, binding_factory=create_binding, validate_episode=validate_episode)
