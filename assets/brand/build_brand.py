"""Builds the DuckLess lockups and social image with the text converted to paths (no font needed to render).

    curl -L -o Bricolage.ttf "https://github.com/google/fonts/raw/main/ofl/bricolagegrotesque/BricolageGrotesque%5Bopsz%2Cwdth%2Cwght%5D.ttf"
    curl -L -o PlexSans.ttf "https://github.com/google/fonts/raw/main/ofl/ibmplexsans/IBMPlexSans%5Bwdth%2Cwght%5D.ttf"
    uv run --with fonttools assets/brand/build_brand.py assets/brand   # fonts next to this script

Both fonts are under the SIL Open Font License. The PNG of the social image is rendered from
duckless-social.svg (e.g. with sharp or rsvg-convert).
"""

import sys
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

HERE = Path(__file__).parent
OUT = Path(sys.argv[1])

AMBER, BEAK, INK, PAPER = "#F7B32B", "#E8590C", "#13233A", "#F2F4F8"
MARK = (
    f'<g fill="{AMBER}"><circle cx="28" cy="68" r="15"/><circle cx="48" cy="60" r="19"/>'
    f'<circle cx="70" cy="68" r="15"/><rect x="28" y="68" width="42" height="15"/>'
    f'<circle cx="67" cy="34" r="14"/><circle cx="64" cy="46" r="10"/></g>'
    f'<path d="M79 33 h7 a5 5 0 0 1 0 10 h-9 z" fill="{BEAK}"/>'
    f'<circle cx="70" cy="30" r="2.8" fill="{INK}"/>'
)


def instance(path: Path, **axes: float) -> TTFont:
    font = TTFont(path)
    return instantiateVariableFont(font, axes) if "fvar" in font else font


def text_path(
    font: TTFont, text: str, size: float, x: float, baseline: float, tracking: float = 0
) -> tuple[str, float]:
    """SVG path data of `text` (baseline at y), and its advance width."""
    glyph_set, cmap = font.getGlyphSet(), font.getBestCmap()
    scale = size / font["head"].unitsPerEm
    pen = SVGPathPen(glyph_set)
    cursor = x
    for char in text:
        name = cmap[ord(char)]
        glyph_set[name].draw(TransformPen(pen, (scale, 0, 0, -scale, cursor, baseline)))
        cursor += glyph_set[name].width * scale + tracking
    return pen.getCommands(), cursor - x - tracking


bold = instance(HERE / "Bricolage.ttf", wght=800, opsz=96, wdth=100)
regular = instance(HERE / "PlexSans.ttf", wght=400, wdth=100)


def lockup(duck_color: str) -> str:
    """Mark + wordmark, 1 line, viewBox fitted to the content."""
    mark_size, gap, size = 120, 18, 92
    baseline = 98
    duck, w_duck = text_path(bold, "Duck", size, mark_size + gap, baseline, tracking=-1.5)
    less, w_less = text_path(bold, "Less", size, mark_size + gap + w_duck - 1.5, baseline, tracking=-1.5)
    width = mark_size + gap + w_duck + w_less + 6
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} 128" role="img" aria-label="DuckLess">'
        f"<title>DuckLess</title>"
        f'<svg x="0" y="4" width="{mark_size}" height="{mark_size}" viewBox="0 0 100 100">{MARK}</svg>'
        f'<path d="{duck}" fill="{duck_color}"/><path d="{less}" fill="{AMBER}"/></svg>'
    )


def social() -> str:
    """1280x640 card for link previews (GitHub social preview, Open Graph)."""
    left = 520
    duck, w_duck = text_path(bold, "Duck", 116, left, 300, tracking=-2)
    less, _ = text_path(bold, "Less", 116, left + w_duck - 2, 300, tracking=-2)
    tag, _ = text_path(regular, "Serverless DuckDB on Google Cloud", 40, left + 4, 372)
    sub1, _ = text_path(regular, "Big VMs, only for the time of the job.", 27, left + 4, 432)
    sub2, _ = text_path(regular, "In your project, no HMAC keys.", 27, left + 4, 470)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="640" viewBox="0 0 1280 640">'
        f'<rect width="1280" height="640" fill="{INK}"/>'
        f'<svg x="110" y="140" width="360" height="360" viewBox="0 0 100 100">{MARK}</svg>'
        f'<path d="{duck}" fill="{PAPER}"/><path d="{less}" fill="{AMBER}"/>'
        f'<path d="{tag}" fill="#C9D2E0"/><path d="{sub1}" fill="#8A97AD"/><path d="{sub2}" fill="#8A97AD"/>'
        "</svg>"
    )


(OUT / "duckless-logo-light.svg").write_text(lockup(INK))  # for light backgrounds
(OUT / "duckless-logo-dark.svg").write_text(lockup(PAPER))  # for dark backgrounds
(OUT / "duckless-social.svg").write_text(social())
print("written to", OUT)
