"""Run the Direct policy in the calling Agent's process using the actor contract."""
from uuid import uuid4

from evoab.services.actor.contracts import ActorObservation, CreateSession, PredictReply, PredictRequest, SessionInfo
from evoab.services.actor.service import ActorService
from evoab.services.errors import ServiceError

from .backend import DirectBackend


class LocalDirectActor:
    def __init__(self, model_preset, environment):
        self.service = ActorService(DirectBackend(model_preset, environment), max_batch_size=1)
        self.info = self.service.info
        self.session: SessionInfo | None = None

    def predict(self, observation: ActorObservation) -> PredictReply:
        if self.session is None:
            self.session = self.service.create_session(CreateSession(
                model_id=self.info.model_id, profile=self.info.profile.name))
        try:
            reply = self.service.predict(PredictRequest(
                session_id=self.session.session_id, request_id=uuid4().hex,
                sequence=self.session.next_sequence, observation=observation))
        except ServiceError as exc:
            if exc.code == "inference_failed":
                self.service.close_session(self.session.session_id)
                self.session = None
            raise
        self.session.next_sequence += 1
        return reply

    def close(self):
        self.service.close()
