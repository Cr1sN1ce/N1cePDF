# Dependency audit (Phase 0, partial)

Status key: **verified here** = checked in the authoring sandbox (no network, so no online doc review).
**to verify** = needs checking against current upstream docs before release.

| Library | Role | License (installed metadata / known) | Notes |
|---|---|---|---|
| pypdfium2 5.14.0 | render, text, search | BSD-3 / Apache-2.0 (PDFium) — verified here | PDFium is not thread-safe; calls are serialized by a lock. Exposes `FPDFText_SetText` and page-object APIs but **no redaction API**. |
| pikepdf 10.16.0 | structure, page ops, encryption | MPL-2.0 (qpdf bindings) — **to verify** (metadata empty here) | Wheels bundle qpdf (Apache-2.0). |
| qpdf 11.9.0 (system) | validation / CLI | Apache-2.0 — to verify | Not required at runtime: pikepdf bundles it. |
| ReportLab 5.0.1 | new PDF content, fixtures | BSD — verified here | |
| Pillow 13.0.0 | images | HPND — to verify | |
| PySide6 | UI | **LGPL-3.0 / GPL / commercial** — to verify | NOT installed here. LGPL requires dynamic linking + notices; PyInstaller one-file builds need care (user must be able to relink/replace Qt). |
| Tesseract 5 (system) | OCR | Apache-2.0 — to verify | External optional executable. v1.1.0 checks requested language data before recognition. |
| OCRmyPDF | searchable PDF | MPL-2.0 — to verify | Not used by v1.1.0; the application calls Tesseract directly. |
| pytest / pytest-qt / PyInstaller | dev | MIT / MIT / GPL-2 with bootloader exception — to verify | NOT installed here. |

Excluded by design: PyMuPDF/MuPDF (AGPL), Ghostscript (AGPL) unless explicitly approved.

## Phase 1 additions
- `PySide6.QtSvg` (part of the PySide6 wheel) is used to tint inline SVG icons; no extra dependency.
- Font discovery uses `fc-match` (fontconfig, optional) via an argument array (no shell), then scans standard font directories; no font is bundled.
- Environment the user reported working: Fedora 44, Python 3.14.3, PySide6 6.12.0. Not re-verified here.

## Phase 2 additions
No new third-party packages. New uses of existing ones: pikepdf `Page.add_overlay` and annotation dictionaries; pypdfium2 page-object APIs (text objects, `FPDFText_SetText`, `remove_obj`, image matrices, `gen_content`); ReportLab TrueType embedding (subset + ToUnicode; fonts whose licence flags forbid embedding are skipped); Pillow for PNG/JPEG/WebP decoding (WebP needs libwebp in your Pillow build). Fonts are never bundled: users need a covering TrueType family installed (DejaVu, Liberation, Noto, Arial or Segoe UI are detected). PDFium has no redaction API; none is claimed.

## 1.0.0 packaging additions
Build-time only: PyInstaller >=6.10,<7 (GPL-2.0 with bootloader exception), Inno Setup 6.3+. Runtime: `lxml` is pulled in by pikepdf (bundled explicitly). Verified here: package licence-file discovery (`packaging/spec_helpers.py`) and the `<pkg>.libs` native-library folders of pikepdf and Pillow on Linux wheels. NOT verified: the Windows wheel layout of those packages, PyInstaller's PySide6 hook output, final bundle size.

## OCR visual processing

NumPy and opencv-python-headless are required Python dependencies for local pixel comparison
and limited inpainting. The PyInstaller specification collects their binaries/data and licence
metadata. No system OpenCV installation or network OCR is used.
