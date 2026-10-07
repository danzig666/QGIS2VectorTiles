"""
Patch the vendored MapLibre GL JS (resources/ml_viewer/maplibre-gl.mjs) so
line patterns (``line-pattern``) and dash arrays keep their screen size at
every zoom, as QGIS draws them.

Stock MapLibre scales them with the tile's integer zoom (``transform.tileZoom``):
exact at whole zooms, growing up to 2x until the next one (measured: a 23 px
raster line repeat drawn 27 / 32 / 37 px at x.25 / x.5 / x.75), and cross-fades
a double-size copy after every whole zoom. The patch:

1. measures the line tile ratio at the real zoom (``transform.zoom``);
2. gives both cross-fade sides of the line programs the same scale;
3. sizes line patterns and dashes by the line's width at the current zoom
   (``width``), not at the whole zoom (``floorwidth``, which stock MapLibre
   multiplies back up through the tile scale): with step 1 that keeps
   screen-size lines constant and map-size lines growing with the map, both
   exact at every zoom;
4. samples a line pattern only inside its image: stock MapLibre maps the line
   onto the image plus one padding texel on each side, and pads patterns with
   the *opposite* edge (for tiling), so the outer sliver of the stroke showed
   the far edge's colour (faint dots along a lineburst's light edge) and each
   repeat was squeezed by two texels. Across the line it now runs from the
   first row's centre to the last row's; along it exactly over the image.

Fill patterns are not changed. Idempotent; run after updating MapLibre:

    python3 tools/patch_maplibre.py [--check]
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE = os.path.join(ROOT, "resources", "ml_viewer", "maplibre-gl.mjs")
MARK = "/*q2vt-screen-line-patterns*/"

# function Vu(e,t){return 1/ce(e,1,t.tileZoom)}  (minified names vary)
RATIO = re.compile(r"function (\w+)\((\w+),(\w+)\)\{return 1/(\w+)\(\2,1,\3\.tileZoom\)\}")


SAMPLING_MARK = "/*q2vt-pattern-inside*/"
SAMPLING_OLD = ("vec2 pos_a=mix(pattern_tl_a*texel_size-texel_size,pattern_br_a*texel_size+texel_size,"
                "vec2(x_a,y));vec2 pos_b=mix(pattern_tl_b*texel_size-texel_size,pattern_br_b*texel_size"
                "+texel_size,vec2(x_b,y));")
SAMPLING_NEW = (SAMPLING_MARK + "vec2 pos_a=vec2(mix(pattern_tl_a.x,pattern_br_a.x,x_a),"
                "mix(pattern_tl_a.y+0.5,pattern_br_a.y-0.5,y))*texel_size;"
                "vec2 pos_b=vec2(mix(pattern_tl_b.x,pattern_br_b.x,x_b),"
                "mix(pattern_tl_b.y+0.5,pattern_br_b.y-0.5,y))*texel_size;")


WIDTH_MARK = "/*q2vt-current-width*/"
WIDTH_EDITS = (("v_linesofar=a_linesofar;v_width2=vec2(outset,inset);v_width=floorwidth;}",
                "v_linesofar=a_linesofar;v_width2=vec2(outset,inset);v_width=width;" + WIDTH_MARK + "}", 1),
               ("a_linesofar*u_patternscale_a_x/floorwidth", "a_linesofar*u_patternscale_a_x/width", 2),
               ("a_linesofar*u_patternscale_b_x/floorwidth", "a_linesofar*u_patternscale_b_x/width", 2))


def patch(text: str) -> str:
    if WIDTH_MARK not in text:
        for old, new, count in WIDTH_EDITS:
            if text.count(old) != count:
                raise SystemExit(f"line width edit: expected {count} matches of {old[:40]}...")
            text = text.replace(old, new)
    if SAMPLING_MARK not in text:
        if text.count(SAMPLING_OLD) != 1:
            raise SystemExit("line-pattern sampling: expected exactly one match")
        text = text.replace(SAMPLING_OLD, SAMPLING_NEW)
    if MARK in text:
        return text
    matches = list(RATIO.finditer(text))
    if len(matches) != 1:
        raise SystemExit(f"line tile ratio function: {len(matches)} matches (expected 1)")
    m = matches[0]
    name = m.group(1)
    text = (text[:m.start()] + f"function {name}({m.group(2)},{m.group(3)}){{{MARK}return 1/"
            f"{m.group(4)}({m.group(2)},1,{m.group(3)}.zoom)}}" + text[m.end():])
    # line-pattern: u_scale [ratio, fromScale, toScale] -> both sides toScale.
    pattern = re.compile(r"u_image:0,u_scale:\[(\w+),(\w+)\.fromScale,\2\.toScale\]")
    if len(pattern.findall(text)) != 1:
        raise SystemExit("line-pattern u_scale: expected exactly one match")
    text = pattern.sub(lambda m: f"u_image:0,u_scale:[{m.group(1)},{m.group(2)}.toScale,"
                                 f"{m.group(2)}.toScale]", text)
    # line dashes: u_crossfade_from -> toScale (both dash programs).
    dash = re.compile(r"u_crossfade_from:(\w+)\.fromScale,u_crossfade_to:\1\.toScale")
    if len(dash.findall(text)) != 2:
        raise SystemExit("dash crossfade: expected exactly two matches")
    text = dash.sub(lambda m: f"u_crossfade_from:{m.group(1)}.toScale,"
                              f"u_crossfade_to:{m.group(1)}.toScale", text)
    return text


def main() -> int:
    with open(BUNDLE, encoding="utf-8") as handle:
        text = handle.read()
    if "--check" in sys.argv:
        done = MARK in text and SAMPLING_MARK in text and WIDTH_MARK in text
        print("patched" if done else "NOT patched")
        return 0 if done else 1
    patched = patch(text)
    if patched != text:
        with open(BUNDLE, "w", encoding="utf-8") as handle:
            handle.write(patched)
        print("patched", BUNDLE)
    else:
        print("already patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
