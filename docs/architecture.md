# Architecture
`n1cepdf/core` is Qt-free (document model, renderer, page ops, atomic save, validation). `n1cepdf/ui` depends on core, never the reverse. pikepdf owns the editable structure; pdfium renders byte snapshots. Workers return plain data tagged with a generation id; only the GUI thread touches widgets.

## v1.1.0 OCR design

`core/ocr.py` runs Tesseract via argument-list subprocess calls, temporary PNG/TSV files and
cancellation-aware polling. PDFium rendering/text extraction use the existing global lock;
Tesseract runs outside it. Recognition uses an immutable PDF snapshot, returns PDF-space lines,
and never mutates the document. All page results commit on the GUI thread as one macro command,
only if the revision still matches. Cancellation or failure discards partial results.

Manual mode filters OCR words overlapping existing text (including nested forms) and edit-layer
bounds; automatic mode skips pages with accessible text. Rendering removes page rotation so
coordinates map to unrotated PDF user space with the existing CropBox transform.

`core/ocr_layout.py` retains word boxes, analyzes adaptive gaps and repeated column boundaries,
and assigns independent field alignment. `ui/ocr_dialog.py` matches fonts per resulting field
using `edit/font_match.py`, then creates normal edit-layer TextBoxes with word geometry and
independent source masks. Manual split/merge is implemented in `edit/ocr_blocks.py` and committed
through ordinary undo macro commands, without another OCR invocation.

`edit/scan_rebuild.py` reconstructs only changed OCR source regions in original source image
coordinates. ReportLab emits replacement glyphs into a temporary PDF, PDFium samples them at
source image density, and the glyph pixels are composited into the original image. Compatible
direct image XObjects are replaced; unsupported placements refuse the operation unless the user
explicitly allows rasterizing that page. Unchanged pages and native text stay structured.

`edit/preview.py` uses the same reconstruction and drawing paths as export, rendering only the
object's crop at display device-pixel scale. `ui/edit_overlay.py` does not fit bitmaps to rounded
bounding-box widths, coalesces in-flight edits, rejects stale results and includes all changed
OCR blocks on the page in reconstruction context. This prevents overlapping blocks from showing
a different combination of pixels than the saved file.

`edit/ocr_state.py` stores versioned, JSON-encoded overlay state; version 1 remains readable and
version 2 retains words/regions/alignment. Changed OCR is baked into base image pixels before
state is stored. Committed OCR objects are excluded, so reopening never restores old image pixels.
Unchanged OCR and other retained overlays restore against this reconstructed base. No pickle or
executable serialization is used. Core imports the restoration helper lazily, as with the writer.

Native PDF inspection reads fonts/styles/colour and unambiguous Tc/Tz states. Font reuse checks
OS/2 fsType and Unicode coverage for embedded TrueType; unavailable fonts use local visual matching.
Replacement text remains vector PDF text. Ambiguous text-state mappings retain conservative defaults.
