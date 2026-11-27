"""Submit one verification request and await its post-write acknowledgement."""
from evoab.services.http import HttpTransport
from .contracts import Completion, VerificationJob


def submit_verification(url: str, job: VerificationJob, timeout_s: float) -> None:
    with HttpTransport(url, timeout_s=timeout_s, connect_timeout_s=min(10, timeout_s)) as transport:
        payload = transport.post_json("/verification/jobs", job.model_dump(mode="json"))
    receipt = Completion.model_validate(payload)
    if receipt.request_id != job.request_id:
        raise ValueError("Verifier acknowledged a different request")
