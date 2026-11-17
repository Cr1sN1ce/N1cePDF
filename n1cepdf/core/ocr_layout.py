"""Geometry-based OCR segmentation. All thresholds scale with each row's glyph metrics."""
from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class OcrWord:
    text: str
    rect: tuple[float,float,float,float]
    confidence: float = 0.0


def bounds(words):
    return (min(w.rect[0] for w in words),min(w.rect[1] for w in words),
            max(w.rect[2] for w in words),max(w.rect[3] for w in words))


def numeric_value(text):
    chars=[c for c in text if not c.isspace()]
    return bool(chars) and any(c.isdigit() for c in chars) and sum(c.isalpha() for c in chars)<=len(chars)*.15


def metrics(words):
    substantial=[w for w in words if any(c.isalnum() for c in w.text)] or words
    height=median(max(.01,w.rect[3]-w.rect[1]) for w in substantial)
    char_width=median(max(.01,(w.rect[2]-w.rect[0])/max(1,len(w.text))) for w in substantial)
    gaps=sorted(max(0,b.rect[0]-a.rect[2]) for a,b in zip(words,words[1:]))
    lower=gaps[:max(1,(len(gaps)+1)//2)]
    ordinary=min(median(lower) if lower else char_width*.65,max(char_width*1.4,height*.4))
    ordinary=max(char_width*.3,ordinary)
    return height,char_width,ordinary


def segment_rows(rows):
    """Split abnormal gutters; corroborate borderline gaps with aligned neighbouring rows.

    Returns (word groups, alignment). Tesseract's block/paragraph/line IDs are hints;
    words retain their own geometry and are never separated by fabricated whitespace.
    """
    ordered=[sorted(row,key=lambda w:w.rect[0]) for row in rows if row]
    descriptions=[]
    for row in ordered:
        height,width,space=metrics(row)
        descriptions.append((row,height,width,space,bounds(row)))
    output=[]
    for index,(row,height,width,space,rect) in enumerate(descriptions):
        cuts=[]
        for n,(left,right) in enumerate(zip(row,row[1:]),1):
            gap=right.rect[0]-left.rect[2]
            strong=gap>max(space*4,width*4,height*1.6)
            supported=False
            if gap>max(space*2.2,width*2.2,height*.9):
                for j,(other,oh,ow,os,orect) in enumerate(descriptions):
                    if index==j or abs((rect[1]+rect[3]-orect[1]-orect[3])/2)>max(height,oh)*8:
                        continue
                    for a,b in zip(other,other[1:]):
                        other_gap=b.rect[0]-a.rect[2]
                        overlap=min(right.rect[0],b.rect[0])-max(left.rect[2],a.rect[2])
                        edge_aligned=abs(right.rect[0]-b.rect[0])<max(width,ow)*1.5
                        if (other_gap>max(os*2.2,ow*2.2,oh*.9) and
                            overlap>min(gap,other_gap)*.5 and edge_aligned):
                            supported=True
                            break
                    if supported: break
            # A sudden font-height change is evidence, but never sufficient without a gap.
            lh,rh=left.rect[3]-left.rect[1],right.rect[3]-right.rect[1]
            different_size=max(lh,rh)>max(.01,min(lh,rh))*1.7
            if strong or supported or (different_size and gap>max(space*3,width*3,height)):
                cuts.append(n)
        start=0
        for end in cuts+[len(row)]:
            words=row[start:end]
            output.append((words,'right' if numeric_value(' '.join(w.text for w in words)) else 'left'))
            start=end
    # Right-aligned text fields also occur without digits. Infer repeated right edges when
    # left edges vary; fixed left edges remain left-aligned (ordinary columns/paragraphs).
    for i,(words,alignment) in enumerate(output):
        if alignment=='right': continue
        rect=bounds(words);height,width,_=metrics(words)
        neighbours=[]
        for j,(other,_) in enumerate(output):
            if i==j: continue
            other_rect=bounds(other)
            dy=abs((rect[1]+rect[3]-other_rect[1]-other_rect[3])/2)
            if height*.5<dy<height*8 and abs(rect[2]-other_rect[2])<width:
                neighbours.append(other_rect)
        if neighbours and any(abs(r[0]-rect[0])>width*2 for r in neighbours):
            output[i]=(words,'right')
    return output
