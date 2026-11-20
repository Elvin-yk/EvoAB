"""Studio HTTP server.

Pages (single-page app): new run, running, history, run detail, benchmarks,
settings. The run monitor's read-only routes are mounted under ``/replay`` and
Studio draws the replay from them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from evoab.ui.studio import benchmarks, jobs, providers, trace
from evoab.ui.studio.launcher import METHODS, REASONING_EFFORTS, LaunchRequest, launch, validate
from evoab.ui.studio.monitor import RescanningCollection, live_progress
from evoab.ui.studio.runs import STUDIO_DIR, load_launches, scan_runs, usage_totals
from evoab.ui.web.server import create_monitor_app, preview_image

STATIC = Path(__file__).parent / "static"
SETTING_KEYS = ("default_provider", "default_model", "default_effort", "default_method", "runtime_wheel")


def _digest(name: str) -> str:
    try:
        return hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


def settings_path() -> Path:
    return providers.config_dir() / "studio.json"


def load_settings() -> dict[str, Any]:
    try:
        value = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    return {key: value.get(key) for key in SETTING_KEYS}


def _provider(provider_id: str) -> providers.Provider:
    provider = providers.load_providers().get(provider_id)
    if provider is None:
        raise HTTPException(404, f"unknown provider {provider_id!r}")
    return provider


def create_app(runs_root: Path) -> FastAPI:
    app = FastAPI(title="EvoAB Studio", docs_url=None, redoc_url=None)
    collection = RescanningCollection(runs_root)
    app.mount("/replay", create_monitor_app(collection))
    app.mount("/static", StaticFiles(directory=STATIC), name="studio-static")

    @app.get("/")
    def index() -> FileResponse:
        # Version each asset URL by its content so browsers never run stale scripts.
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        page = re.sub(r'/static/([\w.\-]+\.(?:js|css))"', lambda m: f'/static/{m.group(1)}?v={_digest(m.group(1))}"', page)
        return HTMLResponse(page, headers={"Cache-Control": "no-cache"})

    # -- configuration --------------------------------------------------------

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return {
            "runs_root": str(runs_root),
            "config_dir": str(providers.config_dir()),
            "reasoning_efforts": REASONING_EFFORTS,
            "methods": METHODS,
            "wire_profiles": providers.agent_profiles(),
            "settings": load_settings(),
        }

    @app.put("/api/settings")
    def save_settings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        current = load_settings()
        current.update({k: payload[k] for k in SETTING_KEYS if k in payload})
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        return current

    # -- benchmarks -----------------------------------------------------------

    @app.get("/api/benchmarks")
    def benchmark_list() -> list[dict[str, Any]]:
        return benchmarks.overview(runs_root)

    @app.get("/api/benchmarks/{integration}/{profile}/tasks")
    def benchmark_tasks(integration: str, profile: str) -> list[dict[str, Any]]:
        return benchmarks.tasks_for(integration, profile)

    @app.get("/api/benchmarks/{integration}/{profile}/demos/{task:path}")
    def benchmark_demo(integration: str, profile: str, task: str) -> FileResponse:
        for runtime in benchmarks.prepared_runtimes():
            if runtime.integration == integration and runtime.profile == profile:
                if path := benchmarks.demo_file(runtime, task):
                    return FileResponse(path, media_type="video/mp4")
        raise HTTPException(404, "Expert preview is not installed")

    @app.post("/api/benchmarks/{integration}/{profile}/setup")
    def benchmark_setup(integration: str, profile: str) -> dict[str, Any]:
        try:
            pid = benchmarks.start_setup(runs_root, integration, profile, load_settings().get("runtime_wheel"))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return {"pid": pid}

    # -- providers ------------------------------------------------------------

    @app.get("/api/providers")
    def provider_list() -> list[dict[str, Any]]:
        return [providers.public_view(p) for p in providers.load_providers().values()]

    @app.put("/api/providers/{provider_id}")
    def provider_save(provider_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            provider = providers.upsert({**payload, "id": provider_id})
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return providers.public_view(provider)

    @app.delete("/api/providers/{provider_id}")
    def provider_delete(provider_id: str) -> dict[str, Any]:
        try:
            providers.delete(provider_id)
        except KeyError as error:
            raise HTTPException(404, f"unknown provider {provider_id!r}") from error
        return {"deleted": provider_id}

    @app.post("/api/providers/{provider_id}/test")
    def provider_test(provider_id: str) -> dict[str, Any]:
        return providers.test_connection(_provider(provider_id))

    @app.get("/api/providers/{provider_id}/quota")
    def provider_quota(provider_id: str) -> dict[str, Any]:
        try:
            return providers.token_quota(_provider(provider_id))
        except (ValueError, httpx.HTTPError) as error:
            raise HTTPException(502, "Could not retrieve provider quota") from error

    @app.get("/api/providers/{provider_id}/models")
    def provider_models(provider_id: str) -> dict[str, Any]:
        provider = _provider(provider_id)
        try:
            models = providers.list_models(provider)
        except Exception as error:  # noqa: BLE001 - report provider failures to the page
            raise HTTPException(502, f"model listing failed: {type(error).__name__}: {error}") from error
        return {"models": models, "capabilities": providers.capabilities(provider_id),
                "recommended": providers.public_view(provider)["recommended"]}

    @app.post("/api/providers/{provider_id}/probe")
    def provider_probe(provider_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        model = str(payload.get("model") or "")
        if not model:
            raise HTTPException(422, "model is required")
        return providers.probe_model(_provider(provider_id), model)

    # -- runs -----------------------------------------------------------------

    @app.get("/api/runs")
    def run_list() -> dict[str, Any]:
        items = scan_runs(runs_root)
        registered = providers.load_providers()
        for run in items:
            provider = registered.get(run.get("provider_id"))
            if provider is not None:
                run["provider"] = provider.label
            if run["status"] == "running" and run.get("monitor_id"):
                run["live"] = live_progress(collection, run["monitor_id"])
        return {"runs": items, "totals": usage_totals(items)}

    @app.post("/api/runs")
    def run_start(payload: dict[str, Any] = Body(...)) -> JSONResponse:
        try:
            request = LaunchRequest(**payload)
            task, provider = validate(request, providers.load_providers())
        except (TypeError, ValueError) as error:
            raise HTTPException(422, str(error)) from error
        launch_id = launch(request, task, provider, runs_root)
        return JSONResponse({"launch_id": launch_id, "request": asdict(request)}, status_code=201)

    def _launch(launch_id: str) -> dict[str, Any]:
        record = next((r for r in load_launches(runs_root) if r.get("launch_id") == launch_id), None)
        if record is None:
            raise HTTPException(404, f"unknown launch {launch_id!r}")
        return record

    @app.post("/api/runs/{launch_id}/stop")
    def run_stop(launch_id: str) -> dict[str, Any]:
        record = _launch(launch_id)
        if not jobs.is_running(record):
            raise HTTPException(409, "run is not running")
        try:
            jobs.interrupt(record)
        except OSError as error:
            raise HTTPException(409, f"could not stop: {error}") from error
        return {"stopped": launch_id}

    @app.get("/api/runs/{launch_id}/log")
    def run_log(launch_id: str) -> dict[str, Any]:
        _launch(launch_id)
        return {"log": jobs.log_tail(runs_root / STUDIO_DIR / f"{launch_id}.json", 200)}

    # -- trace: step-by-step reconstruction of one run ------------------------

    def _run_dir(run_id: str) -> Path:
        if not re.fullmatch(r"[\w.\-]+", run_id) or run_id == STUDIO_DIR:
            raise HTTPException(404, "unknown run")
        path = runs_root / run_id
        if not path.is_dir():
            raise HTTPException(404, "unknown run")
        return path

    @app.get("/api/trace/{run_id}")
    def run_trace(run_id: str) -> dict[str, Any]:
        return trace.build_trace(_run_dir(run_id))

    @app.get("/api/trace/{run_id}/rounds/{index}")
    def run_round(run_id: str, index: int) -> dict[str, Any]:
        try:
            return trace.round_detail(_run_dir(run_id), index)
        except IndexError as error:
            raise HTTPException(404, "unknown round") from error

    @app.get("/api/trace/{run_id}/files/{relative:path}")
    def run_file(run_id: str, relative: str, width: int | None = None) -> Response:
        try:
            path = trace.run_file(_run_dir(run_id), relative)
        except (PermissionError, FileNotFoundError) as error:
            raise HTTPException(404, "file unavailable") from error
        headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}
        if width is not None and path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            if width not in (320, 960):
                raise HTTPException(400, "unsupported preview size")
            return Response(preview_image(str(path), path.stat().st_mtime_ns, width),
                            media_type="image/jpeg", headers=headers)
        if path.suffix in (".md", ".json"):
            headers["Cache-Control"] = "no-cache"
            return FileResponse(path, media_type="text/plain; charset=utf-8", headers=headers)
        return FileResponse(path, headers=headers)

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evoab.ui.studio", description="EvoAB Studio")
    parser.add_argument("--runs-root", type=Path, default=Path.home() / "evoab-runs")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args(argv)
    args.runs_root.mkdir(parents=True, exist_ok=True)
    print(f"Studio: http://{args.host}:{args.port}  (runs {args.runs_root})", flush=True)
    uvicorn.run(create_app(args.runs_root), host=args.host, port=args.port, log_level="warning")
