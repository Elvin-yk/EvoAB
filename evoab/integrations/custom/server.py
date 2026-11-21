"""Serve one episode of a user-defined benchmark through the standard environment service."""
import argparse
import json
from pathlib import Path

from evoab.integrations.simulation import serve_episode


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest-json", type=Path, required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--run-id", required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=18790)
    return p


def main():
    args = parser().parse_args()
    manifest = json.loads(args.manifest_json.read_text(encoding="utf-8"))

    def factory():
        from evoab.integrations.custom.adapter import GymAdapter
        return GymAdapter(manifest, args.task, args.seed)

    serve_episode(factory, args, "custom")


if __name__ == "__main__":
    main()
