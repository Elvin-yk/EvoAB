"""``evoab runs | providers | benchmarks | usage | settings | studio`` commands.

Each command calls the same :class:`Workbench` method as the matching Studio
page. ``--json`` prints the exact payload the Studio API returns, for scripts
and other agents. API keys are read from standard input or an environment
variable, never from command-line arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

COMMANDS = ("runs", "models", "providers", "benchmarks", "usage", "settings", "studio")


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _table(rows: list[list[Any]], header: list[str]) -> None:
    cells = [header, *[["—" if c is None else str(c) for c in row] for row in rows]]
    widths = [max(len(row[i]) for row in cells) for i in range(len(header))]
    for index, row in enumerate(cells):
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
        if index == 0:
            print("  ".join("-" * w for w in widths))


def _when(epoch: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch)) if epoch else "—"


def _tokens(n: int | None) -> str:
    if not n:
        return "0"
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else str(n)


# -- runs -----------------------------------------------------------------------


def _runs_list(bench, args) -> None:
    runs = bench.runs()
    if args.status:
        runs = [r for r in runs if r["status"] == args.status]
    runs = runs[: args.limit] if args.limit else runs
    if args.json:
        _print_json(runs)
        return
    _table([[f"#{r['short_id']}", r["status"], (r.get("task") or "").split("/")[-1][:48], r.get("benchmark"),
             r.get("model"), r.get("attempt"), r.get("environment_steps"), f"{r.get('elapsed_s') or 0:.0f}s",
             _tokens(r["usage"].get("total_tokens")), _when(r.get("started_at"))] for r in runs],
           ["ID", "STATUS", "TASK", "SUITE", "MODEL", "N", "STEPS", "TIME", "TOKENS", "STARTED"])


def _runs_show(bench, args) -> None:
    run = bench.find_run(args.run)
    if args.json:
        _print_json(run)
        return
    for key in ("short_id", "id", "launch_id", "status", "integration", "benchmark", "task", "method", "provider",
                "model", "reasoning_effort", "attempt", "environment_steps", "max_steps", "elapsed_s", "error",
                "agent_message"):
        if run.get(key) not in (None, ""):
            print(f"{key:18} {run[key]}")
    print(f"{'started':18} {_when(run.get('started_at'))}")
    usage = run.get("usage") or {}
    print(f"{'usage':18} {usage.get('requests', 0)} requests, {_tokens(usage.get('input_tokens'))} in "
          f"({_tokens(usage.get('cached_input_tokens'))} cached), {_tokens(usage.get('output_tokens'))} out")


def _runs_trace(bench, args) -> None:
    run = bench.find_run(args.run)
    if not run.get("id"):
        sys.exit(f"run #{run['short_id']} has no run directory yet")
    if args.round is not None:
        _print_json(bench.round(run["id"], args.round - 1))
        return
    trace = bench.trace(run["id"])
    if args.json:
        _print_json(trace)
        return
    if trace.get("kind") == "direct":
        from evoab.workbench.direct_trace import summary_lines
        print("\n".join(summary_lines(trace)))
        for line in (trace.get("ending") or {}).get("chain", []):
            print("  " + line)
        return
    t0 = trace["t0"] or 0
    clock = lambda t: f"+{int((t or t0) - t0) // 60:02d}:{int((t or t0) - t0) % 60:02d}"  # noqa: E731
    for stage in trace["stages"]:
        print(f"{clock(stage.get('at'))}  [code]     {stage['title']}")
    calls = {c["call_id"]: c for c in trace["calls"]}
    for r in trace["rounds"]:
        u = r["usage"]
        print(f"{clock(r['started_at'])}  [round {r['index'] + 1:>3}] {r['trigger']['kind']:<18} "
              f"{r['duration_s'] or 0:6.1f}s  in {_tokens(u['input'])} (cached {_tokens(u['cached'])})  out {_tokens(u['output'])}")
        for item in r["output"]:
            if item["type"] == "message" and item.get("text"):
                print(f"           say: {item['text'][:160]!r}")
        for call_id in r["calls"]:
            call = calls[call_id]
            used = f"→ round {call['used_in_round'] + 1}" if call["used_in_round"] is not None else "→ unused"
            facts = call.get("robot") or {}
            effect = (f" steps {facts['native_steps']}" + (f" stop={facts['stop_reason']}" if facts.get("stop_reason") else "")
                      if facts.get("native_steps") is not None else "")
            print(f"           call {call['name']} [{call['executor']}] {call['duration_s'] or 0:.2f}s{effect} {used}")
            for nested in call["nested"]:
                facts = nested.get("robot") or {}
                steps = f" steps {facts['native_steps']}" if facts.get("native_steps") is not None else ""
                print(f"             └ {nested['name']} {nested['duration_s'] or 0:.2f}s{steps}")
    if trace.get("ending"):
        print("ending:")
        for line in trace["ending"]["chain"]:
            print(f"  - {line}")


def _models(bench, args) -> None:
    data = bench.models_overview()
    if args.json:
        _print_json(data)
        return
    if not data["available"]:
        sys.exit(data["error"])
    print(f"nanobot config: {data['config']}")
    print("providers:")
    for p in data["providers"]:
        print(f"  {p['name']:<14} {p['label']:<20} {p['api_base'] or '(built-in)'}  key={'yes' if p['has_key'] else 'no'}")
    print("presets (use as --model):")
    for p in data["presets"]:
        print(f"  {p['name']:<28} {p['provider']:<12} {p['model']}  effort={p['reasoning_effort'] or '-'}  max_tokens={p['max_tokens']}")


def _runs_launch(bench, args) -> None:
    settings = bench.settings()
    payload = {
        "integration": args.integration,
        "profile": args.profile,
        "task": args.task,
        "provider_id": args.provider or settings.get("default_provider"),
        "model": args.model or settings.get("default_model"),
        "reasoning_effort": args.effort or settings.get("default_effort") or "low",
        "method": args.method or settings.get("default_method") or "direct_policy",
    }
    direct = {key: getattr(args, key) for key in ("representation", "horizon", "history", "prompt_blocks")
              if getattr(args, key) is not None}
    if direct:
        payload["direct"] = direct
    result = bench.launch(payload)
    if args.json:
        _print_json(result)
    else:
        print(f"launched #{result['short_id']} (launch {result['launch_id']}); follow with: evoab runs show {result['short_id']}")


def _runs_stop(bench, args) -> None:
    run = bench.find_run(args.run)
    if not run.get("launch_id"):
        sys.exit("only runs started from Studio or `evoab runs launch` can be stopped here")
    result = bench.stop(run["launch_id"])
    _print_json(result) if args.json else print(f"stop requested for #{run['short_id']}")


def _runs_log(bench, args) -> None:
    run = bench.find_run(args.run)
    if not run.get("launch_id"):
        sys.exit("this run has no launcher log")
    print(bench.log(run["launch_id"], args.lines))


# -- providers ------------------------------------------------------------------


def _read_key(args) -> str | None:
    if args.key_env:
        key = os.environ.get(args.key_env)
        if not key:
            sys.exit(f"environment variable {args.key_env} is empty")
        return key
    if args.key_stdin:
        return sys.stdin.readline().strip()
    return None


def _providers(bench, args) -> None:
    action = args.action
    if action == "list":
        items = bench.providers()
        if args.json:
            _print_json(items)
            return
        _table([[p["id"], p["label"], p["base_url"], p["key_masked"] or "(no key)",
                 ("ok" if p["connection"]["ok"] else "failed") if p.get("connection") else "untested",
                 len(p["recommended"])] for p in items], ["ID", "NAME", "URL", "KEY", "CONNECTION", "VERIFIED"])
        return
    if action == "add":
        payload = {"label": args.label or args.id, "base_url": args.base_url, "profile": args.profile}
        key = _read_key(args)
        if key:
            payload["api_key"] = key
        result = bench.save_provider(args.id, payload)
    elif action == "remove":
        result = bench.delete_provider(args.id)
    elif action == "test":
        result = bench.test_provider(args.id)
    elif action == "models":
        result = bench.models(args.id)
        if not args.json:
            caps = result["capabilities"]
            _table([[m, "yes" if caps.get(m, {}).get("vision") else ("no" if m in caps else "?"),
                     "yes" if caps.get(m, {}).get("tools") else ("no" if m in caps else "?")] for m in result["models"]],
                   ["MODEL", "IMAGES", "TOOLS"])
            return
    elif action == "probe":
        result = bench.probe(args.id, args.model)
    elif action == "quota":
        result = bench.quota(args.id)
    _print_json(result)


# -- benchmarks, usage, settings --------------------------------------------------


def _benchmarks(bench, args) -> None:
    if args.action == "list":
        items = bench.benchmarks()
        if args.json:
            _print_json(items)
            return
        _table([[b["integration"], b["title"], b["kind"], p["profile"], p["status"], p["task_count"]]
                for b in items for p in b["profiles"]], ["INTEGRATION", "TITLE", "KIND", "PROFILE", "STATUS", "TASKS"])
    elif args.action == "tasks":
        tasks = bench.tasks(args.integration, args.profile)
        if args.suite:
            tasks = [t for t in tasks if t["suite"] == args.suite]
        if args.json:
            _print_json(tasks)
            return
        _table([[t["task"], t.get("embodiment"), t.get("max_steps"), (t.get("instruction") or "")[:60]] for t in tasks],
               ["TASK", "EMBODIMENT", "MAX_STEPS", "INSTRUCTION"])
    elif args.action == "prepare":
        _print_json(bench.prepare(args.integration, args.profile))


def _usage(bench, args) -> None:
    totals = bench.usage()
    if args.json:
        _print_json(totals)
        return
    _table([[row["key"], row.get("runs"), row.get("success"), row.get("requests"), _tokens(row.get("input_tokens")),
             _tokens(row.get("cached_input_tokens")), _tokens(row.get("output_tokens"))] for row in totals["by_model"]],
           ["API · MODEL", "RUNS", "OK", "REQUESTS", "IN", "CACHED", "OUT"])


def _settings(bench, args) -> None:
    if args.action == "set":
        _print_json(bench.update_settings({args.key: args.value or None}))
    else:
        _print_json(bench.config() if args.json else bench.settings())


# -- parser -----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evoab", allow_abbrev=False)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--runs-root", type=Path, default=None, help="Run directory (default: $EVOAB_RUNS_ROOT or ~/evoab-runs)")
    common.add_argument("--json", action="store_true", help="Print the machine-readable payload")
    sub = parser.add_subparsers(dest="command", required=True)

    runs = sub.add_parser("runs", help="List, launch, inspect and stop runs").add_subparsers(dest="action", required=True)
    p = runs.add_parser("list", parents=[common], help="List runs, newest first")
    p.add_argument("--status", choices=("running", "starting", "success", "failure", "error", "interrupted", "failed"))
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(handler=_runs_list)
    for name, handler, help_text in (("show", _runs_show, "Summary of one run"),
                                     ("stop", _runs_stop, "Stop a running run"),
                                     ("log", _runs_log, "Launcher log of a run")):
        p = runs.add_parser(name, parents=[common], help=help_text)
        p.add_argument("run", help="Short ID (e.g. 721e018e), run directory name or launch ID")
        if name == "log":
            p.add_argument("--lines", type=int, default=200)
        p.set_defaults(handler=handler)
    p = runs.add_parser("trace", parents=[common], help="Step-by-step account of a run (rounds, tool calls, ending)")
    p.add_argument("run")
    p.add_argument("--round", type=int, help="Full input and output of round N (1-based), as JSON")
    p.set_defaults(handler=_runs_trace)
    p = runs.add_parser("launch", parents=[common], help="Start a run in the background (same as Studio)")
    p.add_argument("--integration", default="libero_pro")
    p.add_argument("--profile", default="default", help="Prepared runtime profile")
    p.add_argument("--task", required=True, help="Task ID as listed by `evoab benchmarks tasks`, e.g. libero_object/pick_up_...")
    p.add_argument("--provider", help="Provider ID (default: settings.default_provider)")
    p.add_argument("--model", help="Model ID (default: settings.default_model)")
    p.add_argument("--effort", help="Reasoning effort (default: settings.default_effort or low)")
    p.add_argument("--method", help="Method ID: direct_policy (nanobot preset as --model) or direct (Codex agent); "
                                    "default: settings.default_method or direct_policy")
    p.add_argument("--representation", help="Direct policy: abs_eef or delta_eef")
    p.add_argument("--horizon", help="Direct policy: rows per chunk, MIN:MAX")
    p.add_argument("--history", help="Direct policy: observation window and stride, LENGTH:STRIDE")
    p.add_argument("--prompt-blocks", dest="prompt_blocks", help="Direct policy: e.g. robot,timing,gripper,camera")
    p.set_defaults(handler=_runs_launch)

    models = sub.add_parser("models", parents=[common], help="nanobot providers and model presets (edited in nanobot's WebUI)")
    models.set_defaults(handler=_models)

    prov = sub.add_parser("providers", help="Manage model API providers").add_subparsers(dest="action", required=True)
    p = prov.add_parser("list", parents=[common])
    p.set_defaults(handler=_providers)
    p = prov.add_parser("add", parents=[common], help="Add or update a provider")
    p.add_argument("id")
    p.add_argument("--base-url", required=True)
    p.add_argument("--label")
    p.add_argument("--profile", default="omnirouter.chat_completions", help="Wire profile")
    key = p.add_mutually_exclusive_group()
    key.add_argument("--key-stdin", action="store_true", help="Read the API key from the first line of standard input")
    key.add_argument("--key-env", metavar="VAR", help="Read the API key from this environment variable")
    p.set_defaults(handler=_providers)
    for name in ("remove", "test", "models", "quota"):
        p = prov.add_parser(name, parents=[common])
        p.add_argument("id")
        p.set_defaults(handler=_providers)
    p = prov.add_parser("probe", parents=[common], help="Check that a model reads images and calls tools")
    p.add_argument("id")
    p.add_argument("model")
    p.set_defaults(handler=_providers)

    bm = sub.add_parser("benchmarks", help="Benchmarks, their tasks and preparation").add_subparsers(dest="action", required=True)
    p = bm.add_parser("list", parents=[common])
    p.set_defaults(handler=_benchmarks)
    for name in ("tasks", "prepare"):
        p = bm.add_parser(name, parents=[common])
        p.add_argument("integration")
        p.add_argument("--profile", default="default")
        if name == "tasks":
            p.add_argument("--suite")
        p.set_defaults(handler=_benchmarks)

    p = sub.add_parser("usage", parents=[common], help="Token usage by API and model")
    p.set_defaults(handler=_usage)

    st = sub.add_parser("settings", help="Show or change Studio defaults").add_subparsers(dest="action")
    p = st.add_parser("show", parents=[common])
    p.set_defaults(handler=_settings)
    p = st.add_parser("set", parents=[common])
    p.add_argument("key")
    p.add_argument("value", nargs="?")
    p.set_defaults(handler=_settings)
    return parser


def main(argv: list[str]) -> int:
    if argv and argv[0] == "studio":
        from evoab.ui.studio.server import main as studio_main
        studio_main(argv[1:])
        return 0
    from evoab.workbench import Workbench, WorkbenchError

    args = build_parser().parse_args(argv)
    if args.command == "settings" and not getattr(args, "action", None):
        args.action, args.json, args.runs_root, args.handler = "show", False, None, _settings
    bench = Workbench(args.runs_root)
    try:
        args.handler(bench, args)
    except WorkbenchError as error:
        print(f"evoab {args.command}: {error}", file=sys.stderr)
        return 2
    return 0
