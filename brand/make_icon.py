"""Генерация app-иконки «Поток» строго по дизайн-файлу (Logo & Tray Icon).

Эквалайзер из 5 капсул (geometry из дизайна), масштаб 96/176 плитки,
градиент баров #4A6CFF→#7BA0FF по bbox (низ-лево → верх-право),
тёмный squircle с диагональным градиентом #23232A→#15151A.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

# --- параметры из дизайна ---
BARS = [  # (x, y, w, h) в viewBox 0..100, rx = w/2 (капсула)
    (9.5, 34, 13, 32),
    (26.5, 21, 13, 58),
    (43.5, 9, 13, 82),
    (60.5, 18, 13, 64),
    (77.5, 30, 13, 40),
]
TILE_TOP = (35, 35, 42)      # #23232A
TILE_BOT = (21, 21, 26)      # #15151A
BAR_BL = (74, 108, 255)      # #4A6CFF (низ-лево)
BAR_TR = (123, 160, 255)     # #7BA0FF (верх-право)
RADIUS_RATIO = 40 / 176      # squircle скругление
GLYPH_RATIO = 96 / 176       # эквалайзер занимает ~55% плитки
SS = 4                        # суперсэмплинг для гладких краёв


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _diag_gradient(size, c_start, c_bot_left, c_top_right, mode):
    """RGBA-градиент. mode='tile' (150deg), mode='bar' (BL→TR)."""
    h = w = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    xn, yn = xx / (w - 1), yy / (h - 1)
    if mode == "tile":
        # 150deg: проекция растёт вправо-вниз
        p = 0.5 * xn + 0.866 * yn
        p = (p - p.min()) / (p.max() - p.min())
        col = np.stack(
            [c_start[i] + (c_bot_left[i] - c_start[i]) * p for i in range(3)], -1
        )
    else:
        # bbox-градиент: низ-лево (#4A6CFF) → верх-право (#7BA0FF)
        p = (xn + (1 - yn)) / 2  # 0 в (0,1) низ-лево, 1 в (1,0) верх-право
        col = np.stack(
            [c_bot_left[i] + (c_top_right[i] - c_bot_left[i]) * p for i in range(3)], -1
        )
    rgba = np.dstack([col, np.full((h, w), 255.0)]).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def _capsule_mask(size):
    """Альфа-маска баров (белое — бар) в координатах плитки."""
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    glyph = size * GLYPH_RATIO
    off = (size - glyph) / 2
    s = glyph / 100.0
    for x, y, w, h in BARS:
        x0, y0 = off + x * s, off + y * s
        x1, y1 = x0 + w * s, y0 + h * s
        r = (w * s) / 2
        d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=255)
    return m


def render(size, with_tile=True):
    S = size * SS
    if with_tile:
        base = _diag_gradient(S, TILE_TOP, TILE_BOT, None, "tile")
        # скруглить углы плитки (вне squircle — прозрачно)
        corner = Image.new("L", (S, S), 0)
        ImageDraw.Draw(corner).rounded_rectangle(
            [0, 0, S - 1, S - 1], radius=int(S * RADIUS_RATIO), fill=255
        )
        base.putalpha(corner)
    else:
        base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bars = _diag_gradient(S, None, BAR_BL, BAR_TR, "bar")
    bars.putalpha(_capsule_mask(S))
    base = Image.alpha_composite(base, bars)
    return base.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    render(256).save(os.path.join(here, "icon-256.png"))
    render(256, with_tile=False).save(os.path.join(here, "icon-256-nobg.png"))
    ico_sizes = [16, 32, 48, 64, 128, 256]
    render(256).save(
        os.path.join(here, "icon.ico"),
        sizes=[(s, s) for s in ico_sizes],
    )
    print("icon-256.png, icon-256-nobg.png, icon.ico — обновлены")
