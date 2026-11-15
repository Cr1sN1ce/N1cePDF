"""Regenerate assets/n1cepdf.ico and assets/n1cepdf.png.

The glyph is the application's existing "logo" icon (n1cepdf/ui/icons.py, 24x24 stroke paths) drawn in
white on the accent colour used by the theme (n1cepdf/ui/theme.py). Only Pillow is required.
Run from the project root:  python scripts/make_icons.py
"""
from pathlib import Path
from PIL import Image, ImageDraw

ACCENT = (0x3B, 0x6C, 0xFF, 255)          # theme.ACCENT
WHITE = (255, 255, 255, 255)
SIZES = [16, 24, 32, 48, 64, 128, 256]
BASE = 1024                               # supersampled canvas

# Same geometry as _PATHS["logo"] in n1cepdf/ui/icons.py (viewBox 0 0 24 24, stroke width 2).
STROKES = [
    [(6, 2), (15, 2), (20, 7), (20, 22), (6, 22), (6, 2)],   # page outline with cut corner
    [(15, 2), (15, 7), (20, 7)],                              # folded corner
    [(9, 13), (17, 13)],
    [(9, 17), (17, 17)],
    [(9, 9), (12, 9)],
]


def render() -> Image.Image:
    img = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, BASE - 1, BASE - 1), radius=int(BASE * 0.22), fill=ACCENT)
    glyph = BASE * 0.62                    # glyph box inside the tile
    unit = glyph / 24.0
    off = (BASE - glyph) / 2.0
    width = max(1, round(2.4 * unit))      # slightly heavier than the 2px UI icon so it reads at 16 px

    def pt(p):
        return (off + p[0] * unit, off + p[1] * unit)

    for stroke in STROKES:
        pts = [pt(p) for p in stroke]
        d.line(pts, fill=WHITE, width=width, joint="curve")
        r = width / 2.0
        for x, y in pts:                   # round caps / joins
            d.ellipse((x - r, y - r, x + r, y + r), fill=WHITE)
    return img


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "assets"
    out.mkdir(exist_ok=True)
    base = render()
    frames = {s: base.resize((s, s), Image.Resampling.LANCZOS) for s in SIZES}
    frames[256].save(out / "n1cepdf.png")
    frames[256].save(out / "n1cepdf.ico", format="ICO", sizes=[(s, s) for s in SIZES])
    print("wrote", out / "n1cepdf.ico", "and", out / "n1cepdf.png")


if __name__ == "__main__":
    main()
