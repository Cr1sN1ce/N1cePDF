"""Persist editable OCR pages alongside their interoperable PDF appearance.

Only pages containing OCR need this state. Original streams/resources and layer objects
allow reopening without baking a second text layer over the saved appearance.
"""
import base64
import json
from dataclasses import asdict
import pikepdf
from .objects import TextBox, ImageBox, Shape, Markup, Comment, Ink

_TYPES = {cls.__name__: cls for cls in (TextBox, ImageBox, Shape, Markup, Comment, Ink)}
_KEY = '/N1ceOCREditState'


def store_page(pdf, page, objects):
    records = []
    for obj in objects:
        record = asdict(obj)
        record.pop('page_key')
        if isinstance(obj, ImageBox):
            record['data'] = base64.b64encode(obj.data).decode('ascii')
        records.append({'type': type(obj).__name__, 'values': record})
    # Copy mutable containers; add_overlay adds resources and wraps content streams.
    resources = pikepdf.Dictionary(page.get('/Resources', pikepdf.Dictionary()))
    for key, value in list(resources.items()):
        if isinstance(value, pikepdf.Dictionary):
            resources[key] = pikepdf.Dictionary(value)
    contents = page.get('/Contents', pikepdf.Array())
    streams = pikepdf.Array(contents) if isinstance(contents, pikepdf.Array) else pikepdf.Array([contents])
    page.obj[_KEY] = pdf.make_indirect(pikepdf.Dictionary(
        Version=2, Objects=pdf.make_stream(json.dumps(records, ensure_ascii=False).encode('utf-8')),
        Contents=streams, Resources=resources,
        Annots=pikepdf.Array(page.get('/Annots', pikepdf.Array()))))


def restore_pages(pdf):
    restored = {}
    for index, page in enumerate(pdf.pages):
        state = page.get(_KEY)
        if state is None:
            continue
        if int(state.Version) not in (1,2):
            raise ValueError('Unsupported N1cePDF OCR edit state version')
        records = json.loads(state.Objects.read_bytes().decode('utf-8'))
        objects = []
        for record in records:
            cls = _TYPES[record['type']]
            values = record['values']
            if cls is ImageBox:
                values['data'] = base64.b64decode(values['data'], validate=True)
            # Restore immutable tuple fields for hashing and preview cache keys.
            def tuples(value):
                return tuple(tuples(v) for v in value) if isinstance(value, list) else value
            values = {key: tuples(value) for key, value in values.items()}
            objects.append(cls(page_key=tuple(page.obj.objgen), **values))
        if len({o.id for o in objects}) != len(objects):
            raise ValueError('Duplicate OCR object identifiers')
        restored[index] = objects
        page.obj.Contents = state.Contents
        page.obj.Resources = state.Resources
        page.obj.Annots = state.Annots
        del page.obj[_KEY]
    return restored
