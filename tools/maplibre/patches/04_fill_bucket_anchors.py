"""Fill buckets of style layers flagged ``"q2vt:pattern-anchor": "feature"``
or ``"feature-clip"`` carry each feature's pattern anchor (its q2vt_pat_x /
q2vt_pat_y properties, EPSG:3857 metres) per vertex, in tile units, for the
fill pattern shaders (patch 03).

The bucket is built in the worker and uploaded on the main thread; both
bundles hold its code, so both are patched.
"""

EDITS = [
    ("both", """
        this.segments2 = new SegmentVector();
        this.stateDependentLayerIds = this.layers.filter((l) => l.isStateDependent()).map((l) => l.id);
    }
    populate(features, options, canonical) {
        this.hasDependencies = hasPattern("fill", this.layers, options);""", """
        this.segments2 = new SegmentVector();
        this.q2anchorArray = this.layers.some((l) => l.metadata && /^feature/.test(l.metadata["q2vt:pattern-anchor"] || "")) ? new StructArrayLayout2f8() : null;
        this.stateDependentLayerIds = this.layers.filter((l) => l.isStateDependent()).map((l) => l.id);
    }
    populate(features, options, canonical) {
        this.hasDependencies = hasPattern("fill", this.layers, options);""", 1),
    ("both", """
            this.indexBuffer2 = context.createIndexBuffer(this.indexArray2);
        }
        this.programConfigurations.upload(context);""", """
            this.indexBuffer2 = context.createIndexBuffer(this.indexArray2);
            if (this.q2anchorArray && this.q2anchorArray.length) this.q2anchorBuffer = context.createVertexBuffer(this.q2anchorArray, [{
                name: "a_q2anchor",
                type: "Float32",
                components: 2,
                offset: 0
            }]);
        }
        this.programConfigurations.upload(context);""", 1),
    ("both", """
        this.segments.destroy();
        this.segments2.destroy();
    }
    addFeature(feature, geometry, index, canonical, imagePositions, subdivisionGranularity) {""", """
        this.segments.destroy();
        this.segments2.destroy();
        if (this.q2anchorBuffer) this.q2anchorBuffer.destroy();
    }
    q2anchor(feature, canonical) {
        const properties = feature.properties || {};
        const x = properties.q2vt_pat_x, y = properties.q2vt_pat_y;
        if (x == null || y == null || !isFinite(x) || !isFinite(y)) return [1e30, 1e30];
        const scale = Math.pow(2, canonical.z), circumference = 40075016.68557849;
        return [((x / circumference + .5) * scale - canonical.x) * 8192, ((.5 - y / circumference) * scale - canonical.y) * 8192];
    }
    addFeature(feature, geometry, index, canonical, imagePositions, subdivisionGranularity) {
        const anchors = this.q2anchorArray, anchor = anchors ? this.q2anchor(feature, canonical) : null;""", 1),
    ("both", """
            fillLargeMeshArrays((x, y) => {
                vertexArray.emplaceBack(x, y);
            }, this.segments, this.layoutVertexArray, this.indexArray, subdivided.verticesFlattened, subdivided.indicesTriangles, this.segments2, this.indexArray2, subdivided.indicesLineList);""", """
            fillLargeMeshArrays((x, y) => {
                vertexArray.emplaceBack(x, y);
                if (anchors) anchors.emplaceBack(anchor[0], anchor[1]);
            }, this.segments, this.layoutVertexArray, this.indexArray, subdivided.verticesFlattened, subdivided.indicesTriangles, this.segments2, this.indexArray2, subdivided.indicesLineList);""", 1),
]
