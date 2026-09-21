"""Quiet/verbose logging switches for Easy Deploy scripts.

Quiet mode is selected with EASYDEPLOY_QUIET=1. EASYDEPLOY_VERBOSE=1 always
wins, including when both are set.
"""

from __future__ import annotations

import os
import sys

_TRUE = {"1", "true", "yes", "on"}


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def is_verbose() -> bool:
    return env_flag("EASYDEPLOY_VERBOSE")


def is_quiet() -> bool:
    if is_verbose():
        return False
    return env_flag("EASYDEPLOY_QUIET")


def info(message: str, *, file=None) -> None:
    if is_quiet():
        return
    print(message, file=file or sys.stdout)
