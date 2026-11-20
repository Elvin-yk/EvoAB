"""Studio HTTP server: catalog, providers, launches and run summaries."""

from __future__ import annotations

import argparse
import os
import signal
from dataclasses import asdict
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from evoab.ui.studio.catalog import catalog_payload, load_runtimes
from evoab.ui.studio.monitor import start_monitor
from evoab.ui.studio.launcher import (
    ACTION_PROFILES,
    REASONING_EFFORTS,
    LaunchRequest,
    launch,
    validate,
)
from evoab.ui.studio.providers import load_providers, list_models
from evoab.ui.studio.runs import load_launches, scan_runs, usage_totals

STATIC = Path(__file__).parent / "static"


def create_app(runs_root: Path, monitor_url: str | None) -> FastAPI:
    app = FastAPI(title="EvoAB Studio")
    app.mount("/static", StaticFiles(directory=STATIC), name="studio-static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return {
            "runs_root": str(runs_root),
            "monitor_url": monitor_url,
            "reasoning_efforts": REASONING_EFFORTS,
            "action_profiles": ACTION_PROFILES,
        }

    @app.get("/api/catalog")
    def catalog() -> list[dict[str, Any]]:
        return catalog_payload(load_runtimes())

    @app.get("/api/providers")
    def providers() -> list[dict[str, Any]]:
        return [
            {"id": p.id, "label": p.label, "profile": p.profile, "recommended": list(p.recommended)}
            for p in load_providers().values()
        ]

    @app.get("/api/providers/{provider_id}/models")
    def models(provider_id: str) -> dict[str, Any]:
        provider = load_providers().get(provider_id)
        if provider is None:
            raise HTTPException(404, f"unknown provider {provider_id!r}")
        try:
            return {"models": list_models(provider)}
        except Exception as error:  # noqa: BLE001 - report provider failures to the page
            raise HTTPException(502, f"model listing failed: {type(error).__name__}: {error}") from error

    @app.get("/api/runs")
    def runs() -> dict[str, Any]:
        items = scan_runs(runs_root)
        return {"runs": items, "totals": usage_totals(items)}

    @app.post("/api/runs")
    def start(payload: dict[str, Any] = Body(...)) -> JSONResponse:
        try:
            request = LaunchRequest(**payload)
            runtime, provider = validate(request, load_runtimes(), load_providers())
        except (TypeError, ValueError) as error:
            raise HTTPException(422, str(error)) from error
        launch_id = launch(request, runtime, provider, runs_root)
        return JSONResponse({"launch_id": launch_id, "request": asdict(request)}, status_code=201)

    @app.post("/api/runs/{launch_id}/stop")
    def stop(launch_id: str) -> dict[str, Any]:
        record = next((r for r in load_launches(runs_root) if r.get("launch_id") == launch_id), None)
        if record is None:
            raise HTTPException(404, f"unknown launch {launch_id!r}")
        try:
            # The run owns its own process group; SIGINT lets the CLI clean up services.
            os.killpg(int(record["pid"]), signal.SIGINT)
        except (OSError, ValueError) as error:
            raise HTTPException(409, f"not running: {error}") from error
        return {"stopped": launch_id}

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evoab.ui.studio", description="EvoAB Studio")
    parser.add_argument("--runs-root", type=Path, default=Path.home() / "evoab-runs")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--monitor-port", type=int, default=8771, help="Port of the embedded replay monitor")
    parser.add_argument(
        "--monitor-url",
        default=None,
        help="Monitor URL as the browser reaches it (default: http://<host>:<monitor-port>)",
    )
    args = parser.parse_args(argv)
    args.runs_root.mkdir(parents=True, exist_ok=True)
    start_monitor(args.runs_root, args.host, args.monitor_port)
    monitor_url = args.monitor_url or f"http://{args.host}:{args.monitor_port}"
    print(f"Studio: http://{args.host}:{args.port}  (monitor {monitor_url}, runs {args.runs_root})", flush=True)
    uvicorn.run(create_app(args.runs_root, monitor_url), host=args.host, port=args.port, log_level="warning")
