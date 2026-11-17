# OCR segmentation, font matching and raster reconstruction

Implemented in `feature/v1.1.0`, using the existing PySide6 document/editor architecture.
This document supersedes the initial white-mask export description in v1.1.0-report.md.

## Spatial information and segmentation

Tesseract is called directly through its local TSV interface, not pytesseract. TSV level-5 rows
contain each word's text, confidence and box. Previously `parse_lines()` grouped every word
sharing block_num/par_num/line_num and joined the strings with one space. A union box then
included the entire gutter between a left label and right counter. Geometry was lost at that
aggregation step, before font matching or PDF writing.

`core/ocr_layout.py` now keeps immutable OcrWords, sorts words geometrically within each row,
and measures median alphanumeric glyph height, median character width, and robust lower-gap
statistics. Large gaps split when they exceed the row's adaptive height/width/space thresholds.
Borderline gaps can be supported by overlapping gutters and aligned edges of neighbouring rows.
A font-height change contributes evidence, but never causes a split by itself. The thresholds
are in row-relative units; scaling OCR resolution does not change segmentation decisions.

Each group becomes a separate OcrLine/TextBox and is independently font-matched. Numeric value
fields default to right alignment; repeated right edges with varying left edges can identify
right-aligned text fields too. Ordinary paragraphs with stable left edges stay left-aligned.
Source word boxes, source mask regions, block bounds and alignment persist in version-2 private
state; version-1 state remains readable. No padding spaces are fabricated to represent gutters.

Properties offers **Split OCR block at cursor** and **Merge neighbouring OCR blocks…**. Place
the cursor between words to split. Only adjacent, non-overlapping fields on the same visual row
can be merged. Macro commands make both operations undoable and redoable. Manual merging keeps
separate source masks, so even a later edit does not clear the intervening gutter. The selected
block supplies the merged style. Text/geometry changes must be undone before manual segmentation;
word positions cannot safely be reused after arbitrary reflow/movement.

Legacy objects without word geometry can be repaired by rerunning OCR on selected pages with
**«Заменить прежние неизменённые OCR-блоки»** checked. Replacement and recognition commit together,
are undoable, and retain edited blocks. Pages returning no recognition retain their old objects.
For already edited legacy overlays, save their reconstruction and recognize the resulting image.

## Font matching and native text

For OCR, each independent source sample is separated from its sampled border background. All
discovered TrueType faces receive a glyph-aspect ranking. Up to 96 candidates covering the actual
Unicode text are rendered locally; symmetric glyph-distance, ink-density and aspect penalties
rank their similarity. The winning face supplies family/bold/italic. Glyph bounds estimate size,
left bearing and baseline; foreground samples estimate colour, and width residual estimates
letter spacing. The same algorithm supports Latin, Cyrillic and Romanian diacritics when the
installed fonts cover them. Discovery includes fontconfig or font metadata in platform font dirs.

This is approximate visual matching, not exact font identification. A bounded shortlist, noisy
scans and ambiguous glyphs limit accuracy. Properties exposes font, size, colour, position,
letter spacing, text width and alignment for correction. Normal edit refreshes preserve the
active text cursor/selection/local undo history, as verified by cursor regression tests.

For native PDF text, existing font/style/size/colour/position inspection is retained. Tc/Tz are
read only when text-show operations map unambiguously to inspected runs. Standard-font in-place
editing preserves the original object. Embedded FontFile2 TrueType programs are reusable only
when OS/2 fsType allows embedding/subsetting and Unicode coverage supports the requested text.
Restricted fonts and subsets with insufficient mappings fall back to an available visual match,
with a substitution warning. Unsupported native forms/rotated replacement limitations still apply.

## Why the preview was blurry

The old overlay fitted each cropped bitmap to a geometric width using
`setScale(target_width / pixmap_logical_width)`. PDFium ceil-rounds raster dimensions, so this
could resample glyphs even immediately after a fresh render. Old bitmaps were also stretched
when geometry changed, and SmoothTransformation added interpolation. Font size was estimated
only from OCR line height; no glyph/baseline comparison was done, creating additional visual
mismatch unrelated to blur.

The overlay now uses device-pixel rendering at zoom × 96/72 × devicePixelRatio, sets QPixmap's
DPR, and keeps item scale at 1 instead of fitting text bitmaps. Temporary stale frames can remain
while a worker runs, but are not geometrically stretched into substitute text. In-flight updates
are coalesced; stale object/scale/context results are discarded. The source image's own resolution
still limits sharpness; the editor does not invent high-resolution scan detail.

Preview and export share ReportLab text layout/PDFium sampling and the same raster reconstruction.
Only the object's crop is rendered for normal previews. OCR preview context includes all changed
blocks on that page, so overlapping source/target regions agree with combined export. Tests
compare preview/export pixels at four zoom scales, all four page rotations and simulated DPR=2.
The tests measure settled raster output, not the platform window compositor or physical monitor.

## Actual raster embedding

For a compatible direct Image XObject, content-stream graphics matrices map PDF coordinates to
original image pixels. Compatible images are unique positive axis-aligned placements without
clipping, masks or ambiguity. The source is decoded once for reconstruction. Each block's source
region (or separate regions for a manual merge) is cleaned independently. Solid background uses
its local sampled colour instead of white. A foreground mask confines cleanup to relevant pixels.
Complex background is refused unless the user explicitly enables local OpenCV TELEA inpainting
and accepts an artifact warning. Dense or excessively large inpainting masks are refused.

Replacement glyphs are drawn through the same PDF writer into a temporary transparent PDF,
sampled at source image density and composited into source pixels. The page's image resource
is replaced by a new losslessly compressed image. Original pixel dimensions are retained. The
normal path leaves the page content stream, native vector objects, page geometry and other pages
unchanged. Shared image resources are copied for the affected page, preserving untouched uses.
There is no visible replacement PDF text object hiding under/over the image.

Edited JPEG data must be decoded to change pixels, then is stored losslessly rather than adding
another JPEG generation. Size can increase. Unchanged JPEG streams are retained byte-for-byte.
Bilevel images promote to grayscale for antialiasing, or RGB if coloured text is requested.
DeviceGray/RGB/CMYK and compatible ICC-based colour spaces are retained when possible; samples
outside cleanup/glyph regions are preserved. Unusual formats can require conversion or refusal.

If direct replacement is unavailable (e.g. forms, skew/flips, clipping or moved text outside an
image), reconstruction refuses with an actionable error. Only an explicit
**Allow affected-page rasterization** setting permits flattening that page's content. This is
page-wide consent: enabling or clearing it on one block updates the entire page, including
preview and export; it does not mark untouched OCR text as changed. The
fallback renders its full MediaBox, retains CropBox/Rotate/annotations, uses at least 300 DPI
and no less than detected direct/nested image density, and affects no other page. A 100-megapixel
limit refuses oversized work instead of lowering DPI. This fallback loses vector editability
on that page and is presented as such before permission is enabled.

Changed OCR objects are committed into the reconstructed base image before private overlay state
is written, and are excluded from that state. Reopening therefore does not restore old pixels.
Unchanged OCR/retained overlays restore against the reconstructed base. Repeated saves do not
accumulate image overlays. After saving, rerun OCR to edit committed scan text; real Tesseract
tests find the new text and do not find the old text. Changed raster OCR has no separate searchable
text layer; unchanged OCR still has its invisible searchable layer.

## Executed verification

On Linux, Python 3.14.3, isolated `/tmp/n1cepdf-venv`, Qt offscreen:

```sh
QT_QPA_PLATFORM=offscreen /tmp/n1cepdf-venv/bin/python -m pytest -q
# 222 passed in 33.02s

QT_QPA_PLATFORM=offscreen /tmp/n1cepdf-venv/bin/python main.py --self-test /tmp/n1cepdf-quality-selftest.json
# exit 0, ok=true, including opening/closing the Qt main window

/tmp/n1cepdf-venv/bin/python -m compileall -q n1cepdf tests packaging
git diff --check
# both exit 0
```

New tests cover adaptive left/right fields at different resolutions, ordinary sentences, mixed
languages, tables/two columns, repeated right edges, actual Romanian Tesseract layouts, independent
mouse selection, manual split/merge/legacy replacement Undo/Redo, state persistence and exact
unchanged-neighbour/gutter pixel comparisons after left-only and right-only edits. Quality tests
cover multiple font families and languages, baseline/size/colour, native Tc/Tz, restricted/full
embedded fonts, white/coloured/complex backgrounds, grayscale/bilevel/CMYK, JPEG resource sharing,
rotated/cropped/moved text, overlapping OCR, true raster structure, repeated save and re-OCR.
GUI coverage includes real Tesseract → automatic font match → Properties typing → Undo/Redo →
HiDPI preview → raster save, with a pixel comparison against the saved PDF.

## Files involved in this update

- Core: `n1cepdf/core/ocr.py`, new `ocr_layout.py`, `file_manager.py`.
- Edit model/persistence: `edit/objects.py`, `session.py`, `ocr_state.py`, new `ocr_blocks.py`.
- Fonts/native PDF: `edit/fonts.py`, `pdfium_edit.py`, new `font_match.py`.
- Raster/preview/export: new `edit/scan_rebuild.py`, `preview.py`, `writer.py`.
- Qt: `ui/ocr_dialog.py`, `edit_controller.py`, `edit_overlay.py`, `properties_panel.py`, `text_dialog.py`.
- Dependencies/package: `requirements/base.txt`, `pyproject.toml`, `packaging/N1cePDF.spec`.
- New tests: `tests/unit/test_scan_quality.py`, `test_ocr_segmentation.py`,
  `tests/gui/test_scan_quality_gui.py`, `test_ocr_segmentation_gui.py`.
- Existing raster expectation/cursor tests updated: `tests/unit/test_ocr.py`,
  `tests/gui/test_text_editor_cursor.py`.
- Documentation: README, CHANGELOG, architecture, limitations, dependency audit, release checklist,
  third-party notices, historical implementation-report note and this report.

No push or commit was made. NumPy/headless OpenCV were installed only in the temporary Python
venv; no system packages were installed. Windows installers and interactive Windows/Fedora
validation were not performed and remain release-checklist tasks.

## Remaining limits

Segmentation is heuristic: justified text, compact tables, unusual baselines, handwriting and
complex multi-column layouts can still need manual split/merge/correction. Manual segmentation
requires original words and unchanged text/geometry. Font matching and inpainting are approximate;
no claim of perfect photo restoration is made. Review the preview, retain the original PDF and
use Undo before saving. This is not secure redaction. External editors can invalidate private
state. Unsupported source placements require explicit page flattening or external image editing.

Subsequent save-fallback and legacy-state regression fixes are documented in ocr-save-fix.md.
