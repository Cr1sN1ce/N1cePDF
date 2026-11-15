# N1cePDF 1.0.0

Offline desktop PDF workspace (Python + PySide6 Qt Widgets): view, search, annotate and edit PDFs.
No network access, accounts or telemetry. Phase 1 (viewer) and Phase 2 (editing) are described in
`docs/feature-matrix.md` and `docs/phase2-feature-matrix.md`, which list exactly what is verified,
unverified or unsupported.

## Install on Windows (end users)

Run `N1cePDF-Setup-1.0.0-x64.exe`. No Python, pip or internet connection is needed — the installer bundles the
Python runtime, Qt, PDFium and every dependency. The wizard offers:

* **Destination folder** – default `%LOCALAPPDATA%\Programs\N1cePDF` (per-user, no administrator rights).
  Choose "all users" in the first dialog for `C:\Program Files\N1cePDF`; you can also browse or type any folder.
* **Additional tasks** – *Create a desktop shortcut* and *Create a Start Menu shortcut* (both ticked, independent), and
  *Offer N1cePDF in the "Open with" list for .pdf files* (unticked). N1cePDF never changes your default PDF app;
  pick it yourself via right-click → Open with, or Settings → Apps → Default apps.
* **Launch N1cePDF** on the last page (ticked).

Uninstall from Settings → Apps → Installed apps. Program files, shortcuts and registry entries are removed;
your PDFs are never touched; you are asked whether to delete settings, the recent-files list and logs (default: keep).

Open files from Explorer or a terminal: `N1cePDF.exe "C:\path with spaces\документ.pdf" other.pdf`.
Each file opens in a tab of one window; the same file is never opened twice.
Logs (no document content) are written to `%LOCALAPPDATA%\N1cePDF\logs\n1cepdf.log`.

## Build the Windows installer (one command)

Requirements on a Windows 10/11 x64 machine: Python 3.12+ (64-bit, with the `py` launcher) and
[Inno Setup 6.3+](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`). Internet is needed
for the first run only (pip downloads packages).

    build-windows.bat

(or `powershell -ExecutionPolicy Bypass -File build-windows.ps1`; the .bat only launches the .ps1). Options:
`-Publisher "Your Name"` (shown in Installed apps; or set `N1CEPDF_PUBLISHER`), `-InstallTest` (also install,
smoke-test and uninstall for the current user), `-SkipTests` (iteration only, never for a release).

The script: 1 checks the environment → 2 creates `.venv-build` and installs `requirements/build.txt` →
3 runs the full test suite and **stops on any failure** → 4 builds `dist\N1cePDF\` with PyInstaller (onedir, windowed,
`packaging/N1cePDF.spec`) → 5 smoke-tests the packaged exe → 6 compiles `packaging/N1cePDF.iss` →
7 verifies `release\N1cePDF-Setup-1.0.0-x64.exe` exists (writes a `.sha256` and `build-environment.txt` with the exact
package versions used) → 8 optional install test. Then work through `docs/release-checklist.md`.

Packaged-build smoke test on its own: `powershell -File packaging\smoke-test.ps1 -ExeDir dist\N1cePDF`
(runs `N1cePDF.exe --self-test`: opens, edits, saves and re-opens a PDF in a Cyrillic/Romanian path with the bundled
libraries, then starts the real window; a JSON report decides pass/fail).

If you later replace `LICENSE` with your final licence text, add `/DWithLicensePage` to the `ISCC` call to show a
licence page in the wizard (currently `LICENSE` is a placeholder, so no licence page is shown).

## Run from source

Fedora:

    sudo dnf install -y python3 python3-pip fontconfig dejavu-sans-fonts
    cd N1cePDF
    python3 -m venv .venv
    ./.venv/bin/python -m pip install --upgrade pip
    ./.venv/bin/python -m pip install -r requirements/dev.txt
    ./.venv/bin/python -m pytest          # core + GUI tests (GUI tests run headless: offscreen)
    ./.venv/bin/python main.py [file.pdf ...]

Windows (development):

    py -3.12 -m venv .venv
    .\.venv\Scripts\python -m pip install -r requirements\dev.txt
    .\.venv\Scripts\python -m pytest
    .\.venv\Scripts\python main.py

`scripts/make_icons.py` regenerates `assets/n1cepdf.ico` / `.png` (the existing app logo on the accent colour).

## Usage
Ctrl+O open · drag a PDF onto the window · Ctrl+F search · Ctrl+wheel zoom · middle-drag pan ·
Ctrl+2 fit width · Ctrl+0 fit page · Ctrl+1 actual size · F9 sidebar · Ctrl+R rotate page ·
Alt+Up/Down previous/next page · Ctrl+C copy selected text · Ctrl+D document properties.
Save asks before overwriting the original; Save As never touches the original; saves are atomic and validated.

## Editing
Edit toolbar (second row) or Tools menu: V browse · S select/move · T edit text · Shift+T add text · I edit images ·
Shift+I add image · H highlight · U underline · K strikethrough · D draw · R shapes · C comment · X erase.
Ctrl+Z undo · Ctrl+Y / Ctrl+Shift+Z redo · Delete removes the selection · Esc cancels · Ctrl+C/Ctrl+V copy/paste an
added object · F10 Properties panel. Edits to existing text/images show a preview with warnings before they are
applied; everything is undoable; the file on disk changes only when you Save (with confirmation) or Save As.

## Layout
`n1cepdf/core` (Qt-free: document, renderer, page ops, atomic save, validation, fonts, PDFium lock) ·
`n1cepdf/edit` (Qt-free editing core) · `n1cepdf/runtime.py` (frozen/dev paths, logging, Windows app id) ·
`n1cepdf/selftest.py` (packaged-build smoke test) · `n1cepdf/services` (recent files) · `n1cepdf/ui` (Qt) ·
`assets/` (icon) · `packaging/` (PyInstaller spec, Inno Setup script, smoke/installer tests) ·
`tests/unit`, `tests/gui` · `docs/`.

## Status — read before publishing
See `CHANGES.md` ("Verification performed in the authoring sandbox") and `docs/known-limitations.md`. In short: the EXE
and installer have **not** been built by the author (Linux-only sandbox); the build scripts were written and statically
checked but must be run on Windows, followed by the release checklist.
