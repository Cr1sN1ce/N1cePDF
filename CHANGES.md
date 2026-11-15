# N1cePDF 1.0.0 — release phase (packaging, hardening, installer)

No redesign and no new editing features. Scope: find release blockers, fix confirmed defects, package.

## The two GUI tests that timed out — root cause: test expectation, not an application defect
`test_comment_and_ink_tools_and_roundtrip` and `test_all_object_types_save_and_reopen_without_rasterizing` both
did *Save As → `open_and_wait(out)`*. `_save_tab.done()` calls `doc.mark_saved(out)`, which re-binds the tab to `out`;
`MainWindow.open_path()` correctly finds a tab already showing that file and only focuses it. `open_and_wait` then
waited for a *second* tab (`count == before + 1`) that is never created, so it hit the 20 s timeout. Evidence: code path
(`document.py::mark_saved`, `main_window.py::open_path`, `tests/gui/test_edit_gui.py::save_as/open_and_wait`); no other
test uses Save-As-then-reopen-same-path. The save itself was not implicated (the PDF-content assertions before the
reopen are core-verified by `test_new_text_image_shapes_round_trip` / `test_annotations_persist_and_stay_annotations`).
**Fix (tests only):** `close_saved_tab()` closes the clean tab before reopening, so the test really re-reads the file from
disk. New regression test `test_opening_an_already_open_file_focuses_the_existing_tab`. The "one tab per file" behaviour
is kept on purpose.
**Not verified here:** Qt is not installable in the sandbox, so these three GUI tests have not been executed by me.
Please run `pytest tests/gui/test_edit_gui.py -k "roundtrip or save_and_reopen or already_open"`.

## Application hardening (small, targeted)
- Duplicate-tab detection uses OS file identity (`core/file_manager.same_file`): Windows case differences, 8.3 names
  and symlinks no longer defeat it.
- Saving: a destination that is open in another program (Windows `PermissionError`), a read-only/missing folder, or a
  full disk now produce a clear message and leave the original untouched and the edits intact (previously a missing
  or read-only folder raised an unhandled "Unexpected error" because the temp file was created outside the error handling).
- Windowed build safety (`n1cepdf/runtime.py`): rotating log file in a per-user folder (a windowed exe has no console),
  uncaught exceptions on any thread are logged and shown as a dialog, `AppUserModelID` so the taskbar groups/icons
  correctly, resource lookup that works both frozen and from source.
- Application/window/taskbar icon from `assets/` (the existing logo glyph on the existing accent colour; nothing re-branded).
- Command-line files (what Explorer passes for double-click / Open with) were already opened; unchanged and now documented.
- `main.py`: `multiprocessing.freeze_support()`; `--self-test` entry for the packaged smoke test.
- Version 1.0.0 (`n1cepdf/__init__.py`, `pyproject.toml`, installer, Windows version resource) — a test keeps them equal.

## Packaging
- `packaging/N1cePDF.spec` — PyInstaller **onedir**, windowed (no console), UPX off, version + icon resources,
  PDFium/qpdf/ReportLab data collected, `<pkg>.libs` native-library folders collected, unused Qt modules excluded,
  every bundled package's licence files copied to `licenses/`.
- `packaging/N1cePDF.iss` — Inno Setup 6.3+: Welcome, (License only with `/DWithLicensePage`), Select Destination
  (default `{autopf}\N1cePDF`, per-user unless the user chooses all-users), Select Additional Tasks (desktop and Start Menu
  shortcut, independent, ticked; optional unticked "Open with" registration), Ready, Progress, Finished with ticked
  "Launch N1cePDF". Start Menu shortcut only when selected (`DisableProgramGroupPage=yes`). Uninstaller shows in Installed
  apps with name/version/publisher; removes files, shortcuts, registry; keeps PDFs; asks before deleting settings.
- `build-windows.bat` / `build-windows.ps1`, `packaging/smoke-test.ps1`, `packaging/installer-test.ps1`,
  `requirements/build.txt`, `docs/release-checklist.md`, `scripts/make_icons.py`.
- **Open item:** `Publisher` defaults to "N1cePDF" — pass `-Publisher "Your Name"`. `LICENSE` is still the placeholder line,
  so no licence page is shown and `THIRD_PARTY_NOTICES.md` still needs the PDFium licence text (pypdfium2's installed
  metadata contains none; the build prints a warning).

## Verification performed in the authoring sandbox (Linux, no PySide6/pytest/PyInstaller/Inno/Windows, no network)
Executed and passing:
- 67 Qt-free tests (56 existing + 11 new in `tests/unit/test_release.py`) via a throw-away pytest stand-in (not real pytest).
- The packaged-build self-test's core steps (reportlab → PDFium render → edit with Cyrillic/Romanian text → undo/redo →
  atomic save → re-open, in a path with Cyrillic, Romanian letters and spaces).
- Measured on a generated 3000-page text PDF (2.3 MB): open 0.06 s, first/last-page render 0.01/0.02 s, save with one
  edit 0.37 s, peak process memory 119 MB. Not representative of scan- or image-heavy files.
- Icon: `.ico` contains 16/24/32/48/64/128/256 px frames, inspected visually.
- Static checks: spec and helper modules parse; spec helpers run against installed packages; `.iss` section, task and
  file-reference consistency; PowerShell files are ASCII with balanced brackets.
**Not executed (be explicit):** every GUI test (old and new), the GUI step of `--self-test`, PyInstaller, Inno Setup compile,
the PowerShell/batch scripts, the installer, the uninstaller, anything on Windows. No EXE or installer exists yet.


---

# Changes — Phase 1 bug-fix round

## Root causes
1. **`AttributeError: 'SearchPanel' object has no attribute 'clear_search'`** (`document_tab.py`, in the
   reload callback `done()`): `SearchPanel` defined `clear_results()`, `DocumentTab` called `clear_search()`.
   A naming mismatch from my previous delivery; my stub-Qt smoke check could not catch it because the stub
   accepts any attribute. It aborted `done()` after the view had reloaded but *before* the outline reload,
   `old.close()` and `state_changed`, and pytest-qt fails a test when a Qt slot raises.
2. **`test_insert_and_delete_page`** — traceback not collected. Insert and delete go through the same
   `_after_edit()` → `done()` path, so the same exception is the most likely cause. This is a hypothesis
   until you re-run; please send the traceback if it still fails.
3. **Apparent hang at ~40 %** — cause NOT confirmed. Test order puts tests 18-19 (the failing ones) just before
   `test_save_as_copy_leaves_original`, ~40 % into the run. Candidates found by review (all addressed, none proven):
   a blocking modal dialog in a headless test; a tab closing its document while a worker was still snapshotting
   or saving it. Instrumentation added so the next hang is diagnosed, not guessed (see below).

## Fixes
- `SearchPanel.clear_results(message="")` is the single reset API; `DocumentTab.invalidate_search()` cancels the
  running search, clears results, highlights and `_last_query`, keeps the typed text. It now runs at the *start*
  of every page edit (rotate/insert/delete) as well as after the reload.
- Search results carry the renderer and `doc.revision` they were computed for; stale results are discarded.
- All tab background work goes through `DocumentTab._submit()`: handles are tracked, cancelled on close, and
  callbacks are dropped once the tab is closed (search, outline, copy, reload).
- `DocumentTab.doc_lock`: held by reload/save/extract workers and by `shutdown()` before `doc.close()`, so a
  document can no longer be closed under a running worker.
- `MainWindow._closing` flag: a document finishing its open after shutdown started is released, not added.
- **Windows (found by review, not user-reported):** `PdfDocument.open` now reads the file into memory. Verified
  on Linux that the old code kept a file descriptor open for the document's lifetime; on Windows that blocks
  `os.replace` over the original (Save) and locks the file.

## Tests / tooling
- GUI: autouse fixture turns any unstubbed modal dialog into an immediate failure instead of a hang.
- GUI regressions: search reset after rotate/insert/delete (parametrized), in-flight search discarded after an
  edit, stale result ignored, clearing text resets highlights, search works again after an edit, closing a tab
  with search/copy/reload in flight.
- Core: no held source-file handle; saving over the document's own source path.
- `pyproject.toml`: `faulthandler_timeout = 90` dumps all thread stacks if any test stalls for 90 s.
- Static attribute check (AST) over the project found no other missing project-level methods.

# Second round — teardown ERROR "unexpected modal dialog: QMessageBox.exec"

## Root cause
Test-fixture ordering, not an application bug. pytest-qt closes every widget registered with
`qtbot.addWidget()` in its own teardown hook, which runs *before* fixture finalizers. The `window`
fixture's cleanup (`force_close_all()` after `yield`) therefore ran too late: pytest-qt's `w.close()` reached
the real `closeEvent` → `close_tab` → `_confirm_discard`, and the modal-dialog guard (correctly) rejected it.
Every test that deliberately ends with a modified document was affected, not just the one reported:
`test_insert_and_delete_page`, the three `test_search_cleared_when_document_modified[...]` cases,
`test_inflight_search_is_discarded_after_edit`, `test_stale_search_result_is_ignored`,
`test_search_works_again_after_edit` (and any later test that ends modified).

## Fix (test code only; production behaviour unchanged)
- `tests/gui/conftest.py`: the `window` fixture registers `_cleanup_window` through
  `qtbot.addWidget(w, before_close_func=...)`, which pytest-qt calls right before its `w.close()`.
  `_cleanup_window` calls `force_close_all()` (cancels tab tasks, closes renderers/documents under
  `doc_lock`) and then `runner.shutdown()` (cancels remaining jobs and waits for pool threads), so workers are
  finished or cancelled before widgets are destroyed. The post-`yield` cleanup was removed (it would touch an
  already-deleted C++ object).
- The modal-dialog guard is unchanged and still active for every test.

## New regression tests (real close path; only the dialog's *answer* is stubbed)
Cancel keeps the document and writes nothing · Discard closes and writes nothing · Save writes the rotation to
disk then closes · Save with "Cancel" in the overwrite prompt keeps the tab · untitled document: Save cancelled
in the file dialog keeps the tab / Save to a path closes · closing the *window*: Cancel ignores the close event,
Discard closes · an unmodified document closes with no dialog at all.

# Phase 2 — content editing

## What was built (all in the existing project; Phase 1 behaviour preserved)
- `n1cepdf/edit/` (Qt-free): PDF-space objects (`objects.py`), edit layer, page geometry (`geometry.py`),
  undo stack + commands (`undo.py`, `commands.py`), PDFium content editing + grafting (`pdfium_edit.py`),
  writer for text/images/shapes (vector overlay) and annotations (`writer.py`), exact previews (`preview.py`),
  font catalog with Unicode coverage/fallback (`fonts.py`), `EditSession` with verified edit plans (`session.py`).
- `n1cepdf/ui/`: edit toolbar/tools, `EditController` (tool state machine, PDF-space hit-testing, drags, plans),
  `EditOverlay` (Qt items, handles, rubber bands), `PropertiesPanel`, text-edit and plan-preview dialogs.
- Page rotate/insert/delete are now undoable commands (Phase 1 had no undo); `reorder_pages` preserves page identity.
- `save_atomic(..., session=...)` writes pending edit objects into the output while the in-memory document stays editable.
- One process-wide PDFium lock (`core/pdfium_lock.py`) now covers renderers, previews and edit planning.

## Design decisions worth knowing
- **Existing text**: PDFium removes/rewrites page objects, regenerates that page's content stream, the result is
  re-read and verified, and only /Contents + /Resources are grafted into the pikepdf document (undo restores the
  exact previous objects). In-place rewrite is used only where it is safe; otherwise an explicit
  remove-and-replace workflow with a preview. Nothing is covered with white rectangles.
- **Added objects** stay editable (PDF-space, undoable) until saved; text/images/shapes become vector content,
  markup/ink/comments become standard annotations with appearance streams.

## Bugs found by testing during development (fixed)
- `reorder_pages` created new page objects (changed page identity) → rewritten to re-add the same objects.
- Overlay re-synced against new page indices before the view finished reloading after an insert (IndexError /
  wrong-page risk) → structural edits now wait for the view's `rebuilt` signal; input is ignored while reloading.
- pypdfium2 finalizer assertions (children closed after the document) → explicit child-first closing.
- Never-saved documents lost their "modified" state when undone to the first command → tracked explicitly.

## Test status (be precise)
- Executed here: 56 Qt-free tests (Phase 1 + Phase 2 core), all passing, via a plain-Python runner (pytest is not
  installable in the sandbox).
- NOT executed here: all GUI tests (Phase 1 + 23 new Phase 2 tests in `tests/gui/test_edit_gui.py`).
  A stub-Qt run exercised controller/tab/window Python logic with a real document (this is how the overlay bug above
  was found) but proves nothing about real Qt behaviour.
