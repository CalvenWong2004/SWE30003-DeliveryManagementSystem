"""File-based persistence: one JSON document per collection (A2, A3-4).

The assignment allows files instead of a database. Writes go to a temporary
file that then replaces the original, so a crash never leaves half a file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class JsonStore:
    """Reads and writes named collections of plain dictionaries."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, collection: str) -> Path:
        return self.directory / f"{collection}.json"

    def load(self, collection: str) -> list[dict[str, Any]]:
        """Return every record in ``collection`` (empty if none saved)."""
        path = self._path(collection)
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    def save(self, collection: str, records: list[dict[str, Any]]) -> None:
        """Replace ``collection`` with ``records`` atomically."""
        path = self._path(collection)
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(records, handle, indent=2)
        os.replace(temp, path)

    @property
    def outbox_path(self) -> Path:
        """Return the file the notification outbox is written to."""
        return self.directory / "outbox.txt"
