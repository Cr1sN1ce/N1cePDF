# Architecture
`n1cepdf/core` is Qt-free (document model, renderer, page ops, atomic save, validation). `n1cepdf/ui` depends on core, never the reverse. pikepdf owns the editable structure; pdfium renders byte snapshots. Workers return plain data tagged with a generation id; only the GUI thread touches widgets.
