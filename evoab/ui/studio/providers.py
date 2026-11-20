"""Model API providers configured for Studio launches.

Provider definitions live outside the repository (``EVOAB_STUDIO_PROVIDERS``,
default ``~/.config/evoab/providers.json``)::

    [{"id": "boden-pro", "label": "Boden PRO", "base_url": "https://host/v1",
      "key_file": "~/.config/eah/boden.env", "key_var": "BODEN_PRO_KEY",
      "profile": "omnirouter.chat_completions",
      "recommended": ["claude-opus-5-5"]}]

Keys are read on the server for model listing and for the launched process;
they are never returned to the browser.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

MODEL_CACHE_S = 300


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    base_url: str
    key_file: str
    key_var: str
    profile: str = "omnirouter.chat_completions"
    recommended: tuple[str, ...] = field(default_factory=tuple)

    def api_key(self) -> str:
        """Read the key from the provider's env-style file."""
        path = Path(os.path.expanduser(self.key_file))
        for line in path.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == self.key_var:
                return value.strip().strip("'\"")
        raise KeyError(f"{self.key_var} not found in {path}")


def providers_path() -> Path:
    return Path(os.environ.get("EVOAB_STUDIO_PROVIDERS") or Path.home() / ".config" / "evoab" / "providers.json")


def load_providers(path: Path | None = None) -> dict[str, Provider]:
    path = path or providers_path()
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    providers = {}
    for entry in entries:
        entry = dict(entry)
        entry["recommended"] = tuple(entry.get("recommended", ()))
        provider = Provider(**entry)
        providers[provider.id] = provider
    return providers


_model_cache: dict[str, tuple[float, list[str]]] = {}


def list_models(provider: Provider) -> list[str]:
    """Model ids the provider reports, cached briefly."""
    cached = _model_cache.get(provider.id)
    if cached and time.time() - cached[0] < MODEL_CACHE_S:
        return cached[1]
    response = httpx.get(
        provider.base_url.rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {provider.api_key()}"},
        timeout=20,
    )
    response.raise_for_status()
    models = sorted(str(m.get("id")) for m in response.json().get("data", []) if m.get("id"))
    _model_cache[provider.id] = (time.time(), models)
    return models
