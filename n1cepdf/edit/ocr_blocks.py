"""Reversible manual OCR segmentation using retained word geometry (no OCR rerun)."""
from dataclasses import replace
from ..core.ocr_layout import OcrWord, bounds, numeric_value
from .objects import TextBox, new_id
from .scan_rebuild import changed


def _source(words):
    l,b,r,t=bounds([OcrWord(text,rect) for text,rect in words])
    return (l-.5,b-.5,r+.5,t+.5)


def split_block(obj,position):
    if not isinstance(obj,TextBox) or obj.ocr_source is None or len(obj.ocr_words)<2:
        raise ValueError('This OCR block has no retained word geometry. Recognize it again before splitting.')
    if changed(obj):
        raise ValueError('Undo text/geometry changes before splitting the original OCR block.')
    original=' '.join(text for text,_ in obj.ocr_words)
    if original!=obj.text:
        raise ValueError('The OCR words no longer match the text. Undo its edits before splitting.')
    boundaries=[]
    offset=0
    for i,(text,_) in enumerate(obj.ocr_words[:-1],1):
        offset+=len(text)
        if position in (offset,offset+1): boundaries.append(i)
        offset+=1
    if not boundaries:
        raise ValueError('Place the text cursor between words to split the OCR block.')
    cut=boundaries[0]
    children=[]
    for index,words in enumerate((obj.ocr_words[:cut],obj.ocr_words[cut:])):
        source=_source(words)
        text=' '.join(word for word,_ in words)
        align='right' if numeric_value(text) or (index==1 and obj.align=='right') else 'left'
        l,b,r,t=source
        rect=(l+.5,obj.y0,r-.5 if align=='right' else r+.5,obj.y1)
        children.append(replace(obj,id=new_id(),x0=rect[0],y0=rect[1],x1=rect[2],y1=rect[3],
                                text=text,align=align,ocr_source=source,ocr_words=tuple(words),
                                ocr_regions=(source,),ocr_original=text,ocr_initial_rect=rect))
    return children


def merge_blocks(first,second):
    if (not all(isinstance(o,TextBox) and o.ocr_source is not None for o in (first,second)) or
            first.page_key!=second.page_key or first.id==second.id):
        raise ValueError('Choose two OCR blocks on the same page.')
    if changed(first) or changed(second):
        raise ValueError('Undo text/geometry changes before merging original OCR blocks.')
    a,b=sorted((first,second),key=lambda obj:obj.x0)
    overlap=min(a.ocr_source[3],b.ocr_source[3])-max(a.ocr_source[1],b.ocr_source[1])
    if overlap < min(a.ocr_source[3]-a.ocr_source[1],b.ocr_source[3]-b.ocr_source[1])*.4:
        raise ValueError('Only neighbouring OCR blocks on the same visual row can be merged.')
    if a.x1>b.x0:
        raise ValueError('Overlapping OCR blocks cannot be merged safely.')
    if not a.ocr_words or not b.ocr_words:
        raise ValueError('Both OCR blocks must retain their original word coordinates.')
    text=a.text+' '+b.text
    source=(min(a.ocr_source[0],b.ocr_source[0]),min(a.ocr_source[1],b.ocr_source[1]),
            max(a.ocr_source[2],b.ocr_source[2]),max(a.ocr_source[3],b.ocr_source[3]))
    rect=(a.x0,min(a.y0,b.y0),b.x1,max(a.y1,b.y1))
    # Keep the separate source masks. A manual merge must not erase the gutter.
    regions=(a.ocr_regions or (a.ocr_source,))+(b.ocr_regions or (b.ocr_source,))
    return replace(first,id=new_id(),x0=rect[0],y0=rect[1],x1=rect[2],y1=rect[3],
                   text=text,ocr_source=source,ocr_regions=regions,ocr_words=a.ocr_words+b.ocr_words,
                   ocr_original=text,ocr_initial_rect=rect,align='left')
