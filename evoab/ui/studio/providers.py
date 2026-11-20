"""Model API providers: definitions, keys, connection tests and model probes.

Everything lives outside the repository under the Studio config directory
(``EVOAB_STUDIO_CONFIG``, default ``~/.config/evoab``):

``providers.json``
    ``[{"id", "label", "base_url", "profile", "recommended"?, "key_file"?, "key_var"?}]``
``keys.env``
    ``EVOAB_KEY_<ID>=...`` for keys entered in Studio (mode 600). A provider may
    instead point ``key_file``/``key_var`` at an existing env-style file.
``provider_state.json``
    Last connection test per provider and probed model capabilities.

Keys are read on the server only. The browser sees a masked form and whether
a key is configured, never the key itself.
"""

from __future__ import annotations

import base64
import json
import os
import re
import struct
import threading
import time
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from evoab.provider.registry import list_provider_profiles

BODEN_API_BASE = "https://apiroute.bodenai.net/v1"
MODEL_CACHE_S = 300
DEFAULT_PROFILE = "omnirouter.chat_completions"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_state_lock = threading.Lock()


def config_dir() -> Path:
    return Path(os.environ.get("EVOAB_STUDIO_CONFIG") or Path.home() / ".config" / "evoab")


def providers_path() -> Path:
    return Path(os.environ.get("EVOAB_STUDIO_PROVIDERS") or config_dir() / "providers.json")


def keys_path() -> Path:
    return config_dir() / "keys.env"


def state_path() -> Path:
    return config_dir() / "provider_state.json"


def agent_profiles() -> tuple[str, ...]:
    """Wire profiles usable by the Codex agent (Responses clients)."""
    return list_provider_profiles(client_protocol="responses")


@dataclass
class Provider:
    id: str
    label: str
    base_url: str
    profile: str = DEFAULT_PROFILE
    recommended: list[str] = field(default_factory=list)
    key_file: str | None = None
    key_var: str | None = None

    def key_location(self) -> tuple[Path, str]:
        if self.key_file and self.key_var:
            return Path(os.path.expanduser(self.key_file)), self.key_var
        return keys_path(), "EVOAB_KEY_" + self.id.upper().replace("-", "_")

    def api_key(self) -> str | None:
        path, var = self.key_location()
        return _read_env(path).get(var) or None


def _read_env(path: Path) -> dict[str, str]:
    values = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        name, sep, value = line.partition("=")
        if sep and not name.startswith("#"):
            values[name.strip()] = value.strip().strip("'\"")
    return values


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _set_env_value(path: Path, var: str, value: str) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    kept = [line for line in lines if line.strip().removeprefix("export ").partition("=")[0].strip() != var]
    _write_private(path, "\n".join([*kept, f"{var}={value}"]) + "\n")


def mask(key: str | None) -> str | None:
    if not key:
        return None
    if len(key) <= 12:
        return "•" * 8
    return f"{key[:4]}…{key[-4:]}"


# -- definitions --------------------------------------------------------------


def load_providers() -> dict[str, Provider]:
    try:
        entries = json.loads(providers_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    providers = {}
    for entry in entries if isinstance(entries, list) else []:
        known = {k: entry[k] for k in Provider.__dataclass_fields__ if k in entry}
        known["recommended"] = list(known.get("recommended") or [])
        provider = Provider(**known)
        providers[provider.id] = provider
    return providers


def _save_providers(providers: dict[str, Provider]) -> None:
    entries = []
    for provider in providers.values():
        entry = {k: v for k, v in asdict(provider).items() if v not in (None, [])}
        entries.append(entry)
    _write_private(providers_path(), json.dumps(entries, indent=2, ensure_ascii=False) + "\n")


def upsert(payload: dict[str, Any]) -> Provider:
    """Create or update a provider. An empty ``api_key`` keeps the current key."""
    provider_id = str(payload.get("id") or "").strip().lower()
    if not _ID.match(provider_id):
        raise ValueError("id must be 1-40 lowercase letters, digits or '-'")
    base_url = str(payload.get("base_url") or "").strip().rstrip("/")
    if not re.match(r"^https?://", base_url):
        raise ValueError("API address must start with http:// or https://")
    profile = str(payload.get("profile") or DEFAULT_PROFILE)
    if profile not in agent_profiles():
        raise ValueError(f"unknown wire profile {profile!r}")
    providers = load_providers()
    existing = providers.get(provider_id)
    provider = Provider(
        id=provider_id,
        label=str(payload.get("label") or provider_id).strip(),
        base_url=base_url,
        profile=profile,
        recommended=list(existing.recommended) if existing else [],
        key_file=existing.key_file if existing else None,
        key_var=existing.key_var if existing else None,
    )
    key = str(payload.get("api_key") or "").strip()
    if key:
        path, var = provider.key_location()
        _set_env_value(path, var, key)
    providers[provider_id] = provider
    _save_providers(providers)
    _model_cache.pop(provider_id, None)
    return provider


def delete(provider_id: str) -> None:
    providers = load_providers()
    provider = providers.pop(provider_id, None)
    if provider is None:
        raise KeyError(provider_id)
    _save_providers(providers)
    if not provider.key_file:  # only remove keys Studio itself stored
        path, var = provider.key_location()
        values = _read_env(path)
        if var in values:
            _write_private(path, "".join(f"{k}={v}\n" for k, v in values.items() if k != var))
    with _state_lock:
        state = _load_state()
        state.pop(provider_id, None)
        _save_state(state)


# -- connection state and capabilities ----------------------------------------


def _load_state() -> dict[str, Any]:
    try:
        value = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    _write_private(state_path(), json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def _update_state(provider_id: str, section: str, key: str | None, value: Any) -> None:
    with _state_lock:
        state = _load_state()
        entry = state.setdefault(provider_id, {})
        if key is None:
            entry[section] = value
        else:
            entry.setdefault(section, {})[key] = value
        _save_state(state)


def capabilities(provider_id: str) -> dict[str, Any]:
    return _load_state().get(provider_id, {}).get("models", {})


def public_view(provider: Provider) -> dict[str, Any]:
    key = provider.api_key()
    state = _load_state().get(provider.id, {})
    caps = state.get("models", {})
    verified = sorted(m for m, c in caps.items() if c.get("vision") and c.get("tools"))
    return {
        "id": provider.id,
        "label": provider.label,
        "base_url": provider.base_url,
        "profile": provider.profile,
        "key_configured": bool(key),
        "quota_supported": provider.base_url.rstrip("/") == BODEN_API_BASE,
        "key_masked": mask(key),
        "key_source": "file" if provider.key_file else "studio",
        "connection": state.get("connection"),
        "recommended": verified or provider.recommended,
        "probed_models": len(caps),
    }


_model_cache: dict[str, tuple[float, list[str]]] = {}


def _headers(provider: Provider) -> dict[str, str]:
    key = provider.api_key()
    if not key:
        raise ValueError("no API key configured")
    return {"Authorization": f"Bearer {key}"}


def token_quota(provider: Provider) -> dict[str, Any]:
    """Return Boden's quota for the configured key in provider units."""
    if provider.base_url.rstrip("/") != BODEN_API_BASE:
        raise ValueError("Quota lookup is unavailable for this provider")
    response = httpx.get("https://apiroute.bodenai.net/api/usage/token/",
                         headers=_headers(provider), timeout=10)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data")
    if not isinstance(data, dict) or type(data.get("unlimited_quota")) is not bool:
        raise ValueError("Provider returned no token quota")
    remaining = data.get("total_available")
    if not data["unlimited_quota"] and type(remaining) not in (int, float):
        raise ValueError("Provider returned no remaining balance")
    return {"unlimited": data["unlimited_quota"], "remaining": None if data["unlimited_quota"] else remaining}


def list_models(provider: Provider, refresh: bool = False) -> list[str]:
    """Model ids the provider reports (``GET /models``), cached briefly."""
    cached = _model_cache.get(provider.id)
    if cached and not refresh and time.time() - cached[0] < MODEL_CACHE_S:
        return cached[1]
    response = httpx.get(provider.base_url + "/models", headers=_headers(provider), timeout=20)
    response.raise_for_status()
    models = sorted(str(m.get("id")) for m in response.json().get("data", []) if m.get("id"))
    _model_cache[provider.id] = (time.time(), models)
    return models


def test_connection(provider: Provider) -> dict[str, Any]:
    started = time.time()
    try:
        models = list_models(provider, refresh=True)
        result = {"ok": True, "model_count": len(models), "error": None}
    except Exception as error:  # noqa: BLE001 - shown to the user
        result = {"ok": False, "model_count": 0, "error": f"{type(error).__name__}: {error}"[:300]}
    result.update(checked_at=time.time(), latency_s=round(time.time() - started, 2))
    _update_state(provider.id, "connection", None, result)
    return result


def _png(width: int, height: int, inside, color: tuple[int, int, int]) -> str:
    """A small white PNG with one colored shape, as a data URL."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(color if inside(x, y) else (255, 255, 255))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(raw).decode()


_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_eef_actions",
        "description": "Emit 3-15 absolute end-effector actions.",
        "parameters": {
            "type": "object",
            "properties": {
                "seen": {"type": "string"},
                "actions": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
            },
            "required": ["seen", "actions"],
        },
    },
}


def probe_model(provider: Provider, model: str) -> dict[str, Any]:
    """Check that ``model`` sees two images and returns a structured tool call.

    This is what a robot agent needs: image input plus function calling. The
    model must name a red square and a blue circle and emit three actions.
    """
    red_box = _png(192, 108, lambda x, y: 70 <= x <= 120 and 30 <= y <= 80, (220, 30, 30))
    blue_disc = _png(192, 108, lambda x, y: (x - 96) ** 2 + (y - 54) ** 2 <= 26 ** 2, (30, 60, 220))
    body = {
        "model": model,
        "tools": [_PROBE_TOOL],
        "tool_choice": {"type": "function", "function": {"name": "emit_eef_actions"}},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Name the color and shape in each image in 'seen', then output 3 EEF actions "
                                     "[x,y,z,qw,qx,qy,qz,g] descending from z=0.3 to 0.25."},
            {"type": "image_url", "image_url": {"url": red_box}},
            {"type": "image_url", "image_url": {"url": blue_disc}},
        ]}],
    }
    started = time.time()
    result: dict[str, Any] = {"vision": False, "tools": False, "note": ""}
    try:
        response = httpx.post(provider.base_url + "/chat/completions", json=body,
                              headers=_headers(provider), timeout=180)
        if response.status_code != 200:
            result["note"] = f"HTTP {response.status_code}: {response.text[:160]}"
        else:
            message = (response.json().get("choices") or [{}])[0].get("message") or {}
            calls = message.get("tool_calls") or []
            if not calls:
                result["note"] = "no tool call returned"
            else:
                args = json.loads(calls[0]["function"]["arguments"] or "{}")
                seen = str(args.get("seen", ""))
                result["tools"] = len(args.get("actions") or []) >= 3
                result["vision"] = "red" in seen.lower() and "blue" in seen.lower()
                result["note"] = seen[:160]
    except Exception as error:  # noqa: BLE001 - shown to the user
        result["note"] = f"{type(error).__name__}: {error}"[:200]
    result.update(latency_s=round(time.time() - started, 1), checked_at=time.time())
    _update_state(provider.id, "models", model, result)
    return result
