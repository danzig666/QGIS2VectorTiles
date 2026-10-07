"""
Patch the vendored MapLibre GL JS (resources/ml_viewer/maplibre-gl.mjs) so
line patterns (``line-pattern``), dash arrays and screen-unit fill patterns
keep their screen size at every zoom, as QGIS draws them.

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
4. fill patterns of style layers flagged ``metadata: {"q2vt:screen-pattern": true}``
   (textures sized in screen units) keep their screen size at every zoom: their
   scale and world anchoring use the real zoom, with the anchor's sub-pixel
   part kept (seamless across tiles) and snapped to whole device pixels
   (crisp texels, no blur from sub-pixel sampling), and no double-size
   cross-fade copy. Layers flagged ``metadata: {"q2vt:pattern-anchor":
   "viewport"}`` (QGIS "Align pattern to: Viewport") start their pattern at
   the corner of the map canvas instead of the map's origin.
   Other fill patterns (map-unit textures) keep MapLibre's scaling, which
   grows with the map like QGIS map units;
5. samples a line pattern only inside its image: stock MapLibre maps the line
   onto the image plus one padding texel on each side, and pads patterns with
   the *opposite* edge (for tiling), so the outer sliver of the stroke showed
   the far edge's colour (faint dots along a lineburst's light edge) and each
   repeat was squeezed by two texels. Across the line it now runs from the
   first row's centre to the last row's; along it exactly over the image.

Idempotent; run after updating MapLibre:

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


FILL_MARK = "/*q2vt-screen-fill-patterns*/"
FILL_OLD_FN = re.compile(
    r"function (\w+)\((\w),(\w),(\w)\)\{let (\w)=1/(\w+)\(\4,1,\3\.transform\.tileZoom\),"
    r"(\w)=2\*\*\4\.tileID\.overscaledZ,(\w)=\4\.tileSize\*2\*\*\3\.transform\.tileZoom/\7,"
    r"(\w)=\8\*\(\4\.tileID\.canonical\.x\+\4\.tileID\.wrap\*\7\),(\w)=\8\*\4\.tileID\.canonical\.y;"
    r"return\{u_image:0,u_texsize:\4\.imageAtlasTexture\.size,u_scale:\[\5,\2\.fromScale,\2\.toScale\],"
    r"u_fade:\2\.t,u_pixel_coord_upper:\[\9>>16,\10>>16\],u_pixel_coord_lower:\[\9&65535,\10&65535\]\}\}")


def _fill_patch(text: str) -> str:
    matches = list(FILL_OLD_FN.finditer(text))
    if len(matches) != 1:
        raise SystemExit(f"fill pattern uniforms: {len(matches)} matches (expected 1)")
    m = matches[0]
    fn, cf, painter, tile = m.group(1), m.group(2), m.group(3), m.group(4)
    ratio, ce = m.group(5), m.group(6)
    new = (f"function {fn}({cf},{painter},{tile},q2l){{{FILL_MARK}"
           f"let q2s=!!(q2l&&q2l.metadata&&q2l.metadata[\"q2vt:screen-pattern\"]),"
           f"q2z=q2s?{painter}.transform.zoom:{painter}.transform.tileZoom,"
           f"{ratio}=1/{ce}({tile},1,q2z),q2n=2**{tile}.tileID.overscaledZ,"
           f"q2a={tile}.tileSize*2**q2z/q2n,"
           f"q2x=q2a*({tile}.tileID.canonical.x+{tile}.tileID.wrap*q2n),q2y=q2a*{tile}.tileID.canonical.y,"
           # Snap the texture grid to whole device pixels (crisp texels, as
           # QGIS's brush): shift the anchor by the sub-pixel part of the
           # map's screen offset (flat, unrotated maps).
           f"q2T={painter}.transform,q2r={painter}.pixelRatio||globalThis.devicePixelRatio||1,"
           f"q2A=!!(q2l&&q2l.metadata&&q2l.metadata[\"q2vt:pattern-anchor\"]===\"viewport\");"
           f"if((q2s||q2A)&&!q2T.pitch&&!(q2T.bearing%360)){{let q2c=q2T.center,q2w=q2T.worldSize,"
           f"q2u=(180+q2c.lng)/360*q2w-q2T.centerPoint.x,"
           f"q2v=(180-180/Math.PI*Math.log(Math.tan(Math.PI/4+q2c.lat*Math.PI/360)))/360*q2w"
           f"-q2T.centerPoint.y;"
           # Viewport-aligned (QGIS "Align pattern to: Viewport"): the pattern
           # starts at the canvas corner (in the pattern's own zoom pixels).
           f"if(q2A){{let q2k=2**(q2z-q2T.zoom);q2x-=q2u*q2k;q2y-=q2v*q2k}}else{{"
           f"q2x+=(Math.round(q2u*q2r)-q2u*q2r)/q2r;"
           f"q2y+=(Math.round(q2v*q2r)-q2v*q2r)/q2r}}}}"
           f"let q2X=Math.floor(q2x/65536),q2Y=Math.floor(q2y/65536);"
           f"return{{u_image:0,u_texsize:{tile}.imageAtlasTexture.size,"
           f"u_scale:[{ratio},q2s?{cf}.toScale:{cf}.fromScale,{cf}.toScale],u_fade:{cf}.t,"
           f"u_pixel_coord_upper:[q2X,q2Y],u_pixel_coord_lower:[q2x-q2X*65536,q2y-q2Y*65536]}}}}")
    text = text[:m.start()] + new + text[m.end():]
    # The fill program passes its style layer along (fill outlines keep stock behaviour).
    caller = re.compile(r"(\w+)=\(e,t,n,r,i\)=>R\(" + re.escape(fn) + r"\(t,e,n\),\{u_fill_translate:r,u_sdf_pattern:\+!!i\}\)")
    found = list(caller.finditer(text))
    if len(found) != 1:
        raise SystemExit(f"fill pattern uniform builder: {len(found)} matches (expected 1)")
    builder = found[0].group(1)
    text = caller.sub(lambda c: f"{builder}=(e,t,n,r,i,q2l)=>R({fn}(t,e,n,q2l),{{u_fill_translate:r,u_sdf_pattern:+!!i}})", text)
    call = f"h=d?{builder}(e,f,r,O,C):pu(O)"
    if text.count(call) != 1:
        raise SystemExit("fill draw call: expected exactly one match")
    return text.replace(call, f"h=d?{builder}(e,f,r,O,C,n):pu(O)")


def patch(text: str) -> str:
    if FILL_MARK not in text:
        text = _fill_patch(text)
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
        done = all(mark in text for mark in (MARK, SAMPLING_MARK, WIDTH_MARK, FILL_MARK))
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
