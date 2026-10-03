"""'Label every feature': the manifest marks the layer, and the label tiles of
its polygons carry the roomiest point (pole of inaccessibility), its free
radius and the polygon's main angle (EPSG:3857), so the viewer puts a label
that cannot fit there without searching; other layers carry none."""

import json
import os
import sqlite3
import sys

from publishing import mvt
from publishing.controller import export_local
from publishing.provenance import layer_logical_id

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_publishing_basemap_themes import EXTENT, _setup  # noqa: E402  pylint: disable=wrong-import-position


def test_label_every_feature_marks_the_layer_and_precomputes_spots(tmp_path):
    project, profile, parcels, second = _setup(tmp_path)
    profile.layers[0].label_always = True
    result = export_local(project, profile, EXTENT)
    manifest = json.load(open(os.path.join(result.release.release_dir, "manifest.json"), encoding="utf-8"))
    flags = {layer["id"]: layer.get("labelAlways") for layer in manifest["layers"]}
    assert flags[layer_logical_id(parcels.id())] is True and flags[layer_logical_id(second.id())] is False
    keys = {}
    with sqlite3.connect(os.path.join(result.export_dir, "tiles.mbtiles")) as conn:
        for (data,) in conn.execute("SELECT tile_data FROM tiles"):
            for name, layer in mvt.decode(data).items():
                for feature in layer["features"]:
                    keys.setdefault(name, set()).update(feature["properties"])
    def prefixes(layer):  # source layer names start with the layer's index ("l00")
        return {c["sourceLayer"][:3] for c in manifest["components"]
                if c.get("sourceLayer") and c["layerId"] == layer_logical_id(layer.id())}
    with_pole = {name for name, fields in keys.items() if {"q2vt_pole_x", "q2vt_pole_y", "q2vt_pole_r",
                                                           "q2vt_pole_a"} <= fields}
    assert with_pole and {name[:3] for name in with_pole} <= prefixes(parcels)
    assert not {name[:3] for name in with_pole} & prefixes(second)
