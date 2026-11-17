"""Font catalog for added/replacement text: discovery, Unicode-coverage checks, fallback chain.

Only TrueType fonts are used (ReportLab embeds a subset with a ToUnicode map, so the saved text
stays searchable and copyable). Fonts whose licence flags forbid embedding are rejected by ReportLab
and skipped.
"""
from __future__ import annotations
import hashlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont

PREFERRED = ["DejaVu Sans", "Liberation Sans", "Noto Sans", "Arial", "Segoe UI"]
_FALLBACK_FAMILIES = ["DejaVu Sans", "Liberation Sans", "Noto Sans", "Arial", "Segoe UI", "Verdana", "Tahoma"]

# filename -> (family, bold, italic); used on Windows and as a no-fontconfig fallback
_KNOWN = {
    "DejaVuSans.ttf": ("DejaVu Sans", False, False), "DejaVuSans-Bold.ttf": ("DejaVu Sans", True, False),
    "DejaVuSans-Oblique.ttf": ("DejaVu Sans", False, True), "DejaVuSans-BoldOblique.ttf": ("DejaVu Sans", True, True),
    "DejaVuSerif.ttf": ("DejaVu Serif", False, False), "DejaVuSerif-Bold.ttf": ("DejaVu Serif", True, False),
    "DejaVuSansMono.ttf": ("DejaVu Sans Mono", False, False), "DejaVuSansMono-Bold.ttf": ("DejaVu Sans Mono", True, False),
    "LiberationSans-Regular.ttf": ("Liberation Sans", False, False), "LiberationSans-Bold.ttf": ("Liberation Sans", True, False),
    "LiberationSans-Italic.ttf": ("Liberation Sans", False, True), "LiberationSans-BoldItalic.ttf": ("Liberation Sans", True, True),
    "LiberationSerif-Regular.ttf": ("Liberation Serif", False, False), "LiberationSerif-Bold.ttf": ("Liberation Serif", True, False),
    "LiberationSerif-Italic.ttf": ("Liberation Serif", False, True), "LiberationSerif-BoldItalic.ttf": ("Liberation Serif", True, True),
    "LiberationMono-Regular.ttf": ("Liberation Mono", False, False), "LiberationMono-Bold.ttf": ("Liberation Mono", True, False),
    "NotoSans-Regular.ttf": ("Noto Sans", False, False), "NotoSans-Bold.ttf": ("Noto Sans", True, False),
    "NotoSans-Italic.ttf": ("Noto Sans", False, True), "NotoSans-BoldItalic.ttf": ("Noto Sans", True, True),
    "NotoSerif-Regular.ttf": ("Noto Serif", False, False), "NotoSerif-Bold.ttf": ("Noto Serif", True, False),
    "arial.ttf": ("Arial", False, False), "arialbd.ttf": ("Arial", True, False),
    "ariali.ttf": ("Arial", False, True), "arialbi.ttf": ("Arial", True, True),
    "times.ttf": ("Times New Roman", False, False), "timesbd.ttf": ("Times New Roman", True, False),
    "timesi.ttf": ("Times New Roman", False, True), "timesbi.ttf": ("Times New Roman", True, True),
    "cour.ttf": ("Courier New", False, False), "courbd.ttf": ("Courier New", True, False),
    "segoeui.ttf": ("Segoe UI", False, False), "segoeuib.ttf": ("Segoe UI", True, False),
    "segoeuii.ttf": ("Segoe UI", False, True), "segoeuiz.ttf": ("Segoe UI", True, True),
    "verdana.ttf": ("Verdana", False, False), "verdanab.ttf": ("Verdana", True, False),
    "tahoma.ttf": ("Tahoma", False, False), "tahomabd.ttf": ("Tahoma", True, False),
    "calibri.ttf": ("Calibri", False, False), "calibrib.ttf": ("Calibri", True, False),
}


@dataclass(frozen=True)
class FontFace:
    family: str
    bold: bool
    italic: bool
    path: Path

    @property
    def rl_name(self) -> str:
        return "n1c_" + hashlib.sha1(str(self.path).encode()).hexdigest()[:10]


_ttf_cache: dict[str, TTFont | None] = {}


def _ttf(path: Path) -> TTFont | None:
    key = str(path)
    if key not in _ttf_cache:
        try:
            _ttf_cache[key] = TTFont("probe_" + hashlib.sha1(key.encode()).hexdigest()[:8], key)
        except (TTFError, OSError, ValueError, KeyError):
            _ttf_cache[key] = None
    return _ttf_cache[key]


def covers(face: FontFace, text: str) -> list[str]:
    """Characters of text that the face has no glyph for (whitespace ignored)."""
    f = _ttf(face.path)
    if f is None:
        return sorted(set(text))
    cmap = f.face.charToGlyph
    return sorted({ch for ch in text if not ch.isspace() and ord(ch) not in cmap})


def metrics(face: FontFace) -> tuple[float, float]:
    """(ascent, descent) as fractions of the font size (descent negative)."""
    f = _ttf(face.path)
    if f is None:
        return 0.93, -0.24
    return f.face.ascent / 1000.0, f.face.descent / 1000.0


def register(face: FontFace) -> str:
    """Register with ReportLab (idempotent); returns its font name. Raises TTFError if not embeddable."""
    if face.rl_name not in pdfmetrics.getRegisteredFontNames():
        if not embedding_allowed(face.path.read_bytes()):
            raise TTFError("Font embedding/subsetting is restricted by OS/2 fsType")
        pdfmetrics.registerFont(TTFont(face.rl_name, str(face.path)))
    return face.rl_name


def _font_dirs() -> list[Path]:
    if sys.platform.startswith("win"):
        dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
        if os.environ.get("LOCALAPPDATA"):
            dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Windows/Fonts")
    elif sys.platform == "darwin":
        dirs = [Path("/Library/Fonts"), Path("/System/Library/Fonts"), Path.home() / "Library/Fonts"]
    else:
        dirs = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
                Path.home() / ".local/share/fonts", Path.home() / ".fonts"]
    return [d for d in dirs if d.is_dir()]


def _scan() -> list[FontFace]:
    faces: dict[Path, FontFace] = {}
    fc = shutil.which("fc-list")
    if fc and not sys.platform.startswith("win"):
        try:  # argument array, no shell
            out = subprocess.run([fc, "--format", "%{family[0]}|%{style[0]}|%{file}\n", ":fontformat=TrueType"],
                                 capture_output=True, text=True, timeout=10, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        for line in out.splitlines():
            parts = line.split("|", 2)
            if len(parts) != 3 or not parts[2].lower().endswith(".ttf"):
                continue
            fam, style, file = parts
            st = style.lower()
            p = Path(file)
            if p.is_file():
                faces[p] = FontFace(fam, "bold" in st, "italic" in st or "oblique" in st, p)
    if not faces:
        from PIL import ImageFont
        known = {name.lower():value for name,value in _KNOWN.items()}
        for d in _font_dirs():
            for hit in d.rglob("*.ttf"):
                info = known.get(hit.name.lower())
                if info is None:
                    try:
                        family,style = ImageFont.truetype(str(hit),12).getname()
                        info = (family,"bold" in style.lower(),any(v in style.lower() for v in ("italic","oblique")))
                    except (OSError,ValueError):
                        continue
                faces[hit] = FontFace(*info,hit)
    return list(faces.values())


class FontCatalog:
    def __init__(self, faces: list[FontFace] | None = None):
        self.faces = _scan() if faces is None else faces

    def families(self) -> list[str]:
        return sorted({f.family for f in self.faces})

    def find(self, family: str, bold: bool, italic: bool) -> FontFace | None:
        fam = [f for f in self.faces if f.family.lower() == family.lower()]
        for want in ((bold, italic), (bold, False), (False, italic), (False, False)):
            for f in fam:
                if (f.bold, f.italic) == want:
                    return f
        return fam[0] if fam else None

    def resolve(self, family: str, bold: bool, italic: bool, text: str) -> tuple[FontFace | None, list[str], str]:
        """Pick a face that can render text. Returns (face, missing_chars, note). The requested family
        is tried first; otherwise a broad-coverage fallback is substituted and the note says so."""
        tried: list[str] = []
        for fam in [family] + [f for f in _FALLBACK_FAMILIES if f.lower() != family.lower()]:
            face = self.find(fam, bold, italic)
            if face is None or face.family in tried:
                continue
            tried.append(face.family)
            missing = covers(face, text)
            if not missing:
                try:
                    register(face)
                except (TTFError, OSError):
                    continue  # embedding not permitted by this font
                note = "" if face.family.lower() == family.lower() else \
                    f"Font '{family}' cannot render this text or is unavailable; substituted '{face.family}'."
                return face, [], note
        # nothing covers everything: best effort with the first registrable face
        for fam in [family] + _FALLBACK_FAMILIES:
            face = self.find(fam, bold, italic)
            if face is not None:
                try:
                    register(face)
                except (TTFError, OSError):
                    continue
                return face, covers(face, text), f"No installed font covers all characters; using '{face.family}'."
        return None, sorted(set(text)), "No usable TrueType font found."


_default: FontCatalog | None = None


def default_catalog() -> FontCatalog:
    global _default
    if _default is None:
        _default = FontCatalog()
    return _default


def guess_family(pdf_font_name: str) -> tuple[str, bool, bool]:
    """Map a PDF BaseFont name (e.g. 'ABCDEF+TimesNewRomanPS-BoldMT') to (family, bold, italic)."""
    name = pdf_font_name.split("+", 1)[-1]
    import re
    normalized = lambda value: re.sub(r"[^a-z0-9]","",value.lower())
    # Prefer the actual installed/embedded family over a generic sans/serif alias.
    catalog = default_catalog()
    for family in sorted(catalog.families(),key=len,reverse=True):
        if normalized(pdf_font_name) == normalized(family) or normalized(name).startswith(normalized(family)):
            low = name.lower()
            return family, any(v in low for v in ('bold','black','heavy')), any(v in low for v in ('italic','oblique'))
    low = name.lower()
    bold = "bold" in low or "black" in low or "heavy" in low
    italic = "italic" in low or "oblique" in low
    if any(k in low for k in ("courier", "mono", "consolas")):
        fam = "Liberation Mono"
    elif any(k in low for k in ("times", "serif", "georgia", "garamond", "palatino", "book", "cambria")) \
            and "sans" not in low:
        fam = "Liberation Serif"
    else:
        fam = "Liberation Sans"
    return fam, bold, italic


def embedding_allowed(data: bytes) -> bool:
    """Respect OS/2 fsType restrictions before attempting embedded TTF reuse."""
    import struct
    try:
        count = struct.unpack_from('>H',data,4)[0]
        for i in range(count):
            tag,_,offset,length = struct.unpack_from('>4sIII',data,12+i*16)
            if tag == b'OS/2':
                flags = struct.unpack_from('>H',data,offset+8)[0]
                return not bool(flags & (0x0002|0x0100|0x0200))
        return False  # no explicit permission information: conservative fallback
    except (struct.error,ValueError):
        return False


def import_embedded_fonts(catalog, pdf):
    """Load usable embedded TrueType fonts; reject restricted fonts and broken subsets."""
    import tempfile
    import pikepdf
    if not hasattr(catalog,'_embedded_directory'):
        catalog._embedded_directory = tempfile.TemporaryDirectory(prefix='n1cepdf-fonts-')
    for page in pdf.pages:
        for _,font in page.get('/Resources',{}).get('/Font',{}).items():
            candidates = list(font.get('/DescendantFonts',[])) or [font]
            for candidate in candidates:
                descriptor = candidate.get('/FontDescriptor')
                stream = descriptor.get('/FontFile2') if descriptor is not None else None
                if stream is None:
                    continue
                try:
                    data = stream.read_bytes()
                except (pikepdf.PdfError,ValueError,TypeError):
                    continue
                if not embedding_allowed(data):
                    continue
                digest = hashlib.sha256(data).hexdigest()
                path = Path(catalog._embedded_directory.name)/(digest+'.ttf')
                if not path.exists(): path.write_bytes(data)
                family = str(font.get('/BaseFont','Embedded')).lstrip('/')
                if any(face.family == family for face in catalog.faces):
                    continue
                probe = _ttf(path)
                if probe is not None:
                    _,bold,italic = guess_family(family)
                    face = FontFace(family,bold,italic,path)
                    try: register(face)
                    except (TTFError,OSError): continue
                    catalog.faces.append(face)
