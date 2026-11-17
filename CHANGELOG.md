# Changelog

## 1.1.0

- Fix page rasterization fallback rejecting consent unless every changed OCR block was flagged.
  Consent now applies to the selected page, is undoable/revocable, and does not dirty OCR text.
- Pass identical page consent to writer and preview, discard obsolete preview denials, and
  report page-specific technical errors. Preserve original files on all failed saves.
- Detect restored legacy merged OCR geometry, propose resegmentation and retain/warn about edits.

- Adaptive OCR word segmentation for separate columns, labels and right-aligned values.
- Retained word coordinates; undoable manual split/merge and legacy OCR resegmentation.
- Local visual font matching, embedded TrueType permission/coverage checks and PDF Tc/Tz retention.
- Device-resolution previews without cached-bitmap fitting; shared raster reconstruction/export.
- Changed OCR baked into source image pixels with local background colour and opt-in inpainting.
- Explicit affected-page raster fallback; original image dimensions and untouched pages preserved.
- Private state is based on reconstructed pixels, excluding committed OCR objects.

- Offline Tesseract OCR for English, Russian and Romanian, including mixed PDFs.
- Current page, explicit page ranges, whole document and textless-page detection.
- Background recognition, progress, process cancellation and atomic result application.
- Recognized line selection through existing edit tools, editable text and geometry,
  undo/redo, invisible searchable text and raster image reconstruction.
- Dependency/language diagnostics and Windows/Linux installation documentation.
- OCR engine, save/edit regression and real Tesseract integration tests.

## 1.0.0

See CHANGES.md for previous implementation history.
