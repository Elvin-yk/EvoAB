"""Expose the baseline StarVLA policy via EvoAB's actor protocol."""

from dataclasses import dataclass
import hashlib
import threading
from uuid import uuid4

import numpy as np

from evoab.servers.actor.contracts import ActorInfo, NumericChunk, validate_prediction
from evoab.servers.actor.images import decode_image
from evoab.servers.errors import ServiceError

from .roboscope_config import RoboScopeStarVLAConfig

RUNTIME = "roboscope_starvla"


@dataclass
class PolicyEpisode:
    episode_id: str
    observations: int = 0


class RoboScopeStarVLABackend:
    def __init__(self, config: RoboScopeStarVLAConfig):
        from roboscope_kit import EpisodeContext, Observation, PolicyClient, SessionContext
        from roboscope_kit.benchmark_specs import get_benchmark_spec

        self.config = config
        self._client_type = PolicyClient
        self._observation_type = Observation
        self._episode_type = EpisodeContext
        self._lock = threading.RLock()
        self._active: PolicyEpisode | None = None
        self._closed = self._failed = False

        checkpoint = config.checkpoint.expanduser().resolve(strict=True)
        with checkpoint.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != config.checkpoint_sha256:
                raise ValueError("StarVLA checkpoint SHA-256 differs from the configured identity")
        with self._client() as client:
            capabilities = client.load(SessionContext(uuid4().hex, str(checkpoint),
                                                      get_benchmark_spec(self.benchmark, self.suite)))
            horizons = capabilities.supported_action_chunk_sizes
            visual_features = {"language.instruction", *self.camera_features.values()}
            admitted_features = visual_features | {"state.proprioception"}
            if (capabilities.action_representations.get(self.benchmark) != self.representation
                    or len(horizons) != 1 or not 1 <= horizons[0] <= 1024
                    or not visual_features <= set(capabilities.required_features) <= admitted_features
                    or self.benchmark in capabilities.step_observation_updates):
                client.unload()
                raise ValueError(f"StarVLA must declare one {self.benchmark} native action horizon and supported inputs")
        profile = self.make_profile(horizons[0])
        identity = f"{self.model}:{RUNTIME}:{config.checkpoint_sha256}:{profile.model_dump_json()}"
        self.info = ActorInfo(
            model=self.model, runtime=RUNTIME, model_id=hashlib.sha256(identity.encode()).hexdigest(),
            checkpoint_sha256=config.checkpoint_sha256, model_config_name=self.suite, profile=profile,
        )

    def _client(self):
        return self._client_type(self.config.socket_path.expanduser(), timeout_s=self.config.request_timeout_s)

    def create_session(self):
        with self._lock:
            if self._closed or self._failed or self._active is not None:
                raise ServiceError("upstream_unavailable", "StarVLA requires an exclusive healthy policy",
                                   status=409, details={"accepted": False})
            episode = PolicyEpisode(uuid4().hex)
            try:
                with self._client() as client:
                    client.reset(self._episode_type(episode.episode_id, f"{self.benchmark}_single_task"))
            except Exception:
                self._failed = True
                raise
            self._active = episode
            return episode

    def predict_batch(self, requests):
        if len(requests) != 1:
            raise ValueError("The StarVLA backend admits one request at a time")
        work = requests[0]
        with self._lock:
            episode = self._active
            if self._closed or self._failed or episode is None or work.state is not episode:
                return [RuntimeError("The StarVLA episode is unavailable")]
            try:
                features = {
                    **{feature: decode_image(work.observation.images[name]) for name, feature in self.camera_features.items()},
                    "state.proprioception": np.asarray(work.observation.state, dtype=np.float32),
                    "language.instruction": work.observation.instruction,
                }
                # The pinned policy conditions on features; this step counts delivered observations.
                observation = self._observation_type(episode.episode_id, episode.observations, features)
                with self._client() as client:
                    actions = np.asarray(client.act(observation).actions, dtype=np.float32)
                if actions.ndim != 2 or actions.shape[1] != 7 or not np.isin(actions[:, -1], (-1, 1)).all():
                    raise ValueError(f"StarVLA returned an invalid {self.benchmark} native action chunk")
                prediction = NumericChunk(actions=actions.tolist())
                validate_prediction(prediction, self.info.profile)
            except Exception as error:
                self._failed = True
                return [error]
            episode.observations += 1
            return [prediction]

    def close_session(self, state):
        with self._lock:
            if state is not self._active or self._active is None:
                return
            episode, self._active = self._active, None
            try:
                with self._client() as client:
                    client.end_episode(episode.episode_id)
            except Exception:
                self._failed = True
                raise

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._active is not None:
                    self.close_session(self._active)
            finally:
                with self._client() as client:
                    client.unload()
