"""Record reproducible cases from the upstream SimpleMemVLA RMBench evaluator."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


def append_json(directory, filename, record):
    with (directory / filename).open("a", encoding="utf-8") as journal:
        journal.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def select_cases(evaluator, args, tasks, seed_pool, terminal_outcomes, record_dir):
    """Use the native rollout once per seed until each task has a successful case."""
    import numpy as np

    candidates = {}
    for task in tasks:
        entry = seed_pool.get(task, {})
        instructions = entry.get("instr", {})
        candidates[task] = [
            (seed, instructions[str(seed)])
            for seed in dict.fromkeys(entry.get("seeds", ()))
            if instructions.get(str(seed))
        ][:args.seeds_per_task]
        if not candidates[task]:
            raise ValueError(f"No cached expert-admitted seeds with instructions for {task}")

    evaluator.torch.manual_seed(0)
    np.random.seed(0)
    policy = evaluator.build_policy(args)
    pool = evaluator.InProcSimPool(
        num_envs=1, repo_root=evaluator.VENDORED_SIM_DIR, task_config=args.task_config,
        instruction_type=args.instruction_type, step_timeout=args.step_timeout,
        reset_timeout=args.reset_timeout, dense_substeps=args.dense_substeps,
    )
    # Keep the simulator lifecycle used by the upstream single-process evaluator.
    # Its entry point writes the summary before exiting the native process.
    pool.start()
    per_seed = {task: {} for task in tasks}
    selected = []
    for task, seeds in candidates.items():
        trials = per_seed[task]
        video_dir = str(Path(args.video_dir) / task) if args.video_dir else None
        video_quota = {"succ": args.video_max_per_task, "fail": args.video_max_per_task}
        for seed, instruction in seeds:
            terminal_outcomes.clear()
            print(f"[selection] starting {task}, seed={seed}", flush=True)
            evaluator.run_group(
                args, pool, *policy, task,
                [{"task": task, "seed": seed, "instruction": instruction,
                  "expert_check": False, "max_seed_search": 1}],
                video_dir=video_dir, video_quota=video_quota,
            )
            outcome = terminal_outcomes.get(0, {})
            if outcome.get("seed") != seed or not outcome.get("done") or "error" in outcome:
                raise RuntimeError(f"No native terminal result for {task}, seed={seed}. Check the case logs.")
            success = outcome["success"] is True
            trials[seed] = [success]
            print(f"[selection] {task}, seed={seed}, native_success={success}", flush=True)
            if success:
                append_json(record_dir, "selected.jsonl", outcome)
                selected.append(outcome)
                print(f"[selection] selected {len(selected)}/{len(tasks)} distinct tasks", flush=True)
                break
        evaluator.torch.cuda.empty_cache()
    source = Path(evaluator.__file__).resolve().parents[1]
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    (record_dir / "manifest.json").write_text(json.dumps({
        "benchmark_revision": revision, "task_config": args.task_config,
        "instruction_type": args.instruction_type, "snapshot_directory": ".", "selected": selected,
    }, indent=2) + "\n", encoding="utf-8")
    return {
        "per_seed": per_seed,
        "per_task": {task: sum(row[0] for row in trials.values()) / len(trials)
                     for task, trials in per_seed.items()},
        "n": {task: len(trials) for task, trials in per_seed.items()},
        "oom": {},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-dir", type=Path, required=True, help="New directory for case records.")
    parser.add_argument("--select-successes", action="store_true",
                        help="Try cached seeds until the first native success for each task.")
    parser.add_argument("evaluator_args", nargs=argparse.REMAINDER, help="Upstream options after --.")
    args = parser.parse_args()
    upstream_args = args.evaluator_args
    if not upstream_args or upstream_args[0] != "--":
        parser.error("Pass upstream evaluator options after --")

    import numpy as np
    from rmbench_sim import eval_success
    from rmbench_sim.inproc_pool import InProcSimPool
    from rmbench_sim.rmbench_env import RMBenchSimEnv

    record_dir = args.record_dir.resolve()
    record_dir.mkdir(parents=True, exist_ok=False)
    parse_evaluator_args = eval_success.parse_args
    original_reset = InProcSimPool.reset
    original_step = InProcSimPool.step
    original_native_reset = RMBenchSimEnv.reset
    active_cases = {}
    terminal_outcomes = {}

    def parse_single_gpu_args():
        evaluator_args = parse_evaluator_args()
        if evaluator_args.num_gpus != 1:
            parser.error("Case recording requires the single-process evaluator (--num_gpus 1)")
        if args.select_successes:
            if evaluator_args.seeds_per_task < 1 or evaluator_args.group_size != 1 or evaluator_args.pipelined:
                parser.error("Selection requires a positive seed limit, --group_size 1 and serial inference")
        return evaluator_args

    def record_native_reset(env, seed, *reset_args, **reset_kwargs):
        try:
            return original_native_reset(env, seed, *reset_args, **reset_kwargs)
        except Exception as exc:
            failure = {"task": env.task_name, "seed": seed, "error": type(exc).__name__, "detail": str(exc)}
            append_json(record_dir, "reset_errors.jsonl", failure)
            print(f"[baseline] reset error: {failure}", flush=True)
            raise

    def record_reset(pool, specs):
        results = original_reset(pool, specs)
        for index, (spec, result) in enumerate(zip(specs, results)):
            result = result or {}
            case = {
                "task": spec["task"],
                "requested_seed": spec["seed"],
                "seed": result.get("seed"),
                "instruction": result.get("instruction"),
                "step_limit": result.get("step_lim"),
                "reset_ok": bool(result.get("ok")),
                "reason": result.get("reason"),
            }
            if case["reset_ok"]:
                directory = record_dir / spec["task"]
                directory.mkdir(exist_ok=True)
                filename = f"initial-{spec['seed']}-{case['seed']}-{index}.npz"
                snapshot = directory / filename
                with snapshot.open("xb") as output:
                    np.savez_compressed(output, state=np.asarray(result["state"]), **result["frame"])
                case["initial_state"] = str(snapshot.relative_to(record_dir))
                case["initial_state_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            active_cases[pool, index] = case
            append_json(record_dir, "episodes.jsonl", case)
        return results

    def record_step(pool, action_chunks, active, *step_args, **step_kwargs):
        results = original_step(pool, action_chunks, active, *step_args, **step_kwargs)
        for index in active:
            result = results[index] or {}
            if result.get("done") or "error" in result:
                outcome = {key: result[key] for key in ("done", "success", "finish_step", "step_lim", "error")
                           if key in result}
                terminal_outcomes[index] = {**active_cases[pool, index], **outcome}
                append_json(record_dir, "outcomes.jsonl", terminal_outcomes[index])
        return results

    def evaluate_missing_tasks(evaluator_args, tasks, seed_pool):
        return select_cases(eval_success, evaluator_args, tasks, seed_pool, terminal_outcomes, record_dir)

    with (patch.object(eval_success, "parse_args", parse_single_gpu_args),
          patch.object(RMBenchSimEnv, "reset", record_native_reset),
          patch.object(InProcSimPool, "reset", record_reset),
          patch.object(InProcSimPool, "step", record_step),
          patch.object(sys, "argv", ["rmbench_sim.eval_success", *upstream_args[1:]])):
        if args.select_successes:
            with patch.object(eval_success, "evaluate_tasks", evaluate_missing_tasks):
                eval_success.main()
        else:
            eval_success.main()


if __name__ == "__main__":
    main()
