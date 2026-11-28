"""Run and validate a Direct prediction in the calling Agent's process."""
from dataclasses import dataclass

from evoab.services.actor.contracts import ActorObservation, NumericChunk, validate_observation, validate_prediction

from .backend import DirectBackend


@dataclass(frozen=True)
class DirectReply:
    prediction: NumericChunk
    sequence: int
    checkpoint_sha256: str


class LocalDirectActor:
    def __init__(self, model_preset, environment):
        self.backend = DirectBackend(model_preset, environment)
        self.info = self.backend.info
        self.sequence = 0

    def predict(self, observation: ActorObservation) -> DirectReply:
        validate_observation(observation, self.info.profile)
        prediction = self.backend.predict(observation)
        validate_prediction(prediction, self.info.profile)
        reply = DirectReply(prediction, self.sequence, self.info.checkpoint_sha256)
        self.sequence += 1
        return reply

    def close(self):
        self.backend.close()
