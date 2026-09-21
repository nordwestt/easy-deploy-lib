"""Tests for quiet/verbose logging switches."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
import edlog  # noqa: E402


def test_quiet_hides_info(monkeypatch, capsys):
    monkeypatch.setenv("EASYDEPLOY_QUIET", "1")
    monkeypatch.delenv("EASYDEPLOY_VERBOSE", raising=False)
    assert edlog.is_quiet() is True
    edlog.info("hidden")
    assert capsys.readouterr().out == ""


def test_verbose_wins_over_quiet(monkeypatch, capsys):
    monkeypatch.setenv("EASYDEPLOY_QUIET", "1")
    monkeypatch.setenv("EASYDEPLOY_VERBOSE", "1")
    assert edlog.is_quiet() is False
    edlog.info("shown")
    assert "shown" in capsys.readouterr().out
