"""``evoab runs | models | benchmarks | usage | settings | studio`` commands.

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

COMMANDS = ("runs", "models", "benchmarks", "usage", "settings", "studio", "research")


def _research(bench, args):
    if args.action == "launch":
        from evoab.research.physicalrsi.config import ResearchConfig
        values = {key: getattr(args, key) for key in ResearchConfig.model_fields if hasattr(args, key)}
        _print_json(bench.start_research(values))
    elif args.action in ("stop", "resume"):
        _print_json(bench.research_action(args.ref, args.action))
    elif args.action == "recycle":
        _print_json(bench.recycle_research(args.refs))
    else:
        _print_json(bench.research_campaigns())


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


def _runs_recycle(bench, args) -> None:
    if args.action == "recycle":
        result = bench.recycle_runs(args.refs)
    elif args.action == "trash":
        result = bench.recycled_runs()
    elif args.action == "restore":
        result = bench.restore_runs(args.ids)
    else:
        if not args.yes:
            raise ValueError("Permanent deletion requires --yes; use runs recycle for recoverable deletion")
        result = bench.purge_runs(args.ids)
    _print_json(result)


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
    if action in ("login", "logout"):
        if args.provider != "antigravity":
            sys.exit(f"CLI {action} currently supports 'antigravity' only (use Studio for other OAuth providers)")
        from evoab.provider import antigravity_acp, catalog
        catalog._activate()
        config_path = catalog.config_path()
        if action == "logout":
            antigravity_acp.logout(config_path)
            _print_json({"provider": "antigravity", "configured": False}) if args.json else print("logged out")
            return
        flow = antigravity_acp.AntigravityOAuthLoginFlow(config_path=config_path, open_browser=False)
        flow.start()
        try:
            print(f"Open this link in a browser to log in with Google:\n\n  {flow.authorization_url}\n")
            response = input("Paste the authorization code or full redirect URL and press Enter: ")
            flow.complete(response)
        except BaseException:
            flow.cancel()
            raise
        status = antigravity_acp.oauth_status()
        _print_json(status) if args.json else print("logged in")
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
        if agent is not None:
            method = "agentic"
        else:
            method, agent = preferred_choice(args.integration, settings.get("default_method"),
                                             settings.get("default_agent"))
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


def _benchmarks(bench, args) -> None:
    if args.action == "list":
        items = bench.benchmarks()
        if args.json:
            _print_json(items)
            return
        _table([[b["integration"], b["title"], b["kind"], p["profile"], p["status"], p["task_count"],
                 p.get("runtime_kind") or "-", ("image" if (b.get("container") or {}).get("source") == "image"
                                                else "build" if b.get("container") else "-")]
                for b in items for p in b["profiles"]],
               ["INTEGRATION", "TITLE", "KIND", "PROFILE", "STATUS", "TASKS", "RUNTIME", "DOCKER"])
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
        _print_json(bench.prepare(args.integration, args.profile, container=args.container,
                                  accept_license=args.accept_license))
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

    research = sub.add_parser("research", help="Full PhysicalRSI research campaigns").add_subparsers(dest="action", required=True)
    for action in ("list", "launch", "stop", "resume", "recycle"):
        p = research.add_parser(action, parents=[common])
        if action == "recycle":
            p.add_argument("refs", nargs="+", help="Completed research campaign IDs")
        if action == "launch":
            p.add_argument("--model", required=True)
            p.add_argument("--integration", default="metaworld")
            p.add_argument("--task", default="reach-v3")
            p.add_argument("--reasoning-effort", default="low")
            for field, default in (("rounds", 2), ("candidates", 1), ("development_cases", 1),
                                   ("validation_cases", 3), ("test_cases", 1), ("max_turns", 8),
                                   ("episode_seconds", 600), ("seed", 0), ("max_trials", 200)):
                p.add_argument("--" + field.replace("_", "-"), type=int, default=default)
        if action in ("stop", "resume"):
            p.add_argument("ref")
        p.set_defaults(handler=_research)

    runs = sub.add_parser("runs", help="List, launch, inspect and stop runs").add_subparsers(dest="action", required=True)
    p = runs.add_parser("list", parents=[common], help="List runs, newest first")
    p.add_argument("--status", choices=("running", "starting", "success", "failure", "error", "interrupted", "failed"))
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(handler=_runs_list)
    for action, help_text in (("recycle", "Move finished runs to the recycle bin"),
                              ("trash", "List recycled runs"),
                              ("restore", "Restore recycle-bin IDs"),
                              ("purge", "Permanently delete recycle-bin IDs")):
        p = runs.add_parser(action, parents=[common], help=help_text)
        if action == "recycle":
            p.add_argument("refs", nargs="+")
        elif action in ("restore", "purge"):
            p.add_argument("ids", nargs="+")
        if action == "purge":
            p.add_argument("--yes", action="store_true", help="Confirm irreversible deletion")
        p.set_defaults(handler=_runs_recycle)
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
    p.add_argument("--method", help="Execution mode (direct_policy, agentic, agent_policy) or legacy alias rua")
    p.add_argument("--agent", help="Registered Agent implementation (for example native or rua)")
    p.add_argument("--representation", help="Direct policy: abs_eef or delta_eef")
    p.add_argument("--horizon", help="Direct policy: rows per chunk, MIN:MAX")
    p.add_argument("--history", help="Direct policy: observation window and stride, LENGTH:STRIDE")
    p.add_argument("--prompt-blocks", dest="prompt_blocks", help="Direct policy: e.g. robot,timing,gripper,camera")
    p.set_defaults(handler=_runs_launch)

    models = sub.add_parser("models", parents=[common], help="Model providers and presets (default: list)")
    models.set_defaults(handler=_models)
    mo = models.add_subparsers(dest="action")
    p = mo.add_parser("list", parents=[common], help="Presets and configured providers")
    p.set_defaults(handler=_models)
    p = mo.add_parser("catalog", parents=[common], help="Models a provider offers")
    p.add_argument("provider")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(handler=_models)
    p = mo.add_parser("login", parents=[common], help="Sign in to an OAuth provider (e.g. antigravity)")
    p.add_argument("provider")
    p.set_defaults(handler=_models)
    p = mo.add_parser("logout", parents=[common], help="Sign out of an OAuth provider (e.g. antigravity)")
    p.add_argument("provider")
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
            p.add_argument("--container", action="store_true",
                           help="Pull or build the benchmark's Docker image instead of a native installation")
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
    bench = Workbench(args.runs_root)
    try:
        args.handler(bench, args)
    except WorkbenchError as error:
        print(f"evoab {args.command}: {error}", file=sys.stderr)
        return 2
    return 0
