"""Browser memory of a dense web map, for comparing export and viewer
changes: a synthetic town of small parcels (categorized fill with an
outline, parcel numbers) and streets (names), published with the Publish
window's pipeline and opened in headless Chromium (measure.mjs).

    QT_QPA_PLATFORM=offscreen python3 tools/bench/memory_bench.py --out /tmp/bench \\
        [--parcels 20000] [--start-zoom 12.5] [--option output.low_memory=false]

Prints the tile archive's size and the browser's memory (MB) after the start
view and after browsing; ``--keep`` leaves the site in ``--out``.
"""

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "gallery"))
import build_gallery as bg  # noqa: E402  pylint: disable=wrong-import-position

ORIGIN = (2405000.0, 6030000.0)  # EPSG:3857, a Hungarian town's latitude
BLOCK, STREET, DEPTH, WIDTH = 100.0, 20.0, 50.0, 20.0
ZONES = [("Lke", "247,200,160"), ("Lk", "240,170,130"), ("Vt", "230,120,110"), ("Gksz", "180,150,210"),
         ("Ev", "150,200,140"), ("Z", "120,190,110"), ("Má", "230,230,160"), ("Köu", "220,220,220")]


def town(count):
    """Parcels (polygons with jittered extra vertices) and streets (lines)."""
    rng = random.Random(7)
    per_block = int(BLOCK / WIDTH) * int(BLOCK / DEPTH)
    side = math.ceil(math.sqrt(count / per_block))
    parcels, streets = [], []
    step = BLOCK + STREET
    for bx in range(side):
        for by in range(side):
            x0, y0 = ORIGIN[0] + bx * step, ORIGIN[1] + by * step
            for i in range(int(BLOCK / WIDTH)):
                for j in range(int(BLOCK / DEPTH)):
                    if len(parcels) >= count:
                        break
                    a, b = x0 + i * WIDTH, y0 + j * DEPTH
                    corners = [(a, b), (a + WIDTH, b), (a + WIDTH, b + DEPTH), (a, b + DEPTH)]
                    ring = []
                    for (p, q), (r, s) in zip(corners, corners[1:] + corners[:1]):
                        ring.append((p, q))
                        ring.append(((p + r) / 2 + rng.uniform(-1, 1), (q + s) / 2 + rng.uniform(-1, 1)))
                    zone = ZONES[(bx * 7 + by * 3 + i) % len(ZONES)][0]
                    parcels.append((ring + ring[:1], f"{len(parcels) + 100}/{j + 1}", zone))
    length = side * step
    for k in range(side + 1):
        street = ORIGIN[0] - STREET / 2 + k * step
        streets.append(([(street, ORIGIN[1] - STREET), (street, ORIGIN[1] + length)], f"{k + 1}. utca"))
        street = ORIGIN[1] - STREET / 2 + k * step
        streets.append(([(ORIGIN[0] - STREET, street), (ORIGIN[0] + length, street)], f"{k + 1}. sor"))
    return parcels, streets, length


def build_project(out, count):
    from qgis.core import (Qgis, QgsCategorizedSymbolRenderer, QgsFeature, QgsField, QgsFillSymbol,  # pylint: disable=import-outside-toplevel
                           QgsGeometry, QgsLineSymbol, QgsPalLayerSettings, QgsPointXY, QgsProject,
                           QgsRendererCategory, QgsSingleSymbolRenderer, QgsTextFormat, QgsVectorFileWriter,
                           QgsVectorLayer, QgsVectorLayerSimpleLabeling)
    from qgis.PyQt.QtCore import QVariant  # pylint: disable=import-outside-toplevel
    parcels, streets, length = town(count)
    project = QgsProject.instance()
    project.clear()

    def saved(memory, name):
        path = os.path.join(out, f"{name}.gpkg")
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        QgsVectorFileWriter.writeAsVectorFormatV3(memory, path, project.transformContext(), options)
        return QgsVectorLayer(path, memory.name(), "ogr")

    memory = QgsVectorLayer("Polygon?crs=EPSG:3857", "Földrészletek", "memory")
    memory.dataProvider().addAttributes([QgsField("hrsz", QVariant.String), QgsField("zone", QVariant.String),
                                         QgsField("terulet", QVariant.Double), QgsField("note", QVariant.String)])
    memory.updateFields()
    features = []
    for ring, hrsz, zone in parcels:
        feature = QgsFeature(memory.fields())
        geometry = QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in ring]])
        feature.setAttributes([hrsz, zone, geometry.area(), "megjegyzés " * 3])
        feature.setGeometry(geometry)
        features.append(feature)
    memory.dataProvider().addFeatures(features)
    parcel_layer = saved(memory, "parcels")
    categories = [QgsRendererCategory(zone, QgsFillSymbol.createSimple(
        {"color": color, "outline_color": "60,60,60", "outline_width": "0.2"}), zone) for zone, color in ZONES]
    parcel_layer.setRenderer(QgsCategorizedSymbolRenderer("zone", categories))
    settings = QgsPalLayerSettings()
    settings.fieldName = "hrsz"
    settings.placement = Qgis.LabelPlacement.Horizontal
    text = QgsTextFormat()
    text.setSize(3)
    text.setSizeUnit(Qgis.RenderUnit.MapUnits)
    settings.setFormat(text)
    parcel_layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    parcel_layer.setLabelsEnabled(True)

    memory = QgsVectorLayer("LineString?crs=EPSG:3857", "Utcák", "memory")
    memory.dataProvider().addAttributes([QgsField("nev", QVariant.String)])
    memory.updateFields()
    features = []
    for line, name in streets:
        feature = QgsFeature(memory.fields())
        feature.setAttributes([name])
        feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in line]))
        features.append(feature)
    memory.dataProvider().addFeatures(features)
    street_layer = saved(memory, "streets")
    street_layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple(
        {"color": "255,255,255", "width": "3", "width_unit": "MapUnit"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "nev"
    settings.placement = Qgis.LabelPlacement.Line
    settings.setFormat(QgsTextFormat())
    street_layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    street_layer.setLabelsEnabled(True)
    for layer in (street_layer, parcel_layer):
        project.addMapLayer(layer)
    return project, parcel_layer, street_layer, length


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--parcels", type=int, default=20000)
    parser.add_argument("--start-zoom", type=float, default=12.5)
    parser.add_argument("--option", action="append", default=[],
                        help="profile setting, e.g. output.low_memory=false")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--repeat", type=int, default=1,
                        help="measure this many times (fresh browser each), report each run")
    args = parser.parse_args(argv)
    app = bg.init_qgis()  # noqa: F841  keep a reference
    from qgis.core import QgsRectangle  # pylint: disable=import-outside-toplevel
    from q2vt_plugin.src.publishing.controller import export_local  # pylint: disable=import-error,import-outside-toplevel
    from q2vt_plugin.src.publishing.models import LayerConfig, PopupField, PublicationProfile  # pylint: disable=import-error,import-outside-toplevel
    from q2vt_plugin.src.publishing.preview_server import PreviewServer  # pylint: disable=import-error,import-outside-toplevel
    out = os.path.abspath(args.out)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    project, parcels, streets, length = build_project(out, args.parcels)
    profile = PublicationProfile(title="Memory bench", slug="bench", locale="hu")
    profile.view.min_zoom, profile.view.max_zoom = 11, 16
    profile.output.local_directory = os.path.join(out, "site")
    profile.layers = [LayerConfig(parcels.id(), initially_visible=True, key_fields=["hrsz"],
                                  popup_fields=[PopupField("hrsz"), PopupField("zone")], search_fields=["hrsz"]),
                      LayerConfig(streets.id(), initially_visible=True)]
    for option in args.option:
        path, value = option.split("=", 1)
        section, name = path.split(".")
        setattr(getattr(profile, section), name, json.loads(value))
    extent = QgsRectangle(ORIGIN[0] - 50, ORIGIN[1] - 50, ORIGIN[0] + length + 50, ORIGIN[1] + length + 50)
    result = export_local(project, profile, extent)
    archive = sum(os.path.getsize(os.path.join(folder, name)) for folder, _, names in
                  os.walk(result.publication_dir) for name in names if name.endswith(".pmtiles"))
    centre = extent.center()
    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform  # pylint: disable=import-outside-toplevel
    lonlat = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:3857"),
                                    QgsCoordinateReferenceSystem("EPSG:4326"), project).transform(centre)
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        url = server.url(f"{profile.slug}/index.html") + \
            f"#v=1&map={args.start_zoom:.2f}/{lonlat.y():.6f}/{lonlat.x():.6f}"
        for _ in range(max(1, args.repeat)):
            run = subprocess.run(["node", os.path.join(HERE, "measure.mjs"), url], capture_output=True,
                                 text=True, timeout=900, check=False)
            if run.returncode:
                raise SystemExit(run.stderr[-3000:])
            measured = json.loads(run.stdout.strip().splitlines()[-1])
            report = {"parcels": args.parcels, "archiveMB": round(archive / 1048576, 1), **measured}
            print(json.dumps(report), flush=True)
    if not args.keep:
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    main()
