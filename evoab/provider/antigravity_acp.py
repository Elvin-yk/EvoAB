"""Google Antigravity integration via the official ``agy acp`` stdio server.

This module isolates EvoAB's Antigravity auth/session state under
``~/.config/evoab/antigravity-acp`` (never touching ``~/.t3``), strips inherited
IDE/T3 environment variables when spawning ``agy``, exposes Google Antigravity
as an OAuth provider in Studio and ``evoab models``, and implements an
``LLMProvider`` backed by ``agy acp`` plus an ephemeral local MCP tool-capture
bridge.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import re
import select
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Any
from uuid import uuid4

from nanobot.providers.base import LLMProvider, LLMResponse, LLMUsage, ToolCallRequest

from evoab.services.mcp.http import HttpMcpServer

PROVIDER_NAME = "antigravity"
PROVIDER_LABEL = "Google Antigravity"
DEFAULT_API_BASE = "https://daily-cloudcode-pa.googleapis.com"
_STRIP_ENV_PREFIXES = ("ANTIGRAVITY_", "AGY_", "GEMINI_", "T3_", "T3CODE_")
_STRIP_ENV_EXACT = frozenset({"TMPDIR", "SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"})
_EFFORT_SUFFIX_RE = re.compile(r"^(?P<base>.+)-(?P<effort>low|medium|high|xhigh|max)$")
_AUTH_URL_RE = re.compile(r"https://accounts\.google\.com/\S+")
_DATA_IMAGE_RE = re.compile(r"^data:(image/[\w.+-]+);base64,(.+)$", re.DOTALL)

_CACHE_LOCK = threading.Lock()
_STATUS_CACHE: tuple[tuple[tuple[str, int, int], ...], dict[str, Any]] | None = None
_MODELS_CACHE: tuple[list[dict[str, Any]], set[str]] | None = None


def acp_home() -> Path:
    """Return EvoAB's isolated Antigravity ACP home directory."""
    path = Path(
        os.environ.get("EVOAB_ANTIGRAVITY_HOME")
        or Path.home() / ".config" / "evoab" / "antigravity-acp"
    ).expanduser()
    return path


def _ensure_acp_home(home: Path | None = None) -> Path:
    path = home or acp_home()
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def find_agy_binary() -> str | None:
    """Locate an executable ``agy`` CLI binary on this host."""
    candidates: list[str] = []
    explicit = os.environ.get("EVOAB_AGY_BINARY")
    if explicit:
        candidates.append(str(Path(explicit).expanduser()))
    which_agy = shutil.which("agy")
    if which_agy:
        candidates.append(which_agy)
    local_agy = Path.home() / ".local" / "bin" / "agy"
    candidates.append(str(local_agy))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def clean_acp_env(home: Path | None = None) -> dict[str, str]:
    """Build a subprocess environment stripped of inherited T3/Antigravity vars."""
    target_home = _ensure_acp_home(home)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_STRIP_ENV_PREFIXES) and key not in _STRIP_ENV_EXACT
    }
    env.update({
        "HOME": str(target_home),
        "AGY_ACP_FORCE_FILE_STORAGE": "1",
        "AGY_CLI_DISABLE_AUTO_UPDATE": "1",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    })
    return env


def invalidate_caches() -> None:
    """Clear cached auth status and model catalog results."""
    global _STATUS_CACHE, _MODELS_CACHE
    with _CACHE_LOCK:
        _STATUS_CACHE = None
        _MODELS_CACHE = None


_VOLATILE_STATE_DIRS = frozenset({"builtin", "log", "logs", "conversations", "implicit", "updater"})
_VOLATILE_STATE_FILES = frozenset({
    "cli.log",
    "last_check.timestamp",
    "conversation_metadata.json",
    "onboarding.json",
    "jetbox_summaries_proto.pb",
})


def _state_signature(home: Path) -> tuple[tuple[str, int, int], ...]:
    if not home.is_dir():
        return ()
    entries: list[tuple[str, int, int]] = []
    for path in sorted(home.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(home)
        if any(part in _VOLATILE_STATE_DIRS for part in rel.parts):
            continue
        if rel.name in _VOLATILE_STATE_FILES or rel.name.endswith((".log", ".db", ".db-shm", ".db-wal")):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((str(rel), stat.st_mtime_ns, stat.st_size))
    return tuple(entries)


def oauth_status() -> dict[str, Any]:
    """Check whether EvoAB's isolated Antigravity ACP directory is authenticated."""
    global _STATUS_CACHE
    binary = find_agy_binary()
    if binary is None:
        return {
            "configured": False,
            "account": None,
            "expires_at": None,
            "login_supported": False,
            "binary_available": False,
        }
    home = acp_home()
    signature = _state_signature(home)
    with _CACHE_LOCK:
        if _STATUS_CACHE is not None and _STATUS_CACHE[0] == signature:
            return dict(_STATUS_CACHE[1])

    configured = False
    if signature:
        try:
            proc = subprocess.run(
                [binary, "models"],
                env=clean_acp_env(home),
                capture_output=True,
                text=True,
                cwd="/",
                timeout=12,
            )
            configured = proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            configured = False

    post_signature = _state_signature(home)
    status = {
        "configured": configured,
        "account": "Google Account (agy ACP)" if configured else None,
        "expires_at": None,
        "login_supported": True,
        "binary_available": True,
    }
    with _CACHE_LOCK:
        _STATUS_CACHE = (post_signature, dict(status))
    return status


def sync_config_provider(config_path: Path, *, enabled: bool) -> None:
    """Synchronize the dynamic ``antigravity`` entry in nanobot's config.json."""
    from nanobot.config.loader import load_config, save_config
    from nanobot.config.schema import ProviderConfig

    config = load_config(config_path)
    extra = config.providers.model_extra or {}
    existing = extra.get(PROVIDER_NAME)
    if enabled:
        desired = ProviderConfig(
            display_name=PROVIDER_LABEL,
            api_base=DEFAULT_API_BASE,
            api_key="antigravity-acp-oauth",
        )
        if existing == desired:
            return
        setattr(config.providers, PROVIDER_NAME, desired)
    else:
        used_by_presets = any(
            preset.provider == PROVIDER_NAME
            for preset in (config.model_presets or {}).values()
        )
        if isinstance(existing, ProviderConfig):
            if used_by_presets:
                existing.api_key = ""
                setattr(config.providers, PROVIDER_NAME, existing)
            else:
                delattr(config.providers, PROVIDER_NAME)
        elif not used_by_presets:
            return
    save_config(config, config_path)
    if config_path.exists():
        os.chmod(config_path, 0o600)


@dataclass
class _OAuthToken:
    access: str


class AntigravityOAuthLoginFlow:
    """Interactive ``agy acp --login`` flow compatible with WebUIOAuthFlowRegistry."""

    def __init__(self, *, config_path: Path, timeout_s: float = 300.0, open_browser: bool = False):
        self.config_path = config_path
        self.timeout_s = timeout_s
        self.open_browser = open_browser
        self._deadline = time.monotonic() + timeout_s
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self.authorization_url: str | None = None

    @property
    def remaining_seconds(self) -> int:
        return max(0, int(self._deadline - time.monotonic()))

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self._deadline

    @property
    def has_pending_work(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        binary = find_agy_binary()
        if binary is None:
            raise RuntimeError("agy CLI not found on PATH or ~/.local/bin/agy")
        env = clean_acp_env()
        if not self.open_browser:
            env["BROWSER"] = "/bin/echo"
        proc = subprocess.Popen(
            [binary, "acp", "--login"],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd="/",
            bufsize=0,
            start_new_session=True,
        )
        with self._lock:
            self._process = proc

        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        chunks: list[str] = []
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                data = os.read(fd, 4096)
                if data:
                    chunks.append(data.decode("utf-8", errors="replace"))
                    combined = "".join(chunks)
                    match = _AUTH_URL_RE.search(combined)
                    if match and ("paste the authorization code" in combined.lower() or "\n" in combined[match.end():]):
                        self.authorization_url = match.group(0)
                        return
                elif proc.poll() is not None:
                    break
            elif proc.poll() is not None:
                break
        combined = "".join(chunks)
        match = _AUTH_URL_RE.search(combined)
        if match:
            self.authorization_url = match.group(0)
            return
        self.cancel()
        raise RuntimeError(f"agy acp --login did not emit an authorization URL: {combined.strip()[-400:]}")

    def complete(self, authorization_response: str | None) -> _OAuthToken | None:
        value = (authorization_response or "").strip()
        if not value:
            raise ValueError("Paste the authorization code or callback URL from the browser.")
        if "\n" in value or "\r" in value:
            raise ValueError("Authorization code or URL must be a single line.")
        with self._lock:
            proc = self._process
        if proc is None or proc.poll() is not None:
            raise RuntimeError("Antigravity login session is no longer running.")
        assert proc.stdin is not None and proc.stdout is not None
        try:
            proc.stdin.write((value + "\n").encode("utf-8"))
            proc.stdin.flush()
            proc.stdin.close()
        except OSError as exc:
            self.cancel()
            raise RuntimeError(f"Failed to send authorization response to agy: {exc}") from exc

        output_chunks: list[bytes] = []
        deadline = time.monotonic() + 25.0
        fd = proc.stdout.fileno()
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                chunk = os.read(fd, 4096)
                if chunk:
                    output_chunks.append(chunk)
                elif proc.poll() is not None:
                    break
            elif proc.poll() is not None:
                break
        if proc.poll() is None:
            self.cancel()
            raise RuntimeError("Timed out waiting for agy acp --login to verify credentials.")
        remaining = proc.stdout.read()
        if remaining:
            output_chunks.append(remaining)
        output_text = b"".join(output_chunks).decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            raise ValueError(f"Antigravity login failed: {output_text[-400:]}")

        invalidate_caches()
        status = oauth_status()
        if not status["configured"]:
            raise ValueError("Antigravity login completed without valid credentials; please try again.")
        sync_config_provider(self.config_path, enabled=True)
        return _OAuthToken(access="antigravity-acp-oauth")

    def cancel(self) -> None:
        with self._lock:
            proc = self._process
        if proc is None:
            return
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                try:
                    proc.terminate()
                except OSError:
                    pass
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def wait_closed(self) -> None:
        with self._lock:
            proc = self._process
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self.cancel()


def logout(config_path: Path) -> None:
    """Clear EvoAB's isolated Antigravity credentials and sync config."""
    home = acp_home()
    if home.exists():
        shutil.rmtree(home, ignore_errors=True)
    _ensure_acp_home(home)
    invalidate_caches()
    sync_config_provider(config_path, enabled=False)


class _AcpStdioClient:
    """Minimal JSON-RPC 2.0 stdio transport for one ``agy acp`` process."""

    def __init__(self, binary: str, cwd: Path, env: dict[str, str]):
        self.process = subprocess.Popen(
            [binary, "acp"],
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self._events: queue.Queue[Any] = queue.Queue()
        self._send_lock = threading.Lock()
        self._sequence = 0
        self._stderr_tail: deque[str] = deque(maxlen=200)
        self._workers = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for worker in self._workers:
            worker.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            for raw in self.process.stdout:
                line = raw.strip()
                if not line:
                    continue
                message = json.loads(line)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise ValueError(f"Invalid ACP JSON-RPC message: {line[:200]}")
                self._events.put(message)
            self._events.put(EOFError("Antigravity ACP stdout closed"))
        except Exception as error:
            self._events.put(error)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        try:
            for raw in self.process.stderr:
                self._stderr_tail.append(raw.rstrip())
        except Exception:
            pass

    def stderr_summary(self) -> str:
        return "\n".join(self._stderr_tail)[-1000:]

    def is_alive(self) -> bool:
        return self.process.poll() is None

    def send(self, payload: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        with self._send_lock:
            self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **payload}, ensure_ascii=False) + "\n")
            self.process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.send({"method": method, "params": params})

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        on_notification=None,
        on_request=None,
        timeout: float | None = 60.0,
    ) -> dict[str, Any]:
        self._sequence += 1
        request_id = self._sequence
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout if timeout else None
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"ACP request timed out: {method}")
            try:
                message = self._events.get(timeout=0.1)
            except queue.Empty:
                if not self.is_alive():
                    raise RuntimeError(
                        f"Antigravity ACP exited ({self.process.returncode}): {self.stderr_summary()}"
                    )
                continue
            if isinstance(message, Exception):
                raise message
            if "method" in message:
                if "id" in message:
                    try:
                        result = (
                            on_request(message["method"], message.get("params") or {})
                            if on_request
                            else None
                        )
                    except Exception as error:
                        self.send({
                            "id": message["id"],
                            "error": {"code": -32602, "message": str(error)},
                        })
                        raise
                    if result is None:
                        self.send({
                            "id": message["id"],
                            "error": {"code": -32601, "message": "Client capability not supported"},
                        })
                        raise RuntimeError(f"Unexpected ACP client request: {message['method']}")
                    self.send({"id": message["id"], "result": result})
                    continue
                if on_notification:
                    on_notification(message["method"], message.get("params") or {})
                continue
            if type(message.get("id")) is not int or message["id"] != request_id:
                continue
            if "error" in message:
                raise RuntimeError("ACP error: " + json.dumps(message["error"], ensure_ascii=False))
            result = message.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("ACP response is missing an object result")
            return result

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except OSError:
                try:
                    self.process.terminate()
                except OSError:
                    pass
            try:
                self.process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    self.process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        for worker in self._workers:
            worker.join(timeout=2.0)


def _group_available_models(available_models: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
    raw_ids: set[str] = set()
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for entry in available_models:
        model_id = str(entry.get("modelId") or "").strip()
        if not model_id:
            continue
        raw_ids.add(model_id)
        name = str(entry.get("name") or model_id).strip()
        match = _EFFORT_SUFFIX_RE.match(model_id)
        if match:
            base = match.group("base")
            effort = match.group("effort")
            clean_label = re.sub(r"\s*\((Low|Medium|High|XHigh|Max)\)\s*$", "", name).strip()
            if base not in grouped:
                order.append(base)
                grouped[base] = {
                    "id": base,
                    "label": clean_label or base,
                    "description": None,
                    "owned_by": PROVIDER_LABEL,
                    "context_window": 1_000_000,
                    "reasoning_efforts": [],
                }
            if effort not in grouped[base]["reasoning_efforts"]:
                grouped[base]["reasoning_efforts"].append(effort)
        else:
            if model_id not in grouped:
                order.append(model_id)
                grouped[model_id] = {
                    "id": model_id,
                    "label": name,
                    "description": None,
                    "owned_by": PROVIDER_LABEL,
                    "context_window": 1_000_000,
                    "reasoning_efforts": [],
                }

    rows = [grouped[key] for key in order]
    return rows, raw_ids


def fetch_acp_models(refresh: bool = False) -> tuple[list[dict[str, Any]], set[str]]:
    """Query available models from ``agy acp`` via ``session/new``."""
    global _MODELS_CACHE
    with _CACHE_LOCK:
        if not refresh and _MODELS_CACHE is not None:
            rows, ids = _MODELS_CACHE
            return [dict(row) for row in rows], set(ids)

    binary = find_agy_binary()
    if binary is None:
        raise RuntimeError("agy CLI not found on PATH or ~/.local/bin/agy")
    with tempfile.TemporaryDirectory(prefix="evoab-agy-models-") as tmp:
        client = _AcpStdioClient(binary, Path(tmp), clean_acp_env())
        try:
            init = client.request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientInfo": {"name": "evoab", "version": "1"},
                    "clientCapabilities": {},
                },
                timeout=15.0,
            )
            if init.get("protocolVersion") != 1:
                raise RuntimeError(f"Unsupported ACP protocol version: {init.get('protocolVersion')}")
            session = client.request(
                "session/new",
                {"cwd": tmp, "mcpServers": []},
                timeout=15.0,
            )
            available = (session.get("models") or {}).get("availableModels") or []
            rows, raw_ids = _group_available_models(available)
            try:
                client.notify("session/close", {"sessionId": session["sessionId"]})
            except Exception:
                pass
        finally:
            client.close()

    with _CACHE_LOCK:
        _MODELS_CACHE = ([dict(row) for row in rows], set(raw_ids))
    return rows, raw_ids


def resolve_acp_model_id(model: str, effort: str | None, available_ids: set[str]) -> str:
    """Resolve base model ID + EvoAB reasoning effort to an exact ACP modelId."""
    model_id = (model or "").strip()
    if not available_ids:
        return model_id
    eff = (effort or "").strip().lower()
    if eff in ("", "none", "minimal"):
        eff = "low"
    if eff and f"{model_id}-{eff}" in available_ids:
        return f"{model_id}-{eff}"
    if model_id in available_ids:
        return model_id
    for fallback in ("medium", "low", "high", "xhigh", "max"):
        if f"{model_id}-{fallback}" in available_ids:
            return f"{model_id}-{fallback}"
    return model_id


def provider_entry() -> dict[str, Any]:
    """Build the Studio/catalog provider record for Google Antigravity."""
    status = oauth_status()
    return {
        "name": PROVIDER_NAME,
        "label": PROVIDER_LABEL,
        "is_custom": False,
        "configured": status["configured"],
        "auth_type": "oauth",
        "api_key_required": False,
        "api_key_hint": None,
        "api_base": None,
        "default_api_base": DEFAULT_API_BASE,
        "model_selectable": True,
        "model_catalog": "hybrid",
        "request_apis": ["responses"],
        "adapter_request_apis": ["responses"],
        "api": None,
        "provider_api_configurable": False,
        "model_api_configurable": False,
        "advanced_fields": [],
        "extra_headers": None,
        "extra_body": None,
        "extra_query": None,
        "thinking_style": None,
        "region": None,
        "profile": None,
        "use_proxy": False,
        "oauth_account": status["account"],
        "oauth_expires_at": status["expires_at"],
        "oauth_login_supported": status["login_supported"],
    }


def decorate_settings_payload(payload: dict[str, Any], config_path: Path | None = None) -> dict[str, Any]:
    """Inject Google Antigravity OAuth provider metadata into settings_payload."""
    status = oauth_status()
    if status["configured"] and config_path is not None:
        try:
            sync_config_provider(config_path, enabled=True)
        except Exception:
            pass

    providers = [p for p in payload.get("providers", []) if p.get("name") != PROVIDER_NAME]
    entry = provider_entry()
    insert_at = next(
        (idx + 1 for idx, p in enumerate(providers) if p.get("name") == "openai_codex"),
        0,
    )
    providers.insert(insert_at, entry)

    presets = []
    for preset in payload.get("model_presets", []):
        if preset.get("provider") == PROVIDER_NAME:
            preset = {
                **preset,
                "resolved_provider": PROVIDER_NAME,
                "reasoning_effort_values": ["low", "medium", "high", "xhigh"],
            }
        presets.append(preset)

    return {**payload, "providers": providers, "model_presets": presets}


def provider_models_payload(refresh: bool = False) -> dict[str, Any]:
    """Return the model catalog payload for Studio and ``evoab models catalog``."""
    base = {
        "provider": PROVIDER_NAME,
        "label": PROVIDER_LABEL,
        "catalog_kind": "hybrid",
        "models": [],
        "model_count": 0,
        "message": None,
        "fetched_at": time.time(),
    }
    if find_agy_binary() is None:
        return {
            **base,
            "status": "error",
            "message": "agy CLI is not installed (~/.local/bin/agy).",
        }
    try:
        rows, _ = fetch_acp_models(refresh=refresh)
    except Exception as error:
        return {
            **base,
            "status": "error",
            "message": f"Could not load Antigravity ACP models: {error}",
        }
    status = oauth_status()
    return {
        **base,
        "status": "available",
        "source": "acp",
        "error_kind": None,
        "models": rows,
        "model_count": len(rows),
        "message": None if status["configured"] else "Sign in with Google to run Antigravity models.",
    }


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") in ("text", "input_text", "output_text")
        ]
        return "\n".join(p for p in parts if p)
    return ""


def _convert_messages(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    non_system: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            text = _extract_text(msg.get("content"))
            if text:
                system_parts.append(text)
        else:
            non_system.append(msg)

    if tools:
        system_parts.append(
            "Use ONLY the provided MCP tools from server 'evoab'. "
            "Never invoke built-in filesystem, shell, search, or web tools."
        )
        if tool_choice == "required":
            system_parts.append("You MUST respond by calling the required tool immediately.")
        elif isinstance(tool_choice, dict):
            fn_name = (tool_choice.get("function") or {}).get("name") or tool_choice.get("name")
            if fn_name:
                system_parts.append(f"You MUST respond by calling tool '{fn_name}' immediately.")

    has_history = any(msg.get("role") in ("assistant", "tool") for msg in non_system)
    prompt_blocks: list[dict[str, Any]] = []
    for msg in non_system:
        role = msg.get("role")
        content = msg.get("content")
        if role == "assistant" and has_history:
            text = _extract_text(content)
            calls = msg.get("tool_calls") or []
            call_summary = ""
            if calls:
                formatted = []
                for call in calls:
                    fn = call.get("function") if isinstance(call.get("function"), dict) else call
                    formatted.append(f"{fn.get('name')}({fn.get('arguments')})")
                call_summary = "\nTool calls: " + "; ".join(formatted)
            combined = f"[Previous Assistant Turn]\n{text}{call_summary}".strip()
            prompt_blocks.append({"type": "text", "text": combined})
            continue
        if role == "tool" and has_history:
            text = _extract_text(content)
            call_id = msg.get("tool_call_id") or ""
            prompt_blocks.append({
                "type": "text",
                "text": f"[Tool Result ({call_id})]\n{text}".strip(),
            })
            continue

        if isinstance(content, str):
            if content:
                prompt_blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                kind = part.get("type")
                if kind in ("text", "input_text", "output_text"):
                    text = str(part.get("text") or "")
                    if text:
                        prompt_blocks.append({"type": "text", "text": text})
                elif kind == "image_url":
                    url_obj = part.get("image_url")
                    url = url_obj.get("url", "") if isinstance(url_obj, dict) else str(url_obj or "")
                    match = _DATA_IMAGE_RE.match(url)
                    if match:
                        prompt_blocks.append({
                            "type": "image",
                            "mimeType": match.group(1),
                            "data": match.group(2),
                        })

    if not prompt_blocks:
        prompt_blocks.append({"type": "text", "text": "Continue."})
    return "\n\n".join(system_parts), prompt_blocks


class _ToolCaptureExecutor:
    """Adapter exposing OpenAI tool schemas through EvoAB's HttpMcpServer."""

    tool_execution_seq = 0

    def __init__(self, specs: list[dict[str, Any]] | None = None):
        self._specs: list[dict[str, Any]] = list(specs or [])

    def set_specs(self, specs: list[dict[str, Any]]) -> None:
        self._specs = list(specs)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return list(self._specs)


def _is_evoab_tool(tool_call: dict[str, Any], allowed_names: set[str]) -> bool:
    server = str(tool_call.get("serverName") or tool_call.get("server") or "").strip().lower()
    if server == "evoab" or server.startswith("evoab_"):
        return True
    for key in ("toolName", "name", "title"):
        raw = str(tool_call.get(key) or "").strip()
        if not raw:
            continue
        if raw in allowed_names:
            return True
        for prefix in ("mcp_evoab_", "mcp__evoab__", "evoab/", "evoab:"):
            if raw.startswith(prefix):
                tail = raw[len(prefix):]
                if tail in allowed_names or any(tail.endswith("_" + name) for name in allowed_names):
                    return True
        if raw.startswith("evoab ") or f"evoab/{raw}" in allowed_names:
            return True
    return False


def _pick_permission_option(options: list[dict[str, Any]], *, allow: bool) -> str | None:
    if allow:
        for preferred in ("allow_once", "allow_always"):
            for opt in options:
                if opt.get("kind") == preferred and opt.get("optionId"):
                    return str(opt["optionId"])
        for opt in options:
            opt_id = str(opt.get("optionId") or "")
            if "allow" in opt_id.lower() or "proceed" in opt_id.lower():
                return opt_id
    else:
        for preferred in ("reject_once", "reject_always"):
            for opt in options:
                if opt.get("kind") == preferred and opt.get("optionId"):
                    return str(opt["optionId"])
        for opt in options:
            opt_id = str(opt.get("optionId") or "")
            if "cancel" in opt_id.lower() or "reject" in opt_id.lower() or "deny" in opt_id.lower():
                return opt_id
    if options and options[0].get("optionId"):
        return str(options[0]["optionId"])
    return None


class AntigravityAcpLLMProvider(LLMProvider):
    """Nanobot ``LLMProvider`` executing completions over an owned ``agy acp`` process."""

    def __init__(
        self,
        default_model: str = "gemini-4-neon",
        *,
        binary: str | None = None,
        home: Path | None = None,
        timeout_s: float = 600.0,
    ):
        super().__init__(
            api_key="antigravity-acp-oauth",
            api_base=DEFAULT_API_BASE,
            provider_name=PROVIDER_NAME,
        )
        self.default_model = default_model
        self._binary = binary
        self._home = home
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self._client: _AcpStdioClient | None = None
        self._available_ids: set[str] = set()
        self._mcp_executor = _ToolCaptureExecutor()
        self._mcp_call_handler: Any = None
        self._mcp_token: str = uuid4().hex
        self._mcp_server: HttpMcpServer | None = None

    def get_default_model(self) -> str:
        return self.default_model

    def _dispatch_mcp_call(self, params: dict[str, Any]) -> dict[str, Any]:
        handler = self._mcp_call_handler
        if handler is None:
            return {"content": [{"type": "text", "text": "No active inference turn"}], "isError": True}
        return handler(params)

    def _ensure_mcp_server(self) -> HttpMcpServer:
        if self._mcp_server is None:
            self._mcp_server = HttpMcpServer(
                self._mcp_executor,
                auth_token=self._mcp_token,
                call_handler=self._dispatch_mcp_call,
            )
            self._mcp_server.start()
        return self._mcp_server

    def _ensure_client(self) -> tuple[_AcpStdioClient, Path]:
        if self._client is not None and self._client.is_alive():
            assert self._tmpdir is not None
            return self._client, Path(self._tmpdir.name)
        self._close_client()
        binary = self._binary or find_agy_binary()
        if binary is None:
            raise RuntimeError("agy CLI not found on PATH or ~/.local/bin/agy")
        self._tmpdir = tempfile.TemporaryDirectory(prefix="evoab-agy-acp-")
        cwd = Path(self._tmpdir.name)
        client = _AcpStdioClient(binary, cwd, clean_acp_env(self._home))
        try:
            init = client.request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientInfo": {"name": "evoab", "version": "1"},
                    "clientCapabilities": {},
                },
                timeout=20.0,
            )
            if init.get("protocolVersion") != 1:
                raise RuntimeError(f"Unsupported ACP protocol version: {init.get('protocolVersion')}")
        except Exception:
            client.close()
            self._tmpdir.cleanup()
            self._tmpdir = None
            raise
        self._client = client
        return client, cwd

    def _close_client(self) -> None:
        self._mcp_call_handler = None
        if self._mcp_server is not None:
            try:
                self._mcp_server.stop()
            finally:
                self._mcp_server = None
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None
        if self._tmpdir is not None:
            try:
                self._tmpdir.cleanup()
            finally:
                self._tmpdir = None

    def close(self) -> None:
        with self._lock:
            self._close_client()

    def _chat_sync(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> LLMResponse:
        with self._lock:
            client, scratch_dir = self._ensure_client()
            system_prompt, prompt_blocks = _convert_messages(
                messages, tools=tools, tool_choice=tool_choice
            )

            specs: list[dict[str, Any]] = []
            specs_by_name: dict[str, dict[str, Any]] = {}
            for raw_tool in tools or []:
                fn = raw_tool.get("function") if "function" in raw_tool else raw_tool
                name = str(fn.get("name") or "").strip()
                if not name:
                    continue
                schema = fn.get("parameters") or fn.get("input_schema") or {"type": "object", "properties": {}}
                spec = {
                    "name": name,
                    "description": str(fn.get("description") or ""),
                    "input_schema": schema,
                }
                specs.append(spec)
                specs_by_name[name] = spec

            captured_calls: list[ToolCallRequest] = []
            calls_lock = threading.Lock()
            cancel_timer: list[threading.Timer] = []
            session_id_box: list[str | None] = [None]
            mcp_servers_param: list[dict[str, Any]] = []

            if specs:
                def on_mcp_call(params: dict[str, Any]) -> dict[str, Any]:
                    raw_name = str(params.get("name") or "")
                    if raw_name not in specs_by_name:
                        return {
                            "content": [{"type": "text", "text": f"Unknown tool: {raw_name}"}],
                            "isError": True,
                        }
                    args = dict(params.get("arguments") or {})
                    props = specs_by_name[raw_name]["input_schema"].get("properties", {})
                    for ui_field in ("toolSummary", "toolAction"):
                        if ui_field not in props:
                            args.pop(ui_field, None)
                    with calls_lock:
                        captured_calls.append(
                            ToolCallRequest(
                                id="call_" + uuid4().hex[:12],
                                name=raw_name,
                                arguments=args,
                            )
                        )
                        if not cancel_timer and session_id_box[0]:
                            sid = session_id_box[0]

                            def send_cancel() -> None:
                                try:
                                    client.notify("session/cancel", {"sessionId": sid})
                                except Exception:
                                    pass

                            timer = threading.Timer(0.08, send_cancel)
                            timer.daemon = True
                            cancel_timer.append(timer)
                            timer.start()
                    return {"content": [{"type": "text", "text": "{\"accepted\": true}"}], "isError": False}

                self._mcp_executor.set_specs(specs)
                self._mcp_call_handler = on_mcp_call
                mcp_server = self._ensure_mcp_server()
                mcp_servers_param = [{
                    "type": "http",
                    "name": "evoab",
                    "url": mcp_server.url,
                    "headers": [{"name": "Authorization", "value": "Bearer " + self._mcp_token}],
                }]

            session_id: str | None = None
            texts: list[str] = []
            thoughts: list[str] = []
            allowed_names = set(specs_by_name)

            def on_notification(method: str, params: dict[str, Any]) -> None:
                if method != "session/update" or params.get("sessionId") != session_id:
                    return
                update = params.get("update") or {}
                kind = update.get("sessionUpdate")
                content = update.get("content") or {}
                if kind == "agent_message_chunk" and content.get("type") == "text":
                    texts.append(str(content.get("text") or ""))
                elif kind == "agent_thought_chunk" and content.get("type") == "text":
                    thoughts.append(str(content.get("text") or ""))

            def on_request(method: str, params: dict[str, Any]) -> dict[str, Any] | None:
                if method != "session/request_permission":
                    return None
                tool_call = params.get("toolCall") or {}
                options = params.get("options") or []
                allow = _is_evoab_tool(tool_call, allowed_names)
                option_id = _pick_permission_option(options, allow=allow)
                if option_id is None:
                    return {"outcome": {"outcome": "cancelled"}}
                return {"outcome": {"outcome": "selected", "optionId": option_id}}

            try:
                new_params: dict[str, Any] = {"cwd": str(scratch_dir), "mcpServers": mcp_servers_param}
                if system_prompt:
                    new_params["_meta"] = {"antigravity": {"systemPrompt": {"append": system_prompt}}}
                session = client.request(
                    "session/new",
                    new_params,
                    on_notification=on_notification,
                    on_request=on_request,
                    timeout=30.0,
                )
                session_id = str(session["sessionId"])
                session_id_box[0] = session_id
                available = (session.get("models") or {}).get("availableModels") or []
                if available:
                    self._available_ids = {
                        str(entry["modelId"])
                        for entry in available
                        if entry.get("modelId")
                    }
                target_model = resolve_acp_model_id(
                    model or self.default_model,
                    reasoning_effort,
                    self._available_ids,
                )
                if target_model:
                    client.request(
                        "session/set_model",
                        {"sessionId": session_id, "modelId": target_model},
                        on_notification=on_notification,
                        on_request=on_request,
                        timeout=15.0,
                    )

                prompt_error: Exception | None = None
                try:
                    client.request(
                        "session/prompt",
                        {"sessionId": session_id, "prompt": prompt_blocks},
                        on_notification=on_notification,
                        on_request=on_request,
                        timeout=self._timeout_s,
                    )
                except Exception as exc:
                    prompt_error = exc

                for timer in cancel_timer:
                    timer.cancel()

                with calls_lock:
                    calls = list(captured_calls)

                if calls:
                    return LLMResponse(
                        content="".join(texts) or None,
                        tool_calls=calls,
                        finish_reason="tool_calls",
                        reasoning_content="".join(thoughts) or None,
                        usage=LLMUsage(
                            input_tokens=0,
                            output_tokens=0,
                            total_tokens=0,
                            estimated_tokens=0,
                            request_count=1,
                        ),
                    )
                if prompt_error is not None:
                    message = str(prompt_error)
                    lowered = message.lower()
                    is_auth = "sign in" in lowered or "logged in" in lowered or "unauthenticated" in lowered
                    return LLMResponse(
                        content=f"Antigravity ACP error: {message}",
                        finish_reason="error",
                        error_kind="auth" if is_auth else "provider_error",
                        error_should_retry=False,
                    )
                return LLMResponse(
                    content="".join(texts),
                    tool_calls=[],
                    finish_reason="stop",
                    reasoning_content="".join(thoughts) or None,
                    usage=LLMUsage(
                        input_tokens=0,
                        output_tokens=0,
                        total_tokens=0,
                        estimated_tokens=0,
                        request_count=1,
                    ),
                )
            except Exception as exc:
                self._close_client()
                message = str(exc)
                lowered = message.lower()
                is_auth = "sign in" in lowered or "logged in" in lowered or "unauthenticated" in lowered
                return LLMResponse(
                    content=f"Antigravity ACP error: {message}",
                    finish_reason="error",
                    error_kind="auth" if is_auth else "provider_error",
                    error_should_retry=False,
                )
            finally:
                self._mcp_call_handler = None
                for timer in cancel_timer:
                    timer.cancel()
                if session_id and client.is_alive():
                    try:
                        client.notify("session/close", {"sessionId": session_id})
                    except Exception:
                        pass

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        del max_tokens, temperature
        return await asyncio.to_thread(
            self._chat_sync,
            messages,
            tools,
            model,
            reasoning_effort,
            tool_choice,
        )
