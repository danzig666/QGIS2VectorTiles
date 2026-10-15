"""Animated markers (QgsAnimatedMarkerSymbolLayer): every frame of the image
is rendered by QGIS into the sprite sheet (the frame QGIS draws at the
marker's frame rate) as one icon layer per frame; the first is drawn, the
others transparent until the web viewer shows them in turn."""

import json
import os
import sys

from qgis.core import (QgsAnimatedMarkerSymbolLayer, QgsFeature, QgsGeometry, QgsMarkerSymbol,
                       QgsSingleSymbolRenderer, QgsVectorLayer)
from qgis.PyQt.QtGui import QImage

from q2vt_fixtures import to_geopackage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_end_to_end import export  # noqa: E402,F401  pylint: disable=wrong-import-position,unused-import

COLOURS = [(220, 0, 0), (0, 160, 0), (0, 0, 220)]


def _gif(path):
    from PIL import Image, ImageDraw  # pylint: disable=import-outside-toplevel
    frames = []
    for colour in COLOURS:
        image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        ImageDraw.Draw(image).ellipse((2, 2, 38, 38), fill=colour + (255,))
        frames.append(image)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=250, loop=0, disposal=2)
    return str(path)


def _points(tmp_path, gif, rate=4.0):
    layer = QgsVectorLayer("Point?crs=EPSG:3857", "beacons", "memory")
    features = []
    for x, y in ((2120000, 6020000), (2122000, 6022000)):
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromWkt(f"POINT({x} {y})"))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer = to_geopackage(layer, str(tmp_path / "beacons.gpkg"))
    marker = QgsAnimatedMarkerSymbolLayer(gif, 6)
    marker.setFrameRate(rate)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol([marker])))
    return layer


def test_every_frame_becomes_an_icon_layer(export, tmp_path):  # noqa: F811
    layer = _points(tmp_path, _gif(tmp_path / "blink.gif"))
    _, result = export(layer)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    frames = [l for l in style["layers"] if "q2vt:frame" in (l.get("metadata") or {})]
    assert sorted(l["metadata"]["q2vt:frame"]["index"] for l in frames) == [0, 1, 2]
    meta = frames[0]["metadata"]["q2vt:frame"]
    assert meta["count"] == 3 and meta["fps"] == 4.0 and meta["opacity"] == 1
    assert {l["metadata"]["q2vt:frame"]["group"] for l in frames} == {frames[0]["id"]}
    by_index = {l["metadata"]["q2vt:frame"]["index"]: l for l in frames}
    assert by_index[0]["paint"]["icon-opacity"] == 1
    assert by_index[1]["paint"]["icon-opacity"] == 0 and by_index[2]["paint"]["icon-opacity"] == 0
    # Each frame's image is the frame QGIS draws: red, green, blue.
    index = json.load(open(os.path.join(result, "style", "sprite", "sprite.json"), encoding="utf-8"))
    sheet = QImage(os.path.join(result, "style", "sprite", "sprite.png"))
    for number, colour in enumerate(COLOURS):
        entry = index[by_index[number]["layout"]["icon-image"]]
        pixel = sheet.pixelColor(entry["x"] + entry["width"] // 2, entry["y"] + entry["height"] // 2)
        assert all(abs(a - b) < 40 for a, b in zip((pixel.red(), pixel.green(), pixel.blue()), colour)), number


def test_a_still_image_is_a_plain_marker(export, tmp_path):  # noqa: F811
    from PIL import Image  # pylint: disable=import-outside-toplevel
    still = tmp_path / "still.png"
    Image.new("RGBA", (20, 20), (200, 0, 0, 255)).save(still)
    layer = _points(tmp_path, str(still))
    _, result = export(layer)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    icons = [l for l in style["layers"] if "icon-image" in l.get("layout", {})]
    assert len(icons) == 1 and "q2vt:frame" not in (icons[0].get("metadata") or {})
