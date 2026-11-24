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

from evoab.ui.studio.model_settings import model_settings_router
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
    app.include_router(model_settings_router())
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

    @app.get("/api/methods")
    def method_list(integration: str | None = None) -> list[dict[str, Any]]:
        return bench.methods(integration)

    @app.get("/api/methods/{method_id}")
    def method_detail(method_id: str) -> dict[str, Any]:
        return bench.method(method_id)

    @app.get("/api/components")
    def components(integration: str | None = None) -> dict[str, Any]:
        return bench.components(integration)

    @app.get("/api/trace/{run_id}/graph")
    def run_graph(run_id: str) -> dict[str, Any]:
        return bench.run_graph(run_id)

    @app.put("/api/settings")
    def save_settings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return bench.update_settings(payload)

    # -- benchmarks -----------------------------------------------------------

    @app.get("/api/benchmarks")
    def benchmark_list() -> list[dict[str, Any]]:
        return bench.benchmarks()

    @app.get("/api/benchmarks/{integration}/team-tools")
    def team_tools(integration: str) -> dict:
        return bench.team_tools(integration)

    @app.post("/api/benchmarks/{integration}/team-preview")
    def team_preview(integration: str, payload: dict = Body(...), graph: bool = False) -> dict:
        return bench.team_preview(integration, payload, graph=graph)

    @app.get("/api/benchmarks/{integration}/tools")
    def benchmark_tools(integration: str, method: str, agent: str) -> dict[str, list[str]]:
        return bench.tool_groups(integration, method, agent)

    @app.get("/api/benchmarks/{integration}/{profile}/tasks")
    def benchmark_tasks(integration: str, profile: str) -> list[dict[str, Any]]:
        return bench.tasks(integration, profile)

    @app.get("/api/benchmarks/{integration}/{profile}/demos/{task:path}")
    def benchmark_demo(integration: str, profile: str, task: str) -> FileResponse:
        return FileResponse(bench.demo(integration, profile, task), media_type="video/mp4")

    @app.get("/api/system/check")
    def system_check() -> dict[str, Any]:
        return bench.system_check()

    @app.get("/api/benchmarks/image-imports")
    def image_imports() -> dict[str, Any]:
        return bench.image_imports()

    @app.post("/api/benchmarks/{integration}/{profile}/setup")
    def benchmark_setup(integration: str, profile: str, container: bool = False,
                        accept_license: str | None = None, image_archive: str | None = None) -> dict[str, Any]:
        return bench.prepare(integration, profile, container=container, accept_license=accept_license, image_archive=image_archive)

    @app.delete("/api/benchmarks/{integration}/{profile}/setup")
    def benchmark_uninstall(integration: str, profile: str) -> dict[str, Any]:
        return bench.uninstall(integration, profile)

    # -- models: providers, presets, usage ------------------------------------

    @app.get("/api/models")
    def model_settings(agent: str | None = None) -> dict[str, Any]:
        return bench.model_settings(agent)

    @app.post("/api/models/presets", status_code=201)
    def preset_create(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return bench.save_preset(payload)

    @app.put("/api/models/presets/{name}")
    def preset_update(name: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return bench.save_preset(payload, existing=name)

    @app.delete("/api/models/presets/{name}")
    def preset_delete(name: str) -> dict[str, Any]:
        return bench.delete_preset(name)

    @app.post("/api/models/providers", status_code=201)
    def provider_create(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return bench.save_provider(payload)

    @app.put("/api/models/providers/{name}")
    def provider_update(name: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return bench.save_provider(payload, existing=name)

    @app.delete("/api/models/providers/{name}")
    def provider_delete(name: str) -> dict[str, Any]:
        return bench.delete_provider(name)

    @app.get("/api/models/providers/{name}/models")
    def provider_model_list(name: str, refresh: bool = False) -> dict[str, Any]:
        return bench.provider_models(name, refresh)

    @app.get("/api/usage")
    def model_usage() -> dict[str, Any]:
        return bench.model_usage()

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
