"""Run the episodes of a method that repeats (``loop.episodes.repeat > 1``).

Each episode is an ordinary ``evoab run`` child process with its own environment
reset and run directory; the shared experience directory carries skills and
programs forward. The sequence stops early on success when the recipe asks.
"""

from __future__ import annotations

import subprocess
import sys


def run_sequence(argv: list[str], args) -> int:
    episodes = args.recipe.loop.episodes
    code = 1
    for index in range(episodes.repeat):
        command = [sys.executable, "-m", "evoab", *argv, "--set", "loop.episodes.repeat=1"]
        if args.experience_dir:
            command += ["--experience-dir", args.experience_dir]
        print(f"[evoab] episode {index + 1}/{episodes.repeat} of {args.recipe_id}", file=sys.stderr, flush=True)
        try:
            code = subprocess.call(command)
        except KeyboardInterrupt:
            return 130
        if code == 0 and episodes.stop_on_success:
            break
        if code not in (0, 1, 75):
            # A crash or interruption is not a learning outcome; stop the sequence.
            break
    return code
