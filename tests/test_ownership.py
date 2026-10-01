"""copy_owner / chown_tree: ownership handling for staged and restored backup files."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import hostfs  # noqa: E402


def _tree(root: Path) -> None:
    (root / "config").mkdir(parents=True)
    (root / "config" / "opencloud.yaml").write_text("x")
    (root / "top.txt").write_text("y")
    (root / "link").symlink_to("top.txt")


def _record_lchown(monkeypatch, owners: dict[Path, tuple[int, int]]) -> list[tuple[Path, int, int]]:
    calls: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(hostfs.os, "geteuid", lambda: 0)
    monkeypatch.setattr(hostfs.os, "lchown", lambda p, u, g: calls.append((Path(p), u, g)))
    real_lstat = Path.lstat

    class _St:
        def __init__(self, path: Path):
            self.st_uid, self.st_gid = owners.get(path, (0, 0))
            self._real = real_lstat(path)

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(Path, "lstat", lambda self: _St(self))
    return calls


def test_copy_owner_mirrors_source_owners(tmp_path: Path, monkeypatch):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _tree(src)
    _tree(dest)
    owners = {
        src: (1000, 1000),
        src / "config": (1000, 1000),
        src / "config" / "opencloud.yaml": (1000, 1000),
        src / "top.txt": (991, 991),
        src / "link": (991, 991),
    }
    calls = _record_lchown(monkeypatch, owners)
    hostfs.copy_owner(src, dest)
    assert sorted(calls) == sorted(
        [
            (dest, 1000, 1000),
            (dest / "config", 1000, 1000),
            (dest / "config" / "opencloud.yaml", 1000, 1000),
            (dest / "top.txt", 991, 991),
            (dest / "link", 991, 991),
        ]
    )


def test_copy_owner_skips_entries_missing_from_dest(tmp_path: Path, monkeypatch):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _tree(src)
    dest.mkdir()
    calls = _record_lchown(monkeypatch, {})
    hostfs.copy_owner(src, dest)
    assert calls == [(dest, 0, 0)]


def test_chown_tree_only_touches_mismatched_entries(tmp_path: Path, monkeypatch):
    root = tmp_path / "config"
    _tree(root)
    owners = {root: (1000, 1000), root / "config": (1000, 1000)}
    calls = _record_lchown(monkeypatch, owners)
    hostfs.chown_tree(root, 1000, 1000)
    assert sorted(p for p, _u, _g in calls) == sorted(
        [root / "config" / "opencloud.yaml", root / "top.txt", root / "link"]
    )


def test_ownership_helpers_are_noops_without_root(tmp_path: Path, monkeypatch):
    _tree(tmp_path / "a")
    monkeypatch.setattr(hostfs.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(hostfs.os, "lchown", lambda *a: (_ for _ in ()).throw(AssertionError("chown")))
    hostfs.copy_owner(tmp_path / "a", tmp_path / "a")
    hostfs.chown_tree(tmp_path / "a", 1000, 1000)
    assert os.path.exists(tmp_path / "a" / "top.txt")
