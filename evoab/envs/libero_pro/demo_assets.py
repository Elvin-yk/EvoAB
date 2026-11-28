"""LIBERO expert previews acquired only during explicit benchmark preparation."""

import hashlib
import json
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen


@cache
def sources() -> dict[str, dict[str, str]]:
    return json.loads((Path(__file__).parent / "previews/sources.json").read_text())


def preview_path(root: Path, task: str) -> Path | None:
    if task not in sources():
        return None
    path = root / "demos" / (task + ".mp4")
    return path if path.is_file() and path.stat().st_size else None


def prepare_previews(runtime) -> None:
    for task, source in sources().items():
        if task not in runtime.data["tasks"]:
            continue
        target = runtime.root / "demos" / (task + ".mp4")
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == source["sha256"]:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(source["url"], timeout=20) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if hashlib.sha256(data).hexdigest() != source["sha256"]:
            raise OSError(f"Unexpected contents for expert preview {task}")
        with TemporaryDirectory(dir=target.parent) as staging:
            partial = Path(staging) / target.name
            partial.write_bytes(data)
            partial.replace(target)
