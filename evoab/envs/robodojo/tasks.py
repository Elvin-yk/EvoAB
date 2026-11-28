"""RoboDojo's task catalog: capability dimensions, layout variants and evaluation sets.

Source: https://robodojo-benchmark.com/doc/sim-tasks/ and the quick-evaluation
page. RoboDojo has 42 base tasks in five capability dimensions. The 12
Generalization tasks also ship a ``<task>_random`` config with the same goal and a
changed initial arrangement ("Random Generalization configs inherit the dimension
of their base task"), so there are 54 runnable configs.

A *seed* is one of three evaluation sets, ``Assets/Eval_Layout/RoboDojo/arx_x5/<seed>/``.
A *layout* is one frozen initial scene in that set, ``<task>_<layout>.json``; the
official evaluation runs one episode per layout (50 per seed, 25 for each
Generalization base and random config). The assets ship a few more layouts per
task (e.g. 55 and 30) as spares the native evaluator moves to when it abandons a
layout; ``layouts_per_seed`` is the official count.
"""

from __future__ import annotations

import ast
from pathlib import Path

CATEGORIES: dict[str, tuple[str, ...]] = {
    "generalization": ("stack_bowls", "push_T", "pack_objects_into_box", "fold_clothes", "hang_mugs",
                       "sweep_blocks", "pour_liquid_into_cup", "make_toast", "arrange_largest_number",
                       "sort_nesting_dolls_by_size", "store_laptop_and_headphones", "stack_blocks"),
    "memory": ("cover_blocks", "match_and_pick_from_conveyor", "swap_blocks", "swap_T", "press_by_number",
               "imitate_sorting_sequence"),
    "precision": ("fasten_screws", "plug_in_charger", "insert_tubes", "pour_balls_into_vase", "play_Xylophone",
                  "deposit_coin", "insert_key", "build_tower"),
    "long_horizon": ("fill_pen_holder", "classify_objects", "put_bottles_into_dustbin", "play_tic_tac_toe",
                     "fill_egg_holder", "organize_table", "make_kong", "play_stacking_toy"),
    "open": ("align_blocks", "general_pickup", "solve_equation", "stack_blocks_by_language",
             "classify_objects_by_language", "pick_from_conveyor_by_image", "store_tools_in_toolbox",
             "pour_by_language"),
}
RANDOM_SUFFIX = "_random"
SEEDS = (0, 1, 2)
_CATEGORY = {task: category for category, tasks in CATEGORIES.items() for task in tasks}


def task_metadata(task: str, native_root: str | Path | None = None) -> dict:
    """Category, layout variant and evaluation sets of one RoboDojo config; empty if unknown.

    With the prepared source checkout, also the instruction and step limit the
    native task declares (``gen_instruction`` templates and ``step_lim``).
    """
    base = task[: -len(RANDOM_SUFFIX)] if task.endswith(RANDOM_SUFFIX) else task
    category = _CATEGORY.get(base)
    if category is None:
        return {}
    random = base != task
    facts = {"category": category, "base_task": base,
             "variant": ("random" if random else "standard") if category == "generalization" else None,
             "seeds": list(SEEDS), "layouts_per_seed": 25 if category == "generalization" else 50}
    if native_root is not None:
        source = source_facts(Path(native_root) / "task/RoboDojo/tasks" / f"{task}.py")
        # Tasks that build their instruction per scene (language, image, random goals) have no fixed text.
        facts.update({"instruction": "(varies by scene)", **source} if source else {})
    return facts


def source_facts(path: Path) -> dict:
    """``max_steps`` and ``instruction`` read statically from a native task file.

    The instruction is the first literal template; a task that builds its
    instruction per layout (for example from a language or image prompt) has none.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return {}
    facts: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if (isinstance(target, ast.Attribute) and target.attr == "step_lim"
                        and isinstance(node.value.value, int)):
                    facts["max_steps"] = node.value.value
        if isinstance(node, ast.FunctionDef) and node.name == "gen_instruction":
            for item in ast.walk(node):
                if (isinstance(item, ast.Assign) and isinstance(item.value, ast.List) and item.value.elts
                        and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in item.value.elts)):
                    facts["instruction"] = item.value.elts[0].value
                    break
    return facts


def layout_path(assets, task: str, seed: int, layout: int):
    return assets / "Eval_Layout/RoboDojo/arx_x5" / str(seed) / f"{task}_{layout}.json"
