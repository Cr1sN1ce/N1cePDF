import hashlib
import os
from pathlib import Path
import pikepdf
import pytest
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from n1cepdf.core.fonts import find_unicode_font

DEJAVU = find_unicode_font()  # portable lookup; None if no suitable font exists


def make_pdf(path: Path, texts: list[str], unicode_font: bool = False, outline: bool = False) -> Path:
    c = canvas.Canvas(str(path))
    font = "Helvetica"
    if unicode_font:
        pdfmetrics.registerFont(TTFont("UniTest", str(DEJAVU)))
        font = "UniTest"
    for n, t in enumerate(texts):
        if outline:
            c.bookmarkPage(f"p{n}")
            c.addOutlineEntry(f"Chapter {n + 1}", f"p{n}", level=0 if n % 2 == 0 else 1)
        c.setFont(font, 18)
        c.drawString(72, 700, t)
        c.showPage()
    c.save()
    return path


@pytest.fixture
def sample3(tmp_path):
    return make_pdf(tmp_path / "three.pdf", ["alpha page", "beta page", "gamma page"])


@pytest.fixture
def encrypted(tmp_path, sample3):
    out = tmp_path / "enc.pdf"
    with pikepdf.open(sample3) as pdf:
        pdf.save(out, encryption=pikepdf.Encryption(user="secret", owner="owner", R=6))
    return out


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()



def make_rich_pdf(path: Path) -> Path:
    """Text line, second line, an image, a vector rect and a form XObject on one page; plus a second page."""
    from PIL import Image
    import io as _io
    buf = _io.BytesIO()
    Image.new("RGB", (40, 30), (200, 30, 30)).save(buf, "PNG")
    from reportlab.lib.utils import ImageReader
    c = canvas.Canvas(str(path))
    c.setFont("Helvetica", 18)
    c.drawString(72, 700, "Hello, my name is John.")
    c.drawString(72, 650, "Second line stays.")
    c.drawImage(ImageReader(_io.BytesIO(buf.getvalue())), 72, 400, 120, 90)
    c.rect(300, 400, 100, 50)
    c.beginForm("f1")
    c.drawString(10, 10, "inside form")
    c.endForm()
    c.doForm("f1")
    c.showPage()
    c.setFont("Helvetica", 18)
    c.drawString(72, 700, "page two text")
    c.showPage()
    c.save()
    return path


@pytest.fixture
def rich(tmp_path):
    return make_rich_pdf(tmp_path / "rich.pdf")


def png_bytes(color=(0, 0, 255, 128), size=(30, 30)) -> bytes:
    from PIL import Image
    import io as _io
    b = _io.BytesIO()
    Image.new("RGBA", size, color).save(b, "PNG")
    return b.getvalue()
