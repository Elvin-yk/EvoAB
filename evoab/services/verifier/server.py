"""Verify finalized sessions with a model preset or a transport stub."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from fastapi import APIRouter

from evoab.artifacts.run import read_result_json, write_json_atomic
from evoab.services.batching import BatchQueueFull, BatchWorker, BatchWorkerClosed
from evoab.services.errors import ServiceError
from evoab.services.hosting import create_app, serve
from .contracts import Completion, VerificationJob


def _shared_file(path: Path, root: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not path.is_absolute() or not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("Evidence must be a file within the shared runs root")
    return resolved


def _read_trajectory(path: Path, root: Path, *, include_actions: bool) -> tuple[int, list[dict], list[dict]]:
    events = 0
    samples = []
    actions = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("Expected a JSON object for each trajectory event")
            events += 1
            if event.get("type") == "observation_sampled":
                manifest = read_result_json(_shared_file(Path(event["snapshot_ref"]), root))
                if manifest["sampled_step"] != event["native_step"]:
                    raise ValueError("Sample event and manifest disagree on native step")
                images = event["image_paths"]
                if not isinstance(images, dict) or not images or images.keys() != manifest["cameras"].keys():
                    raise ValueError("Sample camera paths do not match the snapshot manifest")
                paths = {name: _shared_file(Path(filename), root) for name, filename in images.items()}
                for path_to_image in paths.values():
                    with path_to_image.open("rb") as image:
                        if image.read(8) != b"\x89PNG\r\n\x1a\n":
                            raise ValueError("Sample image is not a PNG")
                samples.append({"event_seq": event["event_seq"], "snapshot_ref": event["snapshot_ref"],
                                "native_step": event["native_step"], "image_paths": paths})
            elif include_actions and event.get("type") in {"ToolCall", "ToolResult", "ToolExecution", "ErrorEvent"}:
                if type(event.get("event_seq")) is int:
                    entry = {key: event[key] for key in ("event_seq", "type", "tool_name", "call_id", "phase", "success")
                             if key in event}
                    for key in ("arguments", "output", "message", "error"):
                        if key in event:
                            entry[key] = json.dumps(event[key], ensure_ascii=False)[:512]
                    actions.append(entry)
    if not events:
        raise ValueError("Expected at least one trajectory event")
    return events, samples, actions


def process_job(job: VerificationJob, root: Path, *, model_preset: str | None = None,
                max_camera_groups: int = 4, images_per_request: int = 12) -> Completion:
    result_path = _shared_file(job.result_path, root)
    expected = VerificationJob.for_session(result_path.parent, job.request_id)
    if job != expected:
        raise ValueError("Expected canonical file paths from one session directory")
    result = read_result_json(result_path)
    request_id = str(job.request_id)
    verification = result.get("verification", {})
    if not isinstance(verification, dict) or verification.get("request_id") != request_id:
        raise ValueError("Verification request does not belong to this session result")
    if verification.get("status") == "completed":
        return Completion(request_id=job.request_id, status="completed")
    if verification.get("status") != "pending":
        raise ValueError("Session result is not waiting for verification")
    native = read_result_json(_shared_file(job.native_outcome_path, root))
    events, samples, actions = _read_trajectory(_shared_file(job.trajectory_path, root), root,
                                               include_actions=bool(model_preset))
    identity = result.get("episode_identity") or {}
    environment = identity.get("environment") or {}
    if environment.get("cameras") and environment.get("metadata", {}).get("image_recording_stride") and not samples:
        raise ValueError("Missing sample entries for a simulation with image recording enabled")
    if model_preset:
        from .model import evaluate
        verdict = evaluate(model_preset, result, native, actions, samples, max_camera_groups, images_per_request)
    else:
        verdict = {"backend": "stub", "result": "insufficient_evidence",
                   "evidence": [{"event_seq": s["event_seq"], "snapshot_ref": s["snapshot_ref"]} for s in samples],
                   "failure_reason": "Stub verifier did not evaluate task completion.",
                   "lesson": "Stub response. Task analysis is pending implementation."}
    result["verification"] = {
        **verdict, "request_id": request_id, "status": "completed",
        "events_read": events, "samples_read": len(samples),
    }
    write_json_atomic(result_path, result)
    return Completion(request_id=job.request_id, status="completed")


def build_app(runs_root: Path, *, model_preset: str | None = None, max_camera_groups: int = 4, images_per_request: int = 12):
    if min(max_camera_groups, images_per_request) < 1:
        raise ValueError("Per-request limits for camera groups and images must be positive")
    root = runs_root.expanduser().resolve()
    worker = BatchWorker(lambda jobs: [process_job(job, root, model_preset=model_preset,
                                                   max_camera_groups=max_camera_groups, images_per_request=images_per_request) for job in jobs],
                         max_batch_size=1, max_wait_ms=0, max_queue_size=32, name="verifier")
    router = APIRouter()

    @router.post("/verification/jobs")
    async def verify(job: VerificationJob) -> Completion:
        try:
            return await asyncio.shield(asyncio.wrap_future(worker.enqueue(job)))
        except (BatchQueueFull, BatchWorkerClosed) as error:
            raise ServiceError("verifier_unavailable", str(error), status=503) from error
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise ServiceError("verification_failed", str(error), status=422) from error

    return create_app(router, on_close=worker.close, max_request_bytes=64 * 1024)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", required=True, type=Path, help="Shared root of session and environment artifacts")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18780)
    parser.add_argument("--model-preset", help="EvoAB preset for a model API that accepts text and images")
    parser.add_argument("--max-camera-groups", type=int, default=4, help="Maximum recorded camera groups per API request")
    parser.add_argument("--images-per-request", type=int, default=12, help="Maximum camera images per API request")
    args = parser.parse_args()
    app = build_app(args.runs_root, model_preset=args.model_preset, max_camera_groups=args.max_camera_groups,
                    images_per_request=args.images_per_request)
    serve(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
