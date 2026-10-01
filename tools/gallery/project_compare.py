"""
Compare a real QGIS project with its export, layer by layer: QGIS renders the
layers in the project CRS (where map-unit sizes are measured, as on the
user's canvas) at the scale of each browser zoom, the browser renders the
exported package at that zoom, and a comparison page is written
(compare_page.py: side by side, overlay, swipe, blink).

Usage::

    python3 tools/gallery/project_compare.py project.qgs --out /tmp/cmp \
        --layers "Földrészletek,Szabályozás övezetkódok" --zooms 15,16.5,18 \
        [--center 20.22,48.16 | --center-layer Épületek] [--size 600] [--max-zoom 18]

``--layers "*"`` compares the whole map (every visible layer, in project
order) at one place, e.g. ``--center-layer Épületek``.

Without ``--center`` each layer is viewed at a feature near the middle of
its extent. Only the listed layers are exported (the others are removed
from the in-memory project; the .qgs file is not changed).
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_gallery as bg  # noqa: E402  pylint: disable=wrong-import-position
import compare_page  # noqa: E402  pylint: disable=wrong-import-position

ROOT = bg.ROOT


def view_center(layer, project):
    """lon/lat of a feature close to the middle of the layer's extent."""
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,  # pylint: disable=import-outside-toplevel
                           QgsFeatureRequest, QgsGeometry, QgsPointXY, QgsRectangle)
    middle = layer.extent().center()
    best = None
    box = QgsRectangle(layer.extent())
    box.scale(0.2)
    for feature in layer.getFeatures(QgsFeatureRequest().setFilterRect(box).setLimit(200)):
        geometry = feature.geometry()
        if geometry.isEmpty():
            continue
        point = geometry.pointOnSurface().asPoint()
        distance = point.distance(middle)
        if best is None or distance < best[0]:
            best = (distance, point)
    point = best[1] if best else middle
    to_wgs = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), project)
    wgs = to_wgs.transform(QgsPointXY(point))
    return wgs.x(), wgs.y()


def qgis_render(layers, project, lon, lat, zoom, size, path):
    """QGIS render in the project CRS at the browser's ground resolution."""
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,  # pylint: disable=import-outside-toplevel
                           QgsExpressionContext, QgsExpressionContextUtils,
                           QgsMapRendererSequentialJob, QgsMapSettings, QgsPointXY, QgsRectangle)
    from qgis.PyQt.QtCore import QSize  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtGui import QColor  # pylint: disable=import-outside-toplevel
    crs = project.crs()
    ground = bg.EARTH / (512 * 2 ** zoom) * math.cos(math.radians(lat))  # metres per pixel
    center = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), crs,
                                    project).transform(QgsPointXY(lon, lat))
    if crs.isGeographic():
        ground /= 111319.49
    half = size / 2 * ground
    settings = QgsMapSettings()
    settings.setLayers(layers)
    settings.setDestinationCrs(crs)
    settings.setTransformContext(project.transformContext())
    settings.setExtent(QgsRectangle(center.x() - half, center.y() - half,
                                    center.x() + half, center.y() + half))
    settings.setOutputSize(QSize(size, size))
    settings.setOutputDpi(96)
    settings.setBackgroundColor(QColor("white"))
    settings.setExpressionContext(QgsExpressionContext([
        QgsExpressionContextUtils.globalScope(),
        QgsExpressionContextUtils.projectScope(project),
        QgsExpressionContextUtils.mapSettingsScope(settings),
    ]))
    settings.setLabelingEngineSettings(project.labelingEngineSettings())
    job = QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    job.renderedImage().save(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("project")
    parser.add_argument("--out", required=True)
    parser.add_argument("--layers", required=True, help="comma-separated layer names")
    parser.add_argument("--zooms", default="15,16.5,18")
    parser.add_argument("--center", default="", help="lon,lat (default: per layer)")
    parser.add_argument("--center-layer", default="", help="view a feature of this layer")
    parser.add_argument("--size", type=int, default=600)
    parser.add_argument("--max-zoom", type=int, default=0)
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    zooms = [float(z) for z in args.zooms.split(",") if z.strip()]
    names = [n.strip() for n in args.layers.split(",") if n.strip()]

    app = bg.init_qgis()  # noqa: F841  keep a reference: QGIS must outlive the export
    from qgis.core import QgsProcessingFeedback, QgsProject, QgsRectangle  # pylint: disable=import-outside-toplevel
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error,import-outside-toplevel

    out = os.path.abspath(args.out)
    shutil.rmtree(out, ignore_errors=True)
    img_dir = os.path.join(out, "images")
    os.makedirs(img_dir)
    project = QgsProject.instance()
    if not project.read(args.project):
        raise SystemExit(f"Cannot read {args.project}")
    whole = names == ["*"]  # the whole map, drawn as in the project
    if whole:
        keep = [node.layer() for node in project.layerTreeRoot().findLayers() if node.isVisible()]
    else:
        keep = []
        for name in names:
            found = project.mapLayersByName(name)
            if not found:
                raise SystemExit(f"No layer named {name!r}")
            keep.append(found[0])
        for layer_id, layer in list(project.mapLayers().items()):
            if layer not in keep:
                project.removeMapLayer(layer_id)
    centers = {}
    for layer in keep:
        if args.center:
            centers[layer.id()] = tuple(float(v) for v in args.center.split(","))
        elif args.center_layer:
            centers[layer.id()] = view_center(project.mapLayersByName(args.center_layer)[0], project)
        else:
            centers[layer.id()] = view_center(layer, project)

    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform  # pylint: disable=import-outside-toplevel
    to_merc = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                     QgsCoordinateReferenceSystem("EPSG:3857"), project)
    extent = QgsRectangle()
    for lon, lat in centers.values():
        point = to_merc.transform(lon, lat)
        reach = args.size * bg.EARTH / (512 * 2 ** min(zooms))
        extent.combineExtentWith(QgsRectangle(point.x() - reach, point.y() - reach,
                                              point.x() + reach, point.y() + reach))

    class Feedback(QgsProcessingFeedback):
        def pushInfo(self, info):  # noqa: N802
            print(info, flush=True)

    started = time.time()
    max_zoom = args.max_zoom or int(math.ceil(max(zooms)))
    exporter = QGIS2VectorTiles(min_zoom=max(0, int(math.floor(min(zooms))) - 1), max_zoom=max_zoom,
                                extent=extent, output_dir=os.path.join(out, "export"),
                                feedback=Feedback(), serve=False, background_type=2)
    export_dir = exporter.convert_project_to_vector_tiles()
    print(f"Export: {time.time() - started:.0f} s -> {export_dir}", flush=True)

    views, cards = [], []
    if whole:  # one card: every visible layer, in project order
        class Whole:  # minimal stand-in for a layer in the loops below
            def __init__(self, first):
                self._id = first.id()

            def id(self):
                return self._id

            @staticmethod
            def name():
                return "Whole map"

            @staticmethod
            def geometryType():
                return 2
        render_layers = list(keep)
        keep = [Whole(keep[0])]
    for index, layer in enumerate(keep):
        lon, lat = centers[layer.id()]
        per_zoom = []
        for k, zoom in enumerate(zooms):
            view_id = f"p{index:02d}_z{k}"
            qgis_render(render_layers if whole else [layer], project, lon, lat, zoom, args.size,
                        os.path.join(img_dir, f"{view_id}_qgis.png"))
            views.append({"id": view_id, "lon": lon, "lat": lat, "zoom": zoom,
                          "width": args.size, "height": args.size, "layer": layer.name()})
            per_zoom.append({"id": view_id, "zoom": zoom})
        cards.append((layer, per_zoom))

    # The browser shows every exported layer; one export per layer keeps
    # views clean, so hide the other layers' style layers per view instead.
    with open(os.path.join(export_dir, "style", "style.json"), encoding="utf-8") as handle:
        style = json.load(handle)
    # Dataset -> QGIS layer name, from the export log ("Rule group <id>: '{layer: NAME,...").
    owner = {}
    with open(os.path.join(export_dir, "export_log.txt"), encoding="utf-8") as handle:
        for line in handle:
            match = re.search(r"Rule group (\S+): '\{layer: (.*?),type:", line)
            if match:
                owner[match.group(1)] = match.group(2)
    for index, (layer, per_zoom) in enumerate(cards):
        if whole:
            continue  # every style layer stays visible
        visible = [l["id"] for l in style["layers"]
                   if owner.get(l.get("source-layer") or "") == layer.name()]
        for view in views:
            if view["id"].startswith(f"p{index:02d}_"):
                view["only"] = visible
    with open(os.path.join(out, "views.json"), "w", encoding="utf-8") as handle:
        json.dump(views, handle)

    server = subprocess.Popen([sys.executable, os.path.join(export_dir, "utils", "tiles_server.py"),
                               "--port", str(args.port)], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://localhost:{args.port}/style/style.json", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        run = subprocess.run(["node", os.path.join(ROOT, "tests", "browser", "gallery_capture.mjs"),
                              export_dir, str(args.port), os.path.join(out, "views.json"), img_dir],
                             capture_output=True, text=True, cwd=os.path.join(ROOT, "tests", "browser"))
        print(run.stdout[-2000:], run.stderr[-2000:], flush=True)
    finally:
        server.kill()

    results = []
    for layer, per_zoom in cards:
        for view in per_zoom:
            view["score"] = bg.score(os.path.join(img_dir, f"{view['id']}_qgis.png"),
                                     os.path.join(img_dir, f"{view['id']}_browser.png"))
        shape = round(sum(v["score"]["shape"] for v in per_zoom) / len(per_zoom), 4)
        results.append({"id": per_zoom[0]["id"], "name": layer.name(), "kind": "layer",
                        "geometry": ["Point", "LineString", "Polygon"][int(getattr(
                            layer.geometryType(), "value", layer.geometryType()))],
                        "score": {"shape": shape}, "diagnostics": [], "views": per_zoom})
    with open(os.path.join(img_dir, "results.json"), "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=1, ensure_ascii=False)
    page = os.path.join(out, "compare.html")
    compare_page.build(out, page, minimum=-1, title=f"{os.path.basename(args.project)}: QGIS vs browser")
    for card in results:
        print(f"{card['name']}: {card['score']['shape']:.1%}")
    print(page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
