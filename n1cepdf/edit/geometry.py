"""PDF user space <-> displayed page pixels, for any /Rotate and CropBox origin.

PDF space: origin bottom-left of the unrotated page, y up, points. Display space: origin
top-left of the *displayed* (rotated) page, y down, pixels. Cross-checked against pdfium's own
FPDF_PageToDevice in the unit tests, for all four rotations.
"""
from __future__ import annotations
from dataclasses import dataclass

Rect = tuple[float, float, float, float]  # (left, bottom, right, top) in PDF space


@dataclass(frozen=True)
class PageTransform:
    crop: Rect            # CropBox (or MediaBox) in PDF space
    rotation: int         # /Rotate: 0, 90, 180 or 270 (clockwise)
    width_px: float       # displayed page size in pixels
    height_px: float

    @property
    def _w(self) -> float:
        return self.crop[2] - self.crop[0]

    @property
    def _h(self) -> float:
        return self.crop[3] - self.crop[1]

    @property
    def _scale(self) -> tuple[float, float]:
        r = self.rotation % 360
        dw, dh = (self._h, self._w) if r in (90, 270) else (self._w, self._h)
        return self.width_px / dw, self.height_px / dh

    def to_px(self, x: float, y: float) -> tuple[float, float]:
        u, v = x - self.crop[0], y - self.crop[1]
        w, h = self._w, self._h
        r = self.rotation % 360
        if r == 0:
            X, Y = u, h - v
        elif r == 90:
            X, Y = v, u
        elif r == 180:
            X, Y = w - u, v
        else:
            X, Y = h - v, w - u
        sx, sy = self._scale
        return X * sx, Y * sy

    def to_pdf(self, px: float, py: float) -> tuple[float, float]:
        sx, sy = self._scale
        X, Y = px / sx, py / sy
        w, h = self._w, self._h
        r = self.rotation % 360
        if r == 0:
            u, v = X, h - Y
        elif r == 90:
            u, v = Y, X
        elif r == 180:
            u, v = w - X, Y
        else:
            u, v = w - Y, h - X
        return u + self.crop[0], v + self.crop[1]

    def rect_to_px(self, rect: Rect) -> tuple[float, float, float, float]:
        """PDF rect -> (x, y, w, h) in display pixels (normalized, y down)."""
        x1, y1 = self.to_px(rect[0], rect[3])
        x2, y2 = self.to_px(rect[2], rect[1])
        return min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)

    def rect_to_pdf(self, x: float, y: float, w: float, h: float) -> Rect:
        a = self.to_pdf(x, y)
        b = self.to_pdf(x + w, y + h)
        return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])

    def delta_to_pdf(self, dx_px: float, dy_px: float) -> tuple[float, float]:
        """A displacement in display pixels expressed in PDF units."""
        ox, oy = self.to_pdf(0.0, 0.0)
        x, y = self.to_pdf(dx_px, dy_px)
        return x - ox, y - oy


def normalize_rect(r: Rect) -> Rect:
    return min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3])


def rects_intersect(a: Rect, b: Rect, tol: float = 0.0) -> bool:
    return not (a[2] <= b[0] + tol or b[2] <= a[0] + tol or a[3] <= b[1] + tol or b[3] <= a[1] + tol)


def point_in_rect(x: float, y: float, r: Rect, pad: float = 0.0) -> bool:
    return r[0] - pad <= x <= r[2] + pad and r[1] - pad <= y <= r[3] + pad
