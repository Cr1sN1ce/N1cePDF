# Phase 2 feature matrix

Status labels
- **Verified (core)** — executed by Qt-free unit tests in the authoring sandbox (`tests/unit/test_edit.py`, 56 core tests passing together with Phase 1).
- **Implemented, GUI unverified** — code exists and has a pytest-qt test, but the author cannot run Qt; run `pytest` on your machine. A stub-Qt smoke run exercised the controller logic only.
- **Partial** / **Unsupported** — see the notes; nothing is faked.

## 2A — Foundation
| Feature | Status | Notes |
|---|---|---|
| 12 editing tools + Browse, one active at a time (exclusive group) | Implemented, GUI unverified | toolbar + Tools menu; letter shortcuts V S T Shift+T I Shift+I H U K D R C X |
| Cursor and Properties panel follow the tool | Implemented, GUI unverified | |
| Esc cancels drag → selection → tool; Delete removes selection | Implemented, GUI unverified | |
| PDF-space edit model (never pixels) | Verified (core) | immutable objects keyed by stable page identity (survives reorder/insert/delete/undo) |
| Coordinate mapping (zoom, scroll, CropBox offset, /Rotate 0/90/180/270) | Verified (core) vs PDFium for all four rotations; GUI hit-testing unverified | selection handles are constant screen size at every zoom |
| Properties panel for text / image / shape / markup / comment / ink / existing text / existing image | Implemented, GUI unverified | |
| Command-based undo/redo (own Qt-free stack; no raster snapshots) | Verified (core) | merge of typing/nudges, macro rollback, clean-state tracking, never-saved docs stay modified |
| Ctrl+Z, Ctrl+Y, Ctrl+Shift+Z; undo updates viewer + modified flag | Implemented, GUI unverified | |
| Undo for: added text/images/shapes/annotations, property edits, text edits, image edits, rotate / insert / delete / reorder pages | Verified (core) | page-drag-and-drop reordering UI does not exist yet (command and tests do) |

## 2B — Existing and new text
| Feature | Status | Notes |
|---|---|---|
| Detect text runs + bounding boxes, click to select | Verified (core detection); click path GUI unverified | granularity = PDFium text objects (usually one line/run) |
| Native in-place rewrite (same font/size/colour/position) | Verified (core) | only non-embedded standard-14 fonts and WinAnsi text; verified by re-reading the result; falls back automatically |
| Remove-and-replace workflow (embedded Unicode font) | Verified (core) | old text object is removed from the content stream (checked in extraction **and** raw file bytes); preview dialog lists every warning |
| Cyrillic, Romanian, Latin | Verified (core) | needs a covering TrueType font installed (DejaVu/Liberation/Noto/Arial/Segoe) |
| Delete existing text | Verified (core) | |
| Font substitution + warnings; font-licence embedding refusal skipped | Verified (core) | bold/italic only when the family has that face (no synthetic bold) |
| Overlap reporting (images, vectors, other text, forms) | Verified (core) | reported, never silently changed |
| Unsupported cases refused with a message | Verified (core) | rotated/skewed text needing replacement; text inside Form XObjects; scanned/outlined text |
| New text boxes (click, type, move, resize, font, size, colour, alignment, spacing, opacity, rotation, multiline) | Core verified; GUI unverified | embedded TrueType subset with ToUnicode → searchable |
| Spacing: line spacing only | Partial | no character spacing |

## 2C — Images and graphics
| Feature | Status | Notes |
|---|---|---|
| Add PNG / JPEG / WebP, transparency | Verified (core) | WebP needs a Pillow build with libwebp; JPEG kept as DCT (no recompression) |
| Move / resize / rotate / delete added images, aspect lock + Shift | Core verified; GUI unverified | handles need rotation 0; rotate via panel |
| Select, move, resize, delete, replace **existing** image objects | Verified (core) | PDFium object matrix; image data untouched; replace = remove + new image over the same bounds |
| Rotate existing images | Partial | supported by the core call; no UI control |
| Copy/paste | Partial | added objects (Ctrl+C / Ctrl+V); system-clipboard image paste is written but unverified |
| Rectangle, ellipse, line, arrow; fill, stroke, width, opacity | Core verified (solid colours); GUI unverified | opacity written via graphics state; pixel-level opacity not tested |
| Freehand pen | Partial | the Draw tool creates an Ink annotation; there is no separate pen *shape* |

## 2D — Annotations and persistence
| Feature | Status | Notes |
|---|---|---|
| Highlight / underline / strikethrough (standard annotations + appearance streams) | Verified (core) | persist across save/reopen/save |
| Sticky-note comments (Text annotation), Ink annotations | Verified (core) | |
| Editing annotations **loaded from an opened file** | Unsupported | they render and survive saves, but the editor does not load them as editable objects |
| Save / Save As / atomic temp-file replace / validation | Verified (core) | pending objects are written at save; document and layer stay editable; Windows handle fix kept |
| No page rasterization | Verified (core) | content edits are vector/text; test asserts no image XObjects are added |
| Metadata, bookmarks, page size and rotation preserved | Verified (core) | |
| Encrypted documents | Verified (core) | existing password flow; saving still requires the explicit unencrypted-copy confirmation |
| Original file never silently overwritten | Verified (core) + existing Phase 1 GUI tests | |
| Worker-thread safety (global PDFium lock, guarded tasks, stale-result rejection, no close under a worker) | Implemented; core parts verified; GUI unverified | |

## Features requiring manual validation (on Windows and Fedora)
1. Every tool with the real mouse (cursor shapes, handles, drag smoothness, Esc/Delete).
2. Edit Text on several real-world PDFs: Word/LibreOffice exports (embedded fonts → replace path), scanned PDFs, forms.
3. Visual fidelity of replacement text (font substitution, baseline, width warnings).
4. Opening saved files in Acrobat/Preview/Chrome: annotations, appearance streams, fonts, no visual shifts.
5. Page-content regeneration side effects on complex pages (transparency groups, layers/OCG, tagged PDF).
6. HiDPI / fractional scaling on Wayland and Windows (preview bitmaps use devicePixelRatio).
7. Large documents: responsiveness while planning edits (a PDFium-wide lock serialises rendering with planning).
8. Clipboard image paste; WebP on your Pillow build.
