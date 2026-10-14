"""Run git without flashing a console window on Windows.

Cursor MCP (pythonw) often inherits Machine PATH with Git\\cmd\\git.exe first.
That wrapper is a console subsystem binary; without CREATE_NO_WINDOW every
`git diff` pops a Terminal/conhost window (incident 2026-10-14).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Sequence

CREATE_NO_WINDOW = 0x08000000

_GIT_CANDIDATES = (
    Path(r"C:\Program Files\Git\mingw64\bin\git.exe"),
    Path(r"C:\Program Files\Git\bin\git.exe"),
)

_cached_git: str | None = None


def git_exe() -> str:
    global _cached_git
    if _cached_git is not None:
        return _cached_git
    for candidate in _GIT_CANDIDATES:
        if candidate.is_file():
            _cached_git = str(candidate)
            return _cached_git
    _cached_git = "git"
    return _cached_git


def run_git(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    cmd = [git_exe(), *list(args)]
    if os.name == "nt":
        flags = int(kwargs.pop("creationflags", 0) or 0) | CREATE_NO_WINDOW
        kwargs["creationflags"] = flags
    return subprocess.run(cmd, **kwargs)
