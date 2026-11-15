"""Portable discovery of a Unicode-capable TrueType font (Cyrillic + Romanian)."""
from __future__ import annotations
import os
import shutil
import subprocess
import sys
from pathlib import Path

_FAMILIES = ["DejaVu Sans", "Liberation Sans", "Noto Sans", "Arial", "Segoe UI"]
_FILES = ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "NotoSans-Regular.ttf",
          "arial.ttf", "segoeui.ttf", "Arial.ttf"]


def _font_dirs() -> list[Path]:
    dirs: list[Path] = []
    if sys.platform.startswith("win"):
        dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    elif sys.platform == "darwin":
        dirs += [Path("/Library/Fonts"), Path("/System/Library/Fonts"), Path.home() / "Library/Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
                 Path.home() / ".local/share/fonts", Path.home() / ".fonts"]
        for d in os.environ.get("XDG_DATA_DIRS", "").split(":"):
            if d:
                dirs.append(Path(d) / "fonts")
    return [d for d in dirs if d.is_dir()]


def _fontconfig(family: str) -> Path | None:
    exe = shutil.which("fc-match")
    if not exe:
        return None
    try:  # argument array, no shell
        out = subprocess.run([exe, "-f", "%{file}", f"{family}:style=Book"], capture_output=True,
                             text=True, timeout=5, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    p = Path(out) if out else None
    return p if p and p.suffix.lower() == ".ttf" and p.is_file() else None


def find_unicode_font() -> Path | None:
    """First usable .ttf: fontconfig match (Linux), then a recursive scan of standard dirs."""
    wanted = {f.lower() for f in _FILES}
    for fam in _FAMILIES:
        p = _fontconfig(fam)
        # fc-match always returns *something*; accept only if it is one of ours
        if p and p.name.lower() in wanted:
            return p
    for name in _FILES:
        for d in _font_dirs():
            for hit in d.rglob(name):
                if hit.is_file():
                    return hit
    return None
