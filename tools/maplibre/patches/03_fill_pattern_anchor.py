"""Fill patterns placed and sized as QGIS draws them (style layer metadata
set by the converter):

- ``"q2vt:screen-pattern": true`` (textures sized in screen units) keep
  their screen size at every zoom: scale and world anchoring use the real
  zoom, the anchor's sub-pixel part is kept (seamless across tiles) and
  snapped to whole device pixels (crisp texels, no blur from sub-pixel
  sampling), with no double-size cross-fade copy;
- ``"q2vt:pattern-anchor": "viewport"`` (QGIS "Align pattern to: Viewport")
  starts the pattern at the corner of the map canvas instead of the map's
  origin;
- ``"q2vt:pattern-anchor": "feature"`` (point, line and SVG patterns: the
  bottom-left of the feature's bounding box) or ``"feature-clip"`` (raster
  fills: the top-left of the part, at most 10 % of the view outside it)
  start the pattern at each feature's anchor, carried per vertex by the fill
  bucket (patch 04) and snapped to whole device pixels as QGIS rounds it;
- ``"q2vt:pattern-zoom": z`` (map-unit textures, the last one rendered for
  zoom z) keep growing with the map above zoom z: scaled with that zoom
  instead of the tile's integer zoom, without the double-size cross-fade.

The outline pattern program follows the same scaling and anchor. Other fill
patterns keep MapLibre's scaling, which grows with the map like QGIS map
units.
"""

EDITS = [
    ("main", """
function patternUniformValues(crossfade, transform, tile) {
    const tileRatio = 1 / pixelsToTileUnits(tile, 1, transform.tileZoom);
    const numTiles = Math.pow(2, tile.tileID.overscaledZ);
    const tileSizeAtNearestZoom = tile.tileSize * Math.pow(2, transform.tileZoom) / numTiles;
    const pixelX = tileSizeAtNearestZoom * (tile.tileID.canonical.x + tile.tileID.wrap * numTiles);
    const pixelY = tileSizeAtNearestZoom * tile.tileID.canonical.y;
    return {
        "u_image": 0,
        "u_texsize": tile.imageAtlasTexture.size,
        "u_scale": [
            tileRatio,
            crossfade.fromScale,
            crossfade.toScale
        ],
        "u_fade": crossfade.t,
        "u_pixel_coord_upper": [pixelX >> 16, pixelY >> 16],
        "u_pixel_coord_lower": [pixelX & 65535, pixelY & 65535]
    };
}""", """
function patternUniformValues(crossfade, transform, tile, layer, pixelRatio) {
    const metadata = layer && layer.metadata || {};
    const screen = !!metadata["q2vt:screen-pattern"];
    const patternZoom = metadata["q2vt:pattern-zoom"];
    const overZoom = !screen && typeof patternZoom === "number" && transform.tileZoom > patternZoom;
    const zoom = screen ? transform.zoom : overZoom ? patternZoom : transform.tileZoom;
    const tileRatio = 1 / pixelsToTileUnits(tile, 1, zoom);
    const numTiles = Math.pow(2, tile.tileID.overscaledZ);
    const tileSizeAtNearestZoom = tile.tileSize * Math.pow(2, zoom) / numTiles;
    let pixelX = tileSizeAtNearestZoom * (tile.tileID.canonical.x + tile.tileID.wrap * numTiles);
    let pixelY = tileSizeAtNearestZoom * tile.tileID.canonical.y;
    const anchor = metadata["q2vt:pattern-anchor"];
    const viewport = anchor === "viewport";
    const featureMode = anchor === "feature" ? 1 : anchor === "feature-clip" ? 2 : 0;
    let featureAnchor = [featureMode, -1e30, -1e30], screenOrigin = [0, 0, 0], screenScale = 1;
    const ratio = pixelRatio || globalThis.devicePixelRatio || 1;
    if ((screen || viewport || featureMode) && !transform.pitch && !(transform.bearing % 360)) {
        // The view's top-left corner in world pixels at the current zoom.
        const center = transform.center, worldSize = transform.worldSize;
        const left = (180 + center.lng) / 360 * worldSize - transform.centerPoint.x;
        const top = (180 - 180 / Math.PI * Math.log(Math.tan(Math.PI / 4 + center.lat * Math.PI / 360))) / 360 * worldSize - transform.centerPoint.y;
        const k = Math.pow(2, zoom - transform.zoom);
        if (featureMode) {
            // The view's corner minus 10 % in tile units (clamp of "feature-clip"),
            // the tile origin on screen (CSS px) and the device pixel ratio.
            const tileUnits = 8192 / tileSizeAtNearestZoom;
            featureAnchor = [featureMode, (left * k - pixelX - .1 * transform.width * k) * tileUnits, (top * k - pixelY - .1 * transform.height * k) * tileUnits];
            screenOrigin = [pixelX / k - left, pixelY / k - top, ratio];
            screenScale = 1 / k;
        }
        if (viewport) {
            pixelX -= left * k;
            pixelY -= top * k;
        } else {
            pixelX += (Math.round(left * ratio) - left * ratio) / ratio;
            pixelY += (Math.round(top * ratio) - top * ratio) / ratio;
        }
    }
    const upperX = Math.floor(pixelX / 65536), upperY = Math.floor(pixelY / 65536);
    return {
        "u_image": 0,
        "u_texsize": tile.imageAtlasTexture.size,
        "u_scale": [
            tileRatio,
            screen || overZoom ? crossfade.toScale : crossfade.fromScale,
            crossfade.toScale
        ],
        "u_fade": crossfade.t,
        "u_pixel_coord_upper": [upperX, upperY],
        "u_pixel_coord_lower": [pixelX - upperX * 65536, pixelY - upperY * 65536],
        "u_q2a": featureAnchor,
        "u_q2b": screenOrigin,
        "u_q2c": screenScale
    };
}""", 1),
    # The fill and fill outline programs pass their style layer and pixel ratio.
    ("main", """
const fillPatternUniformValues = (transform, crossfade, tile, translate, isSdfPattern) => extend(patternUniformValues(crossfade, transform, tile), {""", """
const fillPatternUniformValues = (transform, crossfade, tile, translate, isSdfPattern, layer, pixelRatio) => extend(patternUniformValues(crossfade, transform, tile, layer, pixelRatio), {""", 1),
    ("main", """
const fillOutlinePatternUniformValues = (transform, crossfade, tile, translate, isSdfPattern) => fillPatternUniformValues(transform, crossfade, tile, translate, isSdfPattern);""", """
const fillOutlinePatternUniformValues = (transform, crossfade, tile, translate, isSdfPattern, layer, pixelRatio) => fillPatternUniformValues(transform, crossfade, tile, translate, isSdfPattern, layer, pixelRatio);""", 1),
    ("main", """
            uniformValues = image ? fillPatternUniformValues(transform, crossfade, tile, translateForUniforms, isSdfPattern) : fillUniformValues(translateForUniforms);""", """
            uniformValues = image ? fillPatternUniformValues(transform, crossfade, tile, translateForUniforms, isSdfPattern, layer, frameRenderContext.data.pixelRatio) : fillUniformValues(translateForUniforms);""", 1),
    ("main", """
            uniformValues = programName === "fillOutlinePattern" && image ? fillOutlinePatternUniformValues(transform, crossfade, tile, translateForUniforms, isSdfPattern) : fillOutlineUniformValues(translateForUniforms);""", """
            uniformValues = programName === "fillOutlinePattern" && image ? fillOutlinePatternUniformValues(transform, crossfade, tile, translateForUniforms, isSdfPattern, layer, frameRenderContext.data.pixelRatio) : fillOutlineUniformValues(translateForUniforms);""", 1),
    # Each vertex's feature anchor (patch 04) as a second vertex buffer.
    ("main", """
        program.draw(context, drawMode, depthMode, stencil, colorMode, CullFaceMode.backCCW, uniformValues, terrainData, projectionData, layer.id, bucket.layoutVertexBuffer, indexBuffer, segments, layer.paint, frameRenderContext.transform.zoom, programConfiguration);""", """
        program.draw(context, drawMode, depthMode, stencil, colorMode, CullFaceMode.backCCW, uniformValues, terrainData, projectionData, layer.id, bucket.layoutVertexBuffer, indexBuffer, segments, layer.paint, frameRenderContext.transform.zoom, programConfiguration, image && bucket.q2anchorBuffer || void 0);""", 1),
    # Uniforms of both pattern programs.
    ("main", """
    "u_sdf_pattern": new Uniform1i(context, locations.u_sdf_pattern),
    "u_fill_translate": new Uniform2f(context, locations.u_fill_translate)
});""", """
    "u_sdf_pattern": new Uniform1i(context, locations.u_sdf_pattern),
    "u_fill_translate": new Uniform2f(context, locations.u_fill_translate),
    "u_q2a": new Uniform3f(context, locations.u_q2a),
    "u_q2b": new Uniform3f(context, locations.u_q2b),
    "u_q2c": new Uniform1f(context, locations.u_q2c)
});""", 2),
    # Shaders: the pattern's top-left at the feature's anchor (QGIS brush
    # origin), snapped to whole device pixels on screen like QGIS's rounding.
    ("main",
     "uniform vec2 u_pixel_coord_upper;uniform vec2 u_pixel_coord_lower;uniform vec3 u_scale;"
     "uniform vec2 u_fill_translate;layout(location=0) in vec2 a_pos;",
     "uniform vec2 u_pixel_coord_upper;uniform vec2 u_pixel_coord_lower;uniform vec3 u_scale;"
     "uniform vec2 u_fill_translate;layout(location=0) in vec2 a_pos;"
     "uniform vec3 u_q2a;uniform vec3 u_q2b;uniform float u_q2c;layout(location=1) in vec2 a_q2anchor;", 2),
] + [
    ("main",
     f"v_pos_a=get_pattern_pos(u_pixel_coord_upper,u_pixel_coord_lower,fromScale*display_size_a,{ratio},a_pos);"
     f"v_pos_b=get_pattern_pos(u_pixel_coord_upper,u_pixel_coord_lower,toScale*display_size_b,{ratio},a_pos);",
     f"if(u_q2a.x>0.5&&a_q2anchor.x<1e29){{vec2 q2p=a_q2anchor;if(u_q2a.x>1.5){{q2p=max(q2p,u_q2a.yz);}}"
     f"q2p*={ratio};if(u_q2b.z>0.0){{vec2 q2o=floor((q2p*u_q2c+u_q2b.xy)*u_q2b.z+0.5)/u_q2b.z;"
     f"q2p=(q2o-u_q2b.xy)/u_q2c;}}vec2 q2v={ratio}*a_pos-q2p;"
     f"v_pos_a=q2v/(fromScale*display_size_a);v_pos_b=q2v/(toScale*display_size_b);}}else{{"
     f"v_pos_a=get_pattern_pos(u_pixel_coord_upper,u_pixel_coord_lower,fromScale*display_size_a,{ratio},a_pos);"
     f"v_pos_b=get_pattern_pos(u_pixel_coord_upper,u_pixel_coord_lower,toScale*display_size_b,{ratio},a_pos);}}",
     1)
    # fillOutlinePattern calls it tileRatio, fillPattern tileZoomRatio.
    for ratio in ("tileRatio", "tileZoomRatio")
]
