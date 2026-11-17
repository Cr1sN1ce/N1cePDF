"""Local visual font matching, using Unicode coverage and rendered glyph comparisons."""
from dataclasses import dataclass
import math
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfbase.ttfonts import TTFError
from .fonts import default_catalog, covers, register


@dataclass(frozen=True)
class FontMatch:
    family: str
    bold: bool
    italic: bool
    size: float
    color: tuple[float, float, float]
    char_spacing: float
    baseline: float  # pixels below sample top
    score: float
    left_bearing: float


def foreground(image):
    rgb = np.asarray(image.convert('RGB'))
    border = np.concatenate((rgb[0], rgb[-1], rgb[:,0], rgb[:,-1]))
    background = np.median(border, axis=0)
    distance = np.linalg.norm(rgb.astype(float)-background, axis=2)
    threshold = max(24, float(np.percentile(distance, 92))*0.35)
    mask = (distance > threshold).astype(np.uint8)
    return rgb, background, mask


def match_font(image, text, pixels_per_point, catalog=None, cancel=None):
    """Rank available faces by symmetric glyph distance, ink density and aspect ratio.

    This is a best-effort visual match, not font identification. Only faces covering the
    whole Unicode string participate. Font size, baseline, colour and spacing are estimated.
    """
    cat = catalog or default_catalog()
    rgb, background, mask = foreground(image)
    ys, xs = np.nonzero(mask)
    if len(xs) < 3 or not text.strip():
        return None
    l,t,r,b = int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)
    target = mask[t:b,l:r]
    h,w = target.shape
    normalized = cv2.resize(target, (min(512,max(w,8)), min(96,max(h,8))), interpolation=cv2.INTER_AREA)
    target_binary = (normalized > 0.4).astype(np.uint8)
    target_distance = cv2.distanceTransform(1-target_binary, cv2.DIST_L2, 3)
    best = None
    # First rank every discovered face by glyph aspect metrics. A bounded visual shortlist
    # avoids an alphabetic bias toward a few standard families on font-heavy systems.
    ranked = []
    for face in cat.faces:
        if cancel is not None and cancel.is_set():
            from ..core.ocr import OcrCancelled
            raise OcrCancelled('Font matching cancelled.')
        try:
            probe = ImageFont.truetype(str(face.path),64)
            bounds = probe.getbbox(text,anchor='ls')
            ratio = max(1,bounds[2]-bounds[0])/max(1,bounds[3]-bounds[1])
            ranked.append((abs(math.log(ratio/(w/h))),face))
        except (OSError,ValueError):
            continue
    faces = [face for _,face in sorted(ranked,key=lambda item:item[0])]
    for face in faces[:96]:
        if cancel is not None and cancel.is_set():
            from ..core.ocr import OcrCancelled
            raise OcrCancelled('Font matching cancelled.')
        if covers(face, text):
            continue
        try:
            register(face)
            probe = ImageFont.truetype(str(face.path), 64)
            bb = probe.getbbox(text, anchor='ls')
            size_px = 64*h/max(1, bb[3]-bb[1])
            font = ImageFont.truetype(str(face.path), max(1,round(size_px)))
            bb = font.getbbox(text, anchor='ls')
            cw,ch = max(1,bb[2]-bb[0]),max(1,bb[3]-bb[1])
            sample = Image.new('L',(cw,ch))
            ImageDraw.Draw(sample).text((-bb[0],-bb[1]),text,font=font,fill=255,anchor='ls')
            rendered = cv2.resize(np.asarray(sample), (normalized.shape[1],normalized.shape[0]), interpolation=cv2.INTER_AREA)
            candidate = (rendered>96).astype(np.uint8)
            reverse = cv2.distanceTransform(1-candidate,cv2.DIST_L2,3)
            distance = (target_distance[candidate>0].mean() + reverse[target_binary>0].mean()) / max(1, normalized.shape[0])
            density = abs(float(candidate.mean())-float(target_binary.mean()))
            aspect = abs(math.log(max(0.01,cw/w)))
            score = float(distance + density*0.4 + aspect*0.18)
            spacing_px = max(-size_px*0.12,min(size_px*0.18,(w-cw)/max(1,len(text)-1)))
            color = np.median(rgb[mask>0],axis=0)/255
            match = FontMatch(face.family,face.bold,face.italic,size_px/pixels_per_point,
                              tuple(float(c) for c in color),spacing_px/pixels_per_point,
                              t-bb[1],score,(bb[0]-l)/pixels_per_point)
            if best is None or score < best.score:
                best = match
        except (OSError,ValueError, TTFError):
            continue
    return best
