"""Hybrid target-prompt resolution: task registry -> VLM / rule auto-extraction."""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import httpx

from evoab.records.telemetry.logging import get_logger

from .config import AutoPromptConfig, TargetConfig, TargetsConfig
from .contracts import TargetSpec

logger = get_logger("observation.toolpackage.prompts")

# Action verbs, prepositions, quantifiers and generic filler words removed by the rule extractor.
_STOPWORDS = frozenset({
    "a", "an", "the", "this", "that", "these", "those", "my", "your", "its", "their",
    "and", "or", "then", "into", "onto", "in", "on", "at", "to", "from", "of", "for",
    "with", "by", "up", "down", "out", "off", "over", "under", "inside", "outside",
    "between", "next", "near", "beside", "behind", "front", "above", "below", "around",
    "together", "apart", "back", "away", "it", "them", "they", "there", "here",
    "one", "two", "three", "four", "five", "all", "both", "each", "every", "some", "any",
    "first", "second", "third", "last", "other", "another",
    "pick", "place", "put", "stack", "grasp", "grab", "move", "push", "pull", "lift",
    "drop", "open", "close", "turn", "rotate", "pour", "press", "click", "insert",
    "arrange", "sort", "align", "fold", "unfold", "wipe", "clean", "sweep", "hang",
    "take", "bring", "carry", "hold", "set", "make", "keep", "slide", "flip", "stand",
    "upright", "carefully", "gently", "slowly", "neatly", "properly", "robot", "arm",
    "arms", "gripper", "hand", "hands", "left", "right", "top", "bottom", "middle",
    "center", "side", "using", "use", "then", "after", "before", "while", "until",
})

_CLAUSE_SPLIT = re.compile(
    r"\b(?:and|then|into|onto|in|on|at|to|from|with|by|over|under|inside|outside|"
    r"between|next\s+to|near|beside|behind|in\s+front\s+of|above|below|of|for)\b|[,.;:!?()]+",
    re.IGNORECASE,
)

_LEADING_VERBS = re.compile(
    r"^(?:please\s+)?(?:pick\s+up|put\s+down|take\s+out|set\s+down|place|put|stack|"
    r"grasp|grab|move|push|pull|lift|drop|open|close|turn\s+on|turn\s+off|turn|rotate|"
    r"pour|press|insert|arrange|sort|align|fold|wipe|clean|sweep|hang|take|bring|carry|"
    r"hold|slide|flip|stand)\s+",
    re.IGNORECASE,
)


def _singularize_tail(phrase: str) -> str:
    """Keep simple plural nouns usable as SAM3 singular prompts when unambiguous."""
    words = phrase.split()
    if not words:
        return phrase
    tail = words[-1]
    if len(tail) > 4 and tail.endswith("ies"):
        words[-1] = tail[:-3] + "y"
    elif len(tail) > 3 and tail.endswith("s") and not tail.endswith(("ss", "us", "is", "cheese")):
        words[-1] = tail[:-1]
    return " ".join(words)


def extract_prompts_by_rule(instruction: str, *, max_targets: int = 6) -> list[TargetSpec]:
    """Deterministic offline noun-phrase extractor from a task instruction."""
    if not instruction or not instruction.strip():
        return []
    seen: set[str] = set()
    targets: list[TargetSpec] = []
    for raw_clause in _CLAUSE_SPLIT.split(instruction):
        clause = _LEADING_VERBS.sub("", raw_clause.strip())
        tokens = [tok.lower() for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", clause)]
        while tokens and tokens[0] in _STOPWORDS:
            tokens.pop(0)
        while tokens and tokens[-1] in _STOPWORDS:
            tokens.pop()
        if not tokens:
            continue
        # Drop any remaining pure stopwords inside a short fragment, keeping adjectives + nouns.
        filtered = [tok for tok in tokens if tok not in _STOPWORDS]
        if not filtered:
            continue
        # Cap phrase length to avoid feeding entire sentences into SAM3.
        phrase = _singularize_tail(" ".join(filtered[:4]))
        if not phrase or phrase in seen:
            continue
        seen.add(phrase)
        targets.append(TargetSpec(label=phrase, prompt=phrase, source="auto_rule"))
        if len(targets) >= max_targets:
            break
    return targets


def extract_prompts_by_vlm(
    instruction: str,
    config: AutoPromptConfig,
    *,
    first_frame_png: bytes | None = None,
) -> list[TargetSpec]:
    """Call an OpenAI-compatible VLM/LLM once at episode start to extract object prompts."""
    if not config.vlm_base_url or not config.vlm_model:
        raise ValueError("VLM prompt extraction requires vlm_base_url and vlm_model")
    api_key = os.environ.get(config.vlm_api_key_env, "")
    if not api_key:
        raise ValueError(f"Missing VLM credential environment variable: {config.vlm_api_key_env}")

    user_content: list[dict[str, Any]] = [{
        "type": "text",
        "text": (
            f"Robot task instruction: {instruction}\n"
            f"List up to {config.max_targets} distinct physical target objects or receptacles "
            "that a segmentation model (SAM3) should detect in the camera views. "
            "Exclude the robot arms, grippers, table background unless referenced as a target, "
            "and abstract words. Respond ONLY with a JSON object: "
            '{"targets": [{"label": "short_id", "prompt": "concise visual phrase"}]}'
        ),
    }]
    if config.include_first_frame and first_frame_png:
        b64 = base64.b64encode(first_frame_png).decode("ascii")
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })

    url = config.vlm_base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": config.vlm_model,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You extract concise visual detection prompts for robot manipulation."},
            {"role": "user", "content": user_content},
        ],
    }
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=config.vlm_timeout_s,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed = _parse_json_object(content)
    items = parsed.get("targets") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        raise ValueError("VLM response missing 'targets' list")
    seen: set[str] = set()
    result: list[TargetSpec] = []
    for item in items:
        if isinstance(item, str):
            label = prompt = item.strip()
        elif isinstance(item, dict):
            prompt = str(item.get("prompt") or item.get("label") or "").strip()
            label = str(item.get("label") or prompt).strip()
        else:
            continue
        key = prompt.lower()
        if not prompt or key in seen:
            continue
        seen.add(key)
        result.append(TargetSpec(label=label, prompt=prompt, source="auto_vlm"))
        if len(result) >= config.max_targets:
            break
    return result


def _parse_json_object(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL).strip()
    return json.loads(cleaned)


def _from_config(items: list[TargetConfig], *, source: str) -> list[TargetSpec]:
    return [
        TargetSpec(
            label=item.label,
            prompt=item.effective_prompt(),
            mesh_path=item.mesh_path,
            mesh_id=item.mesh_id,
            color=item.color,
            min_score=item.min_score,
            source=source,
        )
        for item in items
    ]


def resolve_targets(
    *,
    task: str,
    instruction: str,
    targets_config: TargetsConfig,
    auto_prompt: AutoPromptConfig,
    first_frame_png: bytes | None = None,
) -> tuple[TargetSpec, ...]:
    """Resolve episode target prompts: explicit task config -> default -> VLM/rule auto-extraction."""
    if task and task in targets_config.by_task and targets_config.by_task[task]:
        return tuple(_from_config(targets_config.by_task[task], source=f"task_config:{task}"))
    # Also match normalized task basename (e.g. "suite/task_name" -> "task_name").
    short_task = task.rsplit("/", 1)[-1] if task else ""
    if short_task and short_task in targets_config.by_task and targets_config.by_task[short_task]:
        return tuple(_from_config(targets_config.by_task[short_task], source=f"task_config:{short_task}"))
    if targets_config.default:
        return tuple(_from_config(targets_config.default, source="default_config"))
    if not auto_prompt.enabled:
        return ()

    if auto_prompt.strategy in ("vlm", "hybrid"):
        has_vlm = bool(
            auto_prompt.vlm_base_url
            and auto_prompt.vlm_model
            and os.environ.get(auto_prompt.vlm_api_key_env)
        )
        if has_vlm:
            try:
                extracted = extract_prompts_by_vlm(instruction, auto_prompt, first_frame_png=first_frame_png)
                if extracted:
                    return tuple(extracted)
            except Exception as error:
                if auto_prompt.strategy == "vlm":
                    raise
                logger.warning("VLM auto-prompt failed, falling back to rule extractor: %s", error)
        elif auto_prompt.strategy == "vlm":
            raise ValueError(
                "auto_prompt.strategy='vlm' requires vlm_base_url, vlm_model and credential env "
                f"{auto_prompt.vlm_api_key_env}"
            )

    fallback_text = instruction or short_task.replace("_", " ")
    return tuple(extract_prompts_by_rule(fallback_text, max_targets=auto_prompt.max_targets))
