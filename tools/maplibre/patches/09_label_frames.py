"""Label frames (icons fitted to their text with ``icon-text-fit``) drawn
as QGIS draws text backgrounds:

1. a frame of a style layer flagged ``"q2vt:frame-shape": "ellipse" |
   "circle" | "square"`` is fitted around the text box as QGIS bounds the
   text by that shape (qgstextrenderer drawBackground): the box times
   sqrt(2) (ellipse), its diagonal both ways (circle) or its longer side both
   ways (square), then the padding (the frame's buffer) around it. Stock
   MapLibre fitted every frame to the bare text box: ellipses were 20-25 %
   smaller than QGIS's;
2. a frame turned by ``icon-rotate`` keeps its corners: its fixed parts
   (border and corners) are moved by pixel offsets, which stock MapLibre
   added unturned, so the corners stuck out of a turned frame. The offsets
   of each corner are turned with it.

Symbol layout runs in the worker; both bundles hold its code.
"""

EDITS = [
    ("both", """
function fitIconToText(shapedIcon, shapedText, textFit, padding, iconOffset, fontScale) {""", """
function q2FrameBox(shapedText, layer) {
    const shape = layer && layer.metadata && layer.metadata["q2vt:frame-shape"];
    if (!shapedText || !shape) return shapedText;
    let width = shapedText.right - shapedText.left, height = shapedText.bottom - shapedText.top;
    const x = (shapedText.left + shapedText.right) / 2, y = (shapedText.top + shapedText.bottom) / 2;
    if (shape === "ellipse") {
        width *= Math.SQRT2;
        height *= Math.SQRT2;
    } else if (shape === "circle") width = height = Math.hypot(width, height);
    else if (shape === "square") width = height = Math.max(width, height);
    return Object.assign({}, shapedText, {
        left: x - width / 2,
        right: x + width / 2,
        top: y - height / 2,
        bottom: y + height / 2
    });
}
function fitIconToText(shapedIcon, shapedText, textFit, padding, iconOffset, fontScale) {""", 1),
    ("both",
     "verticallyShapedIcon = fitIconToText(shapedIcon, shapedTextOrientations.vertical, iconTextFit,",
     "verticallyShapedIcon = fitIconToText(shapedIcon, q2FrameBox(shapedTextOrientations.vertical, bucket.layers[0]), iconTextFit,",
     1),
    ("both",
     "shapedIcon = fitIconToText(shapedIcon, defaultHorizontalShaping, iconTextFit,",
     "shapedIcon = fitIconToText(shapedIcon, q2FrameBox(defaultHorizontalShaping, bucket.layers[0]), iconTextFit,",
     1),
    # 2. Each corner's pixel offset turned with the frame.
    ("both", """
            pixelOffsetTL,
            pixelOffsetBR,
            minFontScaleX: fixedContentWidth / pixelRatio / iconWidth,""", """
            pixelOffsetTL,
            pixelOffsetBR,
            q2px: angle ? [
                [pixelOffsetTL.x, pixelOffsetTL.y],
                [pixelOffsetBR.x, pixelOffsetTL.y],
                [pixelOffsetTL.x, pixelOffsetBR.y],
                [pixelOffsetBR.x, pixelOffsetBR.y]
            ].map(([x, y]) => [Math.cos(angle) * x - Math.sin(angle) * y, Math.sin(angle) * x + Math.cos(angle) * y]) : void 0,
            minFontScaleX: fixedContentWidth / pixelRatio / iconWidth,""", 1),
    ("both", """
            const { tl, tr, bl, br, tex, pixelOffsetTL, pixelOffsetBR, minFontScaleX, minFontScaleY, glyphOffset, isSDF, sectionIndex } = quads[i];
            const index = segment.vertexLength;
            const y = glyphOffset[1];
            addVertex(layoutVertexArray, labelAnchor.x, labelAnchor.y, tl.x, y + tl.y, tex.x, tex.y, sizeVertex, isSDF, pixelOffsetTL.x, pixelOffsetTL.y, minFontScaleX, minFontScaleY, elevation);
            addVertex(layoutVertexArray, labelAnchor.x, labelAnchor.y, tr.x, y + tr.y, tex.x + tex.w, tex.y, sizeVertex, isSDF, pixelOffsetBR.x, pixelOffsetTL.y, minFontScaleX, minFontScaleY, elevation);
            addVertex(layoutVertexArray, labelAnchor.x, labelAnchor.y, bl.x, y + bl.y, tex.x, tex.y + tex.h, sizeVertex, isSDF, pixelOffsetTL.x, pixelOffsetBR.y, minFontScaleX, minFontScaleY, elevation);
            addVertex(layoutVertexArray, labelAnchor.x, labelAnchor.y, br.x, y + br.y, tex.x + tex.w, tex.y + tex.h, sizeVertex, isSDF, pixelOffsetBR.x, pixelOffsetBR.y, minFontScaleX, minFontScaleY, elevation);""", """
            const { tl, tr, bl, br, tex, pixelOffsetTL, pixelOffsetBR, minFontScaleX, minFontScaleY, glyphOffset, isSDF, sectionIndex, q2px } = quads[i];
            const index = segment.vertexLength;
            const y = glyphOffset[1];
            const offsets = q2px || [
                [pixelOffsetTL.x, pixelOffsetTL.y],
                [pixelOffsetBR.x, pixelOffsetTL.y],
                [pixelOffsetTL.x, pixelOffsetBR.y],
                [pixelOffsetBR.x, pixelOffsetBR.y]
            ];
            addVertex(layoutVertexArray, labelAnchor.x, labelAnchor.y, tl.x, y + tl.y, tex.x, tex.y, sizeVertex, isSDF, offsets[0][0], offsets[0][1], minFontScaleX, minFontScaleY, elevation);
            addVertex(layoutVertexArray, labelAnchor.x, labelAnchor.y, tr.x, y + tr.y, tex.x + tex.w, tex.y, sizeVertex, isSDF, offsets[1][0], offsets[1][1], minFontScaleX, minFontScaleY, elevation);
            addVertex(layoutVertexArray, labelAnchor.x, labelAnchor.y, bl.x, y + bl.y, tex.x, tex.y + tex.h, sizeVertex, isSDF, offsets[2][0], offsets[2][1], minFontScaleX, minFontScaleY, elevation);
            addVertex(layoutVertexArray, labelAnchor.x, labelAnchor.y, br.x, y + br.y, tex.x + tex.w, tex.y + tex.h, sizeVertex, isSDF, offsets[3][0], offsets[3][1], minFontScaleX, minFontScaleY, elevation);""", 1),
]
