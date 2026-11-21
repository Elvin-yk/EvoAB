"""Studio HTTP server.

A thin HTTP layer over :class:`evoab.workbench.Workbench`; the ``evoab runs |
providers | benchmarks | usage | settings`` commands call the same methods. The
run monitor's read-only routes are mounted under ``/replay`` for live
thumbnails. The machine-readable API description is served at
``/openapi.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from evoab.ui.studio.monitor import RescanningCollection, live_progress
from evoab.ui.web.server import create_monitor_app, preview_image
from evoab.workbench import Workbench, WorkbenchError, default_runs_root

STATIC = Path(__file__).parent / "static"


def _digest(name: str) -> str:
    try:
        return hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


def create_app(runs_root: Path) -> FastAPI:
    app = FastAPI(title="EvoAB Studio", docs_url=None, redoc_url=None)
    bench = Workbench(runs_root)
    collection = RescanningCollection(bench.runs_root)
    app.mount("/replay", create_monitor_app(collection))
    app.mount("/static", StaticFiles(directory=STATIC), name="studio-static")

    @app.exception_handler(WorkbenchError)
    async def workbench_error(_: Request, error: WorkbenchError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    @app.get("/", include_in_schema=False)
    def index() -> HTMLResponse:
        # Version each asset URL by its content so browsers never run stale scripts.
        page = (STATIC / "index.html").read_text(encoding="utf-8")
        page = re.sub(r'/static/([\w.\-]+\.(?:js|css))"', lambda m: f'/static/{m.group(1)}?v={_digest(m.group(1))}"', page)
        return HTMLResponse(page, headers={"Cache-Control": "no-cache"})

    # -- configuration --------------------------------------------------------

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return bench.config()

    @app.put("/api/settings")
    def save_settings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return bench.update_settings(payload)

    # -- benchmarks -----------------------------------------------------------

    @app.get("/api/benchmarks")
    def benchmark_list() -> list[dict[str, Any]]:
        return bench.benchmarks()

    @app.get("/api/benchmarks/{integration}/{profile}/tasks")
    def benchmark_tasks(integration: str, profile: str) -> list[dict[str, Any]]:
        return bench.tasks(integration, profile)

    @app.get("/api/benchmarks/{integration}/{profile}/demos/{task:path}")
    def benchmark_demo(integration: str, profile: str, task: str) -> FileResponse:
        return FileResponse(bench.demo(integration, profile, task), media_type="video/mp4")

    @app.post("/api/benchmarks/{integration}/{profile}/setup")
    def benchmark_setup(integration: str, profile: str) -> dict[str, Any]:
        return bench.prepare(integration, profile)

    # -- providers ------------------------------------------------------------

    @app.get("/api/providers")
    def provider_list() -> list[dict[str, Any]]:
        return bench.providers()

    @app.put("/api/providers/{provider_id}")
    def provider_save(provider_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return bench.save_provider(provider_id, payload)

    @app.delete("/api/providers/{provider_id}")
    def provider_delete(provider_id: str) -> dict[str, Any]:
        return bench.delete_provider(provider_id)

    @app.post("/api/providers/{provider_id}/test")
    def provider_test(provider_id: str) -> dict[str, Any]:
        return bench.test_provider(provider_id)

    @app.get("/api/providers/{provider_id}/quota")
    def provider_quota(provider_id: str) -> dict[str, Any]:
        return bench.quota(provider_id)

    @app.get("/api/providers/{provider_id}/models")
    def provider_models(provider_id: str) -> dict[str, Any]:
        return bench.models(provider_id)

    @app.post("/api/providers/{provider_id}/probe")
    def provider_probe(provider_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return bench.probe(provider_id, str(payload.get("model") or ""))

    @app.get("/api/models")
    def model_overview() -> dict[str, Any]:
        return bench.models_overview()

    # -- runs -----------------------------------------------------------------

    @app.get("/api/runs")
    def run_list() -> dict[str, Any]:
        items = bench.runs(live=lambda monitor_id: live_progress(collection, monitor_id))
        return {"runs": items, "totals": bench.usage(items)}

    @app.post("/api/runs", status_code=201)
    def run_start(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return bench.launch(payload)

    @app.post("/api/runs/{launch_id}/stop")
    def run_stop(launch_id: str) -> dict[str, Any]:
        return bench.stop(launch_id)

    @app.get("/api/runs/{launch_id}/log")
    def run_log(launch_id: str) -> dict[str, Any]:
        return {"log": bench.log(launch_id)}

    # -- trace: step-by-step reconstruction of one run ------------------------

    @app.get("/api/trace/{run_id}")
    def run_trace(run_id: str) -> dict[str, Any]:
        return bench.trace(run_id)

    @app.get("/api/trace/{run_id}/rounds/{index}")
    def run_round(run_id: str, index: int) -> dict[str, Any]:
        return bench.round(run_id, index)

    @app.get("/api/trace/{run_id}/files/{relative:path}")
    def run_file(run_id: str, relative: str, width: int | None = None) -> Response:
        path = bench.run_file(run_id, relative)
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
    parser = argparse.ArgumentParser(prog="evoab studio", description="EvoAB Studio web client")
    parser.add_argument("--runs-root", type=Path, default=default_runs_root())
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args(argv)
    args.runs_root.mkdir(parents=True, exist_ok=True)
    print(f"Studio: http://{args.host}:{args.port}  (runs {args.runs_root})", flush=True)
    uvicorn.run(create_app(args.runs_root), host=args.host, port=args.port, log_level="warning")
