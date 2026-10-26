"""The GWASHub logo as the site header draws it: PrimeIcons' chart-scatter icon in the HERMES navy, then "GWASHub" in
Inter, letters converted to outlines so the SVG needs no font. Writes <out>.svg, <out>-white.svg (for dark backgrounds)
and <out>-mark.svg (the icon alone, square); render PNGs with rsvg-convert (brew install librsvg):

    .venv/bin/python devtools/make_logo.py <Inter .otf/.ttf> web/node_modules/primeicons/raw-svg/chart-scatter.svg \
        web/public/brand/gwashub-logo.svg
    rsvg-convert -w 2400 web/public/brand/gwashub-logo.svg -o web/public/brand/gwashub-logo.png   (and -white)
    rsvg-convert -w 1024 web/public/brand/gwashub-logo-mark.svg -o web/public/brand/gwashub-logo-mark.png

The files in web/public/brand/ were made with Inter SemiBold (600; the header uses 700 from Google Fonts)."""
import re, sys
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen

FONT, ICON, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
NAVY, TEXT = "#303880", "#1b1f3a"
SIZE = 100.0            # font size in SVG units; the icon is 1em, as the header's <i>
GAP = 0.435 * SIZE      # 8px at the header's 1.15rem (18.4px)
PAD = 0.12 * SIZE

font = TTFont(FONT)
upm = font["head"].unitsPerEm
gs = font.getGlyphSet()
cmap = font.getBestCmap()
scale = SIZE / upm
asc, desc = font["hhea"].ascent, font["hhea"].descent  # desc negative

# letters, with kerning from GPOS ignored (Inter's defaults at this size are close to none for these pairs)
x = 0.0
letters = []
for ch in "GWASHub":
    g = cmap[ord(ch)]
    letters.append((g, x))
    x += gs[g].width
text_w = x * scale

def text_paths(x0, baseline, colour):
    pen = SVGPathPen(gs)
    for g, gx in letters:
        tp = TransformPen(pen, (scale, 0, 0, -scale, x0 + gx * scale, baseline))
        gs[g].draw(tp)
    return f'<path fill="{colour}" d="{pen.getCommands()}"/>'

# vertical: centre the icon on the text's x-height band like the header's flex align-items:center (line box)
line_h = (asc - desc) * scale
baseline = PAD + asc * scale
icon = re.search(r' d="([^"]+)"', open(ICON).read()).group(1)
icon_y = PAD + (line_h - SIZE) / 2
icon_svg = f'<g transform="translate({PAD:.2f} {icon_y:.2f}) scale({SIZE / 24:.4f})"><path fill-rule="evenodd" clip-rule="evenodd" fill="{NAVY}" d="{icon}"/></g>'
# crop to what is drawn (the icon's own drawing spans 3.75 to 20.25 of its 24 units), with an even margin
bp = BoundsPen(gs)
for g, gx in letters:
    gs[g].draw(TransformPen(bp, (scale, 0, 0, -scale, PAD + SIZE + GAP + gx * scale, baseline)))
tx0, ty0, tx1, ty1 = bp.bounds
ix0, iy0 = PAD + 3.75 * SIZE / 24, icon_y + 3.75 * SIZE / 24
ix1, iy1 = PAD + 20.25 * SIZE / 24, icon_y + 20.25 * SIZE / 24
x0, y0, x1, y1 = min(ix0, tx0), min(iy0, ty0), max(ix1, tx1), max(iy1, ty1)
M = 0.08 * SIZE
vx, vy, width, height = x0 - M, y0 - M, x1 - x0 + 2 * M, y1 - y0 + 2 * M
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx:.2f} {vy:.2f} {width:.2f} {height:.2f}" width="{width:.0f}" height="{height:.0f}">'
       f'<title>GWASHub</title>{icon_svg}{text_paths(PAD + SIZE + GAP, baseline, TEXT)}</svg>')
open(OUT, "w").write(svg)
# the mark alone, square
mark = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="3 3 18 18" width="512" height="512"><title>GWASHub</title>'
        f'<path fill-rule="evenodd" clip-rule="evenodd" fill="{NAVY}" d="{icon}"/></svg>')
open(OUT.replace(".svg", "-mark.svg"), "w").write(mark)
# light-on-dark version of the full logo
open(OUT.replace(".svg", "-white.svg"), "w").write(svg.replace(NAVY, "#ffffff").replace(TEXT, "#ffffff"))
print(f"{width:.0f} x {height:.0f}")
