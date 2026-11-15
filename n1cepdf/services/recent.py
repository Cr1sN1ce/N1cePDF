"""Recent documents list (local JSON; Qt-free so it is unit-testable)."""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

MAX_RECENT = 10


def config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "N1cePDF"
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support/N1cePDF"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "n1cepdf"
    return base


class RecentFiles:
    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "recent.json"

    def _load(self) -> list[str]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
            return [s for s in data if isinstance(s, str)]
        except (OSError, ValueError):
            return []

    def list(self) -> list[str]:
        return [s for s in self._load() if Path(s).is_file()][:MAX_RECENT]

    def add(self, file: str | Path) -> None:
        f = str(Path(file).resolve())
        items = [f] + [s for s in self._load() if s != f]
        self._write(items[:MAX_RECENT])

    def clear(self) -> None:
        self._write([])

    def _write(self, items: list[str]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(items, fh)
            os.replace(tmp, self.path)
        except OSError:
            pass  # recent list is a convenience; failure must not break the app
