"""Read run directories: identity, status, outcome and model usage.

Usage comes from the provider responses each run records under
``codex_responses/exchange_*/response.body.gz`` (OpenAI-style ``usage``).
Finished runs cache their totals in ``studio_usage.json``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from evoab.ui.studio import jobs

#: A run without result.json whose files changed this recently counts as running.
RUNNING_WINDOW_S = 180
USAGE_CACHE = "studio_usage.json"
#: Launch records and logs written by Studio under the runs root.
STUDIO_DIR = ".studio"
_RUN_DIR_LINE = re.compile(r"--run-dir (\S+)")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _usage_from_body(raw: str) -> dict[str, Any] | None:
    try:
        return (json.loads(raw) or {}).get("usage")
    except ValueError:
        pass
    usage = None
    for line in raw.splitlines():  # server-sent events: keep the last usage seen
        if line.startswith("data:") and '"usage"' in line:
            try:
                usage = json.loads(line[5:]).get("usage") or usage
            except ValueError:
                continue
    return usage


def collect_usage(run_dir: Path) -> dict[str, int]:
    """Sum token usage over every recorded provider exchange of one run."""
    totals = {
        "requests": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    for body in sorted(run_dir.glob("*_responses/exchange_*/response.body.gz")):
        totals["requests"] += 1
        try:
            usage = _usage_from_body(gzip.open(body).read().decode(errors="replace"))
        except OSError:
            continue
        if not usage:
            continue
        prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
        completion = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
        totals["input_tokens"] += int(prompt)
        totals["output_tokens"] += int(completion)
        totals["cached_input_tokens"] += int(prompt_details.get("cached_tokens") or 0)
        totals["reasoning_tokens"] += int(completion_details.get("reasoning_tokens") or 0)
        totals["total_tokens"] += int(usage.get("total_tokens") or prompt + completion)
    return totals


def _usage(run_dir: Path, finished: bool) -> dict[str, int]:
    cache = run_dir / USAGE_CACHE
    if finished:
        cached = _read_json(cache)
        if cached is not None:
            return cached
    usage = collect_usage(run_dir)
    if finished:
        try:
            cache.write_text(json.dumps(usage), encoding="utf-8")
        except OSError:
            pass
    return usage


def _last_change(run_dir: Path) -> float:
    latest = run_dir.stat().st_mtime
    for name in ("trajectory.jsonl", "monitor_frames.jsonl", "service.log"):
        try:
            latest = max(latest, (run_dir / name).stat().st_mtime)
        except OSError:
            continue
    responses = sorted(run_dir.glob("*_responses/exchange_*"))
    if responses:
        latest = max(latest, responses[-1].stat().st_mtime)
    return latest


def load_launches(root: Path) -> list[dict[str, Any]]:
    """Studio launch records, each with its resolved run directory if known."""
    launches = []
    for record_path in sorted((root / STUDIO_DIR).glob("*.json")):
        record = _read_json(record_path)
        if record is None:
            continue
        log = record_path.with_suffix(".log")
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        match = _RUN_DIR_LINE.search(text)
        record["run_dir"] = Path(match.group(1)).name if match else None
        record["log_tail"] = "\n".join(text.strip().splitlines()[-6:])
        record["alive"] = jobs.is_running(record)
        launches.append(record)
    return launches


def summarize_run(
    run_dir: Path, now: float | None = None, launch: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Describe one run directory, or None if it is not a run."""
    now = time.time() if now is None else now
    result = _read_json(run_dir / "result.json")
    launch = launch or {}
    if result is None and not launch and not (run_dir / "supervisor.json").exists():
        return None
    outcome = _read_json(run_dir / "native_outcome.json") or {}
    config = (result or {}).get("config") or {}
    finished = result is not None
    if finished:
        status = "success" if result.get("task_success") else ("error" if result.get("error") else "failure")
    elif launch.get("alive") or (not launch and now - _last_change(run_dir) < RUNNING_WINDOW_S):
        status = "running"
    else:
        status = "interrupted"
    started = launch.get("started_at") or run_dir.stat().st_ctime
    elapsed = (result or {}).get("elapsed_s")
    if elapsed is None:
        elapsed = (now if status == "running" else _last_change(run_dir)) - started
    return {
        "id": run_dir.name,
        # Same id as the replay monitor's ``#run/<id>`` route.
        "monitor_id": hashlib.sha256(str(run_dir.resolve()).encode()).hexdigest()[:16],
        "launch_id": launch.get("launch_id"),
        "status": status,
        "task": (result or {}).get("task_id") or launch.get("task"),
        "integration": (result or {}).get("integration") or launch.get("integration"),
        "profile": launch.get("profile") or config.get("runtime_profile"),
        "benchmark": launch.get("benchmark"),
        "embodiment": launch.get("embodiment"),
        "simulated": launch.get("simulated", True),
        "provider_id": launch.get("provider_id"),
        "provider": launch.get("provider_label") or config.get("provider_url"),
        "model": (result or {}).get("model") or launch.get("model"),
        "agent": (result or {}).get("agent_backend") or launch.get("agent"),
        "tool_profile": (result or {}).get("tool_profile") or launch.get("tool_profile"),
        "action_profile": (result or {}).get("action_profile") or launch.get("action_profile"),
        "reasoning_effort": (result or {}).get("reasoning_effort") or launch.get("reasoning_effort"),
        "seed": (result or {}).get("seed"),
        "max_steps": config.get("max_steps") or launch.get("max_steps"),
        "started_at": started,
        "elapsed_s": round(float(elapsed), 1),
        "environment_steps": outcome.get("native_steps"),
        "task_success": (result or {}).get("task_success"),
        "agent_message": (result or {}).get("agent_message"),
        "error": (result or {}).get("error") or launch.get("error"),
        "usage": _usage(run_dir, finished),
    }


def _pending(launch: dict[str, Any], now: float) -> dict[str, Any]:
    """A launch whose run directory has not appeared yet."""
    alive = bool(launch.get("alive"))
    return {
        "id": None,
        "monitor_id": None,
        "launch_id": launch.get("launch_id"),
        "status": "starting" if alive else "failed",
        "task": launch.get("task"),
        "integration": launch.get("integration"),
        "profile": launch.get("profile"),
        "benchmark": launch.get("benchmark"),
        "embodiment": launch.get("embodiment"),
        "simulated": launch.get("simulated", True),
        "provider_id": launch.get("provider_id"),
        "provider": launch.get("provider_label"),
        "model": launch.get("model"),
        "agent": launch.get("agent"),
        "tool_profile": launch.get("tool_profile"),
        "action_profile": launch.get("action_profile"),
        "reasoning_effort": launch.get("reasoning_effort"),
        "seed": None,
        "max_steps": launch.get("max_steps"),
        "started_at": launch.get("started_at"),
        "elapsed_s": round(float(launch.get("finished_at") or now) - float(launch.get("started_at") or now), 1),
        "environment_steps": None,
        "task_success": None,
        "agent_message": None,
        "error": None if alive else launch.get("log_tail"),
        "usage": {"requests": 0, "input_tokens": 0, "cached_input_tokens": 0,
                  "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0},
    }


def _method(run: dict[str, Any]) -> str:
    return f"{run.get('agent') or 'codex'} · {run.get('tool_profile') or run.get('action_profile') or 'direct'}"


def short_id(run: dict[str, Any]) -> str:
    """Display id: the random suffix of the run directory, else of the launch id."""
    if run.get("id"):
        return str(run["id"]).rsplit("_", 1)[-1]
    return str(run.get("launch_id") or "").rsplit("_", 1)[-1]


def scan_runs(root: Path) -> list[dict[str, Any]]:
    """All runs directly under ``root`` plus pending Studio launches, newest first.

    Each run also gets its method label, a short display id and ``attempt``:
    the how-many-th started run of the same task with the same model (oldest
    first). Launches that never created a run directory get no attempt.
    """
    if not root.is_dir():
        return []
    now = time.time()
    launches = load_launches(root)
    by_dir = {launch["run_dir"]: launch for launch in launches if launch["run_dir"]}
    runs = []
    for child in root.iterdir():
        if child.is_dir() and child.name != STUDIO_DIR:
            summary = summarize_run(child, now, by_dir.get(child.name))
            if summary is not None:
                runs.append(summary)
    runs.extend(_pending(launch, now) for launch in launches if not launch["run_dir"])
    runs.sort(key=lambda r: r["started_at"] or 0)
    counts: dict[tuple[Any, ...], int] = {}
    for run in runs:
        if run["id"]:
            key = (run.get("integration"), run.get("profile"), run.get("task"), run.get("model"))
            counts[key] = counts.get(key, 0) + 1
            run["attempt"] = counts[key]
        else:
            run["attempt"] = None
        run["short_id"] = short_id(run)
        run["method"] = _method(run)
        run["key"] = f"run/{run['id']}" if run["id"] else f"launch/{run['launch_id']}"
    runs.reverse()
    return runs


def _add(bucket: dict[str, Any], run: dict[str, Any]) -> None:
    bucket["runs"] = bucket.get("runs", 0) + 1
    bucket["success"] = bucket.get("success", 0) + (1 if run["status"] == "success" else 0)
    for name, value in run["usage"].items():
        bucket[name] = bucket.get(name, 0) + value


def usage_totals(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Usage overall and grouped by API, by API · model and by day."""
    overall: dict[str, Any] = {"runs": 0, "success": 0}
    groups: dict[str, dict[str, dict[str, Any]]] = {"by_provider": {}, "by_model": {}, "by_day": {}}
    for run in runs:
        provider = run.get("provider") or "?"
        day = time.strftime("%Y-%m-%d", time.localtime(run.get("started_at") or 0))
        _add(overall, run)
        _add(groups["by_provider"].setdefault(provider, {}), run)
        _add(groups["by_model"].setdefault(f"{provider} · {run.get('model') or '?'}", {}), run)
        _add(groups["by_day"].setdefault(day, {}), run)
    result: dict[str, Any] = {"overall": overall}
    for name, items in groups.items():
        ordered = sorted(items.items(), key=lambda kv: kv[0] if name == "by_day" else -kv[1].get("total_tokens", 0))
        result[name] = [{"key": key, **value} for key, value in ordered]
    return result
