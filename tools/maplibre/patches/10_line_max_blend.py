"""Line layers flagged ``"q2vt:max-blend": true`` (in their metadata) paint
each pixel once, with its strongest coverage, as QGIS draws a glow or a
shadow: as one image of the whole line.

Stock MapLibre draws a line as quads per segment; at a corner turning more
than about 120 degrees (miter length over 2) it ends both segments with
round caps that overlap inside the corner. A semi-transparent or blurred
line was painted twice there: a dark wedge in a glow at every sharp corner.

A flagged layer (plain lines: no dashes, pattern or gradient) is drawn
twice per tile. First into the depth buffer only: each fragment's depth is
its layer's depth less its coverage times a fraction of the gap to the next
layer, kept where less (closer) than what the pixel has, so each pixel
ends up with its strongest coverage. Then in colour, a hair closer still
with a strict test: the strongest fragment of each pixel passes, once, and
moves the pixel closer, so no other fragment of the layer paints it again.
Other layers are unaffected (their depths are further apart). Between
features of the layer the same holds (QGIS draws a glow per feature:
crossing glows are a little lighter than QGIS's).
"""

EDITS = [
    # The plain line shader writes its depth: the layer's, less the coverage.
    ("main", 'line: prepare("in vec2 v_width2;',
     'line: prepare("uniform highp float u_q2maxblend;uniform highp float u_q2pass;in vec2 v_width2;', 1),
    ("main", "0.0,1.0);fragColor=color*(alpha*opacity);",
     "0.0,1.0);fragColor=color*(alpha*opacity);"
     "gl_FragDepth=gl_FragCoord.z-floor(alpha*255.0+0.5)/255.0*u_q2maxblend-u_q2pass;", 1),
    ("main", """
const lineUniforms = (context, locations) => ({
    "u_translation": new Uniform2f(context, locations.u_translation),
    "u_ratio": new Uniform1f(context, locations.u_ratio)
});""", """
const lineUniforms = (context, locations) => ({
    "u_translation": new Uniform2f(context, locations.u_translation),
    "u_ratio": new Uniform1f(context, locations.u_ratio),
    "u_q2maxblend": new Uniform1f(context, locations.u_q2maxblend),
    "u_q2pass": new Uniform1f(context, locations.u_q2pass)
});""", 1),
    ("main", """
const lineUniformValues = (transform, tile, layer, ratioScale) => {
    return {
        "u_translation": calculateTranslation(transform, tile, layer),
        "u_ratio": ratioScale / pixelsToTileUnits(tile, 1, transform.zoom)
    };
};""", """
const lineUniformValues = (transform, tile, layer, ratioScale) => {
    return {
        "u_translation": calculateTranslation(transform, tile, layer),
        "u_ratio": ratioScale / pixelsToTileUnits(tile, 1, transform.zoom),
        "u_q2maxblend": q2MaxBlend(layer) ? Q2_MAX_BLEND_RANGE : 0,
        "u_q2pass": 0
    };
};
// Depth range of a max-blend layer's coverages: 12 of its 14 sublayer depths
// (coverage steps of 1/255 are 12 units of 24-bit depth apart);
// the colour pass is a quarter step closer.
const Q2_MAX_BLEND_RANGE = 12 / Math.pow(2, 16);
const Q2_MAX_BLEND_STEP = Q2_MAX_BLEND_RANGE / 255 / 4;
function q2MaxBlend(layer) {
    return !!(layer.metadata && layer.metadata["q2vt:max-blend"]);
}""", 1),
    ("main", """
function drawLineTiles(painter, tileManager, layer, coords, frameRenderContext) {
    const depthMode = frameRenderContext.getDepthModeForSublayer(0, DepthMode.ReadOnly);""", """
function drawLineTiles(painter, tileManager, layer, coords, frameRenderContext) {
    const depthMode = frameRenderContext.getDepthModeForSublayer(0, DepthMode.ReadOnly);
    const q2max = q2MaxBlend(layer) && depthMode !== DepthMode.disabled
        && !layer.paint.get("line-pattern").constantOr(1) && !layer.paint.get("line-dasharray").constantOr(1)
        && !layer.paint.get("line-gradient");
    const q2depth = q2max ? new DepthMode(frameRenderContext.context.gl.LESS, DepthMode.ReadWrite, depthMode.range) : null;""", 1),
    ("main", """
        const stencil = frameRenderContext.stencilModeForClipping(coord);
        program.draw(context, gl.TRIANGLES, depthMode, stencil, colorMode, CullFaceMode.disabled, uniformValues, terrainData, projectionData, layer.id, bucket.layoutVertexBuffer, bucket.indexBuffer, bucket.segments, layer.paint, frameRenderContext.transform.zoom, programConfiguration, bucket.layoutVertexBuffer2);""", """
        const stencil = frameRenderContext.stencilModeForClipping(coord);
        if (q2max) {
            program.draw(context, gl.TRIANGLES, q2depth, stencil, ColorMode.disabled, CullFaceMode.disabled, uniformValues, terrainData, projectionData, layer.id, bucket.layoutVertexBuffer, bucket.indexBuffer, bucket.segments, layer.paint, frameRenderContext.transform.zoom, programConfiguration, bucket.layoutVertexBuffer2);
            uniformValues = extend({}, uniformValues, { "u_q2pass": Q2_MAX_BLEND_STEP });
            program.draw(context, gl.TRIANGLES, q2depth, stencil, colorMode, CullFaceMode.disabled, uniformValues, terrainData, projectionData, layer.id, bucket.layoutVertexBuffer, bucket.indexBuffer, bucket.segments, layer.paint, frameRenderContext.transform.zoom, programConfiguration, bucket.layoutVertexBuffer2);
            firstTile = false;
            continue;
        }
        program.draw(context, gl.TRIANGLES, depthMode, stencil, colorMode, CullFaceMode.disabled, uniformValues, terrainData, projectionData, layer.id, bucket.layoutVertexBuffer, bucket.indexBuffer, bucket.segments, layer.paint, frameRenderContext.transform.zoom, programConfiguration, bucket.layoutVertexBuffer2);""", 1),
]
