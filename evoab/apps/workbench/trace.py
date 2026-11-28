"""Reconstruct a run as a causal, replayable sequence of steps.

A run directory records three independent streams:

* ``codex_responses/exchange_NNNN``: every model request the agent made, with
  the agent-side request (``request.codex.json.gz``), the request actually sent
  to the provider, the response stream, timing, retries and token usage;
* ``agent/rounds.jsonl``: native Agent iterations, tool calls, results and usage;
* ``trajectory.jsonl``: agent and tool events, including EvoAB robot tool
  executions (queue, start, result, finish);
* ``monitor_frames.jsonl`` plus ``telemetry/trace.jsonl``: published camera
  snapshots with robot state, and spans for service calls.

This module joins them into one timeline. Fixed harness stages (deployment,
initial observation, prompt assembly) come first. Each model request becomes a
*round*: why it was sent (the input items that were new since the previous
request), what it cost, and what it returned. Each tool call the model made is
linked to its execution and to the later round whose input contains its result,
so every output can be followed to where it was used.
"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXCHANGES = "codex_responses"
#: Tool namespace under which EvoAB registers its robot tools with Codex.
ROBOT_NAMESPACE = "mcp__evoab"
#: Characters of text kept per item in the timeline payload (full text via the round detail).
EXCERPT = 1200
_WALL_TIME = re.compile(r"Wall time:? ([0-9.]+) seconds")
#: Marker Codex puts in the request that asks the model for a handoff summary.
COMPACTION_PROMPT = "CONTEXT CHECKPOINT COMPACTION"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_gz_json(path: Path) -> Any:
    try:
        return json.loads(gzip.open(path).read())
    except (OSError, ValueError, EOFError):
        return None


def _jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return records
    for line in lines:
        try:
            records.append(json.loads(line))
        except ValueError:
            continue  # the last line of a live run may be partial
    return records


_BASE64 = re.compile(r"[A-Za-z0-9+/]{400,}={0,2}")


def _scrub(text: str) -> str:
    """Replace inline base64 payloads (e.g. images inside script output) with a size marker."""
    return _BASE64.sub(lambda m: f"‹base64 数据 {len(m.group(0)) * 3 // 4 // 1024} KB›", text)


def _clip(text: str, limit: int = EXCERPT) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit], True)


# -- items --------------------------------------------------------------------


def _image_ref(part: dict[str, Any]) -> str | None:
    url = part.get("image_url")
    if isinstance(url, dict):
        return url.get("$evoab_image")
    return None


def _content_parts(content: Any) -> tuple[str, list[str], int]:
    """Text, run-relative image paths and inline image count of a content value."""
    if isinstance(content, str):
        return content, [], 0
    texts, images, inline = [], [], 0
    for part in content or []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("input_text", "output_text", "text", "summary_text"):
            texts.append(str(part.get("text", "")))
        elif kind in ("input_image", "image_url", "image"):
            ref = _image_ref(part)
            if ref:
                images.append(ref)
            else:
                inline += 1
    return "\n".join(texts), images, inline


def _tool_names(tools: list[dict[str, Any]], prefix: str = "") -> list[str]:
    names = []
    for tool in tools:
        if tool.get("tools"):
            names += _tool_names(tool["tools"], f"{tool.get('name')}." if tool.get("name") not in ("functions", None) else "")
        else:
            names.append(prefix + str(tool.get("name")))
    return names


def item_key(item: dict[str, Any]) -> str:
    """Stable identity of a conversation item across requests."""
    if item.get("id"):
        return str(item["id"])
    if item.get("call_id"):
        return f"{item.get('type')}:{item['call_id']}"
    return "h:" + str(hash(json.dumps(item, sort_keys=True, ensure_ascii=False)))


def describe_item(item: dict[str, Any], full: bool = False) -> dict[str, Any]:
    """A display form of one Responses item: who produced it and what it carries."""
    kind = item.get("type", "message")
    out: dict[str, Any] = {"key": item_key(item), "type": kind}
    text, images, inline = "", [], 0
    if kind == "message":
        out["role"] = item.get("role")
        text, images, inline = _content_parts(item.get("content"))
    elif kind == "reasoning":
        text, _, _ = _content_parts(item.get("summary"))
        out["encrypted"] = bool(item.get("encrypted_content"))
    elif kind in ("function_call", "custom_tool_call"):
        out.update(call_id=item.get("call_id"), name=item.get("name"), namespace=item.get("namespace"))
        text = str(item.get("arguments") or item.get("input") or "")
    elif kind == "additional_tools":
        out["role"] = item.get("role")
        names = _tool_names(item.get("tools") or [])
        text = "工具清单（" + str(len(names)) + "）：" + ", ".join(names)
    elif kind in ("function_call_output", "custom_tool_call_output"):
        out["call_id"] = item.get("call_id")
        text, images, inline = _content_parts(item.get("output"))
    else:
        text = json.dumps(item, ensure_ascii=False)[:EXCERPT * 4]
    text = _scrub(text)
    out["chars"] = len(text)
    if full:
        out["text"] = text
    else:
        out["text"], out["truncated"] = _clip(text)
    out["images"] = images
    out["inline_images"] = inline
    return out


# -- exchanges ----------------------------------------------------------------


def _response_output(directory: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Output items and usage of a recorded response stream (Responses SSE)."""
    try:
        raw = gzip.open(directory / "response.codex.body.gz").read().decode("utf-8", errors="replace")
    except (OSError, EOFError):
        return [], None
    done: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:])
        except ValueError:
            continue
        if event.get("type") == "response.completed":
            response = event.get("response") or {}
            return list(response.get("output") or []), response.get("usage")
        if event.get("type") == "response.output_item.done" and isinstance(event.get("item"), dict):
            done.append(event["item"])
    return done, None


@dataclass
class Exchange:
    """What one model request needs for the timeline, without its full payload.

    Requests repeat the whole conversation, so keeping them would grow with the
    square of the run length. Items are described once per run in ``RunItems``
    and an exchange keeps only their keys.
    """

    name: str
    meta: dict[str, Any]
    model: str | None
    effort: str | None
    instructions_chars: int
    tool_count: int
    item_keys: list[str]
    output: list[dict[str, Any]]


@dataclass
class RunItems:
    """Per-run cache: parsed exchanges and every distinct conversation item."""

    exchanges: dict[str, tuple[float, Exchange]]
    items: dict[str, dict[str, Any]]


_runs: dict[str, RunItems] = {}


def _cache_for(run_dir: Path) -> RunItems:
    key = str(run_dir.resolve())
    if key not in _runs:
        if len(_runs) > 16:
            _runs.pop(next(iter(_runs)))
        _runs[key] = RunItems({}, {})
    return _runs[key]


def load_exchange(directory: Path, cache: RunItems) -> Exchange | None:
    """Parse one exchange once (re-read only while its meta.json is changing)."""
    meta_path = directory / "meta.json"
    try:
        stamp = meta_path.stat().st_mtime
    except OSError:
        return None
    cached = cache.exchanges.get(directory.name)
    if cached and cached[0] == stamp:
        return cached[1]
    meta = _read_json(meta_path) or {}
    request = _read_gz_json(directory / "request.codex.json.gz") or {}
    output, usage = _response_output(directory)
    if usage and not meta.get("usage"):
        meta["usage"] = usage
    meta.pop("response_headers", None)
    keys = []
    tool_count = len(request.get("tools") or [])
    for item in request.get("input") or []:
        key = item_key(item)
        keys.append(key)
        if key not in cache.items:
            cache.items[key] = describe_item(item)
        if item.get("type") == "additional_tools":
            tool_count += len(_tool_names(item.get("tools") or []))
    exchange = Exchange(directory.name, meta, request.get("model"), (request.get("reasoning") or {}).get("effort"),
                        len(request.get("instructions") or ""), tool_count, keys, output)
    cache.exchanges[directory.name] = (stamp, exchange)
    return exchange


def load_exchanges(run_dir: Path) -> tuple[list[Exchange], dict[str, dict[str, Any]]]:
    cache = _cache_for(run_dir)
    exchanges = []
    for directory in sorted((run_dir / EXCHANGES).glob("exchange_*")):
        exchange = load_exchange(directory, cache)
        if exchange is not None:
            exchanges.append(exchange)
    return exchanges, cache.items


def _usage(meta: dict[str, Any]) -> dict[str, int]:
    usage = meta.get("usage") or {}
    inp = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    out = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    inp_d = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    out_d = usage.get("output_tokens_details") or usage.get("completion_tokens_details") or {}
    return {
        "input": int(inp),
        "cached": int(inp_d.get("cached_tokens") or usage.get("cached_input_tokens") or 0),
        "output": int(out),
        "reasoning": int(out_d.get("reasoning_tokens") or 0),
        "total": int(usage.get("total_tokens") or inp + out),
    }


# -- tool executions ----------------------------------------------------------


def _robot_executions(events: list[dict[str, Any]], *, native: bool = False) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """EvoAB tool executions by their transport id, and agent call id → transport id."""
    executions: dict[str, dict[str, Any]] = {}
    links: dict[str, str] = {}
    for event in events:
        kind = event.get("type")
        if kind == "AgentActivity" and event.get("transport_call_id") and event.get("item_id"):
            links[str(event["item_id"])] = str(event["transport_call_id"])
        call_id = event.get("call_id") or (event.get("execution_id") if native else None)
        if not call_id or kind not in ("ToolCall", "ToolExecution", "ToolResult"):
            continue
        record = executions.setdefault(str(call_id), {"transport_id": call_id, "name": event.get("tool_name")})
        if kind == "ToolCall":
            record["called_at"] = event.get("timestamp")
        elif kind == "ToolExecution":
            record[f"{event.get('phase')}_at"] = event.get("timestamp")
            if "arguments" in event:
                record["arguments"] = event["arguments"]
            record["resource"] = event.get("resource")
            if event.get("phase") == "finished":
                record["success"] = event.get("success")
        elif kind == "ToolResult":
            record["arguments"] = event.get("arguments") or record.get("arguments")
            record["output"] = event.get("output")
            record["success"] = event.get("success", record.get("success"))
            record["result_at"] = event.get("timestamp")
    return executions, links


def _agent_activity(events: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Start/finish times the agent runtime reported for each of its call ids."""
    times: dict[str, dict[str, float]] = {}
    for event in events:
        if event.get("type") == "AgentActivity" and event.get("item_id"):
            phase = event.get("kind", "")
            if phase in ("tool_started", "tool_completed", "tool_failed"):
                times.setdefault(str(event["item_id"]), {})[phase] = event.get("timestamp")
    return times


def _spans(run_dir: Path) -> list[dict[str, Any]]:
    spans = []
    for span in _jsonl(run_dir / "telemetry" / "trace.jsonl"):
        if span.get("kind") != "span" or span.get("start_wall_ns") is None:
            continue
        start = span["start_wall_ns"] / 1e9
        attrs = span.get("attributes") or {}
        target = attrs.get("url") or attrs.get("path") or ""
        spans.append({"name": span.get("name"), "start": start, "end": start + (span.get("duration_ns") or 0) / 1e9,
                      "method": attrs.get("method"), "target": re.sub(r"^https?://[^/]+", "", str(target))})
    return spans


def _service_calls(spans: list[dict[str, Any]], start: float | None, end: float | None) -> dict[str, Any]:
    """Environment service requests made while one robot tool executed."""
    if start is None or end is None:
        return {}
    counts: dict[str, dict[str, float]] = {}
    for span in spans:
        if span["name"] != "rpc.client" or span["start"] < start - 0.01 or span["end"] > end + 0.01:
            continue
        if "/chat/completions" in span["target"] or "/responses" in span["target"]:
            continue
        key = f"{span['method']} {re.sub(r'/[0-9a-f]{16,}', '/<id>', span['target'])}"
        entry = counts.setdefault(key, {"count": 0, "seconds": 0.0})
        entry["count"] += 1
        entry["seconds"] = round(entry["seconds"] + span["end"] - span["start"], 3)
    return counts


def _robot_summary(output: Any) -> dict[str, Any]:
    """The robot facts a reader looks for first in an EvoAB tool result."""
    if not isinstance(output, dict):
        return {}
    state = output.get("state") or {}
    feedback = output.get("feedback") or {}
    arms = {}
    for name, arm in ((state.get("arms") or {}).items()):
        pose = (arm.get("control_pose") or {}).get("position")
        arms[name] = {"position": pose, "gripper": arm.get("gripper")}
    errors = {}
    for name, arm in ((feedback.get("arms") or {}).items()):
        errors[name] = {k: arm.get(k) for k in ("position_error_m", "orientation_error_rad", "pose_reached",
                                               "target_position", "actual_position", "opening_status")}
    return {
        "native_steps": state.get("native_steps"),
        "ended": state.get("ended"),
        "remaining_steps": output.get("remaining_steps"),
        "steps_executed": feedback.get("steps_executed"),
        "stop_reason": feedback.get("stop_reason"),
        "pose_reached": feedback.get("pose_reached"),
        "arms": arms,
        "arm_feedback": errors,
        "snapshot_id": output.get("snapshot_id"),
        "cameras": output.get("image_cameras"),
        "error": output.get("error"),
    }


# -- frames -------------------------------------------------------------------


def _frames(run_dir: Path) -> list[dict[str, Any]]:
    """Published snapshots: one entry per capture with every camera image."""
    frames = []
    for record in _jsonl(run_dir / "monitor_frames.jsonl"):
        cameras = {name: image.get("path") for name, image in (record.get("cameras") or {}).items()
                   if isinstance(image, dict) and image.get("path")}
        if not cameras or record.get("captured_at") is None:
            continue
        state = record.get("state") or {}
        frames.append({"t": record["captured_at"], "cameras": cameras, "steps": state.get("native_steps"),
                       "state_version": record.get("state_version"), "snapshot_id": record.get("snapshot_id")})
    frames.sort(key=lambda f: f["t"])
    return frames


# -- fixed stages -------------------------------------------------------------


def _stages(run_dir: Path, events: list[dict[str, Any]], spans: list[dict[str, Any]],
            frames: list[dict[str, Any]], first_request: float | None) -> list[dict[str, Any]]:
    """Harness stages before the agent's first model request (fixed code, no model involved)."""
    monitor = _read_json(run_dir / "monitor.json") or {}
    deployment = _read_json(run_dir / "deployment.json") or {}
    started = monitor.get("started_at")
    service_up = next((s["start"] for s in spans if s["name"] == "rpc.client" and s["target"].endswith("/info")), None)
    opened = next((e.get("timestamp") for e in events if e.get("type") == "SessionOpened"), None)
    session = next((e.get("timestamp") for e in events if e.get("type") == "SessionStart"), None)
    initial = frames[0] if frames else None
    files = [name for name in ("effective_prompt.md", "codex_base_instructions.md", "codex_user_input.md",
                               "tools.json", "initial_observation.json", "robot_resources.json")
             if (run_dir / name).exists()]
    identity = deployment.get("identity") or {}
    stages = [
        {"id": "launch", "title": "启动运行", "origin": "code", "at": started,
         "summary": "命令行解析配置，创建运行目录，开始记录。",
         "detail": {"config": (_read_json(run_dir / "result.json") or {}).get("config")}},
        {"id": "deploy", "title": "启动环境服务", "origin": "code", "at": started, "end": service_up,
         "summary": f"在本机启动 {deployment.get('runtime') or '环境'} 服务（{deployment.get('url') or '—'}），"
                    "校验回合身份（套件、任务、种子、初始状态）。",
         "detail": {"url": deployment.get("url"), "expected": deployment.get("expected"),
                    "protocol": identity.get("protocol_version"), "tool_schema": identity.get("tool_schema_version")}},
        {"id": "observe", "title": "读取初始观测", "origin": "code", "at": initial["t"] if initial else None,
         "summary": "环境复位到初始状态，采集所有相机图像与机器人状态，写入首条提示词。",
         "frame": initial, "file": "initial_observation.json" if "initial_observation.json" in files else None},
        {"id": "prompt", "title": "组装提示词与工具", "origin": "code", "at": opened,
         "summary": "按固定模板把任务指令、机器人资源、初始观测和工具规则拼成 Agent 的输入；"
                    "载入本次运行的 EvoAB 工具清单。",
         "files": files},
        {"id": "agent", "title": "启动 Agent 会话", "origin": "runtime", "at": session or first_request, "end": first_request,
         "summary": "Agent 载入任务指令与工具清单，开始模型调用。"},
    ]
    return stages


# -- the trace ----------------------------------------------------------------


def _identity(run_dir: Path, result: dict, monitor: dict) -> tuple[str | None, str | None]:
    manifest = _read_json(run_dir / "telemetry/run_manifest.json") or {}
    agent = manifest.get("agent_backend") or result.get("agent_backend")
    if not agent:
        if (run_dir / "agent/rounds.jsonl").is_file():
            agent = "native"
        elif (run_dir / EXCHANGES).is_dir():
            agent = "codex"
    return agent, result.get("model") or manifest.get("model") or monitor.get("model")


def _native_items(row: dict, *, full: bool = False) -> tuple[list[dict], list[dict]]:
    # The hook runs after tool dispatch. The assistant reply and following tool
    # messages were produced in this iteration, not sent to its model request.
    messages = row.get("input") or []
    split = next((i for i in reversed(range(len(messages))) if messages[i].get("role") == "assistant"),
                 len(messages))
    incoming = [describe_item(msg, full) for msg in messages[:split]]
    output = []
    if row.get("reasoning"):
        output.append({"type": "reasoning", "summary": row["reasoning"]})
    if row.get("content"):
        output.append({"role": "assistant", "content": row["content"]})
    for call in row.get("tool_calls") or []:
        output.append({"type": "function_call", "call_id": call.get("id"), "name": call.get("name"),
                       "arguments": json.dumps(call.get("arguments"), ensure_ascii=False)})
    return incoming, [describe_item(item, full) for item in output]


def _native_timeline(run_dir: Path, model: str | None, executions: dict, spans: list[dict]) -> tuple[list[dict], list[dict]]:
    rows = _jsonl(run_dir / "agent/rounds.jsonl")
    registered = {tool["name"] for tool in (_read_json(run_dir / "tools.json") or [])}
    pending = dict(executions)
    rounds, calls = [], []
    for index, row in enumerate(rows):
        start, end = row["started_at"], row["finished_at"]
        incoming, output = _native_items(row)
        ids = []
        events = list(row.get("tool_events") or [])
        for ordinal, tool in enumerate(row.get("tool_calls") or []):
            name = tool["name"]
            arguments = tool.get("arguments") or {}
            key = f"{index}/{ordinal}"
            ids.append(key)
            # Older native runs have separate provider and executor IDs. Match
            # each execution once within its iteration, including repeated calls.
            match = next((eid for eid, e in pending.items() if e["name"] == name
                          and start <= (e.get("queued_at") or e.get("started_at") or 0) <= end
                          and (e.get("arguments") or {}) == arguments), None)
            execution = pending.pop(match) if match is not None else {}
            event = next((e for e in events if e.get("name") == name), {})
            if event:
                events.remove(event)
            began, finished = execution.get("started_at"), execution.get("finished_at")
            result = execution.get("output")
            detail = json.dumps(result, ensure_ascii=False) if result is not None else event.get("detail")
            robot = bool(execution) or name in registered
            calls.append({"call_id": key, "provider_call_id": tool.get("id"), "transport_id": match,
                          "round": index, "name": name, "arguments": arguments, "executor": "robot" if robot else "agent",
                          "issued_at": execution.get("queued_at"), "queued_at": execution.get("queued_at"),
                          "started_at": began, "finished_at": finished,
                          "duration_s": finished - began if began and finished else None,
                          "success": execution.get("success", event["status"] == "ok" if event.get("status") else None),
                          "result": describe_item({"type": "function_call_output", "call_id": key, "output": detail}) if detail else None,
                          "robot": _robot_summary(result) if result else None, "nested": [],
                          "service_calls": _service_calls(spans, began, finished), "used_in_round": None})
        rounds.append({"index": index, "model": model, "started_at": start, "finished_at": end,
                       "duration_s": end - start, "complete": True, "error": row.get("error"),
                       "usage": _usage(row), "context": {"tools": len(registered)}, "attempts": [],
                       "trigger": {"kind": "initial" if index == 0 else "continuation",
                                   "text": "原生 Agent 根据任务和观测请求模型。" if index == 0 else "原生 Agent 继续本轮推理。"},
                       "new_input": incoming, "output": output, "calls": ids})
    return rounds, calls


def build_trace(run_dir: Path) -> dict[str, Any]:
    events = _jsonl(run_dir / "trajectory.jsonl")
    spans = _spans(run_dir)
    frames = _frames(run_dir)
    result = _read_json(run_dir / "result.json")
    monitor = _read_json(run_dir / "monitor.json") or {}

    agent, model = _identity(run_dir, result or {}, monitor)
    executions, links = _robot_executions(events, native=agent == "native")
    if agent == "native":
        rounds, calls = _native_timeline(run_dir, model, executions, spans)
    else:
        rounds, calls = _exchange_timeline(run_dir, executions, links, _agent_activity(events), spans)
    t0 = monitor.get("started_at") or (rounds[0]["started_at"] if rounds else None)
    end_at = monitor.get("finished_at") or next((e.get("timestamp") for e in reversed(events)
                                                  if e.get("type") == "SessionClosed"), None)
    ending = _ending(run_dir, result, rounds, calls, monitor.get("max_episode_steps"))
    return {
        "agent": agent, "model": model,
        "t0": t0,
        "t_end": end_at,
        "live": result is None,
        "instruction": monitor.get("instruction"),
        "max_steps": monitor.get("max_episode_steps"),
        "stages": _stages(run_dir, events, spans, frames, rounds[0]["started_at"] if rounds else None),
        "rounds": rounds,
        "calls": calls,
        "frames": frames,
        "ending": ending,
    }


def _exchange_timeline(run_dir: Path, executions: dict, links: dict, activity: dict, spans: list[dict]) -> tuple[list[dict], list[dict]]:
    exchanges, store = load_exchanges(run_dir)
    # Where each call's output first appears as input: that round consumed it.
    consumed_in: dict[str, tuple[int, str]] = {}
    for index, exchange in enumerate(exchanges):
        for key in exchange.item_keys:
            item = store[key]
            if item["type"].endswith("_output") and item.get("call_id"):
                consumed_in.setdefault(str(item["call_id"]), (index, key))

    rounds: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    previous_keys: set[str] = set()
    produced_keys: set[str] = set()
    for index, exchange in enumerate(exchanges):
        meta = exchange.meta
        items = exchange.item_keys
        new_keys = [key for key in items if key not in previous_keys]
        fresh = [store[key] for key in new_keys if key not in produced_keys]
        echoed = [key for key in new_keys if key in produced_keys]
        previous_keys = set(items)
        output = [describe_item(item) for item in exchange.output]
        produced_keys |= {item_key(item) for item in exchange.output}
        last_text = (fresh[-1].get("text") or "") if fresh else ""
        if index == 0:
            trigger = {"kind": "initial", "text": "运行开始：Codex 把系统指令、AGENTS 规则和任务提示词作为首个输入发给模型。"}
        elif COMPACTION_PROMPT in last_text:
            before = rounds[-1]["usage"]["input"] if rounds else None
            trigger = {"kind": "compaction_request",
                       "text": f"上下文已累计到约 {before or '—'} tokens，Codex 自动追加一条压缩指令，"
                               "要求模型写出交接摘要（固定逻辑：Codex 自动上下文压缩，非模型决定）。"}
        elif rounds and len(items) < rounds[-1]["context"]["items"]:
            trigger = {"kind": "compacted",
                       "text": f"Codex 用上一轮的交接摘要替换了此前 {rounds[-1]['context']['items']} 项对话历史，"
                               f"以 {len(items)} 项重新开始（固定逻辑：上下文压缩后的新会话）。"}
        else:
            outputs = [i for i in fresh if i["type"].endswith("_output")]
            others = [i for i in fresh if not i["type"].endswith("_output")]
            if outputs and not others:
                names = ", ".join(c["name"] for c in calls if c["call_id"] in {o["call_id"] for o in outputs})
                trigger = {"kind": "tool_results",
                           "text": f"上一轮的 {len(outputs)} 个工具结果已返回（{names}）。"
                                   "Codex 运行时把结果追加到对话，自动发起下一次请求（固定逻辑，非模型决定）。"}
            elif others:
                trigger = {"kind": "message", "text": "对话中加入了新消息（见新增输入），Codex 据此发起请求。"}
            else:
                trigger = {"kind": "continuation", "text": "上一轮输出后没有新的工具结果，Codex 继续请求模型。"}
        policy = meta.get("context_policy") or {}
        attempts = meta.get("provider_attempts") or []
        started, finished = meta.get("started_at"), meta.get("finished_at")
        round_calls = []
        for item in exchange.output:
            if item.get("type") not in ("function_call", "custom_tool_call"):
                continue
            call_id = str(item.get("call_id"))
            namespace = item.get("namespace")
            robot = (namespace == ROBOT_NAMESPACE and item.get("type") == "function_call") or call_id in links
            execution = executions.get(links.get(call_id, ""), {}) if robot else {}
            times = activity.get(call_id, {})
            try:
                arguments = json.loads(item.get("arguments") or "{}") if item.get("type") == "function_call" \
                    else {"script": item.get("input")}
            except ValueError:
                arguments = item.get("arguments")
            consumer, output_key = consumed_in.get(call_id, (None, None))
            output_item = store.get(output_key) if output_key else None
            begin = execution.get("started_at") or times.get("tool_started") or finished
            end = execution.get("finished_at") or times.get("tool_completed") or times.get("tool_failed")
            duration = (end - begin) if begin and end else None
            if not robot and output_item:
                match = _WALL_TIME.search(output_item.get("text") or "")
                if match:
                    duration = float(match.group(1))
            call = {
                "call_id": call_id,
                "name": item.get("name"),
                "namespace": namespace,
                "executor": "robot" if robot else "agent",
                "round": index,
                "arguments": arguments,
                "issued_at": finished,
                "queued_at": execution.get("queued_at"),
                "started_at": begin,
                "finished_at": end,
                "duration_s": round(duration, 3) if duration is not None else None,
                "success": execution.get("success") if robot else (times.get("tool_failed") is None),
                "robot": _robot_summary(execution.get("output")) if robot else None,
                "service_calls": _service_calls(spans, begin, end) if robot else None,
                "result": output_item,
                "used_in_round": consumer,
                "nested": [],
            }
            calls.append(call)
            round_calls.append(call_id)
        rounds.append({
            "index": index,
            "exchange": exchange.name,
            "started_at": started,
            "finished_at": finished,
            "duration_s": round(finished - started, 3) if started and finished else None,
            "status": meta.get("http_status") or meta.get("status"),
            "complete": bool(meta.get("complete")),
            "error": meta.get("error") or meta.get("response_error") or meta.get("delivery_error"),
            "attempts": [{"attempt": a.get("attempt"), "status": a.get("status"), "elapsed_s": a.get("elapsed_s")}
                         for a in attempts],
            "retries": meta.get("provider_retry_count") or 0,
            "model": exchange.model,
            "endpoint": meta.get("request_url"),
            "wire_profile": meta.get("wire_profile"),
            "reasoning": exchange.effort,
            "usage": _usage(meta),
            "context": {
                "items": len(items),
                "instructions_chars": exchange.instructions_chars,
                "tools": exchange.tool_count,
                "estimated_tokens": policy.get("estimated_tokens_after"),
                "truncated": bool(policy.get("truncated")),
                "dropped": len(policy.get("dropped_item_ids") or []),
                "policy": policy.get("name"),
            },
            "trigger": trigger,
            "new_input": fresh,
            "echoed": len(echoed),
            "output": output,
            "calls": round_calls,
        })

    direct = {links[c["call_id"]] for c in calls if c["call_id"] in links}
    _attach_nested(rounds, calls, executions, direct, spans)
    return rounds, calls


def _attach_nested(rounds: list[dict[str, Any]], calls: list[dict[str, Any]],
                   executions: dict[str, dict[str, Any]], linked: set[str], spans: list[dict[str, Any]]) -> None:
    """Attach robot executions that no model call made directly.

    In Codex code mode the model writes a script for its ``exec`` tool and the
    script calls robot tools. Those executions happen after the round that
    issued the script and before the next request, so they belong to that
    round's script (the one naming the tool, else the round's first call).
    """
    by_id = {c["call_id"]: c for c in calls}
    for transport, execution in executions.items():
        if transport in linked:
            continue
        at = execution.get("called_at") or execution.get("started_at")
        if at is None:
            continue
        owner = None
        for index, current in enumerate(rounds):
            nxt = rounds[index + 1]["started_at"] if index + 1 < len(rounds) else float("inf")
            if (current["finished_at"] or 0) <= at < (nxt or float("inf")) and current["calls"]:
                owner = current
                break
        if owner is None:
            continue
        name = execution.get("name") or ""
        candidates = [by_id[c] for c in owner["calls"] if c in by_id]
        parent = next((c for c in candidates if name and name in json.dumps(c["arguments"])), candidates[0])
        begin, end = execution.get("started_at"), execution.get("finished_at")
        parent["nested"].append({
            "transport_id": transport,
            "name": name,
            "arguments": execution.get("arguments"),
            "queued_at": execution.get("queued_at"),
            "started_at": begin,
            "finished_at": end,
            "duration_s": round(end - begin, 3) if begin and end else None,
            "success": execution.get("success"),
            "robot": _robot_summary(execution.get("output")),
            "service_calls": _service_calls(spans, begin, end),
        })
    for call in calls:
        call["nested"].sort(key=lambda n: n.get("started_at") or 0)
        if call["nested"] and call["executor"] == "agent":
            call["executor"] = "script"


def _ending(run_dir: Path, result: dict[str, Any] | None, rounds: list[dict[str, Any]],
            calls: list[dict[str, Any]], step_limit: int | None) -> dict[str, Any] | None:
    """Why the run ended and what decided the outcome, in plain terms."""
    if result is None:
        return None
    native = _read_json(run_dir / "native_outcome.json") or {}
    finish = _read_json(run_dir / "finish_request.json") or {}
    finish_call = next((c for c in reversed(calls) if c["name"] == "finish"
                        or any(n["name"] == "finish" for n in c["nested"])), None)
    last = rounds[-1] if rounds else None
    last_calls = last["calls"] if last else []
    steps = next(
        (v for v in (native.get("native_steps"), native.get("steps"), result.get("steps")) if v is not None),
        None,
    )
    budget = native.get("step_limit") or step_limit
    chain = []
    if result.get("error"):
        chain.append(f"运行出错或被中断：{result['error']}")
    exhausted = None
    for call in calls:
        for entry in [call, *call["nested"]]:
            facts = entry.get("robot") or {}
            if exhausted is None and (facts.get("ended") or facts.get("remaining_steps") == 0):
                exhausted = (call["round"], entry["name"], facts.get("native_steps"))
    if exhausted:
        chain.append(f"第 {exhausted[0] + 1} 轮的 {exhausted[1]} 执行后环境回合结束"
                     f"（{exhausted[2]} / {budget or '—'} 步）；之后的动作不再改变环境。")
    elif budget and steps is not None and steps >= budget:
        chain.append(f"环境步数用尽：{steps} / {budget} 步。")
    if finish_call:
        chain.append(f"模型在第 {finish_call['round'] + 1} 轮调用了 finish（{json.dumps(finish_call['arguments'], ensure_ascii=False)[:200]}）。")
    elif last and not last_calls and last["complete"] and not result.get("error"):
        chain.append(f"第 {last['index'] + 1} 轮模型只回复了文字、没有调用任何工具，会话结束，未调用 finish。")
    category = result.get("failure_category")
    if category:
        chain.append(f"失败类别：{category}。")
    verdict = result.get("task_success")
    chain.append("环境验证器判定：" + ("任务成功。" if verdict is True else "任务未完成。" if verdict is False else "无判定。"))
    return {
        "task_success": verdict,
        "failure_category": category,
        "stop_reason": result.get("stop_reason"),
        "agent_finished": result.get("agent_finished"),
        "finish_request": finish,
        "native_outcome": native,
        "steps": steps,
        "step_limit": budget,
        "chain": chain,
        "agent_message": result.get("agent_message"),
    }


def round_detail(run_dir: Path, index: int) -> dict[str, Any]:
    """Everything sent and received in one round, untruncated."""
    if _identity(run_dir, _read_json(run_dir / "result.json") or {}, {})[0] == "native":
        rows = _jsonl(run_dir / "agent/rounds.jsonl")
        if not 0 <= index < len(rows):
            raise IndexError(index)
        row = rows[index]
        incoming, output = _native_items(row, full=True)
        return {"kind": "native", "index": index, "input": incoming, "output": output,
                "tools": _read_json(run_dir / "tools.json") or [],
                "meta": {k: row.get(k) for k in ("iteration", "started_at", "finished_at", "usage",
                                                  "finish_reason", "stop_reason", "error", "tool_events")}}
    directories = sorted((run_dir / EXCHANGES).glob("exchange_*"))
    if not 0 <= index < len(directories):
        raise IndexError(index)
    directory = directories[index]
    request = _read_gz_json(directory / "request.codex.json.gz") or {}
    meta = _read_json(directory / "meta.json") or {}
    output, _ = _response_output(directory)
    provider = _read_gz_json(directory / "request.provider.json.gz") or {}
    declared = list(request.get("tools") or [])
    for item in request.get("input") or []:
        if item.get("type") == "additional_tools":
            declared += item.get("tools") or []
    tools = []
    for tool in declared:
        entry = {"name": tool.get("name"), "type": tool.get("type"), "description": tool.get("description") or ""}
        if tool.get("tools"):
            entry["tools"] = [{"name": t.get("name"), "description": t.get("description") or "",
                               "parameters": t.get("parameters") or t.get("format")} for t in tool["tools"]]
        else:
            entry["parameters"] = tool.get("parameters")
        tools.append(entry)
    return {
        "index": index,
        "exchange": directory.name,
        "instructions": request.get("instructions") or "",
        "input": [describe_item(item, full=True) for item in request.get("input") or []
                  if item.get("type") != "additional_tools"],
        "output": [describe_item(item, full=True) for item in output],
        "tools": tools,
        "request_settings": {k: request.get(k) for k in ("model", "reasoning", "tool_choice",
                                                         "parallel_tool_calls", "store", "stream")},
        "provider_request": {
            "endpoint": meta.get("request_url"),
            "wire_profile": meta.get("wire_profile"),
            "settings": {k: v for k, v in provider.items() if k not in ("messages", "tools")},
            "messages": [_provider_message(m) for m in provider.get("messages") or []],
            "tool_count": len(provider.get("tools") or []),
        },
        "meta": {k: meta.get(k) for k in ("started_at", "finished_at", "provider_attempts", "usage",
                                          "context_policy", "error")},
    }


def _provider_message(message: dict[str, Any]) -> dict[str, Any]:
    """A Chat Completions message as sent upstream, with inline images summarized."""
    content = message.get("content")
    texts, images = [], 0
    if isinstance(content, str):
        texts.append(content)
    for part in content if isinstance(content, list) else []:
        if part.get("type") == "text":
            texts.append(str(part.get("text", "")))
        elif part.get("type") == "image_url":
            images += 1
    calls = [{"name": (c.get("function") or {}).get("name"), "arguments": (c.get("function") or {}).get("arguments")}
             for c in message.get("tool_calls") or []]
    return {"role": message.get("role"), "text": "\n".join(texts), "images": images,
            "tool_calls": calls, "tool_call_id": message.get("tool_call_id")}


#: Files a reader may open from a run directory (relative paths, no traversal).
READABLE = re.compile(r"^(images|monitor_media)/[\w.\-/]+\.(png|jpe?g|webp)$|^(direct/)?[\w.\-]+\.(md|json)$")


def run_file(run_dir: Path, relative: str) -> Path:
    if not READABLE.match(relative) or ".." in relative:
        raise PermissionError(relative)
    path = (run_dir / relative).resolve()
    if not path.is_relative_to(run_dir.resolve()) or not path.is_file():
        raise FileNotFoundError(relative)
    return path
