"""Run the Direct policy in the calling Agent's process using the actor contract."""
from uuid import uuid4

from evoab.services.actor.contracts import ActorObservation, CreateSession, PredictReply, PredictRequest
from evoab.services.actor.service import ActorService

from .backend import DirectBackend


class LocalDirectActor:
    def __init__(self, model_preset, environment):
        self.service = ActorService(DirectBackend(model_preset, environment), max_batch_size=1)
        self.info = self.service.info
        self.sequence = 0
        self.session = self.service.create_session(CreateSession(
            model_id=self.info.model_id, profile=self.info.profile.name))

    def predict(self, observation: ActorObservation) -> PredictReply:
        reply = self.service.predict(PredictRequest(
            session_id=self.session.session_id, request_id=uuid4().hex,
            sequence=self.sequence, observation=observation))
        self.sequence += 1
        return reply

    def close(self):
        self.service.close()
