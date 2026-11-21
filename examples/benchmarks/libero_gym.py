"""Example custom benchmark: LIBERO tasks through their Gym-style environment.

EvoAB only needs ``make(task_id, seed)`` returning an object with ``reset()``
and ``step(action)``; ``TASKS`` lists the tasks. Set ``LIBERO_ROOT`` to a LIBERO
checkout (the directory containing ``libero/libero``) in the manifest's
``[environment]`` table.
"""
import os
import sys
import tempfile
from pathlib import Path

SUITE = os.environ.get("LIBERO_GYM_SUITE", "libero_object")
MAX_STEPS = {"libero_spatial": 220, "libero_object": 280, "libero_goal": 300, "libero_10": 520}


def _benchmark():
    root = Path(os.environ["LIBERO_ROOT"]).expanduser().resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    if "LIBERO_CONFIG_PATH" not in os.environ:
        # A private config so this process never edits another LIBERO installation's settings.
        import yaml
        native = root / "libero/libero"
        config = Path(tempfile.mkdtemp(prefix="libero-config-"))
        (config / "config.yaml").write_text(yaml.safe_dump({
            "benchmark_root": str(native), "bddl_files": str(native / "bddl_files"),
            "init_states": str(native / "init_files"), "assets": str(native / "assets"),
            "datasets": str(root / "datasets")}))
        os.environ["LIBERO_CONFIG_PATH"] = str(config)
    from libero.libero import benchmark
    return benchmark.get_benchmark_dict()[SUITE](task_order_index=0)


def TASKS():
    suite = _benchmark()
    return [{"id": suite.get_task(i).name, "instruction": suite.get_task(i).language, "max_steps": MAX_STEPS.get(SUITE, 300)}
            for i in range(suite.n_tasks)]


class LiberoTask:
    """reset() restores the task's first official initial state and lets objects settle."""

    def __init__(self, task_id, seed):
        from libero.libero.envs import OffScreenRenderEnv
        self.suite = _benchmark()
        self.index = self.suite.get_task_names().index(task_id)
        self.env = OffScreenRenderEnv(bddl_file_name=self.suite.get_task_bddl_file_path(self.index),
                                      camera_heights=256, camera_widths=256, ignore_done=True,
                                      horizon=MAX_STEPS.get(SUITE, 300) + 20)
        self.env.seed(seed)

    def reset(self):
        self.env.reset()
        self.env.set_init_state(self.suite.get_task_init_states(self.index)[0])
        for _ in range(10):
            obs, _, _, _ = self.env.step([0, 0, 0, 0, 0, 0, -1])
        return obs

    def step(self, action):
        return self.env.step(action)

    def check_success(self):
        return bool(self.env.check_success())

    def close(self):
        self.env.close()


def make(task_id, seed):
    return LiberoTask(task_id, seed)
