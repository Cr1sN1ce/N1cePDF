"""Reconstruct scan pixels and replace image XObjects; never silently flatten a page."""
from dataclasses import replace
import io
import math
import numpy as np
import cv2
import pikepdf
import pypdfium2 as pdfium
from PIL import Image
from ..core.pdfium_lock import PDFIUM_LOCK
from .geometry import PageTransform


class ReconstructionError(ValueError):
    pass


class LocalImageUnavailable(ReconstructionError):
    """The source image cannot be edited directly, but page rendering may be possible."""


def changed(obj):
    return (obj.ocr_source is not None and
            (obj.ocr_original != obj.text or obj.ocr_initial_rect != (obj.x0,obj.y0,obj.x1,obj.y1)))


def multiply(a,b):
    return (a[0]*b[0]+a[2]*b[1],a[1]*b[0]+a[3]*b[1],
            a[0]*b[2]+a[2]*b[3],a[1]*b[2]+a[3]*b[3],
            a[0]*b[4]+a[2]*b[5]+a[4],a[1]*b[4]+a[3]*b[5]+a[5])


def placements(page):
    """Conservative direct image placements. Forms, clipping, masks and affine skew fall back."""
    matrix = (1,0,0,1,0,0)
    stack = []
    clipped = False
    result = []
    xobjects = page.get('/Resources',{}).get('/XObject',{})
    for operands,operator in pikepdf.parse_content_stream(page):
        op = str(operator)
        if op == 'q': stack.append((matrix,clipped))
        elif op == 'Q':
            if stack: matrix,clipped = stack.pop()
        elif op == 'cm': matrix = multiply(matrix,tuple(float(v) for v in operands))
        elif op in ('W','W*'): clipped = True
        elif op == 'Do':
            name = str(operands[0])
            stream = xobjects.get(name)
            if stream is not None and stream.get('/Subtype') == '/Image':
                result.append((name,stream,matrix,clipped))
    return result


def contains(outer, inner):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def object_region(obj):
    from .preview import display_bounds
    return display_bounds(obj,0)


def _clean(image, rect, mode):
    """Remove local foreground only; complex backgrounds require explicit inpaint consent."""
    x0,y0,x1,y1 = rect
    pad = 4
    left,top,right,bottom = max(0,x0-pad),max(0,y0-pad),min(image.width,x1+pad),min(image.height,y1+pad)
    crop = np.array(image.crop((left,top,right,bottom)).convert('RGB'))
    if crop.size == 0:
        raise ReconstructionError('OCR region is outside its source image.')
    edge = np.concatenate((crop[0],crop[-1],crop[:,0],crop[:,-1]))
    background = np.median(edge,axis=0)
    variability = np.percentile(np.linalg.norm(edge-background,axis=1),90)
    distance = np.linalg.norm(crop.astype(float)-background,axis=2)
    threshold = max(24,float(variability)*2) if variability > 20 else 12
    mask = (distance > threshold).astype(np.uint8)*255
    source = np.zeros(mask.shape,dtype=np.uint8)
    source[max(0,y0-top):y1-top,max(0,x0-left):x1-left] = 255
    mask = cv2.bitwise_and(cv2.dilate(mask,np.ones((3,3),np.uint8)),source)
    if variability > 20:
        if mode != 'inpaint':
            raise ReconstructionError('Complex scan background: choose local inpainting in Properties, review the preview and accept possible artifacts.')
        if float((mask>0).mean()) > 0.65 or (x1-x0)*(y1-y0) > image.width*image.height*0.3:
            raise ReconstructionError('Inpainting refused: region is too large or contains too much detail. Reduce the OCR source region or use an external image editor.')
        cleaned = cv2.inpaint(crop,mask,3,cv2.INPAINT_TELEA)
    else:
        cleaned = crop.copy()
        cleaned[mask>0] = background.astype(np.uint8)
    patch = Image.fromarray(cleaned)
    if image.mode in ('L','1','CMYK'):
        patch = patch.convert(image.mode)
    # Preserve source samples outside the foreground mask, including CMYK values.
    image.paste(patch,(left,top),Image.fromarray(mask))


def _render_text(rect, objects, scale, catalog):
    from .writer import build_overlay_pdf
    # Clear source fields: only replacement glyphs enter this temporary PDF.
    boxes = [replace(o,ocr_source=None,ocr_original=None,ocr_initial_rect=None) for o in objects]
    data,warnings = build_overlay_pdf(rect,boxes,catalog)
    with pikepdf.open(io.BytesIO(data)) as pdf:
        page = pdf.pages[0]
        page.obj.MediaBox = list(rect)
        page.obj.CropBox = list(rect)
        out = io.BytesIO()
        pdf.save(out)
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(out.getvalue())
        try:
            page = doc[0]
            try:
                bitmap = page.render(scale=scale,fill_color=(255,255,255,0))
                try: image = bitmap.to_pil().convert('RGBA').copy()
                finally: bitmap.close()
            finally: page.close()
        finally: doc.close()
    return image,warnings


def reconstruct(image, image_rect, objects, catalog):
    """Keep original dimensions and all pixels outside source/glyph regions unchanged."""
    original_mode = image.mode
    # Bilevel originals retain dimensions but promote to grayscale for antialiased new glyphs.
    needs_color = original_mode in ('1','L') and any(o.text and max(o.color)-min(o.color)>.01 for o in objects)
    target_mode = 'RGB' if needs_color else 'L' if original_mode == '1' else original_mode
    result = image.convert(target_mode).copy()
    tr = PageTransform(image_rect,0,result.width,result.height)
    for obj in objects:
        for source in obj.ocr_regions or (obj.ocr_source,):
            x,y,w,h = tr.rect_to_px(source)
            _clean(result,(math.floor(x),math.floor(y),math.ceil(x+w),math.ceil(y+h)),obj.ocr_background)
    warnings = ['Grayscale/bilevel scan promoted to RGB to preserve the requested text colour.'] if needs_color else []
    for obj in objects:
        if not obj.text:
            continue
        region = object_region(replace(obj,ocr_source=None))
        x,y,w,h = tr.rect_to_px(region)
        # Align the crop to source pixels so preview and export use identical glyph phase.
        ix,iy = math.floor(x),math.floor(y)
        iw,ih = max(1,math.ceil(x+w)-ix),max(1,math.ceil(y+h)-iy)
        region = tr.rect_to_pdf(ix,iy,iw,ih)
        glyphs,notes = _render_text(region,[obj],max(tr._scale),catalog)
        if glyphs.size != (iw,ih):
            glyphs = glyphs.resize((iw,ih),Image.Resampling.LANCZOS)
        result.paste(glyphs.convert(result.mode),(ix,iy),glyphs.getchannel('A'))
        warnings += notes
    return result,warnings


def image_stream(pdf, image):
    if image.mode not in ('L','RGB','CMYK'):
        image = image.convert('RGB')
    stream = pdf.make_stream(image.tobytes())
    stream.Type = pikepdf.Name.XObject
    stream.Subtype = pikepdf.Name.Image
    stream.Width,stream.Height = image.size
    stream.BitsPerComponent = 8
    stream.ColorSpace = pikepdf.Name({'L':'/DeviceGray','RGB':'/DeviceRGB','CMYK':'/DeviceCMYK'}[image.mode])
    return stream


def rebuild_page(pdf,index,objects,catalog,allow_page_raster=None):
    """Use one explicit page-wide consent value, shared by preview and export.

    The legacy per-object flag remains readable. One grant on this page authorizes the page
    fallback; flags on other pages never do. Callers may explicitly revoke with False.
    """
    allowed=(any(obj.ocr_allow_page_raster for obj in objects)
             if allow_page_raster is None else allow_page_raster)
    try:
        return _replace_images(pdf,index,objects,catalog)
    except LocalImageUnavailable as exc:
        if not allowed:
            raise ReconstructionError(f"Page {index+1}: Local image replacement is unavailable: {exc}. "
                                      "Enable affected-page rasterization in Properties after reviewing its loss of vector content.") from exc
        try:
            return rasterize_page(pdf,index,objects,catalog)
        except (ValueError,pikepdf.PdfError,pdfium.PdfiumError,OSError,RuntimeError) as fallback_error:
            raise ReconstructionError(f"Page {index+1}: affected-page rasterization failed: "
                                      f"{type(fallback_error).__name__}: {fallback_error}") from fallback_error
    except ReconstructionError as exc:
        raise ReconstructionError(f"Page {index+1}: {exc}") from exc


def _replace_images(pdf,index,objects,catalog):
    page = pdf.pages[index]
    try:
        items = placements(page)
    except pikepdf.PdfError as exc:
        raise LocalImageUnavailable(f"cannot inspect image placements ({type(exc).__name__}: {exc})") from exc
    groups = {}
    for obj in objects:
        eligible = []
        for name,stream,matrix,clipped in items:
            a,b,c,d,e,f = matrix
            if (clipped or abs(b)>1e-6 or abs(c)>1e-6 or a<=0 or d<=0 or
                stream.get('/SMask') is not None or stream.get('/Mask') is not None or stream.get('/ImageMask',False)):
                continue
            region = (e,f,e+a,f+d)
            if contains(region,object_region(obj)) and sum(n==name for n,*_ in items)==1:
                eligible.append((name,stream,region))
        if len(eligible) != 1:
            reason=("no direct image (the scan may be inside a Form XObject)" if not items else
                    "clipping, masks, transformed images or OCR bounds outside the source image" if not eligible else
                    "multiple overlapping source images")
            raise LocalImageUnavailable(f"OCR block {obj.id}: {reason}")
        name,stream,region = eligible[0]
        groups.setdefault(name,(stream,region,[]))[2].append(obj)
    resources = pikepdf.Dictionary(page.get('/Resources',pikepdf.Dictionary()))
    xobjects = pikepdf.Dictionary(resources.get('/XObject',pikepdf.Dictionary()))
    warnings = []
    for name,(stream,region,edits) in groups.items():
        if int(stream.Width)*int(stream.Height) > 100_000_000:
            raise ReconstructionError('Source scan exceeds the 100 megapixel safety limit; it was not downsampled.')
        try:
            source = pikepdf.PdfImage(stream).as_pil_image()
        except (pikepdf.PdfError,NotImplementedError,OSError) as exc:
            raise LocalImageUnavailable(f"cannot decode source image {name}: {type(exc).__name__}: {exc}") from exc
        rebuilt,notes = reconstruct(source,region,edits,catalog)
        replacement = image_stream(pdf,rebuilt)
        original_space = stream.get('/ColorSpace')
        if source.mode == rebuilt.mode and source.mode in ('RGB','L','CMYK') and isinstance(original_space,pikepdf.Array) and str(original_space[0]) == '/ICCBased':
            replacement.ColorSpace = original_space
        xobjects[name] = replacement
        warnings += notes
        filters = stream.get('/Filter')
        if '/DCTDecode' in ([str(f) for f in filters] if isinstance(filters,pikepdf.Array) else [str(filters)]):
            warnings.append('Edited JPEG scan decoded once and saved with lossless compression; unchanged JPEGs are untouched.')
        if source.mode == '1':
            warnings.append('Edited bilevel scan promoted at its original pixel dimensions for antialiased/coloured text.')
    resources.XObject = xobjects
    page.obj.Resources = resources
    return warnings


def rasterize_page(pdf,index,objects,catalog):
    page = pdf.pages[index]
    media = tuple(float(v) for v in page.mediabox)
    crop = tuple(float(v) for v in (page.get('/CropBox') or page.mediabox))
    # Preserve at least 300 DPI and never lower the sampling density of direct source scans.
    scale = 300/72
    for _,stream,m,_ in placements(page):
        if m[0] and m[3]:
            scale = max(scale,int(stream.Width)/abs(m[0]),int(stream.Height)/abs(m[3]))
    width,height = media[2]-media[0],media[3]-media[1]
    if width*height*scale*scale > 100_000_000:
        raise ReconstructionError('Page rasterization exceeds the 100 megapixel safety limit; resolution was not reduced. Use local image editing.')
    # Render complete MediaBox, retaining CropBox/Rotate on the output page.
    saved_crop = page.obj.get('/CropBox')
    saved_rotation = page.obj.get('/Rotate')
    page.obj.CropBox = list(media)
    page.obj.Rotate = 0
    out = io.BytesIO()
    try:
        pdf.save(out)
    finally:
        if saved_rotation is None: del page.obj['/Rotate']
        else: page.obj.Rotate = saved_rotation
        if saved_crop is None: del page.obj['/CropBox']
        else: page.obj.CropBox = saved_crop
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(out.getvalue())
        try:
            pg = doc[index]
            try:
                image_objects = list(pg.get_objects(max_depth=32))
                try:
                    for image_object in image_objects:
                        if isinstance(image_object,pdfium.PdfImage):
                            metadata = image_object.get_metadata()
                            scale = max(scale,metadata.horizontal_dpi/72,metadata.vertical_dpi/72)
                finally:
                    for image_object in image_objects:
                        image_object.close()
                if width*height*scale*scale > 100_000_000:
                    raise ReconstructionError('Source image density exceeds the page rasterization safety limit; resolution was not reduced.')
                bm = pg.render(scale=scale,draw_annots=False)
                try: source = bm.to_pil().convert('RGB').copy()
                finally: bm.close()
            finally: pg.close()
        finally: doc.close()
    rebuilt,notes = reconstruct(source,media,objects,catalog)
    stream = image_stream(pdf,rebuilt)
    page.obj.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(N1ceScan=stream))
    page.obj.Contents = pdf.make_stream(f'q {width} 0 0 {height} {media[0]} {media[1]} cm /N1ceScan Do Q'.encode())
    return notes+['Affected page rasterized with explicit permission: vector content on this page is now raster; other pages are unchanged.']
