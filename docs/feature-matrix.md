# Feature matrix

Legend: **Verified** = executed in the authoring sandbox. **Written, unverified** = code exists but
could not be executed there (no PySide6, no network). Run `pytest` on your machine to verify.

| Area | State |
|---|---|
| Open/validate/reject malformed PDFs, password open | Verified (core tests) |
| Render, bounded cache, stale-render discard, cancellation | Verified (core tests) |
| Closed-renderer guard for late worker threads | Verified (core test) |
| Text extraction, search with snippets, outline parsing | Verified (core tests) |
| English / Russian / Romanian extraction + rendering | Verified (core tests, font found portably) |
| Text-selection geometry (incl. rotated page), char hit-test | Verified (core tests) |
| Page ops, merge/split, atomic Save As, original untouched | Verified (core tests) |
| Recent-documents store, blank-document creation | Verified (core tests) |
| Welcome screen, drag & drop, recent list | Written, unverified |
| Menu bar (File/Edit/View/Document/Tools/Help), toolbar, status bar | Written, unverified |
| Sidebar: lazy thumbnails, bookmarks tree, search results | Written, unverified |
| Continuous view: zoom, fit width/page, Ctrl+wheel, page tracking | Written, unverified |
| Text selection, copy, select all, search highlighting | Written, unverified |
| Tabs: independent state, modified marker, close confirmation | Written, unverified |
| Rotate / insert / delete page, extract pages, merge PDFs, properties | Written, unverified |
| pytest-qt suite (40 GUI test cases incl. search-invalidation, stale-callback and close-confirmation regressions) | User-reported on Windows 11: the 2 earlier failures now pass; teardown ERROR fixed in the second bugfix (unverified until re-run); the rest not yet confirmed |
| Undo / Redo | Disabled in UI (Phase 2) |
| Encrypt/decrypt UI, redaction | Not started |
| Packaging, Windows | Not started / untested |

OCR, editable scan overlays and pending OCR search are implemented in v1.1.0; see README.md.
