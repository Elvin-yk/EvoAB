"""Replay data for Direct-policy runs (records under ``direct/``).

Every control step has a frame (all cameras + measured state); every inference
has its request, attempts, the decoded chunk and what each executed row did.
The viewer plays frames at the control rate and can insert each inference's
real wait, so the timeline shows both simulated and wall-clock time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evoab.workbench import trace as base


def is_direct(run_dir: Path) -> bool:
    return (run_dir / "direct" / "policy.json").is_file()


def _relative(run_dir: Path, path: str) -> str:
    """Run-relative path; older records stored absolute paths, possibly from another location."""
    candidate = Path(path)
    if not candidate.is_absolute():
        return path
    try:
        return str(candidate.resolve().relative_to(run_dir.resolve()))
    except ValueError:
        parts = candidate.parts
        if "images" in parts:
            return str(Path(*parts[len(parts) - 1 - parts[::-1].index("images"):]))
        return path


def _sum_usage(attempts: list[dict[str, Any]]) -> dict[str, int]:
    total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cached_input_tokens": 0}
    for attempt in attempts:
        for key in total:
            total[key] += int((attempt.get("usage") or {}).get(key) or 0)
    return total


def _ending(run_dir: Path, result: dict | None, calls: list[dict], policy: dict, steps: int) -> dict | None:
    if result is None:
        return None
    native = base._read_json(run_dir / "native_outcome.json") or {}
    budget = policy.get("max_episode_steps")
    steps = native.get("native_steps", steps)
    chain = []
    if result.get("error"):
        chain.append(f"运行出错或被中断：{result['error']}")
    stop = result.get("stop_reason")
    if budget and steps is not None and steps >= budget:
        chain.append(f"环境步数用尽：{steps} / {budget} 步。Direct 策略在回合结束前持续推理并执行动作块。")
    elif stop == "call_limit":
        chain.append(f"达到推理次数上限（{len(calls)} 次）后停止。")
    elif stop == "episode_ended":
        chain.append(f"环境在第 {steps} 步结束回合。")
    holds = [c["index"] for c in calls if c.get("safe_hold")]
    if holds:
        chain.append(f"第 {', '.join(map(str, holds))} 次推理没有得到合法动作块，执行了一步原地保持。")
    verdict = result.get("task_success")
    chain.append("环境验证器判定：" + ("任务成功。" if verdict is True else "任务未完成。" if verdict is False else "无判定。"))
    return {"task_success": verdict, "failure_category": result.get("failure_category"), "stop_reason": stop,
            "native_outcome": native, "steps": steps, "step_limit": budget, "chain": chain}


def build(run_dir: Path) -> dict[str, Any]:
    root = run_dir / "direct"
    policy = base._read_json(root / "policy.json") or {}
    result = base._read_json(run_dir / "result.json")
    monitor = base._read_json(run_dir / "monitor.json") or {}
    frames = [{"step": f["step"], "t": f.get("captured_at"), "proprio": f["proprio"],
               "images": {name: _relative(run_dir, path) for name, path in f["images"].items()}}
              for f in base._jsonl(root / "frames.jsonl")]
    calls = []
    for record in base._jsonl(root / "calls.jsonl"):
        attempts = [{key: a.get(key) for key in ("attempt", "started_at", "latency_s", "finish_reason", "usage",
                                                  "error", "reasoning", "content")}
                    for a in record.get("attempts", [])]
        executed = record.get("executed", [])
        last = attempts[-1] if attempts else {}
        calls.append({
            **{key: record.get(key) for key in (
                "index", "start_step", "observed_steps", "requested_at", "request_build_s", "api_latency_s",
                "execution_s", "output_attempts", "error", "safe_hold", "safety_clamped", "tool", "note",
                "targets", "clamped", "horizon", "request", "execution_error")},
            "responded_at": (last.get("started_at") or 0) + (last.get("latency_s") or 0) if last else None,
            "end_step": executed[-1]["step"] if executed else record.get("start_step"),
            "executed": executed, "attempts": attempts, "usage": _sum_usage(record.get("attempts", [])),
        })
    steps = frames[-1]["step"] if frames else 0
    totals = {
        "calls": len(calls), "steps": steps,
        "api_s": round(sum(c["api_latency_s"] or 0 for c in calls), 2),
        "execution_s": round(sum(c["execution_s"] or 0 for c in calls), 2),
        "repairs": sum(max(0, (c["output_attempts"] or 1) - 1) for c in calls),
        "safe_holds": sum(1 for c in calls if c["safe_hold"]),
        "clamped_calls": sum(1 for c in calls if c["safety_clamped"]),
        **{key: sum(c["usage"][key] for c in calls) for key in ("input_tokens", "output_tokens", "total_tokens")},
    }
    t0 = monitor.get("started_at") or policy.get("started_at")
    t_end = monitor.get("finished_at") or (result or {}).get("finished_at")
    return {
        "kind": "direct", "t0": t0, "t_end": t_end, "live": result is None,
        "instruction": policy.get("instruction") or monitor.get("instruction"),
        "max_steps": policy.get("max_episode_steps"), "policy": {k: v for k, v in policy.items() if k != "blocks"},
        "prompt_blocks": policy.get("blocks") or {}, "frames": frames, "calls": calls, "totals": totals,
        "ending": _ending(run_dir, result, calls, policy, steps),
    }


def call_detail(run_dir: Path, index: int) -> dict[str, Any]:
    """Everything one inference saw and produced: request messages, tool schema, every attempt."""
    records = base._jsonl(run_dir / "direct" / "calls.jsonl")
    record = next((r for r in records if r.get("index") == index), None)
    if record is None:
        raise IndexError(index)
    request = base._read_json(run_dir / record["request"]) or {}
    messages = []
    for message in request.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            content = [{"type": "image", "path": _relative(run_dir, part["$evoab_image"])} if "$evoab_image" in part
                       else part for part in content]
        messages.append({"role": message.get("role"), "content": content})
    return {"index": index, "messages": messages, "tools": request.get("tools"),
            "tool_choice": request.get("tool_choice"), "model": request.get("model"),
            "attempts": record.get("attempts"), "arguments": record.get("arguments"),
            "targets": record.get("targets"), "executed": record.get("executed"),
            "request_file": record.get("request")}


def summary_lines(data: dict[str, Any]) -> list[str]:
    """Plain-text replay for ``evoab runs trace``."""
    policy, totals = data["policy"], data["totals"]
    lines = [f"Direct policy · {policy.get('representation_label')} · tool {policy.get('tool')} · "
             f"horizon {policy.get('horizon')} · {policy.get('control_hz')} Hz",
             f"{totals['calls']} inferences, {totals['steps']} control steps, API {totals['api_s']} s, "
             f"execution {totals['execution_s']} s, tokens {totals['total_tokens']}, repairs {totals['repairs']}, "
             f"safe holds {totals['safe_holds']}"]
    for call in data["calls"]:
        flags = [f for f, on in (("repaired", (call["output_attempts"] or 1) > 1), ("safe-hold", call["safe_hold"]),
                                 ("clamped", call["safety_clamped"])) if on]
        lines.append(f"  #{call['index']:>3} steps {call['start_step']}→{call['end_step']} "
                     f"({call['horizon']} rows) · {call['api_latency_s']} s · {call['usage']['total_tokens']} tok"
                     f"{' · ' + ', '.join(flags) if flags else ''}")
        if call.get("note"):
            lines.append(f"       {call['note'][:200]}")
    return lines

