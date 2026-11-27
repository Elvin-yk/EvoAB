"""Serve one MetaWorld MT50 episode through the standard environment service."""
from __future__ import annotations

import argparse
from pathlib import Path

from evoab.integrations.simulation import serve_episode
from .deployment import DEFAULT_MAX_STEPS, DEFAULT_PORT


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    p.add_argument("--depth", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    return p


def prepare(args):
    from hashlib import sha256
    from importlib.metadata import version

    import metaworld
    import numpy as np
    from .adapter import MetaWorldAdapter

    if args.seed < 0 or args.max_steps < 1:
        raise ValueError("Seed must be nonnegative and max_steps must be positive")
    mt50 = metaworld.MT50(seed=args.seed)
    if args.task not in mt50.train_classes:
        raise ValueError(f"Unknown MetaWorld MT50 task: {args.task}")
    env_cls = mt50.train_classes[args.task]
    task_variants = [t for t in mt50.train_tasks if t.env_name == args.task]
    if not task_variants:
        raise ValueError(f"No MT50 task variants found for {args.task}")
    env = env_cls(render_mode="rgb_array")
    try:
        variant = args.seed % len(task_variants)
        task = task_variants[variant]
        env.set_task(task)
        obs, _ = env.reset(seed=args.seed)
        initial_state = sha256(task.data)
        for values in (env.data.qpos, env.data.qvel, obs):
            initial_state.update(np.ascontiguousarray(values, dtype="<f8").tobytes())
        title = args.task[:-3] if args.task.endswith(("-v3", "-v2")) else args.task
        instruction = title.replace("-", " ")
        max_steps = min(args.max_steps, int(getattr(env, "max_path_length", args.max_steps)))
        return MetaWorldAdapter(
            env,
            obs,
            episode_id=args.run_id,
            instruction=instruction,
            metadata={
                "benchmark": "metaworld",
                "suite": "mt50",
                "task_id": args.task,
                "seed": args.seed,
                "variant_index": variant,
                "native_revision": version("metaworld"),
                "initial_state_sha256": initial_state.hexdigest(),
                "max_steps": max_steps,
                "evaluation_profile": "direct-rgbd" if args.depth else "direct-rgb",
            },
            max_steps=max_steps,
            depth_enabled=args.depth,
        )
    except BaseException:
        env.close()
        raise


def main():
    args = parser().parse_args()
    serve_episode(lambda: prepare(args), args, "metaworld",
                  capabilities=("pose", "native", "published_observations"))


if __name__ == "__main__":
    main()
