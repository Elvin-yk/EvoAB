"""Serve nanobot's Models view with the Studio agent's configuration."""
import gzip
import re
from functools import lru_cache, partial
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from evoab.agent.models import catalog

STATIC = Path(__file__).parent / "static"
MODEL_ASSETS = STATIC / "model-assets"
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}
_OPERATIONS = {
    "settings.model_configuration.create": "create_model_configuration",
    "settings.model_configuration.update": "update_model_configuration",
    "settings.model_configuration.delete": "delete_model_configuration",
    "settings.model_configuration.migrate": "migrate_model_configurations",
    "settings.model_call_order.update": "update_model_call_order",
    "settings.provider.create": "create_provider_settings",
    "settings.provider.update": "update_provider_settings",
    "settings.provider.oauth_login": "login_oauth_provider",
    "settings.provider.oauth_complete": "complete_oauth_provider",
    "settings.provider.oauth_logout": "logout_oauth_provider",
}


def model_settings_router():
    router = APIRouter(prefix="/model-ui")

    @lru_cache(maxsize=1)
    def oauth_flows():
        from nanobot.webui.settings_services import WebUIOAuthFlowRegistry
        return WebUIOAuthFlowRegistry()

    def close_flows():
        if oauth_flows.cache_info().currsize:
            oauth_flows().close()

    router.add_event_handler("shutdown", close_flows)

    @router.get("/", response_class=HTMLResponse)
    def index():
        try:
            assets = files("nanobot.web").joinpath("dist/assets")
        except ModuleNotFoundError:
            raise HTTPException(503, catalog.INSTALL_HINT)
        script = (MODEL_ASSETS / "entry.txt").read_text(encoding="utf-8").strip()
        styles = [next(assets.glob(pattern)).name for pattern in ("index-*.css", "SettingsView-*.css")]
        links = "".join(f'<link rel="stylesheet" href="assets/{name}">' for name in styles)
        return ('<!doctype html><html lang="zh-CN" class="dark"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1"><title>模型设置</title>'
                '<script>const theme=matchMedia("(prefers-color-scheme: dark)");'
                'function syncTheme(){document.documentElement.classList.toggle("dark",theme.matches)}'
                'syncTheme();theme.addEventListener("change",syncTheme);</script>'
                + links + '<link rel="stylesheet" href="/static/layout.css"><style>html,body,#root{height:100%;margin:0}#root{display:flex;flex-direction:column}'
                '.settings-feature-header{display:none}.settings-grid.settings-feature-page{padding-top:18px}</style></head><body><div id="root"></div>'
                f'<script type="module" src="bundle/{script}"></script></body></html>')

    @lru_cache(maxsize=2)
    def stylesheet_bytes(name: str) -> bytes:
        assets = files("nanobot.web").joinpath("dist/assets")
        if not name.endswith(".css") or not name.startswith(("index-", "SettingsView-")):
            raise HTTPException(404)
        path = assets.joinpath(name)
        if not path.is_file():
            raise HTTPException(404)
        return gzip.compress(path.read_text().replace("/assets/", "/model-ui/fonts/").encode(), mtime=0)

    @router.get("/assets/{name}")
    def stylesheet(name: str):
        return Response(stylesheet_bytes(name), media_type="text/css", headers={**IMMUTABLE, "Content-Encoding": "gzip"})

    @router.get("/fonts/{name}")
    def font(name: str):
        if not name.startswith("open-sans-") or not name.endswith(".woff2"):
            raise HTTPException(404)
        path = files("nanobot.web").joinpath("dist/assets", name)
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="font/woff2", headers=IMMUTABLE)

    @router.get("/bundle/{name}")
    def bundle(name: str):
        if not re.fullmatch(r"(?:(?:models|chunk)-[\w-]+\.js|asset-[\w-]+\.css)", name):
            raise HTTPException(404)
        path = MODEL_ASSETS / f"{name}.gz"
        if not path.is_file():
            raise HTTPException(404)
        media_type = "text/javascript" if name.endswith(".js") else "text/css"
        return FileResponse(path, media_type=media_type, headers={**IMMUTABLE, "Content-Encoding": "gzip"})

    @router.get("/settings")
    def settings():
        from nanobot.webui import settings_api
        catalog._activate()
        return settings_api.settings_payload(config_path=catalog.config_path())

    @router.get("/settings/nanobot-features")
    def features():
        from nanobot.webui.nanobot_features_api import nanobot_features_payload
        return nanobot_features_payload(config_path=catalog.config_path())

    @router.get("/settings/{kind}")
    def model_catalog(kind: str, request: Request):
        from nanobot.webui import settings_api
        readers = {"provider-models": settings_api.provider_models_payload,
                   "model-api": settings_api.model_api_resolution_payload}
        if kind not in readers:
            raise HTTPException(404)
        try:
            return catalog._call(readers[kind], dict(request.query_params))
        except catalog.CatalogError as error:
            raise HTTPException(error.status, str(error)) from error

    @router.post("/actions/{action}")
    def mutate(action: str, request: Request, payload: dict = Body(...)):
        from nanobot.optional_features import OptionalFeatureError
        from nanobot.webui import settings_api
        from nanobot.webui.nanobot_features_api import nanobot_features_action
        from nanobot.webui.settings_contracts import WebUISettingsError
        from nanobot.webui.settings_services import WebUISettingsConfig

        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(403)
        catalog._activate()
        config_path = catalog.config_path()
        if action == "settings.runtime_config.update":
            values = payload.get("values")
            if not isinstance(values, dict) or set(values) != {"network.proxy"}:
                raise HTTPException(400, "仅支持模型提供商的代理设置")
            operation = partial(settings_api.update_runtime_config_settings, values)
        elif action == "settings.feature.enable" and payload.get("name") in ("azure", "bedrock"):
            local = request.client and request.client.host in {"127.0.0.1", "::1"}
            allowed = local or catalog.load_config().tools.webui_allow_remote_package_install
            operation = partial(nanobot_features_action, "enable", {"name": [payload["name"]]}, allow_install=allowed)
        elif action in _OPERATIONS:
            handler = getattr(settings_api, _OPERATIONS[action])
            if ".oauth_" in action:
                handler = partial(handler, oauth_flows=oauth_flows())
            if action.endswith(".oauth_complete"):
                handler = partial(handler, authorization_response=payload.pop("authorization_response", None))
            def operation(*, config_path):
                return catalog._call(handler, payload)
        else:
            raise HTTPException(404)
        try:
            result = WebUISettingsConfig(config_path).run_serialized(
                lambda path: operation(config_path=path)
            )
            return result
        except (WebUISettingsError, catalog.CatalogError, OptionalFeatureError) as error:
            raise HTTPException(getattr(error, "status", 400), str(error)) from error

    return router
