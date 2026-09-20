"""
Roll number -> name, from a CSV the college would own.

Exodus never asks a student for their name: identity is the roll number in
their address, and the roster is what turns that into something a person
recognises in their cab. A name is display only — nothing matches, groups or
authorises on it.

The file is `roster.csv` next to this module (override with ROSTER_FILE):

    student_id,name
    imt2022101,Ananya Rao

Unknown ids simply have no name, so the UI falls back to the roll number.
A missing or unreadable roster is not an error: names are a nicety, and a
release must never fail because a CSV moved.
"""

from __future__ import annotations

import csv
import os
import threading
from pathlib import Path

ROSTER_FILE = Path(os.environ.get("ROSTER_FILE", Path(__file__).parent / "roster.csv"))

_lock = threading.Lock()
_names: dict[str, str] | None = None


def _load() -> dict[str, str]:
    names: dict[str, str] = {}
    try:
        with open(ROSTER_FILE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                sid, name = (row.get("student_id") or "").strip().lower(), (row.get("name") or "").strip()
                if sid and name:
                    names[sid] = name
    except OSError:
        pass  # no roster: everyone is known by their roll number alone
    return names


def names() -> dict[str, str]:
    """The whole roster, read once per process."""
    global _names
    with _lock:
        if _names is None:
            _names = _load()
        return _names


def name_for(student_id: str) -> str | None:
    return names().get((student_id or "").lower())


def reload() -> dict[str, str]:
    """Forget the cached roster; for tests and for editing the CSV while running."""
    global _names
    with _lock:
        _names = None
    return names()
