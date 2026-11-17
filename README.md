# N1cePDF 1.1.0

Offline desktop PDF workspace (Python + PySide6 Qt Widgets): view, search, annotate and edit PDFs.
No network access, accounts or telemetry. Phase 1 (viewer) and Phase 2 (editing) are described in
`docs/feature-matrix.md` and `docs/phase2-feature-matrix.md`, which list exactly what is verified,
unverified or unsupported.

## Install on Windows (end users)

Run `N1cePDF-Setup-1.1.0-x64.exe`. No Python, pip or internet connection is needed — the installer bundles the
Python runtime, Qt, PDFium and Python dependencies. Optional OCR requires separately installed Tesseract. The wizard offers:

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
7 verifies `release\N1cePDF-Setup-1.1.0-x64.exe` exists (writes a `.sha256` and `build-environment.txt` with the exact
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

## OCR (v1.1.0)

Tools → **Распознать текст (OCR)…** (`Ctrl+Shift+O`) runs local Tesseract in a worker.
Choose English, Russian, Romanian or all three; current page, explicit ranges (`1,3-5`),
whole document or automatically only pages without text. Manual mode also recognizes image
text on mixed pages and filters overlaps with existing text. Cancel discards all results.

OCR uses word coordinates and adaptive gap/row analysis to separate independent fields, table
cells and columns. Labels and counters such as `Parole și identitate` and `4/4 · 100%` become
separate objects. Numeric fields retain right alignment. Ordinary interword gaps remain inside
one object; artificial spaces are not used to reconstruct large gutters.

Blue outlines identify independent blocks. Use **Edit Text (T)** to select one and change its
text/font in Properties; **Select (S)** moves/resizes it. Local font matching compares installed
Unicode-capable fonts with the original glyph image and estimates size, style, colour, baseline
and spacing. It is approximate. Letter spacing, width and alignment can also be changed manually.
Properties scrolls on smaller screens. Cursor/selection, clipboard and text Undo/Redo are retained.

For ambiguous layouts, put the text cursor between words and click **Split OCR block at cursor**.
**Merge neighbouring OCR blocks…** joins adjacent fields on the same row. Both operations are
undoable and use retained word coordinates without calling Tesseract. Split/merge requires
unedited OCR geometry; undo text/geometry edits first. To repair legacy OCR lacking word data,
rerun the relevant pages with **«Заменить прежние неизменённые OCR-блоки»** checked. The dialog
automatically proposes this for legacy merged geometry and explains which edited blocks will
be retained. This replaces
only unchanged OCR blocks and is itself undoable; edited blocks are retained.

Unchanged OCR saves invisible searchable text over the preserved scan. **Changed OCR is saved
inside image pixels**, not as a visible PDF text layer. The normal path replaces the source
image XObject at its original pixel dimensions; each block masks only its own source region.
A solid background uses its sampled local colour. Complex backgrounds require explicit
**Local inpainting** permission in Properties and review of possible artifacts; risky large
regions are refused. The live preview uses the same reconstruction and PDF drawing as export,
rendered at current zoom/device-pixel ratio without fitting/resampling cached text bitmaps.

Forms, clipping, skewed/flipped source placements and out-of-image moves may prevent direct image
replacement. They produce an actionable error. **Allow affected-page rasterization** is a page-wide permission: enabling it on one block
applies to all OCR blocks on that page, and clearing it revokes permission for the page.
It explicitly permits rasterizing only that page, retaining MediaBox/CropBox/Rotate and annotations. This loses
vector editability on that page; other pages are preserved. Its density is at least 300 DPI and
never below detected source image density; oversized operations are refused, not downsampled.
Edited JPEGs are decoded and saved losslessly (file size can increase); untouched JPEGs retain
their encoded streams. Bilevel scans promote to grayscale for antialiasing, or RGB for coloured
text. Existing DeviceRGB/Gray/CMYK and compatible ICC-based spaces are retained when possible.

After save/reopen, edited OCR is part of the scan; run OCR again to edit those pixels. Private
state retains only uncommitted/unchanged OCR overlays over the actual reconstructed image,
including word coordinates and separate source masks. It never restores the old pixels of
committed edits. Review every edit: font matching and background reconstruction are approximate,
and this is **not secure redaction**. Pending OCR blocks participate in N1cePDF search.
See [technical details and limitations](docs/ocr-quality.md).

Tesseract is optional and installed separately; normal PDF features work without it.
No cloud services, OCRmyPDF or Ghostscript are used. NumPy and headless OpenCV are Python
dependencies installed through requirements/base.txt; no system OpenCV installation is needed.

- Fedora: `sudo dnf install tesseract tesseract-langpack-eng tesseract-langpack-rus tesseract-langpack-ron`
- Debian/Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-rus tesseract-ocr-ron`
- Windows 10/11: install a Windows Tesseract distribution, select English/Russian/Romanian
  language data and add its installation folder to PATH. Alternatively set the environment
  variable `N1CEPDF_TESSERACT` to the full path of `tesseract.exe` and restart N1cePDF.
  If traineddata is stored separately, set `TESSDATA_PREFIX` to its directory.

Verify installation with `tesseract --list-langs`: `eng`, `rus`, `ron` must appear.
The installer does not download or bundle Tesseract or traineddata. Release builders should
verify external OCR discovery and missing-dependency messages on a clean Windows machine.

Development: `python -m pip install -r requirements/dev.txt`, then `python main.py`.
Tests: `QT_QPA_PLATFORM=offscreen python -m pytest` (Linux/headless); real OCR tests
skip when Tesseract or English data are unavailable. OCR pages are rendered up to 300 DPI
with a maximum dimension of 8000 pixels; cancellation is checked between rendering stages
and while Tesseract runs (120-second limit per page).

OCR save-fallback diagnosis and 41-page regression coverage: [critical save fix](docs/ocr-save-fix.md).
