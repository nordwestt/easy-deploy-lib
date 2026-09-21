"""Tests for easydeploy-lib/python/update_lock.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
import update_lock  # noqa: E402


def _init_repo(path: Path) -> str:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=path, check=True, capture_output=True)
    (path / ".gitignore").write_text("deploy.yaml\n.state/\n")
    (path / "README").write_text("hello\n")
    subprocess.run(["git", "add", "README", ".gitignore"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def _spec(root: Path, *, config_paths: tuple[Path, ...] | None = None) -> update_lock.UpdateSpec:
    state = root / ".state"
    state.mkdir(exist_ok=True)
    deploy = root / "deploy.yaml"
    if not deploy.exists():
        deploy.write_text("service: demo\n")
    return update_lock.UpdateSpec(
        project_root=root,
        state_dir=state,
        config_paths=config_paths if config_paths is not None else (deploy,),
        compose_projects=("demo",),
    )


def test_config_sha256_stable_and_order_independent(tmp_path: Path):
    a = tmp_path / "a.yaml"
    b = tmp_path / "sub" / "b.yaml"
    b.parent.mkdir()
    a.write_text("one: 1\n")
    b.write_text("two: 2\n")
    first = update_lock.config_sha256([b, a], root=tmp_path)
    second = update_lock.config_sha256([a, b], root=tmp_path)
    assert first == second
    assert len(first) == 64


def test_config_sha256_changes_when_file_changes(tmp_path: Path):
    path = tmp_path / "deploy.yaml"
    path.write_text("tag: 1\n")
    before = update_lock.config_sha256([path], root=tmp_path)
    path.write_text("tag: 2\n")
    assert update_lock.config_sha256([path], root=tmp_path) != before


def test_config_sha256_skips_missing_files(tmp_path: Path):
    present = tmp_path / "deploy.yaml"
    present.write_text("ok\n")
    missing = tmp_path / "gone.yaml"
    assert update_lock.config_sha256([present, missing], root=tmp_path) == update_lock.config_sha256(
        [present], root=tmp_path
    )


def test_integration_config_paths_sorted(tmp_path: Path):
    integration = tmp_path / "integration"
    (integration / "nested").mkdir(parents=True)
    (integration / "b.yaml").write_text("b\n")
    (integration / "nested" / "a.yaml").write_text("a\n")
    (integration / "nested").joinpath("skip-dir")  # not a file
    paths = update_lock.integration_config_paths(tmp_path)
    assert [p.name for p in paths] == ["b.yaml", "a.yaml"]


def test_output_reports_noop():
    assert update_lock.output_reports_noop("Nothing to update\n")
    assert update_lock.output_reports_noop("Pulling…\nNothing to update")
    assert not update_lock.output_reports_noop("Update complete.\n")
    assert not update_lock.output_reports_noop("")


def test_missing_lock_does_not_skip(tmp_path: Path):
    _init_repo(tmp_path)
    spec = _spec(tmp_path)
    assert update_lock.should_skip_update(spec) is False


def test_force_does_not_skip(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    spec = _spec(tmp_path)
    update_lock.write_lock(
        spec.lock_path,
        {
            "version": 1,
            "git": update_lock.git_head(tmp_path),
            "submodules": {},
            "config_sha256": update_lock.config_sha256(spec.config_paths, root=tmp_path),
            "images": {"img:1": "sha256:aaa"},
            "containers": ["demo"],
        },
    )
    monkeypatch.setattr(update_lock, "container_exists", lambda _name: True)
    monkeypatch.setattr(update_lock, "inspect_image_ids", lambda _refs: {"img:1": "sha256:aaa"})
    monkeypatch.setattr(update_lock, "pull_image_refs", lambda *_a, **_k: None)
    assert update_lock.should_skip_update(spec) is True
    assert update_lock.should_skip_update(spec, force=True) is False


def test_dirty_tree_does_not_skip(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    spec = _spec(tmp_path)
    (tmp_path / "README").write_text("dirty\n")
    update_lock.write_lock(
        spec.lock_path,
        {
            "version": 1,
            "git": update_lock.git_head(tmp_path),
            "submodules": {},
            "config_sha256": update_lock.config_sha256(spec.config_paths, root=tmp_path),
            "images": {"img:1": "sha256:aaa"},
            "containers": ["demo"],
        },
    )
    monkeypatch.setattr(update_lock, "container_exists", lambda _name: True)
    monkeypatch.setattr(update_lock, "inspect_image_ids", lambda _refs: {"img:1": "sha256:aaa"})
    monkeypatch.setattr(update_lock, "pull_image_refs", lambda *_a, **_k: None)
    assert update_lock.git_is_dirty(tmp_path)
    assert update_lock.should_skip_update(spec) is False


def test_image_id_mismatch_does_not_skip(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    spec = _spec(tmp_path)
    update_lock.write_lock(
        spec.lock_path,
        {
            "version": 1,
            "git": update_lock.git_head(tmp_path),
            "submodules": {},
            "config_sha256": update_lock.config_sha256(spec.config_paths, root=tmp_path),
            "images": {"img:1": "sha256:old"},
            "containers": ["demo"],
        },
    )
    monkeypatch.setattr(update_lock, "container_exists", lambda _name: True)
    monkeypatch.setattr(update_lock, "inspect_image_ids", lambda _refs: {"img:1": "sha256:new"})
    monkeypatch.setattr(update_lock, "pull_image_refs", lambda *_a, **_k: None)
    assert update_lock.should_skip_update(spec) is False


def test_missing_container_does_not_skip(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    spec = _spec(tmp_path)
    update_lock.write_lock(
        spec.lock_path,
        {
            "version": 1,
            "git": update_lock.git_head(tmp_path),
            "submodules": {},
            "config_sha256": update_lock.config_sha256(spec.config_paths, root=tmp_path),
            "images": {"img:1": "sha256:aaa"},
            "containers": ["demo"],
        },
    )
    monkeypatch.setattr(update_lock, "container_exists", lambda _name: False)
    monkeypatch.setattr(update_lock, "inspect_image_ids", lambda _refs: {"img:1": "sha256:aaa"})
    monkeypatch.setattr(update_lock, "pull_image_refs", lambda *_a, **_k: None)
    assert update_lock.should_skip_update(spec) is False


def test_matching_lock_skips(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    spec = _spec(tmp_path)
    update_lock.write_lock(
        spec.lock_path,
        {
            "version": 1,
            "git": update_lock.git_head(tmp_path),
            "submodules": {},
            "config_sha256": update_lock.config_sha256(spec.config_paths, root=tmp_path),
            "images": {"img:1": "sha256:aaa"},
            "containers": ["demo"],
        },
    )
    pulls: list[tuple] = []
    monkeypatch.setattr(update_lock, "container_exists", lambda _name: True)
    monkeypatch.setattr(update_lock, "inspect_image_ids", lambda _refs: {"img:1": "sha256:aaa"})
    monkeypatch.setattr(update_lock, "pull_image_refs", lambda refs, **kwargs: pulls.append((tuple(refs), kwargs)))
    assert update_lock.should_skip_update(spec) is True
    assert pulls == [(("img:1",), {"quiet": True})]


def test_skip_pull_does_not_call_registry(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    spec = _spec(tmp_path)
    update_lock.write_lock(
        spec.lock_path,
        {
            "version": 1,
            "git": update_lock.git_head(tmp_path),
            "submodules": {},
            "config_sha256": update_lock.config_sha256(spec.config_paths, root=tmp_path),
            "images": {"img:1": "sha256:aaa"},
            "containers": ["demo"],
        },
    )
    monkeypatch.setattr(update_lock, "container_exists", lambda _name: True)
    monkeypatch.setattr(update_lock, "inspect_image_ids", lambda _refs: {"img:1": "sha256:aaa"})

    def boom(*_a, **_k):
        raise RuntimeError("should not pull")

    monkeypatch.setattr(update_lock, "pull_image_refs", boom)
    assert update_lock.should_skip_update(spec, skip_pull=True) is True


def test_run_standard_update_skips_apply(tmp_path: Path, monkeypatch, capsys):
    _init_repo(tmp_path)
    spec = _spec(tmp_path)
    apply_sh = tmp_path / "apply.sh"
    apply_sh.write_text("#!/bin/bash\nexit 1\n")
    monkeypatch.setattr(update_lock, "should_skip_update", lambda *_a, **_k: True)
    runs: list[list[str]] = []
    monkeypatch.setattr(update_lock.subprocess, "run", lambda cmd, **_k: runs.append(list(cmd)) or type("R", (), {"returncode": 0})())
    assert update_lock.run_standard_update(spec, skip_git=True) == "skipped"
    assert capsys.readouterr().out.strip() == update_lock.NOTHING_TO_UPDATE
    assert runs == []


def test_run_standard_update_force_runs_apply_and_writes_lock(tmp_path: Path, monkeypatch):
    _init_repo(tmp_path)
    spec = _spec(tmp_path)
    (tmp_path / "apply.sh").write_text("#!/bin/bash\nexit 0\n")
    recorded: list[update_lock.UpdateSpec] = []

    class Result:
        returncode = 0

    monkeypatch.setattr(update_lock.subprocess, "run", lambda *_a, **_k: Result())
    monkeypatch.setattr(update_lock, "record_update_lock", lambda item: recorded.append(item) or item.lock_path)
    monkeypatch.setattr(update_lock, "should_skip_update", lambda *_a, **_k: False)
    assert update_lock.run_standard_update(spec, skip_git=True, force=True) == "updated"
    assert recorded == [spec]


def test_record_update_lock_writes_yaml(tmp_path: Path, monkeypatch):
    sha = _init_repo(tmp_path)
    spec = _spec(tmp_path)
    monkeypatch.setattr(update_lock, "discover_stack", lambda _spec: [("demo", "img:1")])
    monkeypatch.setattr(update_lock, "inspect_image_ids", lambda refs: {refs[0]: "sha256:abc"})
    path = update_lock.record_update_lock(spec)
    data = yaml.safe_load(path.read_text())
    assert data["version"] == 1
    assert data["git"] == sha
    assert data["images"] == {"img:1": "sha256:abc"}
    assert data["containers"] == ["demo"]
    assert data["config_sha256"] == update_lock.config_sha256(spec.config_paths, root=tmp_path)
