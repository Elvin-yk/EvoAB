"""Mirror artifacts returned by a remote environment into evoab storage.

The environment service owns snapshot identity and ordering. Perception,
actor bindings and prompt assembly share local copies, so each remote artifact
is downloaded once per mirror.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from evoab.artifacts.store import ArtifactStore


class RemoteArtifactMirror:
    """Materialize remote artifact references in a local ``ArtifactStore``.

    The client must expose ``artifact_bytes(remote_id)``. The first fetch
    checks any supplied size and hash; subsequent requests for that remote ID
    reuse the content-addressed local reference.
    """

    def __init__(self, client, store: ArtifactStore):
        self.client = client
        self.store = store
        self._refs: dict[str, dict] = {}

    def mirror_ref(self, reference: dict) -> dict:
        """Fetch one remote ref once and return its local artifact reference."""
        remote_id = _artifact_id(reference)
        cached = self._refs.get(remote_id)
        if cached is not None:
            return dict(cached)

        data = bytes(self.client.artifact_bytes(remote_id))
        expected_size = reference.get("size_bytes")
        if expected_size is not None and int(expected_size) != len(data):
            raise ValueError(f"Remote artifact size mismatch: {remote_id}")
        expected_sha256 = reference.get("sha256")
        if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError(f"Remote artifact hash mismatch: {remote_id}")

        local = self.store.put_bytes(data, reference.get("media_type") or "application/octet-stream",
                                     retain=False if reference.get("temporary") else None)
        # Local IDs include the media type and may differ from remote IDs.
        # Keep both identities: local storage reads use the returned artifact_id.
        result = {
            **local,
            "remote_artifact_id": remote_id,
            "remote_sha256": expected_sha256 or hashlib.sha256(data).hexdigest(),
        }
        self._refs[remote_id] = result
        return dict(result)

    def local_path(self, reference: dict) -> Path:
        """Return the verified file owned by the shared artifact store."""
        local = self.mirror_ref(reference)
        return self.store.local_path(local["artifact_id"])


def _artifact_id(reference: dict) -> str:
    # A mirrored frame contains both identities.  Always address the remote
    # service with its original ID; the local content-addressed ID is only for
    # reads from ``ArtifactStore``.
    value = None
    if isinstance(reference, dict):
        value = reference.get("remote_artifact_id") or reference.get("artifact_id")
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("Invalid remote artifact ID")
    return value


__all__ = ["RemoteArtifactMirror"]
