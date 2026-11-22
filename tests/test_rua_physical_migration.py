"""Small CPU-only resource fixtures for the host migration state machine."""

import argparse
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from evoab import __version__
from evoab.agents.rua import RuaAgent
from evoab.integrations.robodojo.deployment import recipe
from evoab.services.deployment.container import runtime_python, spec_for


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "rua"))
try:
    m = importlib.import_module("migrate_resources")
finally:
    sys.path.pop(0)
check_idle = m.Migration.check_idle


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def fixture_wheel(directory):
    wheel = directory / "sample_dep-1.2-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("sample_dep.py", "VERSION = '1.2'\n")
        archive.writestr("sample_dep-1.2.dist-info/METADATA", "Metadata-Version: 2.1\nName: sample-dep\nVersion: 1.2\n")
        archive.writestr("sample_dep-1.2.dist-info/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("sample_dep-1.2.dist-info/RECORD", "")


def fixture_source(source):
    (source / "uni_robot_agent").mkdir()
    (source / "uni_robot_agent/__init__.py").write_text("__version__ = '0.2.0'\n")
    (source / "pyproject.toml").write_text(
        '[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ["."]\n'
    )
    (source / "backend.py").write_text(r"""
from pathlib import Path
import zipfile

def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    filename = "uni_robot_agent-0.2.0-py3-none-any.whl"
    with zipfile.ZipFile(Path(wheel_directory) / filename, "w") as archive:
        archive.writestr("_rua_fixture.pth", str(Path(__file__).parent) + "\n")
        archive.writestr("uni_robot_agent-0.2.0.dist-info/METADATA",
                         "Metadata-Version: 2.1\nName: uni-robot-agent\nVersion: 0.2.0\nRequires-Dist: sample-dep==1.2\n")
        archive.writestr("uni_robot_agent-0.2.0.dist-info/WHEEL",
                         "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("uni_robot_agent-0.2.0.dist-info/RECORD", "")
    return filename
""")


@pytest.fixture
def layout(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    resources = repo / "rua"
    resources.mkdir(parents=True)
    (resources / ".gitignore").write_text((REPO / "rua/.gitignore").read_text())
    run("git", "init", "-q", cwd=repo)
    worker = repo / "evoab/agents/rua/worker.py"
    worker.parent.mkdir(parents=True)
    worker.write_text(
        'import json, sys, uni_robot_agent, sample_dep\n'
        'assert sys.argv[1:] == ["--check-runtime"] and sample_dep.VERSION == "1.2"\n'
        'print(json.dumps({"rua": uni_robot_agent.__file__, "version": "0.2.0", "sdk": "fixture"}))\n'
    )
    # Use the real checkout when the fixture invokes python -m evoab.
    original = REPO / "evoab"
    (repo / "evoab/__init__.py").write_text(
        f"__path__[:] = [{str(original)!r}]\n" + (original / "__init__.py").read_text()
    )
    args = argparse.Namespace(native=False, cache_dir=tmp_path / "home-cache", manifest=[],
                              wheelhouse=tmp_path / "wheels", run_dir=None)
    args.wheelhouse.mkdir()
    migration = m.Migration(repo, args)
    for name, old in migration.old.items():
        old.mkdir()
        migration.new[name].symlink_to(old)
    (migration.old["candidate"] / "data").write_bytes(b"exact data")
    (migration.old["cache"] / "internal").symlink_to(migration.old["candidate"] / "data")
    (migration.old["cache"] / "relative").symlink_to("../rua-candidate-0.2.0/data")
    native = migration.old["cache"] / "native"
    assets = migration.old["cache-reuse"] / "build/Assets"
    for name in ("Robots/x5", "Robots/franka", "Object", "Material", "Eval_Layout/RoboDojo/arx_x5/0"):
        (assets / name).mkdir(parents=True)
    for robot in ("x5", "franka"):
        (assets / "Robots" / robot / "curobo.yml").write_text(f"asset_path: '{assets}'\n")
    (assets / "Eval_Layout/RoboDojo/arx_x5/0/example_0.json").write_text("{}")
    (native / "task/RoboDojo/config").mkdir(parents=True)
    (native / "task/RoboDojo/config/example.yml").write_text("fixture: true\n")
    (native / "Assets").symlink_to(assets)
    manifest = migration.active_manifest(args.cache_dir)
    manifest.parent.mkdir(parents=True)
    spec = spec_for("robodojo")
    mounts = [str(native), str(assets.parent)]
    manifest.write_text(json.dumps({
        "schema_version": 1, "name": "robodojo", "root": str(manifest.parent),
        "recipe_digest": recipe(None).digest, "package_version": __version__,
        "python": runtime_python("fixture:image", spec.python, mounts=mounts),
        "data": {"native_root": str(native), "assets_dir": str(assets), "tasks": {"example": {}},
                 "container": {"image": "fixture:image", "root": False, "mounts": mounts,
                               "recipe_digest": spec.digest}},
    }))
    real_command = m.command

    def mock_command(argv, **kwargs):
        if argv[:3] == ["docker", "image", "inspect"]:
            return "fixture-id"
        return real_command(argv, **kwargs)

    monkeypatch.setattr(m, "command", mock_command)
    monkeypatch.setattr(m.Migration, "check_idle", lambda self: None)
    migration.control.mkdir(mode=0o700)
    return migration, manifest


def install_old(migration):
    fixture_wheel(migration.args.wheelhouse)
    fixture_source(migration.old["source"])
    run(sys._base_executable, "-m", "venv", "--without-pip", migration.old["venv"])
    return run(sys.executable, "-m", "pip", "--python", migration.old["venv"] / "bin/python",
               "install", "--no-index", "--find-links", migration.args.wheelhouse,
               "sample-dep==1.2", "-e", migration.old["source"])


@pytest.mark.parametrize("external", [True, False], ids=["external-manifest", "copied-manifest"])
def test_complete_cpu_migration_and_rollback(layout, monkeypatch, external):
    migration, manifest = layout
    if not external:
        migration.args.cache_dir = migration.old["cache-reuse"]
        original = m.read_json(manifest)
        manifest = migration.active_manifest(migration.args.cache_dir)
        manifest.parent.mkdir(parents=True)
        original["root"] = str(manifest.parent)
        manifest.write_text(json.dumps(original))
    new_manifest = migration.mapped(manifest)
    initial_manifest = manifest.read_bytes()
    install_old(migration)
    migration.audit()
    assert not migration.state_file.exists()

    migration.migrate()
    assert migration.new["candidate"].joinpath("data").read_bytes() == b"exact data"
    assert migration.new["cache"].joinpath("internal").resolve() == migration.new["candidate"] / "data"
    assert migration.new["cache"].joinpath("relative").readlink() == Path("../candidate/data")
    assert initial_manifest != new_manifest.read_bytes()
    if not external:
        assert initial_manifest == manifest.read_bytes()
    for name, path in migration.new.items():
        assert path.is_dir() and not path.is_symlink() and migration.old[name].is_dir()
    migration = m.Migration(migration.repo, migration.args)
    migration.migrate()
    original_rename = m.rename

    def interrupt_second_isolation(source, target):
        if source == migration.old["venv"] and target.parent == migration.backup:
            raise OSError("fixture interruption")
        return original_rename(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(m, "rename", interrupt_second_isolation)
        with pytest.raises(OSError, match="fixture interruption"):
            migration.isolate()
    assert migration.state["phase"] == "isolating"
    assert not migration.old["source"].exists() and migration.old["venv"].exists()
    migration = m.Migration(migration.repo, migration.args)
    migration.isolate()
    assert not any(m.exists(path) for path in migration.old.values())
    migration.verify()

    evidence = migration.repo / "run"
    evidence.mkdir()
    migration.args.run_dir = evidence
    records = {
        "deployment.json": {"state": "ready", "runtime_manifest": str(new_manifest), "run_id": "acceptance"},
        "supervisor.json": {"episode_exit_code": 0},
        "result.json": {"agent_backend": "rua", "task_success": False, "accepted": True},
    }
    for name, content in records.items():
        (evidence / name).write_text(json.dumps(content))
    (evidence / "evaluator.log").write_text("RUA attached to robodojo episode acceptance (protocol 4, tool schema 8)")
    with pytest.raises(ValueError, match="officially successful"):
        migration.finalize()
    records["result.json"]["task_success"] = True
    (evidence / "result.json").write_text(json.dumps(records["result.json"]))
    migration.finalize()
    assert migration.state["phase"] == "complete"
    migration.rollback()
    migration.rollback()
    assert manifest.read_bytes() == initial_manifest
    assert all(path.is_symlink() and path.resolve() == migration.old[name] for name, path in migration.new.items())
    assert (migration.work / "rollback" / "venv").is_dir()


def test_audit_refuses_unknown_binary_reference(layout):
    migration, _ = layout
    old = migration.old["cache"] / "unknown.so"
    old.write_bytes(b"binary\0" + os.fsencode(migration.old["source"]) + b"/path\0")
    with pytest.raises(ValueError, match="unknown.so"):
        migration.inspect_paths("cache")
    assert old.exists() and migration.new["cache"].is_symlink()


def test_mapping_respects_path_boundaries_and_file_urls(layout):
    migration, _ = layout
    original = migration.old["cache"]
    sample = f'{original}-external file://{original}/file {original}-reuse/file'
    changed = os.fsdecode(migration.rebase(os.fsencode(sample)))
    assert changed == (
        f'{original}-external file://{migration.new["cache"]}/file {migration.new["cache-reuse"]}/file'
    )
    path = original / "boundary"
    # A read chunk ends inside the longer cache-reuse name.
    with path.open("wb") as stream:
        prefix = os.fsencode(original)
        stream.write(b" " * (2**20 - len(prefix)))
        stream.write(prefix + b"-external\n")
    assert not migration.contains_legacy_path(path)


def test_preflight_refuses_conflicting_directory(layout):
    migration, _ = layout
    target = migration.new["cache"]
    target.unlink()
    target.mkdir()
    with pytest.raises(ValueError, match="Existing path"):
        migration.plan()
    assert target.is_dir() and not target.is_symlink()


def test_process_using_candidate_blocks_migration(layout, monkeypatch):
    migration, _ = layout
    migration.args.native = True
    original_which = m.shutil.which
    monkeypatch.setattr(m.shutil, "which", lambda name: None if name == "docker" else original_which(name))
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                          cwd=migration.old["candidate"]) as process:
        try:
            with pytest.raises(ValueError, match=f"PID {process.pid}"):
                check_idle(migration)
        finally:
            process.terminate()


def test_adapter_ignores_unverified_checkout_venv_and_prefers_overrides(monkeypatch, tmp_path):
    monkeypatch.delenv("EVOAB_RUA_PYTHON", raising=False)
    mock_file = tmp_path / "evoab/agents/rua/__init__.py"
    monkeypatch.setattr(sys.modules[RuaAgent.__module__], "__file__", str(mock_file))
    venv = tmp_path / "rua/venv"
    venv.mkdir(parents=True)
    parsed = argparse.Namespace(rua_python=None, reasoning_effort=None)
    RuaAgent.resolve_defaults(parsed)
    assert parsed.rua_python == sys.executable
    (venv / ".evoab-rua-ready").touch()
    parsed.rua_python = None
    RuaAgent.resolve_defaults(parsed)
    assert parsed.rua_python == str(venv / "bin/python")
    monkeypatch.setenv("EVOAB_RUA_PYTHON", "/from-env")
    parsed.rua_python = None
    RuaAgent.resolve_defaults(parsed)
    assert parsed.rua_python == "/from-env"
    parsed.rua_python = "/explicit"
    RuaAgent.resolve_defaults(parsed)
    assert parsed.rua_python == "/explicit"
