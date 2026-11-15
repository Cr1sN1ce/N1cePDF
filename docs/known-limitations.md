# Known limitations
- **The author cannot run Qt.** PySide6/pytest/pytest-qt are not installable in the authoring sandbox. Only the Qt-free core is executed there (26 unit tests, plain-Python runner). GUI results come from the user's Windows 11 runs; fixes made after those runs are unverified until re-run (see CHANGES.md).
- **Qt warnings** `QFontDatabase: Cannot find font directory …/PySide6/lib/fonts` and `This plugin does not support propagateSizeHints()` are emitted by Qt's `offscreen` platform plugin (the tests' default). They are expected to be harmless (Qt falls back to system fonts) but this was not verified here. Run `set QT_QPA_PLATFORM=windows` to use the native plugin instead.
- Empty-window cause: the previous central widget was an empty `QTabWidget`, so nothing was shown without a document (certain from the code). The invisible menu bar could not be reproduced here; the fix is defensive (in-window non-native menu bar, removal of six empty placeholder menus, palette theme instead of a global stylesheet). Please confirm on Fedora KDE/Wayland.
- Text selection works within one page per drag; Select All spans the document. No double/triple-click word/line selection.
- Selection and highlights use pdfium geometry; ligatures and complex scripts may select slightly different character spans.
- Page operations (rotate/insert/delete) have no undo until Phase 2. The file on disk is unchanged until Save (confirmed) or Save As.
- Saving an encrypted document always writes an unencrypted copy (after confirmation); re-encryption is Phase 2.
- Rendering snapshots re-serialize the whole document after an edit (background thread); fine for typical files, slow for very large ones.
- HiDPI: bitmaps render at devicePixelRatio; fractional-scale behavior on Wayland is untested.
- Single-page/facing-page modes, light-theme toggle, and settings persistence are not implemented.
- Windows: the owner reports Phase 1–2 working on Windows; the 1.0.0 packaging (PyInstaller build, Inno installer, scripts) has been written and statically checked but **not yet built or run** by the author — see CHANGES.md.


## Phase 2 limitations (see docs/phase2-feature-matrix.md for the full table)
- **PDFium regenerates the whole content stream of every page whose existing text/image is edited.** Visual output is preserved, but marked-content/optional-content/tagged-PDF structure on that page may be flattened.
- Editing granularity is the PDF text object (a run/line). A single word inside a run is edited by editing the run; paragraphs are not reflowed.
- Only non-embedded standard-14 fonts are rewritten in place. Everything else uses remove-and-replace with a *different* embedded font (the preview says so). Rotated/skewed text can only be edited in place.
- Text inside Form XObjects, outlined text and scanned text cannot be edited (reported to the user).
- Object removal is not certified redaction: metadata, XMP, other pages and embedded files are untouched; secure redaction remains a later phase.
- Annotations already present in an opened file are not loaded into the editable layer.
- A process-wide PDFium lock serialises rendering, previews and edit planning; heavy planning briefly pauses background rendering.
- Page reordering by drag-and-drop in the sidebar is not implemented (the undoable command exists).
- Digital-signature invalidation warnings are not implemented.


## 1.0.0 release notes
- One window per process: opening several PDFs from Explorer at once starts one N1cePDF window per file (no single-instance forwarding).
- Packaged build: size and start-up time have not been measured yet (no Windows build was possible in the authoring sandbox).
- The installer is not code-signed, so Windows SmartScreen may warn about an unknown publisher; signing needs a certificate.
- "Open with" registration is optional and never sets the default PDF app (Windows protects that choice).
- `LICENSE` is a placeholder; PDFium's licence text must be added to `THIRD_PARTY_NOTICES.md` before public redistribution.
- A document must have a covering TrueType font installed for Cyrillic/Romanian text edits (Arial/Segoe UI on Windows are used); fonts are not bundled.
