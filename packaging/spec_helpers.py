"""Pure-Python helpers used by N1cePDF.spec (no PyInstaller import, so they can be tested anywhere).

* license_datas  - copy each bundled package's licence files into ``licenses/<package>`` of the build.
* sibling_libs   - collect ``<package>.libs`` directories (auditwheel / delvewheel bundles such as
                   ``pikepdf.libs`` or ``pillow.libs``) that sit NEXT TO the package, which PyInstaller's
                   per-package collection does not pick up.
"""
from __future__ import annotations

import importlib.metadata as md
import re
from pathlib import Path

_LICENSE_NAME = re.compile(r"^(licen[cs]e|copying|notice)", re.I)
_NATIVE = re.compile(r"\.(dll|pyd|so|dylib)(\.\d+)*$", re.I)


def _dist(name: str):
    try:
        return md.distribution(name)
    except md.PackageNotFoundError:
        return None


def license_datas(dist_names: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Return (datas, problems). datas = [(source_file, 'licenses/<dist>')]; problems = human-readable
    warnings for packages that are missing or carry no licence file in their metadata."""
    datas: list[tuple[str, str]] = []
    problems: list[str] = []
    for name in dist_names:
        d = _dist(name)
        if d is None:
            problems.append(f"{name}: not installed")
            continue
        found = 0
        for f in d.files or []:
            if f.parts and f.parts[0].endswith(".dist-info") and _LICENSE_NAME.match(f.name):
                src = Path(str(d.locate_file(f)))
                if src.is_file():
                    datas.append((str(src), f"licenses/{name}"))
                    found += 1
        if not found:
            problems.append(f"{name} {d.version}: no licence file in installed metadata (attach it manually)")
    return datas, problems


def sibling_libs(dist_names: list[str]) -> list[tuple[str, str]]:
    """Return PyInstaller ``binaries`` tuples (source, dest_dir) for ``<pkg>.libs`` directories."""
    out: list[tuple[str, str]] = []
    for name in dist_names:
        d = _dist(name)
        if d is None:
            continue
        for f in d.files or []:
            if len(f.parts) >= 2 and f.parts[0].endswith(".libs") and _NATIVE.search(f.name):
                src = Path(str(d.locate_file(f)))
                if src.is_file():
                    out.append((str(src), f.parts[0]))
    return out
