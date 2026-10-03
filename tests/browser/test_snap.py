"""snap.mjs: measurement snapping - the nearest vertex first, else the nearest
point of an edge; within the radius only; tile cuts (a polygon cut at a
tile edge) are neither corners nor edges; only the snap layers count."""

import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODULE = os.path.join(ROOT, "resources", "web_viewer", "snap.mjs")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")


def _run(body):
    script = f"import * as s from {json.dumps('file://' + MODULE)};\n{body}"
    run = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(run.stdout)


# A fake map: world units * 1000 = screen pixels (z0, one tile of extent 1000),
# so tile coordinates are pixels. One parcel square cut by the tile's right
# edge (x = 1000), and a line of another layer.
FAKE = """
const tileFeature = (type, rings, layer) => ({ _z: 0, _x: 0, _y: 0, sourceLayer: layer, id: rings.length,
  properties: {}, layer: { id: layer },
  _vectorTileFeature: { type, extent: 1000, loadGeometry: () => rings.map((r) => r.map(([x, y]) => ({ x, y }))) } });
const parcel = tileFeature(3, [[[600, 100], [1000, 100], [1000, 400], [600, 400], [600, 100]]], "parcels");
const road = tileFeature(2, [[[100, 700], [900, 700]]], "roads");
const map = {
  getLayer: (id) => ({ parcels_fill: { type: "fill" }, parcels_label: { type: "symbol" }, roads_line: { type: "line" } })[id],
  project: ([lng, lat]) => {
    const x = (lng + 180) / 360, y = (1 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / Math.PI) / 2;
    return { x: x * 1000, y: y * 1000 };
  },
  queryRenderedFeatures: (box, { layers }) => [parcel, parcel, road].filter((f) =>
    layers.includes(f.sourceLayer === "parcels" ? "parcels_fill" : "roads_line")),
};
const manifest = { layers: [{ id: "P", title: "Parcels", snap: true }, { id: "R", title: "Roads", snap: false }],
  components: [{ layerId: "P", styleLayerIds: ["parcels_fill", "parcels_label"] }, { layerId: "R", styleLayerIds: ["roads_line"] }] };
const snapper = new s.Snapper({ map, manifest });
const px = (r) => r && { kind: r.kind, at: Object.values(map.project(r.lngLat)).map((v) => Math.round(v * 100) / 100) };
"""


def test_vertex_first_then_edge_within_the_radius():
    out = _run(FAKE + """console.log(JSON.stringify({
      layers: snapper.styleLayers, titles: snapper.titles,
      corner: px(snapper.snap([608, 105], 12)),         // near the corner (600, 100)
      edge: px(snapper.snap([700, 108], 12)),           // near the top edge, far from corners
      far: snapper.snap([700, 250], 12),                // inside, far from edges
      cutCorner: px(snapper.snap([996, 104], 12)),      // near (1000, 100): a cut corner
      cutEdge: snapper.snap([995, 250], 12),            // near the tile edge x = 1000: a cut
      road: snapper.snap([500, 702], 12),               // a layer not marked for snapping
    }));""")
    assert out["layers"] == ["parcels_fill"] and out["titles"] == ["Parcels"]  # no label layers
    assert out["corner"] == {"kind": "vertex", "at": [600, 100]}
    assert out["edge"] == {"kind": "edge", "at": [700, 100]}
    assert out["far"] is None and out["cutEdge"] is None and out["road"] is None
    # Not the cut corner (1000, 100): the real top edge next to it instead.
    assert out["cutCorner"]["kind"] == "edge" and out["cutCorner"]["at"][1] == 100 and out["cutCorner"]["at"][0] < 1000


def test_clip_segment_and_world_coordinates():
    out = _run("""console.log(JSON.stringify({
      clipped: s.clipSegment([-5, 5], [15, 5], [0, 0, 10, 10]),
      outside: s.clipSegment([-5, -5], [-1, -1], [0, 0, 10, 10]),
      origin: s.worldToLngLat([0.5, 0.5]), corner: s.worldToLngLat([0, 0])[0] }));""")
    assert out["clipped"] == [[0, 5], [10, 5]] and out["outside"] is None
    assert out["origin"] == pytest.approx([0, 0], abs=1e-9) and out["corner"] == -180
