"""``evoab runs | methods | models | benchmarks | usage | settings | studio`` commands.

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

COMMANDS = ("runs", "methods", "models", "benchmarks", "usage", "settings", "studio")


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
    action = args.action or "list"
    if action == "list":
        data = bench.model_settings()
        if args.json:
            _print_json(data)
            return
        print("presets (use as --model):")
        _table([[p["name"], p.get("resolved_provider") or p["provider"], p["model"], p.get("reasoning_effort") or "-",
                 p.get("max_tokens"), p.get("context_window_tokens")] for p in data["presets"]],
               ["PRESET", "PROVIDER", "MODEL", "EFFORT", "MAX_OUT", "CONTEXT"])
        print("\nproviders:")
        _table([[p["name"], p.get("label"), p.get("api_base") or p.get("default_api_base") or "(built-in)",
                 ",".join((p.get("api") or {}).get("supported_apis") or p.get("request_apis") or [])]
                for p in data["providers"] if p.get("configured")], ["KEY", "NAME", "API BASE", "APIS"])
        return
    if action == "catalog":
        result = bench.provider_models(args.provider, args.refresh)
        if args.json:
            _print_json(result)
        else:
            for row in result.get("models") or []:
                print(row.get("id") if isinstance(row, dict) else row)
        return
    if action in ("add-provider", "set-provider"):
        fields = {"name": args.name, "api_base": args.base_url, "thinking_style": args.reasoning_format}
        if args.apis:
            apis = args.apis.split(",")
            fields["api"] = {"supportedApis": apis, "preferredApi": args.preferred_api or apis[0]}
        if key := _read_key(args):
            fields["api_key"] = key
        if action == "add-provider":
            result = bench.save_provider(fields)
        else:
            fields.pop("name")
            result = bench.save_provider({k: v for k, v in fields.items() if v is not None}, existing=args.name)
    elif action == "remove-provider":
        result = bench.delete_provider(args.name)
    elif action in ("add-preset", "edit-preset"):
        fields = {"name": args.new_name if action == "edit-preset" else args.name, "provider": args.provider,
                  "model": args.model, "reasoning_effort": args.effort, "max_tokens": args.max_tokens,
                  "context_window_tokens": args.context, "temperature": args.temperature}
        fields = {k: v for k, v in fields.items() if v is not None}
        result = bench.save_preset(fields, existing=args.name if action == "edit-preset" else None)
    elif action == "remove-preset":
        result = bench.delete_preset(args.name)
    if args.json:
        _print_json(result)
    else:
        print("saved")


def _runs_launch(bench, args) -> None:
    from evoab.workbench.launcher import preferred_choice

    settings = bench.settings()
    method, agent = args.method, args.agent
    if method is None:
        method, saved_agent = preferred_choice(args.integration, settings.get("default_method"),
                                               settings.get("default_agent"))
        agent = agent or saved_agent
    params = {}
    for text in args.set or ():
        name, sep, value = text.partition("=")
        if not sep:
            sys.exit(f"--set expects PATH=VALUE, got {text!r}")
        params[name] = value
    payload = {
        "integration": args.integration,
        "profile": args.profile,
        "task": args.task,
        "model": args.model or settings.get("default_model"),
        "reasoning_effort": args.effort or settings.get("default_effort") or "low",
        "method": method,
    }
    if agent is not None:
        payload["agent"] = agent
    if params:
        payload["params"] = params
    result = bench.launch(payload)
    if args.json:
        _print_json(result)
    else:
        print(f"launched #{result['short_id']} (launch {result['launch_id']}); follow with: evoab runs show {result['short_id']}")


# -- methods ------------------------------------------------------------------


def _methods(bench, args) -> None:
    action = getattr(args, "action", None) or "list"
    if action == "list":
        items = bench.methods(args.integration)
        if args.json:
            _print_json(items)
            return
        _table([[m["id"], m["channel"], len(m["roles"]), m["label"], m["file"]] for m in items],
               ["ID", "CHANNEL", "ROLES", "LABEL", "RECIPE"])
    elif action == "components":
        catalog = bench.components(args.integration)
        if args.json:
            _print_json(catalog)
            return
        for slot, items in catalog.items():
            print(f"{slot}:")
            for item in items:
                tools = f" [{', '.join(item['tools'])}]" if item.get("tools") else ""
                print(f"  {item['id']:20} {item['label']}{tools}")
    elif action == "validate":
        from evoab.composition.compiler import compile_recipe
        from evoab.composition.methods import get_method
        from evoab.integrations import load_integration
        method = get_method(args.recipe)
        compiled = compile_recipe(method.recipe, load_integration(args.integration).get_integration_spec(),
                                  base_dir=method.root)
        for name, role in compiled.roles.items():
            print(f"{name:12} {role.agent or '(run agent)':12} {role.channel:7} tools={', '.join(role.selected_tools) or '—'}"
                  + (f" delegates={', '.join(role.delegates)}" if role.delegates else ""))
        print(f"{method.id}: valid for {args.integration}")


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


# -- keys -----------------------------------------------------------------------


def _read_key(args) -> str | None:
    if getattr(args, "key_env", None):
        key = os.environ.get(args.key_env)
        if not key:
            sys.exit(f"environment variable {args.key_env} is empty")
        return key
    if getattr(args, "key_stdin", False):
        return sys.stdin.readline().strip()
    return None


# -- benchmarks, usage, settings --------------------------------------------------


def _dimension(task: dict) -> str:
    dimension = (task.get("category") or "—").replace("_", "-")
    return f"{dimension} ({task['variant']})" if task.get("variant") else dimension


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _benchmarks(bench, args) -> None:
    if args.action == "list":
        items = bench.benchmarks()
        if args.json:
            _print_json(items)
            return
        # Benchmarks only (real robots are not benchmarks); ready when any profile is prepared.
        _table([[b["integration"], b["title"],
                 "ready" if any(p["status"] == "ready" for p in b["profiles"]) else "not prepared"]
                for b in items if b["kind"] != "real"],
               ["INTEGRATION", "TITLE", "STATUS"])
    elif args.action == "tasks":
        tasks = bench.tasks(args.integration, args.profile)
        if args.suite:
            tasks = [t for t in tasks if t["suite"] == args.suite]
        if args.json:
            _print_json(tasks)
            return
        embodiments = {t.get("embodiment") for t in tasks}
        dimensions = any(t.get("category") for t in tasks)
        if len(embodiments) == 1 and None not in embodiments:
            print(f"Robot: {embodiments.pop()}")
            embodiments = set()
        _table([[t["task"], *([_dimension(t)] if dimensions else []), *([t.get("embodiment")] if embodiments else []),
                 t.get("max_steps") or "—", _clip(t.get("instruction") or "—", 70)] for t in tasks],
               ["TASK", *(["DIMENSION"] if dimensions else []), *(["ROBOT"] if embodiments else []),
                "MAX_STEPS", "INSTRUCTION"])
        layouts = sorted({t["layouts_per_seed"] for t in tasks if t.get("layouts_per_seed")})
        if layouts:
            seeds = sorted({s for t in tasks for s in t.get("seeds") or ()})
            print(f"\nScenes: --seed {seeds[0]}..{seeds[-1]} picks the evaluation set, --layout-id the scene in it "
                  f"(0..{layouts[0] - 1} for generalization tasks, 0..{layouts[-1] - 1} for the others).")
    elif args.action == "prepare":
        _print_json(bench.prepare(args.integration, args.profile, accept_license=args.accept_license))
    elif args.action == "check":
        result = bench.system_check()
        if args.json:
            _print_json(result)
            return
        for check in result["checks"]:
            print(f"{'ok ' if check['ok'] else 'NO '} {check['key']:<15} {check['detail']}" + (f"\n      fix: {check['fix']}" if check["fix"] else ""))


def _usage(bench, args) -> None:
    data = {"calls": bench.model_usage(), "runs": bench.usage()}
    if args.json:
        _print_json(data)
        return
    calls = data["calls"]
    print(f"model calls, last 30 days: {calls.get('requests_30d', 0)} requests, "
          f"{_tokens(calls.get('total_tokens_30d'))} tokens ({calls.get('failed_requests_30d', 0)} failed)")
    _table([[row["provider"], row["model"], row["requests"], _tokens(row["input_tokens"]),
             _tokens(row["cache_read_tokens"]), _tokens(row["output_tokens"])] for row in calls.get("providers_30d", [])],
           ["PROVIDER", "MODEL", "REQUESTS", "IN", "CACHED", "OUT"])
    print("\nby run:")
    _table([[row["key"], row.get("runs"), row.get("success"), row.get("requests"), _tokens(row.get("input_tokens")),
             _tokens(row.get("output_tokens"))] for row in data["runs"]["by_model"]],
           ["MODEL", "RUNS", "OK", "REQUESTS", "IN", "OUT"])


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
    p.add_argument("--model", help="Model preset (default: the last one launched)")
    p.add_argument("--effort", help="Reasoning effort (default: settings.default_effort or low)")
    p.add_argument("--method", help="Method id from `evoab methods list` (default: the last one launched)")
    p.add_argument("--agent", help="Override the method's entry agent runtime (for example native or codex)")
    p.add_argument("--set", action="append", default=[], metavar="PATH=VALUE",
                   help="Override a recipe value by dotted path, e.g. --set 'roles.main.action.horizon=[16,32]'")
    p.set_defaults(handler=_runs_launch)

    me = sub.add_parser("methods", help="The method library and its components").add_subparsers(dest="action")
    p = me.add_parser("list", parents=[common], help="Methods, their channels, paradigms and agents")
    p.add_argument("--integration", help="Only methods usable on this integration")
    p.set_defaults(handler=_methods)
    p = me.add_parser("components", parents=[common], help="Building blocks a recipe can name")
    p.add_argument("--integration", help="Include this integration's tool modules")
    p.set_defaults(handler=_methods)
    p = me.add_parser("validate", parents=[common], help="Compile a method id or recipe file against an integration")
    p.add_argument("recipe")
    p.add_argument("--integration", required=True)
    p.set_defaults(handler=_methods)

    models = sub.add_parser("models", parents=[common], help="Model providers and presets (default: list)")
    models.set_defaults(handler=_models)
    mo = models.add_subparsers(dest="action")
    p = mo.add_parser("list", parents=[common], help="Presets and configured providers")
    p.set_defaults(handler=_models)
    p = mo.add_parser("catalog", parents=[common], help="Models a provider offers")
    p.add_argument("provider")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(handler=_models)
    for name, help_text in (("add-provider", "Add an OpenAI-compatible or Anthropic-compatible provider"),
                            ("set-provider", "Change a provider (also sets keys of built-in providers)")):
        p = mo.add_parser(name, parents=[common], help=help_text)
        p.add_argument("name", help="Display name (add-provider) or provider key (set-provider)")
        p.add_argument("--base-url", required=name == "add-provider")
        p.add_argument("--apis", help="Supported request APIs, e.g. chat_completions,responses or anthropic_messages")
        p.add_argument("--preferred-api")
        p.add_argument("--reasoning-format", choices=("thinking_type", "enable_thinking", "reasoning_split"))
        key = p.add_mutually_exclusive_group()
        key.add_argument("--key-stdin", action="store_true", help="Read the API key from the first line of standard input")
        key.add_argument("--key-env", metavar="VAR", help="Read the API key from this environment variable")
        p.set_defaults(handler=_models)
    p = mo.add_parser("remove-provider", parents=[common])
    p.add_argument("name")
    p.set_defaults(handler=_models)
    for name in ("add-preset", "edit-preset"):
        p = mo.add_parser(name, parents=[common], help="Create or change a model preset")
        p.add_argument("name")
        if name == "edit-preset":
            p.add_argument("--new-name")
        p.add_argument("--provider", required=name == "add-preset")
        p.add_argument("--model", required=name == "add-preset")
        p.add_argument("--effort")
        p.add_argument("--max-tokens", type=int)
        p.add_argument("--context", type=int, help="Context window tokens")
        p.add_argument("--temperature", type=float)
        p.set_defaults(handler=_models)
    p = mo.add_parser("remove-preset", parents=[common])
    p.add_argument("name")
    p.set_defaults(handler=_models)

    bm = sub.add_parser("benchmarks", help="Benchmarks, their tasks and preparation").add_subparsers(dest="action", required=True)
    p = bm.add_parser("list", parents=[common])
    p.set_defaults(handler=_benchmarks)
    for name in ("tasks", "prepare"):
        p = bm.add_parser(name, parents=[common])
        p.add_argument("integration")
        p.add_argument("--profile", default="default")
        if name == "tasks":
            p.add_argument("--suite")
        else:
            p.add_argument("--accept-license", metavar="NAME", help="Accept the licence the container recipe names")
        p.set_defaults(handler=_benchmarks)

    p = bm.add_parser("check", parents=[common], help="Check Docker, GPU containers, disk and model presets for deployment")
    p.set_defaults(handler=_benchmarks)
    p = sub.add_parser("usage", parents=[common], help="Recorded model calls and token usage by run")
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
    if args.command == "methods" and not getattr(args, "action", None):
        args.action, args.json, args.runs_root, args.handler, args.integration = "list", False, None, _methods, None
    bench = Workbench(args.runs_root)
    try:
        args.handler(bench, args)
    except (WorkbenchError, ValueError) as error:
        print(f"evoab {args.command}: {error}", file=sys.stderr)
        return 2
    return 0
