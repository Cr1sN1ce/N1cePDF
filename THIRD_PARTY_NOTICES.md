# Third-party notices (1.1.0 — complete before public redistribution)

N1cePDF's Windows build bundles the components below. The build copies the licence files found in each package's
installed metadata to `licenses/<package>/` inside the application folder (`_internal\licenses`). Licence
identifiers are from upstream metadata and should be re-verified at release time.

| Component | Use | Licence (upstream) | Notes |
|---|---|---|---|
| Python runtime | interpreter | PSF License | bundled by PyInstaller |
| PySide6 / Qt for Python (Qt 6, shiboken6) | user interface | LGPL-3.0 (or GPL / commercial) | Dynamically linked; the onedir build keeps Qt as separate replaceable DLLs. Licence texts: `licenses/PySide6*`. |
| pypdfium2 / PDFium | rendering, text, search | BSD-3-Clause / Apache-2.0 | **Licence text not present in the installed metadata — attach PDFium's and pypdfium2's licence files manually.** |
| pikepdf (+ bundled qpdf) | PDF structure, saving | MPL-2.0 (pikepdf), Apache-2.0 (qpdf) | licence file bundled |
| ReportLab | new text/vector content | BSD-3-Clause | licence file bundled |
| Pillow | images | HPND | licence file bundled |
| lxml | XMP metadata (pikepdf dependency) | BSD-3-Clause | licence files bundled |
| packaging | pikepdf dependency | Apache-2.0 / BSD-2-Clause | licence files bundled |
| PyInstaller bootloader | build tool output | GPL-2.0 with bootloader exception | the exception allows distributing the built application under its own terms |
| Inno Setup | installer | Inno Setup License | build tool only |

Excluded by design: PyMuPDF/MuPDF (AGPL), Ghostscript (AGPL). Tesseract is an optional external executable and is not redistributed with N1cePDF. OCRmyPDF is not used.

Visual font matching and reconstruction use NumPy and opencv-python-headless. Include their
installed distribution licence texts when packaging; PyInstaller licence collection includes both.
