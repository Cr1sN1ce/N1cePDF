# Critical OCR save fallback fix — v1.1.0

## Reproduced cause

The Properties checkbox correctly reached the immutable OCR object and the writer. The actual
failure was `all(o.ocr_allow_page_raster for o in objects)` inside `rebuild_page()`: consent was
required independently on every changed block, despite permitting rasterization of a whole page.
A second edited block with False caused the quoted error even when the selected block was True.
A new regression reproduced this failure before the fix on a generated 41-page PDF with its first
scan inside a Form XObject (direct image replacement is therefore unavailable).

Two related problems amplified the failure:

- `EditSession.update_object()` cleared `ocr_original` for configuration-only changes, so merely
  enabling permission could mark previously untouched OCR text as a raster edit.
- Preview failures were not checked for staleness. A failed task started before permission was
  granted could display the old denial and clear the preview after consent had already changed.

Properties could also display an obsolete immutable object if `select()` received an older value;
selection now resolves the current layer object by ID before displaying properties.

## Corrected flow

Properties asks for consent with the existing vector-loss explanation, then calls the session's
page-wide setter. A single undo macro updates flags on every OCR object of that stable page key.
Clearing the checkbox revokes consent for the page. Configuration changes preserve original OCR
text/geometry; newly added blocks inherit the page's permission. Restored legacy mixed flags are
normalized per page. No permission propagates to another page.

The writer determines consent from all OCR objects on the affected page, including unchanged
objects. Preview receives the same explicit page permission. Permission is no longer inferred
with an all-block requirement over only the changed subset.

If image placement inspection or source extraction cannot provide a direct replacement,
`LocalImageUnavailable` triggers affected-page rasterization when authorized. This renders and
reconstructs only that page, writes an image XObject and preserves the remaining pages. It does
not write visible replacement text objects. Page content geometry/annotations are retained;
the preview/export still share reconstruction, font matching and block segmentation.

Actual failures report the page number and technical reason (e.g. the 100-megapixel limit,
source density or renderer/extraction failure). The misleading permission error is only shown
when that page lacks permission. Temporary CropBox/Rotate changes are restored even if snapshot
creation fails. Saving still writes/validates a sibling temporary file before atomic replacement;
original/destination files survive any failure, including a later unapproved page.

Stale preview errors are discarded just like stale successful frames, so granting permission
cannot be followed by an obsolete denial from an earlier task.

## Segmentation and restored state

The restored common frame was another path: repeated OCR excluded existing objects, so old
merged geometry could survive without passing through the new word segmentation again.
The OCR dialog now detects unchanged legacy objects lacking words or containing a detectable
large internal gutter and automatically proposes replacing them through the existing checkbox.
Deliberate manual merges with separate masks are not automatically targeted.

Edited legacy blocks are excluded from replacement, retained exactly and explained in the
visible dialog hint and completion message. Their old frame may remain intentionally. Undo their
edits first to resegment, or save a copy, reopen its committed image and run OCR again. Replacement
itself is undoable. GUI tests reopen actual saved common-frame state with and without word data,
then prove independent left/right selection and protection of an edited legacy object.

## Executed checks

Linux, Python 3.14.3, Qt offscreen, existing local Tesseract installation:

```sh
QT_QPA_PLATFORM=offscreen /tmp/n1cepdf-venv/bin/python -m pytest -q
# 235 passed in 42.58s

QT_QPA_PLATFORM=offscreen /tmp/n1cepdf-venv/bin/python main.py --self-test /tmp/n1cepdf-save-fix-selftest.json
# exit 0, ok=true; includes Qt window open/close

/tmp/n1cepdf-venv/bin/python -m compileall -q n1cepdf tests
git diff --check
# both exit 0
```

Thirteen new regressions cover:

- 41-page PDF, a scan inside a Form XObject, actual OCR and changed raster pixels after reopening.
- Multiple changed blocks with only one legacy consent flag, and consent on an untouched block.
- A real GUI checkbox → session → writer → raster fallback → Save As sequence.
- No visible text replacement layer, no old title after real re-OCR, retained independent counter.
- Unchanged content streams/font resources/geometry on the other 40 pages.
- Consent revocation, page isolation, Undo/Redo and preservation of files/temporary cleanup.
- Specific failure diagnostics and restoration of temporary geometry on snapshot failure.
- Source extraction failure falling back to the renderer when authorized.
- Restored common-frame replacement, independent selection and retention of edited blocks.
- Discarding a stale preview denial after consent changes.

The user's actual 41-page file/screenshot was not attached in the workspace; verification used
reproducible generated PDFs exercising the same unavailable-local-replacement path. Interactive
Windows/Fedora testing and Windows installers were not run.

## Files changed for this fix

- `n1cepdf/edit/session.py` — page consent, migration/inheritance, configuration vs visual edits.
- `n1cepdf/edit/writer.py` — explicit page consent across the full layer.
- `n1cepdf/edit/scan_rebuild.py` — authorized fallback, extraction fallback, page diagnostics,
  exception-safe temporary geometry.
- `n1cepdf/edit/preview.py`, `n1cepdf/ui/edit_overlay.py` — consistent permission and stale errors.
- `n1cepdf/ui/edit_controller.py`, `properties_panel.py` — canonical selection and page checkbox.
- `n1cepdf/ui/ocr_dialog.py` — legacy geometry detection/proposal and protection of edits.
- `n1cepdf/core/file_manager.py` — preserve concrete unexpected error details.
- New `tests/unit/test_ocr_save_fallback.py` and `tests/gui/test_ocr_save_fallback_gui.py`.
- README.md, CHANGELOG.md, docs/ocr-quality.md and this report.

Work remains in `feature/v1.1.0`. No commit/push or system-package installation was performed.
