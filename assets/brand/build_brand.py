"""Builds the DuckLess brand files from the mark (duckless-mark.svg) with the wordmark outlined.

    curl -L -o Poppins-ExtraBold.ttf "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-ExtraBold.ttf"
    curl -L -o PlexSans.ttf "https://github.com/google/fonts/raw/main/ofl/ibmplexsans/IBMPlexSans%5Bwdth%2Cwght%5D.ttf"
    uv run --with fonttools build_brand.py <fonts-dir> <out-dir>

Fonts are under the SIL Open Font License; the outputs need no font to render.
"""

import re
import sys
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

FONTS, OUT = Path(sys.argv[1]), Path(sys.argv[2])
MARK_SVG = (OUT / "duckless-mark.svg").read_text()
MARK_DEFS = re.search(r"<defs>(.*?)</defs>", MARK_SVG, re.S).group(1)
MARK_BODY = re.sub(r"<defs>.*?</defs>", "", MARK_SVG.split(">", 1)[1].rsplit("</svg>", 1)[0], flags=re.S)
MARK_W, MARK_H = 944, 480

INK, PAPER, NIGHT = "#0B1530", "#F2F4F8", "#0B1530"
LESS_GRADIENT = (
    '<linearGradient id="less" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#0EA5FF"/><stop offset="1" stop-color="#4338F5"/></linearGradient>'
)


def font(path: Path, **axes: float) -> TTFont:
    f = TTFont(path)
    return instantiateVariableFont(f, axes) if "fvar" in f else f


def text_path(f: TTFont, text: str, size: float, x: float, baseline: float, tracking: float = 0) -> tuple[str, float]:
    glyphs, cmap = f.getGlyphSet(), f.getBestCmap()
    scale = size / f["head"].unitsPerEm
    pen, cursor = SVGPathPen(glyphs), x
    for char in text:
        name = cmap[ord(char)]
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, cursor, baseline)))
        cursor += glyphs[name].width * scale + tracking
    return pen.getCommands(), cursor - x - tracking


def mark(x: float, y: float, width: float, view: str = f"0 0 {MARK_W} {MARK_H}") -> str:
    vw, vh = (float(v) for v in view.split()[2:])
    return f'<svg x="{x}" y="{y}" width="{width}" height="{width * vh / vw:.1f}" viewBox="{view}">{MARK_BODY}</svg>'


bold = font(FONTS / "Poppins-ExtraBold.ttf")
regular = font(FONTS / "PlexSans.ttf", wght=400, wdth=100)


def wordmark(x: float, baseline: float, size: float, duck_color: str) -> tuple[str, float]:
    duck, w_duck = text_path(bold, "Duck", size, x, baseline, tracking=-size * 0.02)
    less, w_less = text_path(bold, "Less", size, x + w_duck - size * 0.02, baseline, tracking=-size * 0.02)
    return f'<path d="{duck}" fill="{duck_color}"/><path d="{less}" fill="url(#less)"/>', w_duck + w_less


def horizontal(duck_color: str) -> str:
    """Mark on the left, wordmark on the right: site header, wide spaces."""
    mark_w = 250
    words, width = wordmark(mark_w + 16, 92, 96, duck_color)
    total = mark_w + 16 + width + 6
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total:.0f} 128" role="img" aria-label="DuckLess">'
        f"<title>DuckLess</title><defs>{MARK_DEFS}{LESS_GRADIENT}</defs>"
        f"{mark(0, 0, mark_w)}{words}</svg>"
    )


def stacked(duck_color: str) -> str:
    """Mark above the wordmark, as in the original artwork: README hero."""
    size, mark_w = 150, 560
    _, words_w = wordmark(0, 0, size, duck_color)
    width = max(mark_w, words_w) + 24
    words, _ = wordmark((width - words_w) / 2, 470, size, duck_color)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} 500" role="img" aria-label="DuckLess">'
        f"<title>DuckLess</title><defs>{MARK_DEFS}{LESS_GRADIENT}</defs>"
        f"{mark((width - mark_w) / 2, 0, mark_w)}{words}</svg>"
    )


def icon() -> str:
    """The duck without its speed lines, square: favicon and avatars, where the lines would be noise."""
    body = re.sub(r'<g fill="url\(#speed\)">.*?</g>', "", MARK_BODY, flags=re.S)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="296 -76 652 652" role="img" aria-label="DuckLess">'
        f"<title>DuckLess</title><defs>{MARK_DEFS}</defs>{body}</svg>"
    )


def social() -> str:
    left = 600
    words, _ = wordmark(left, 300, 118, PAPER)
    tag, _ = text_path(regular, "Serverless DuckDB on Google Cloud", 38, left + 4, 370)
    sub1, _ = text_path(regular, "Big VMs, only for the time of the job.", 26, left + 4, 430)
    sub2, _ = text_path(regular, "In your project, no HMAC keys.", 26, left + 4, 467)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="640" viewBox="0 0 1280 640">'
        f"<defs>{MARK_DEFS}{LESS_GRADIENT}</defs>"
        f'<rect width="1280" height="640" fill="{NIGHT}"/>{mark(40, 205, 520)}{words}'
        f'<path d="{tag}" fill="#C9D2E0"/><path d="{sub1}" fill="#8A97AD"/><path d="{sub2}" fill="#8A97AD"/></svg>'
    )


(OUT / "duckless-logo-light.svg").write_text(horizontal(INK))
(OUT / "duckless-logo-dark.svg").write_text(horizontal(PAPER))
(OUT / "duckless-stacked-light.svg").write_text(stacked(INK))
(OUT / "duckless-stacked-dark.svg").write_text(stacked(PAPER))
(OUT / "duckless-icon.svg").write_text(icon())
(OUT / "duckless-social.svg").write_text(social())
print("written to", OUT)
