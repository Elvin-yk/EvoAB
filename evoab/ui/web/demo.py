"""Published example runs for `evoab replay demo`: recorded episodes, no GPU or model needed."""
from __future__ import annotations

import hashlib
import os
import tarfile
import tempfile
import urllib.request
from pathlib import Path

#: Release asset with recorded runs; EVOAB_DEMO_URL overrides it (for mirrors).
DEMO_URL = "https://github.com/Elvin-yk/EvoAB/releases/download/demo-runs-v1/evoab-demo-runs-v1.tar.gz"
DEMO_SHA256 = ""


def demo_runs() -> Path:
    """Download and unpack the example runs once; return their directory."""
    root = Path(os.environ.get("EVOAB_STATE_DIR", Path.home() / ".cache" / "evoab")) / "demo-runs-v1"
    if (root / ".complete").is_file():
        return root
    url = os.environ.get("EVOAB_DEMO_URL", DEMO_URL)
    print(f"Downloading example runs from {url} ...", flush=True)
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=root.parent, suffix=".tar.gz", delete=False) as handle:
        archive = Path(handle.name)
    try:
        urllib.request.urlretrieve(url, archive)  # noqa: S310 - fixed https release asset or user override
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if DEMO_SHA256 and url == DEMO_URL and digest != DEMO_SHA256:
            raise RuntimeError(f"example runs checksum mismatch ({digest})")
        root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as bundle:
            bundle.extractall(root, filter="data")
        (root / ".complete").write_text(digest + "\n")
    except OSError as error:
        raise RuntimeError(f"cannot download the example runs ({error}); pass a run directory instead") from error
    finally:
        archive.unlink(missing_ok=True)
    return root
