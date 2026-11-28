"""Transparent Environment Protocol v4 HTTP proxy with visual observation enhancement."""
from __future__ import annotations

import copy
import io
import threading
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image
from starlette.concurrency import run_in_threadpool

from evoab.artifacts.store import ArtifactStore
from evoab.services.hosting import HttpServer, create_app
from evoab.telemetry.logging import get_logger

from .config import ObservationPipelineConfig
from .contracts import FrameInput, VisualProcessor
from .pipeline import ObservationPipeline

logger = get_logger("observation.toolpackage.proxy")

_PASSTHROUGH_REQUEST_HEADERS = ("x-episode-id", "content-type", "traceparent")


class ObservationProxyService:
    """Protocol v4 reverse proxy that enhances snapshots and serves rewritten artifacts."""

    def __init__(
        self,
        upstream_url: str,
        pipeline: ObservationPipeline,
        *,
        archive_dir: str | Path | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self.upstream_url = upstream_url.rstrip("/")
        self.pipeline = pipeline
        self._timeout_s = float(timeout_s)
        self._http = httpx.Client(
            base_url=self.upstream_url,
            timeout=self._timeout_s,
            trust_env=False,
        )
        store_root = None if archive_dir is None else Path(archive_dir).expanduser().resolve() / "store"
        self.artifacts = ArtifactStore(store_root)
        self._info_lock = threading.RLock()
        self._info_cache: dict[str, Any] | None = None
        self._cache_lock = threading.RLock()
        self._enhanced_cache: dict[str, dict[str, Any]] = {}
        self._manifest_alias: dict[str, str] = {}
        self._snapshot_locks: dict[str, threading.Lock] = {}
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fn in (self._http.close, self.pipeline.close, self.artifacts.close):
            try:
                fn()
            except Exception as error:
                logger.warning("Observation proxy cleanup error: %s", error)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._enhanced_cache.clear()
            self._manifest_alias.clear()

    def status(self) -> dict[str, Any]:
        with self._cache_lock:
            cached = len(self._enhanced_cache)
        return {
            "upstream_url": self.upstream_url,
            "cached_snapshots": cached,
            "pipeline": self.pipeline.status(),
        }

    def _get_episode_context(self) -> tuple[str, str, list[list[float]] | None]:
        with self._info_lock:
            info = self._info_cache
        if info is None:
            try:
                response = self._http.get("/info")
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, dict):
                        with self._info_lock:
                            self._info_cache = data
                        info = data
            except Exception as error:
                logger.debug("Could not fetch upstream /info for observation context: %s", error)
        if not isinstance(info, dict):
            return "", "", None
        task = str(info.get("task") or "")
        meta = info.get("metadata") if isinstance(info.get("metadata"), dict) else {}
        env_meta = (
            info.get("environment", {}).get("metadata")
            if isinstance(info.get("environment"), dict) and isinstance(info["environment"].get("metadata"), dict)
            else {}
        )
        instruction = str(meta.get("instruction") or env_meta.get("instruction") or "")
        workspace_bounds = env_meta.get("workspace_bounds")
        return task, instruction, workspace_bounds

    def _snapshot_lock_for(self, snapshot_id: str) -> threading.Lock:
        with self._cache_lock:
            lock = self._snapshot_locks.get(snapshot_id)
            if lock is None:
                lock = threading.Lock()
                self._snapshot_locks[snapshot_id] = lock
            return lock

    def _fetch_upstream_artifact(self, artifact_id: str, headers: dict[str, str] | None = None) -> bytes:
        response = self._http.get(f"/artifacts/{artifact_id}", headers=headers or {})
        response.raise_for_status()
        return response.content

    def enhance_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Enhance camera images and optional metadata for one snapshot, cached by snapshot_id."""
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("cameras"), dict):
            return snapshot
        snapshot_id = snapshot.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            return snapshot
        record_index = snapshot.get("record_index")

        with self._cache_lock:
            cached = self._enhanced_cache.get(snapshot_id)
        if cached is not None:
            return _attach_record_index(cached, record_index)

        lock = self._snapshot_lock_for(snapshot_id)
        with lock:
            with self._cache_lock:
                cached = self._enhanced_cache.get(snapshot_id)
            if cached is not None:
                return _attach_record_index(cached, record_index)

            enhanced = self._compute_enhanced_snapshot(snapshot, headers=headers)
            with self._cache_lock:
                self._enhanced_cache[snapshot_id] = enhanced
                manifest = enhanced.get("manifest")
                if isinstance(manifest, dict) and isinstance(manifest.get("artifact_id"), str):
                    self._manifest_alias[manifest["artifact_id"]] = snapshot_id
            return _attach_record_index(enhanced, record_index)

    def _compute_enhanced_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        snapshot_id = str(snapshot["snapshot_id"])
        state_version = int(snapshot.get("state_version", 0))
        task, instruction, workspace_bounds = self._get_episode_context()

        enhanced = copy.deepcopy(snapshot)
        enhanced.pop("record_index", None)
        cameras = enhanced.get("cameras") or {}

        any_image_modified = False
        per_camera_annotations: dict[str, list[dict[str, Any]]] = {}
        per_camera_errors: dict[str, dict[str, str]] = {}

        for cam_name, cam_payload in cameras.items():
            if not isinstance(cam_payload, dict):
                continue
            if self.pipeline.config.cameras is not None and cam_name not in self.pipeline.config.cameras:
                continue
            rgb_ref = cam_payload.get("rgb")
            if not isinstance(rgb_ref, dict) or not isinstance(rgb_ref.get("artifact_id"), str):
                continue

            rgb_png = self._fetch_upstream_artifact(rgb_ref["artifact_id"], headers=headers)
            rgb_array = np.asarray(Image.open(io.BytesIO(rgb_png)).convert("RGB"), dtype=np.uint8)

            depth_array = None
            depth_ref = cam_payload.get("depth_m")
            if isinstance(depth_ref, dict) and isinstance(depth_ref.get("artifact_id"), str):
                try:
                    depth_bytes = self._fetch_upstream_artifact(depth_ref["artifact_id"], headers=headers)
                    depth_array = np.load(io.BytesIO(depth_bytes), allow_pickle=False)
                except Exception as error:
                    logger.warning("Could not decode depth for camera %s: %s", cam_name, error)

            intrinsic_raw = cam_payload.get("intrinsic_K")
            extrinsic_raw = cam_payload.get("T_world_from_optical")
            intrinsic_K = None if intrinsic_raw is None else np.asarray(intrinsic_raw, dtype=np.float64)
            t_world_opt = None if extrinsic_raw is None else np.asarray(extrinsic_raw, dtype=np.float64)

            frame = FrameInput(
                camera=cam_name,
                snapshot_id=snapshot_id,
                state_version=state_version,
                rgb=rgb_array,
                rgb_png=rgb_png,
                depth_m=depth_array,
                intrinsic_K=intrinsic_K,
                T_world_from_optical=t_world_opt,
                calibration=cam_payload.get("calibration"),
                task=task,
                instruction=instruction,
                workspace_bounds=workspace_bounds,
            )
            result = self.pipeline.process_frame(frame)
            if result.modified:
                new_ref = self.artifacts.put_bytes(result.enhanced_png, "image/png")
                cam_payload["rgb"] = new_ref
                any_image_modified = True
            if result.annotations:
                per_camera_annotations[cam_name] = [
                    ann.to_public_dict(index=idx + 1) for idx, ann in enumerate(result.annotations)
                ]
            if result.plugin_errors:
                per_camera_errors[cam_name] = dict(result.plugin_errors)

        metadata_modified = False
        if self.pipeline.config.inject_metadata and (
            per_camera_annotations or per_camera_errors or self.pipeline.get_targets()
        ):
            state = enhanced.get("state")
            if isinstance(state, dict):
                state_meta = state.get("metadata")
                if not isinstance(state_meta, dict):
                    state_meta = {}
                    state["metadata"] = state_meta
                visual_summary: dict[str, Any] = {
                    "targets": self.pipeline.get_targets(),
                    "cameras": per_camera_annotations,
                }
                if per_camera_errors:
                    visual_summary["errors"] = per_camera_errors
                state_meta["visual_annotations"] = visual_summary
                metadata_modified = True

        if any_image_modified or metadata_modified:
            manifest = {
                key: value
                for key, value in enhanced.items()
                if key not in ("snapshot_id", "manifest", "record_index")
            }
            new_manifest_ref = self.artifacts.put_json(manifest)
            enhanced["manifest"] = new_manifest_ref
            # Keep upstream snapshot_id so subsequent GET /snapshots/{snapshot_id} remains valid.
            enhanced["snapshot_id"] = snapshot_id

        return enhanced

    def read_local_artifact(self, artifact_id: str) -> tuple[bytes, str] | None:
        try:
            ref = self.artifacts.describe(artifact_id)
            data = self.artifacts.read_bytes(artifact_id)
            return data, str(ref.get("media_type") or "application/octet-stream")
        except (KeyError, ValueError):
            return None

    def resolve_snapshot_alias(self, snapshot_id: str) -> str:
        with self._cache_lock:
            return self._manifest_alias.get(snapshot_id, snapshot_id)

    def forward_request(
        self,
        method: str,
        path: str,
        *,
        query: str = "",
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        enhance_mode: str = "none",
    ) -> tuple[int, bytes, str]:
        target = f"{path}?{query}" if query else path
        response = self._http.request(
            method,
            target,
            headers=headers or {},
            content=body if body else None,
        )
        media_type = response.headers.get("content-type", "application/json")
        if response.status_code != 200:
            return response.status_code, response.content, media_type

        if path == "/info":
            try:
                data = response.json()
                if isinstance(data, dict):
                    with self._info_lock:
                        self._info_cache = data
            except ValueError:
                pass
            return response.status_code, response.content, media_type

        if enhance_mode == "snapshot":
            data = response.json()
            enhanced = self.enhance_snapshot(data, headers=headers)
            return 200, _json_bytes(enhanced), "application/json"

        if enhance_mode == "reply_snapshot":
            data = response.json()
            if isinstance(data, dict) and isinstance(data.get("snapshot"), dict):
                data["snapshot"] = self.enhance_snapshot(data["snapshot"], headers=headers)
                return 200, _json_bytes(data), "application/json"
            return response.status_code, response.content, media_type

        return response.status_code, response.content, media_type


def _attach_record_index(snapshot: dict[str, Any], record_index: Any) -> dict[str, Any]:
    copied = copy.deepcopy(snapshot)
    if record_index is not None:
        copied["record_index"] = record_index
    return copied


def _json_bytes(payload: Any) -> bytes:
    import json

    return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")


def _extract_forward_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name in _PASSTHROUGH_REQUEST_HEADERS:
        value = request.headers.get(name)
        if value is not None:
            headers[name] = value
    return headers


def build_proxy_router(service: ObservationProxyService) -> APIRouter:
    """Build a FastAPI router speaking full Environment Protocol v4 plus /ob/* controls."""
    router = APIRouter()

    async def _proxy(request: Request, path: str, *, enhance_mode: str = "none") -> Response:
        body = await request.body()
        headers = _extract_forward_headers(request)
        status, content, media_type = await run_in_threadpool(
            service.forward_request,
            request.method,
            path,
            query=request.url.query,
            headers=headers,
            body=body,
            enhance_mode=enhance_mode,
        )
        return Response(content=content, status_code=status, media_type=media_type)

    @router.get("/info")
    async def info(request: Request):
        return await _proxy(request, "/info")

    @router.get("/snapshots/latest")
    async def latest_snapshot(request: Request):
        return await _proxy(request, "/snapshots/latest", enhance_mode="snapshot")

    @router.get("/snapshots/{snapshot_id}")
    async def snapshot(snapshot_id: str, request: Request):
        resolved_id = service.resolve_snapshot_alias(snapshot_id)
        return await _proxy(request, f"/snapshots/{resolved_id}", enhance_mode="snapshot")

    @router.get("/records")
    async def records(request: Request):
        return await _proxy(request, "/records", enhance_mode="snapshot")

    @router.get("/artifacts/{artifact_id}")
    async def artifact(artifact_id: str, request: Request):
        local = await run_in_threadpool(service.read_local_artifact, artifact_id)
        if local is not None:
            data, media_type = local
            return Response(content=data, status_code=200, media_type=media_type)
        return await _proxy(request, f"/artifacts/{artifact_id}")

    @router.get("/requests/{request_id}")
    async def request_status(request_id: str, request: Request):
        mode = "reply_snapshot" if service.pipeline.config.enhance_step_snapshots else "none"
        return await _proxy(request, f"/requests/{request_id}", enhance_mode=mode)

    @router.get("/outcome")
    async def outcome(request: Request):
        return await _proxy(request, "/outcome")

    @router.post("/steps")
    async def steps(request: Request):
        mode = "reply_snapshot" if service.pipeline.config.enhance_step_snapshots else "none"
        return await _proxy(request, "/steps", enhance_mode=mode)

    @router.post("/previews")
    async def previews(request: Request):
        return await _proxy(request, "/previews")

    @router.post("/goal-regions")
    async def goal_regions(request: Request):
        return await _proxy(request, "/goal-regions")

    @router.post("/captures")
    async def captures(request: Request):
        return await _proxy(request, "/captures", enhance_mode="snapshot")

    @router.post("/finish")
    async def finish(request: Request):
        return await _proxy(request, "/finish")

    # Control and inspection endpoints scoped under /ob/ so they never collide with Protocol v4.
    @router.get("/ob/status")
    def ob_status():
        return JSONResponse(service.status())

    @router.get("/ob/targets")
    def ob_get_targets():
        return JSONResponse({"targets": service.pipeline.get_targets()})

    @router.post("/ob/targets")
    async def ob_set_targets(request: Request):
        try:
            body = await request.json()
        except ValueError as error:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {error}") from error
        if not isinstance(body, dict) or not isinstance(body.get("targets"), list):
            raise HTTPException(status_code=422, detail="Body must be a JSON object with a 'targets' list")
        try:
            updated = await run_in_threadpool(service.pipeline.set_targets, body["targets"])
        except Exception as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        service.clear_cache()
        return JSONResponse({"targets": [item.to_dict() for item in updated]})

    return router


class ObservationProxyServer:
    """Managed background or foreground HTTP server for the observation tool package."""

    def __init__(
        self,
        upstream_url: str,
        config: ObservationPipelineConfig,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        archive_dir: str | Path | None = None,
        processors: list[VisualProcessor] | None = None,
        default_sam3_url: str | None = None,
        default_foundationpose_url: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self.pipeline = ObservationPipeline(
            config,
            archive_dir=archive_dir,
            processors=processors,
            default_sam3_url=default_sam3_url,
            default_foundationpose_url=default_foundationpose_url,
        )
        self.service = ObservationProxyService(
            upstream_url,
            self.pipeline,
            archive_dir=archive_dir,
            timeout_s=timeout_s,
        )
        router = build_proxy_router(self.service)
        self.app = create_app(router)
        self._server = HttpServer(self.app, host=host, port=port)
        self._started = False
        self._closed = False

    @property
    def url(self) -> str:
        return self._server.url

    def start(self, *, timeout_s: float = 10.0) -> str:
        if self._closed:
            raise RuntimeError("ObservationProxyServer is closed")
        if not self._started:
            self._server.start_background(timeout_s=timeout_s)
            self._started = True
        return self.url

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._server.stop()
        finally:
            self.service.close()

    def __enter__(self) -> ObservationProxyServer:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
