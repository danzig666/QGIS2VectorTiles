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
   the corner of the map canvas instead of the map's origin. Layers flagged
   ``"feature"`` (point, line and SVG patterns: the bottom-left of the
   feature's bounding box) or ``"feature-clip"`` (raster fills: the top-left
   of the part, at most 10 % of the view outside it) start their pattern at
   each feature's anchor, carried per vertex by the fill bucket
   (``q2vt_pat_x`` / ``q2vt_pat_y`` properties, EPSG:3857) and snapped to
   whole device pixels as QGIS rounds it. The outline pattern program
   follows the same scaling and anchor.
   Other fill patterns (map-unit textures) keep MapLibre's scaling, which
   grows with the map like QGIS map units;
5. samples a line pattern only inside its image: stock MapLibre maps the line
   onto the image plus one padding texel on each side, and pads patterns with
   the *opposite* edge (for tiling), so the outer sliver of the stroke showed
   the far edge's colour (faint dots along a lineburst's light edge) and each
   repeat was squeezed by two texels. Across the line it now runs from the
   first row's centre to the last row's; along it exactly over the image.

6. joins a line whose last point is its first (polygon outlines exported as
   lines, closed contours) at that point like a polygon ring, as Qt strokes
   a closed path, instead of drawing two caps there; a ring's dashes start
   at its first vertex (stock MapLibre counted the closing segment first).

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
           f"q2A=!!(q2l&&q2l.metadata&&q2l.metadata[\"q2vt:pattern-anchor\"]===\"viewport\"),"
           # Feature-aligned (QGIS "Align pattern to: Feature"): the bucket
           # carries each feature's anchor (a_q2anchor, tile units); mode 2
           # clamps it to the view's top-left minus 10 % (raster fills).
           f"q2F=q2l&&q2l.metadata&&q2l.metadata[\"q2vt:pattern-anchor\"],"
           f"q2M=q2F===\"feature\"?1:q2F===\"feature-clip\"?2:0,q2Q=[q2M,-1e30,-1e30],q2B=[0,0,0],q2C=1;"
           f"if((q2s||q2A||q2M)&&!q2T.pitch&&!(q2T.bearing%360)){{let q2c=q2T.center,q2w=q2T.worldSize,"
           f"q2u=(180+q2c.lng)/360*q2w-q2T.centerPoint.x,"
           f"q2v=(180-180/Math.PI*Math.log(Math.tan(Math.PI/4+q2c.lat*Math.PI/360)))/360*q2w"
           f"-q2T.centerPoint.y;"
           # The view's corner in tile units, the tile origin on screen (CSS
           # px) and screen px per pattern px: anchors snap to device pixels.
           f"if(q2M){{let q2k=2**(q2z-q2T.zoom),q2t=8192/q2a;"
           f"q2Q=[q2M,(q2u*q2k-q2x-.1*q2T.width*q2k)*q2t,(q2v*q2k-q2y-.1*q2T.height*q2k)*q2t];"
           f"q2B=[q2x/q2k-q2u,q2y/q2k-q2v,q2r];q2C=1/q2k}}"
           # Viewport-aligned (QGIS "Align pattern to: Viewport"): the pattern
           # starts at the canvas corner (in the pattern's own zoom pixels).
           f"if(q2A){{let q2k=2**(q2z-q2T.zoom);q2x-=q2u*q2k;q2y-=q2v*q2k}}else{{"
           f"q2x+=(Math.round(q2u*q2r)-q2u*q2r)/q2r;"
           f"q2y+=(Math.round(q2v*q2r)-q2v*q2r)/q2r}}}}"
           f"let q2X=Math.floor(q2x/65536),q2Y=Math.floor(q2y/65536);"
           f"return{{u_image:0,u_texsize:{tile}.imageAtlasTexture.size,"
           f"u_scale:[{ratio},q2s?{cf}.toScale:{cf}.fromScale,{cf}.toScale],u_fade:{cf}.t,"
           f"u_pixel_coord_upper:[q2X,q2Y],u_pixel_coord_lower:[q2x-q2X*65536,q2y-q2Y*65536],"
           f"u_q2a:q2Q,u_q2b:q2B,u_q2c:q2C}}}}")
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
    text = text.replace(call, f"h=d?{builder}(e,f,r,O,C,n):pu(O)")
    outline = re.compile(r"(\w+)=\(e,t,n,r,i\)=>" + re.escape(builder) + r"\(e,t,n,r,i\)")
    found = list(outline.finditer(text))
    if len(found) != 1:
        raise SystemExit(f"outline pattern uniform builder: {len(found)} matches (expected 1)")
    ob = found[0].group(1)
    text = outline.sub(lambda c: f"{ob}=(e,t,n,r,i,q2l)=>{builder}(e,t,n,r,i,q2l)", text)
    call = f"{ob}(e,f,r,O,C):"
    if text.count(call) != 1:
        raise SystemExit("outline pattern call: expected exactly one match")
    text = text.replace(call, f"{ob}(e,f,r,O,C,n):")
    draw = "S.layoutVertexBuffer,g,_,n.paint,e.transform.zoom,w)"
    if text.count(draw) != 1:
        raise SystemExit("fill draw: expected exactly one match")
    return text.replace(draw, "S.layoutVertexBuffer,g,_,n.paint,e.transform.zoom,w,d&&S.q2anchorBuffer||void 0)")


ANCHOR_MARK = "/*q2vt-feature-anchor*/"
ANCHOR_DECL_OLD = ("uniform vec2 u_pixel_coord_upper;uniform vec2 u_pixel_coord_lower;uniform vec3 u_scale;"
                   "uniform vec2 u_fill_translate;layout(location=0) in vec2 a_pos;")
ANCHOR_DECL_NEW = (ANCHOR_DECL_OLD + "uniform vec3 u_q2a;uniform vec3 u_q2b;uniform float u_q2c;"
                   "layout(location=1) in vec2 a_q2anchor;")
ANCHOR_POS = re.compile(
    r"v_pos_a=get_pattern_pos\(u_pixel_coord_upper,u_pixel_coord_lower,fromScale\*display_size_a,(\w+),a_pos\);"
    r"v_pos_b=get_pattern_pos\(u_pixel_coord_upper,u_pixel_coord_lower,toScale\*display_size_b,\1,a_pos\);")


def _anchor_shader(match) -> str:
    ratio = match.group(1)
    # The pattern's top-left at the feature's anchor (QGIS brush origin),
    # snapped to whole device pixels on screen like QGIS's rounding.
    return (f"if(u_q2a.x>0.5&&a_q2anchor.x<1e29){{{ANCHOR_MARK}vec2 q2p=a_q2anchor;"
            f"if(u_q2a.x>1.5){{q2p=max(q2p,u_q2a.yz);}}q2p*={ratio};"
            f"if(u_q2b.z>0.0){{vec2 q2o=floor((q2p*u_q2c+u_q2b.xy)*u_q2b.z+0.5)/u_q2b.z;"
            f"q2p=(q2o-u_q2b.xy)/u_q2c;}}vec2 q2v={ratio}*a_pos-q2p;"
            f"v_pos_a=q2v/(fromScale*display_size_a);v_pos_b=q2v/(toScale*display_size_b);}}"
            f"else{{{match.group(0)}}}")


def _anchor_patch(text: str) -> str:
    """Main bundle: the fill pattern shaders read a feature anchor."""
    if text.count(ANCHOR_DECL_OLD) != 2:
        raise SystemExit("fill pattern shader declarations: expected exactly two matches")
    text = text.replace(ANCHOR_DECL_OLD, ANCHOR_DECL_NEW)
    if len(ANCHOR_POS.findall(text)) != 2:
        raise SystemExit("fill pattern positions: expected exactly two matches")
    text = ANCHOR_POS.sub(_anchor_shader, text)
    uniforms = re.compile(r"(u_sdf_pattern:new (\w+)\(e,t\.u_sdf_pattern\),"
                          r"u_fill_translate:new (\w+)\(e,t\.u_fill_translate\))\}\)")
    scale = re.search(r"u_scale:new (\w+)\(e,t\.u_scale\),u_fade:new (\w+)\(e,t\.u_fade\)", text)
    found = uniforms.findall(text)
    if len(found) != 2 or scale is None:
        raise SystemExit(f"fill pattern uniform bindings: {len(found)} matches (expected 2)")
    vec3, flt = scale.group(1), scale.group(2)
    return uniforms.sub(lambda m: f"{m.group(1)},u_q2a:new {vec3}(e,t.u_q2a),u_q2b:new {vec3}(e,t.u_q2b),"
                                  f"u_q2c:new {flt}(e,t.u_q2c)}})", text)


SHARED = os.path.join(ROOT, "resources", "ml_viewer", "maplibre-gl-shared.mjs")
BUCKET_MARK = "/*q2vt-fill-anchors*/"


def _bucket_patch(text: str) -> str:
    """Shared bundle: fill buckets of layers flagged ``q2vt:pattern-anchor``
    "feature" / "feature-clip" carry each feature's pattern anchor (its
    q2vt_pat_x / q2vt_pat_y, EPSG:3857 metres) per vertex, in tile units."""
    layout = re.search(r"W\(`StructArrayLayout2f8`,(\w+)\)", text)
    if layout is None:
        raise SystemExit("StructArrayLayout2f8 not found")
    floats = layout.group(1)
    ctor = re.compile(r"(this\.segments2=new (\w+),)(this\.stateDependentLayerIds=this\.layers\.filter\(e=>e\.isStateDependent\(\)\)"
                      r"\.map\(e=>e\.id\)\}populate\(e,t,n\)\{this\.hasDependencies=\w+\(`fill`)")
    if len(ctor.findall(text)) != 1:
        raise SystemExit("fill bucket constructor: expected exactly one match")
    text = ctor.sub(lambda m: m.group(1) + BUCKET_MARK + "this.q2anchorArray=this.layers.some(e=>e.metadata&&"
                    "/^feature/.test(e.metadata[\"q2vt:pattern-anchor\"]||\"\"))?new " + floats + ":null,"
                    + m.group(3), text)
    upload = re.compile(r"(upload\(e\)\{this\.uploaded\|\|\(this\.layoutVertexBuffer=e\.createVertexBuffer\(this\.layoutVertexArray,\w+\),"
                        r"this\.indexBuffer=e\.createIndexBuffer\(this\.indexArray\),this\.indexBuffer2=e\.createIndexBuffer\(this\.indexArray2\))\)")
    if len(upload.findall(text)) != 1:
        raise SystemExit("fill bucket upload: expected exactly one match")
    text = upload.sub(lambda m: m.group(1) + ",this.q2anchorArray&&this.q2anchorArray.length&&(this.q2anchorBuffer="
                      "e.createVertexBuffer(this.q2anchorArray,[{name:\"a_q2anchor\",type:\"Float32\",components:2,offset:0}])))",
                      text)
    destroy = "this.indexBuffer2.destroy(),this.programConfigurations.destroy(),this.segments.destroy(),this.segments2.destroy())}"
    if text.count(destroy) != 1:
        raise SystemExit("fill bucket destroy: expected exactly one match")
    text = text.replace(destroy, destroy[:-2] + ",this.q2anchorBuffer&&this.q2anchorBuffer.destroy())}")
    add = re.compile(r"addFeature\(e,t,n,r,i,a\)\{for\(let e of (\w+)\(t,500\)\)\{let t=(\w+)\(e,r,a\.fill\.getGranularityForZoomLevel\(r\.z\)\),"
                     r"n=this\.layoutVertexArray;(\w+)\(\(e,t\)=>\{n\.emplaceBack\(e,t\)\}")
    if len(add.findall(text)) != 1:
        raise SystemExit("fill bucket addFeature: expected exactly one match")
    return add.sub(lambda m: (
        "addFeature(e,t,n,r,i,a){let q2=this.q2anchorArray,q2x=1e30,q2y=1e30;if(q2){let q2p=e.properties||{},"
        "q2X=q2p.q2vt_pat_x,q2Y=q2p.q2vt_pat_y;if(q2X!=null&&q2Y!=null&&isFinite(q2X)&&isFinite(q2Y)){"
        "let q2s=2**r.z,q2w=40075016.68557849;q2x=((q2X/q2w+.5)*q2s-r.x)*8192;q2y=((.5-q2Y/q2w)*q2s-r.y)*8192}}"
        f"for(let e of {m.group(1)}(t,500)){{let t={m.group(2)}(e,r,a.fill.getGranularityForZoomLevel(r.z)),"
        f"n=this.layoutVertexArray;{m.group(3)}((e,t)=>{{n.emplaceBack(e,t);q2&&q2.emplaceBack(q2x,q2y)}}"), text)


CLOSED_MARK = "/*q2vt-closed-lines*/"
CLOSED = re.compile(r"let (\w)=(\w+)\.types\[(\w)\.type\]===`Polygon`,(\w)=(\w)\.length;")


# for(let t=d;t<u;t++){if(g=t===u-1?l?e[d+1]:void 0:e[t+1]  ...  h&&this.updateDistance(h,m),w===`miter`)
RING_LOOP = re.compile(r"for\(let (\w)=(\w);\1<(\w);\1\+\+\)\{if\((\w)=\1===\3-1\?(\w)\?")
RING_DISTANCE = re.compile(r"(\w)&&this\.updateDistance\(\1,(\w)\),(\w)===`miter`\)")


def _closed_line_patch(text: str) -> str:
    """Shared bundle: a line whose last point is its first (a polygon outline
    exported as a line, a closed contour) is joined there like a polygon
    ring, as Qt strokes a closed path; stock MapLibre gave it two caps
    there (a knob with square caps, a darker spot on translucent lines).
    A ring's dashes start at its first vertex, as in QGIS: stock MapLibre
    counted the closing segment before it (the pattern started shifted)."""
    if len(CLOSED.findall(text)) != 1:
        raise SystemExit("line bucket polygon test: expected exactly one match")
    text = CLOSED.sub(lambda m: (
        f"let {m.group(1)}={m.group(2)}.types[{m.group(3)}.type]===`Polygon`||{CLOSED_MARK}"
        f"{m.group(5)}.length>3&&{m.group(5)}[0].equals({m.group(5)}[{m.group(5)}.length-1]),"
        f"{m.group(4)}={m.group(5)}.length;"), text)
    loops = RING_LOOP.findall(text)
    distances = RING_DISTANCE.findall(text)
    if len(loops) != 1 or len(distances) != 1:
        raise SystemExit("line bucket ring distance: expected exactly one loop and one update")
    index, first = loops[0][0], loops[0][1]
    return RING_DISTANCE.sub(lambda m: (
        f"{m.group(1)}&&{index}>{first}&&this.updateDistance({m.group(1)},{m.group(2)}),"
        f"{m.group(3)}===`miter`)"), text)


ROTATED_MARK = "/*q2vt-rotated-stretch*/"
# fy(): pixelOffsetTL:ie,pixelOffsetBR:ae,minFontScaleX:C/o/O,minFontScaleY:...
QUAD_RETURN = re.compile(r"pixelOffsetTL:(\w+),pixelOffsetBR:(\w+),minFontScaleX:(\w+)/(\w+)/(\w+),")
QUAD_ANGLE = re.compile(r"(\w+)=(\w+)\*Math\.PI/180;if\(\1\)\{let (\w+)=Math\.sin\(\1\)")
SYMBOL_QUAD = re.compile(r"let\{tl:\w+,tr:\w+,bl:\w+,br:\w+,tex:\w+,pixelOffsetTL:(\w+),pixelOffsetBR:(\w+),")


def _rotated_stretch_patch(text: str) -> str:
    """Shared bundle: a stretchable icon (a label frame fitted to its text
    with ``icon-text-fit``) turned by ``icon-rotate`` had only its stretched
    parts turned. Its fixed parts (the frame's border and corners) are moved
    by pixel offsets, which stock MapLibre added unturned: the corners stuck
    out of a turned frame. The offsets of each corner are turned with it."""
    returns = list(QUAD_RETURN.finditer(text))
    if len(returns) != 1:
        raise SystemExit(f"icon quad pixel offsets: {len(returns)} matches (expected 1)")
    ret = returns[0]
    angles = [m for m in QUAD_ANGLE.finditer(text, 0, ret.start()) if ret.start() - m.start() < 1500]
    if len(angles) != 1:
        raise SystemExit("icon quad angle: expected exactly one match before the quad")
    angle = angles[0].group(1)
    tl, br = ret.group(1), ret.group(2)
    text = (text[:ret.start()] + f"pixelOffsetTL:{tl},pixelOffsetBR:{br},{ROTATED_MARK}q2px:{angle}?"
            f"[[{tl}.x,{tl}.y],[{br}.x,{tl}.y],[{tl}.x,{br}.y],[{br}.x,{br}.y]].map(([x,y])=>"
            f"[Math.cos({angle})*x-Math.sin({angle})*y,Math.sin({angle})*x+Math.cos({angle})*y]):void 0,"
            + text[ret.end() - len(f"minFontScaleX:{ret.group(3)}/{ret.group(4)}/{ret.group(5)},"):])
    quads = list(SYMBOL_QUAD.finditer(text))
    if len(quads) != 1:
        raise SystemExit(f"symbol quad loop: {len(quads)} matches (expected 1)")
    q = quads[0]
    tl, br = q.group(1), q.group(2)
    end = text.index("emplaceBack", q.end())
    segment = text[q.end():end]
    corners = [(f"{tl}.x,{tl}.y,", 0), (f"{br}.x,{tl}.y,", 1), (f"{tl}.x,{br}.y,", 2), (f"{br}.x,{br}.y,", 3)]
    for old, index in corners:
        if segment.count(old) != 1:
            raise SystemExit(f"symbol quad corner offsets: {old} not found exactly once")
        segment = segment.replace(old, f"q2Q?q2Q[{index}][0]:{old.split(',')[0]},"
                                       f"q2Q?q2Q[{index}][1]:{old.split(',')[1]},")
    return text[:q.end()] + "q2px:q2Q," + segment + text[end:]


def _shared_patch(text: str) -> str:
    if BUCKET_MARK not in text:
        text = _bucket_patch(text)
    if CLOSED_MARK not in text:
        text = _closed_line_patch(text)
    if ROTATED_MARK not in text:
        text = _rotated_stretch_patch(text)
    return text


def patch(text: str) -> str:
    if FILL_MARK not in text:
        text = _fill_patch(text)
    if ANCHOR_MARK not in text:
        text = _anchor_patch(text)
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
    with open(SHARED, encoding="utf-8") as handle:
        shared = handle.read()
    if "--check" in sys.argv:
        done = all(mark in text for mark in (MARK, SAMPLING_MARK, WIDTH_MARK, FILL_MARK, ANCHOR_MARK)) \
            and BUCKET_MARK in shared and CLOSED_MARK in shared \
            and ROTATED_MARK in shared
        print("patched" if done else "NOT patched")
        return 0 if done else 1
    for path, old, new in ((BUNDLE, text, patch(text)),
                           (SHARED, shared, _shared_patch(shared))):
        if new != old:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(new)
            print("patched", path)
        else:
            print("already patched", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
