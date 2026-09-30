"""
Build a QGIS-vs-browser comparison gallery for real styles.

Every symbol / label style (from a QGIS style database, optionally filtered
by tag) and every QML file becomes one synthetic layer placed in its own
cell near Budapest (EPSG:3857). The project is exported with the plugin, and
each cell is rendered by QGIS and by the bundled MapLibre build at the same
viewport. The HTML gallery lists the worst matches first, with a pixel
difference score and the export diagnostics of that style.

Usage (QGIS Python, e.g. ``python3`` of your QGIS install)::

    python3 tools/gallery/build_gallery.py --style-db symbology-style.db \
        --tags abel,ábel --qml path/to/*.qml --out /tmp/gallery

Browser capture needs Node and ``npm install`` in ``tests/browser``.
"""

import argparse
import html
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CELL = 400.0            # map units (Web Mercator metres) per style cell
GAP = 200.0
ORIGIN = (2110000.0, 6030000.0)
COLUMNS = 16
EARTH = 40075016.68557849
_APP = None


def init_qgis():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qgis.core import QgsApplication
    app = QgsApplication([], False)
    app.initQgis()
    for candidate in ("/usr/share/qgis/python/plugins", "/usr/lib/qgis/python/plugins"):
        if os.path.isdir(candidate) and candidate not in sys.path:
            sys.path.append(candidate)
    from processing.core.Processing import Processing
    Processing.initialize()
    spec = importlib.util.spec_from_file_location(
        "q2vt_plugin", os.path.join(ROOT, "__init__.py"), submodule_search_locations=[ROOT])
    module = importlib.util.module_from_spec(spec)
    sys.modules["q2vt_plugin"] = module
    spec.loader.exec_module(module)
    return app


# --------------------------------------------------------------------------
# Items
# --------------------------------------------------------------------------
def load_items(args):
    from qgis.core import QgsStyle
    items = []
    if args.style_db:
        style = QgsStyle()
        style.load(args.style_db)
        tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
        symbols, labels = set(), set()
        for tag in tags or [None]:
            if tag is None:
                symbols |= set(style.symbolNames())
                labels |= set(style.labelSettingsNames())
                continue
            tag_id = style.tagId(tag)
            symbols |= set(style.symbolsWithTag(QgsStyle.SymbolEntity, tag_id))
            labels |= set(style.symbolsWithTag(QgsStyle.LabelSettingsEntity, tag_id))
        for name in sorted(symbols):
            symbol = style.symbol(name)
            if symbol is not None:
                items.append({"kind": "symbol", "name": name, "symbol": symbol})
        for name in sorted(labels):
            settings = style.labelSettings(name)
            items.append({"kind": "label", "name": name, "settings": settings})
    for path in args.qml or []:
        items.append({"kind": "qml", "name": os.path.basename(path), "path": path})
    if args.limit:
        items = items[: args.limit]
    return items


def _expressions_of_symbol(symbol):
    exprs = []
    for layer in symbol.symbolLayers():
        props = layer.dataDefinedProperties()
        for key in props.propertyKeys():
            prop = props.property(key)
            if prop.isActive():
                exprs.append(prop.asExpression())
        if layer.layerType() == "GeometryGenerator":
            exprs.append(layer.geometryExpression())
        if layer.subSymbol():
            exprs += _expressions_of_symbol(layer.subSymbol())
    return exprs


def _expressions_of_labels(settings):
    exprs = [settings.fieldName if settings.isExpression else f'"{settings.fieldName}"']
    props = settings.dataDefinedProperties()
    for key in props.propertyKeys():
        prop = props.property(key)
        if prop.isActive():
            exprs.append(prop.asExpression())
    return exprs


_EQUALS = re.compile(r"\"?([A-Za-z_][\w]*)\"?\s*=\s*'([^']*)'")
_BARE_BOOL = re.compile(r"(?:\bnot\s+|\band\s+|\bor\s+|\(\s*)\"?([A-Za-z_]\w*)\"?\s*(?=\)|\s+and\b|\s+or\b|$)",
                        re.IGNORECASE)


def _qml_geometry(path: str) -> str:
    """Geometry type a QML was written for (layerGeometryType or symbol types)."""
    import xml.etree.ElementTree as ET
    root = ET.parse(path).getroot()
    node = root.find("layerGeometryType")
    if node is not None and (node.text or "").strip() in ("0", "1", "2"):
        return ["Point", "LineString", "Polygon"][int(node.text)]
    renderer = root.find("renderer-v2")
    types = [s.get("type") for s in renderer.find("symbols")] if renderer is not None \
        and renderer.find("symbols") is not None else []
    for kind, geom in (("fill", "Polygon"), ("line", "LineString"), ("marker", "Point")):
        if kind in types:
            return geom
    return "Polygon"


def describe_layer(item):
    """(geometry, rules, expressions): rules = [(filter, label)] for features."""
    from qgis.core import QgsRuleBasedRenderer, QgsVectorLayer, QgsCategorizedSymbolRenderer
    if item["kind"] == "symbol":
        symbol = item["symbol"]
        geom = {0: "Point", 1: "LineString", 2: "Polygon"}[int(symbol.type())]
        return geom, [("", "")], _expressions_of_symbol(symbol), None
    if item["kind"] == "label":
        settings = item["settings"]
        placement = getattr(settings.placement, "name", str(settings.placement))
        geom = "LineString" if placement in ("Line", "Curved", "PerimeterCurved") else "Polygon"
        return geom, [("", "")], _expressions_of_labels(settings), None
    geom = _qml_geometry(item["path"])
    probe = QgsVectorLayer(f"{geom}?crs=EPSG:3857", "probe", "memory")
    probe.loadNamedStyle(item["path"])
    exprs, rules = [], [("", "")]
    renderer = probe.renderer()
    if isinstance(renderer, QgsRuleBasedRenderer):
        rules = []
        for rule in renderer.rootRule().descendants():
            flt = rule.filterExpression()
            if flt and flt != "ELSE":
                exprs.append(flt)
            if rule.symbol():
                exprs += _expressions_of_symbol(rule.symbol())
                rules.append((flt, rule.label()))
    elif isinstance(renderer, QgsCategorizedSymbolRenderer):
        rules = [(f'"{renderer.classAttribute()}" = \'{c.value()}\'', c.label())
                 for c in renderer.categories()]
    elif renderer is not None:
        for s in renderer.symbols(__import__("qgis.core").core.QgsRenderContext()):
            exprs += _expressions_of_symbol(s)
    if probe.labelsEnabled() and probe.labeling():
        for provider in [probe.labeling()]:
            if hasattr(provider, "settings"):
                try:
                    exprs += _expressions_of_labels(provider.settings())
                except TypeError:
                    pass
    return geom, rules, exprs, probe


def feature_geometries(geom, x0, y0, n):
    """``n`` geometries of ``geom`` type inside the cell at (x0, y0)."""
    cols = max(1, math.ceil(math.sqrt(n)))
    size = CELL / cols
    out = []
    for k in range(n):
        cx = x0 + (k % cols) * size
        cy = y0 - (k // cols + 1) * size
        s = size
        if geom == "Polygon":
            wkt = (f"POLYGON(({cx + .08*s} {cy + .1*s}, {cx + .92*s} {cy + .1*s}, {cx + .92*s} {cy + .75*s}, "
                   f"{cx + .5*s} {cy + .92*s}, {cx + .08*s} {cy + .75*s}, {cx + .08*s} {cy + .1*s}),"
                   f"({cx + .3*s} {cy + .3*s}, {cx + .45*s} {cy + .3*s}, {cx + .45*s} {cy + .45*s}, "
                   f"{cx + .3*s} {cy + .45*s}, {cx + .3*s} {cy + .3*s}))")
        elif geom == "LineString":
            wkt = (f"LINESTRING({cx + .1*s} {cy + .2*s}, {cx + .4*s} {cy + .8*s}, "
                   f"{cx + .6*s} {cy + .35*s}, {cx + .9*s} {cy + .75*s})")
        else:
            wkt = f"POINT({cx + .5*s} {cy + .5*s})"
        out.append(wkt)
    return out


def build_layer(item, index, data_dir):
    from qgis.core import (QgsCoordinateTransformContext, QgsExpression, QgsFeature, QgsField,
                           QgsGeometry, QgsSingleSymbolRenderer, QgsVectorFileWriter,
                           QgsVectorLayer, QgsVectorLayerSimpleLabeling)
    from qgis.PyQt.QtCore import QVariant
    geom, rules, exprs, probe = describe_layer(item)
    columns, bools = set(), set()
    for text in exprs + [r[0] for r in rules]:
        expression = QgsExpression(text or "")
        columns |= {c for c in expression.referencedColumns() if c and not c.startswith("#")}
    for flt, _ in rules:
        for m in _BARE_BOOL.finditer(flt or ""):
            bools.add(m.group(1))
    row, col = divmod(index, COLUMNS)
    x0 = ORIGIN[0] + col * (CELL + GAP)
    y0 = ORIGIN[1] - row * (CELL + GAP)
    layer = QgsVectorLayer(f"{geom}?crs=EPSG:3857", f"i{index:03d}", "memory")
    fields = sorted(columns)
    layer.dataProvider().addAttributes(
        [QgsField(c, QVariant.Int if c in bools else QVariant.String) for c in fields])
    layer.updateFields()
    geoms = feature_geometries(geom, x0, y0, len(rules))
    features = []
    for k, ((flt, label), wkt) in enumerate(zip(rules, geoms)):
        feature = QgsFeature(layer.fields())
        attrs = {c: (1 if c in bools else f"{k + 1}ő") for c in fields}
        for field, value in _EQUALS.findall(flt or ""):
            if field in attrs and field not in bools:
                attrs[field] = value
        for m in re.finditer(r"not\s+\"?(\w+)\"?", flt or "", re.IGNORECASE):
            if m.group(1) in bools:
                attrs[m.group(1)] = 0
        for field, value in attrs.items():
            feature.setAttribute(field, value)
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    path = os.path.join(data_dir, f"i{index:03d}.gpkg")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, QgsCoordinateTransformContext(), options)
    saved = QgsVectorLayer(path, f"i{index:03d}", "ogr")
    if item["kind"] == "symbol":
        saved.setRenderer(QgsSingleSymbolRenderer(item["symbol"].clone()))
    elif item["kind"] == "label":
        from qgis.core import QgsFillSymbol, QgsLineSymbol
        base = QgsFillSymbol.createSimple({"color": "240,240,240", "outline_color": "150,150,150"}) \
            if geom == "Polygon" else QgsLineSymbol.createSimple({"color": "150,150,150"})
        saved.setRenderer(QgsSingleSymbolRenderer(base))
        settings = item["settings"]
        saved.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        saved.setLabelsEnabled(True)
    else:
        saved.loadNamedStyle(item["path"])
    saved.setScaleBasedVisibility(False)
    center = (x0 + CELL / 2, y0 - CELL / 2)
    return saved, center


# --------------------------------------------------------------------------
# Rendering and scoring
# --------------------------------------------------------------------------
def qgis_render(layer, center, zoom, size, path):
    from qgis.core import QgsMapRendererSequentialJob, QgsMapSettings, QgsRectangle
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QColor
    resolution = EARTH / (512 * 2 ** zoom)
    half = size / 2 * resolution
    settings = QgsMapSettings()
    settings.setLayers([layer])
    settings.setDestinationCrs(layer.crs())
    settings.setExtent(QgsRectangle(center[0] - half, center[1] - half, center[0] + half, center[1] + half))
    settings.setOutputSize(QSize(size, size))
    settings.setOutputDpi(96)
    settings.setBackgroundColor(QColor("white"))
    # Like the map canvas: without the map settings scope, @map_scale and
    # other map variables are NULL in symbol and label expressions.
    from qgis.core import QgsExpressionContext, QgsExpressionContextUtils, QgsProject
    settings.setExpressionContext(QgsExpressionContext([
        QgsExpressionContextUtils.globalScope(),
        QgsExpressionContextUtils.projectScope(QgsProject.instance()),
        QgsExpressionContextUtils.mapSettingsScope(settings),
    ]))
    job = QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    job.renderedImage().save(path)


def score(qgis_png, browser_png):
    from PIL import Image, ImageChops, ImageFilter
    a = Image.open(qgis_png).convert("RGB")
    b = Image.open(browser_png).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size)
    ink_a = a.convert("L").point(lambda v: 255 if v < 245 else 0)
    ink_b = b.convert("L").point(lambda v: 255 if v < 245 else 0)
    grow_a = ink_a.filter(ImageFilter.MaxFilter(3))
    grow_b = ink_b.filter(ImageFilter.MaxFilter(3))
    miss_a = ImageChops.subtract(ink_a, grow_b)   # QGIS ink not near browser ink
    miss_b = ImageChops.subtract(ink_b, grow_a)
    total = sum(1 for v in ink_a.getdata() if v) + sum(1 for v in ink_b.getdata() if v)
    unmatched = sum(1 for v in miss_a.getdata() if v) + sum(1 for v in miss_b.getdata() if v)
    diff = ImageChops.difference(a, b).convert("L")
    diff.save(qgis_png.replace("_qgis.png", "_diff.png"))
    return {"shape": round(unmatched / total, 4) if total else 0.0,
            "ink_qgis": sum(1 for v in ink_a.getdata() if v),
            "ink_browser": sum(1 for v in ink_b.getdata() if v)}


def write_html(out_dir, cards, summary):
    esc = html.escape
    rows = []
    for card in cards:
        diags = "".join(f"<li class='{esc(d['severity'])}'><code>{esc(d['code'])}</code> "
                        f"{esc(d['message'])}</li>" for d in card["diagnostics"])
        rows.append(f"""
<section class="card">
  <h2>{esc(card['name'])} <small>{esc(card['kind'])} · shape mismatch {card['score']['shape']:.1%}</small></h2>
  <div class="imgs">
    <figure><img src="{esc(card['id'])}_qgis.png" alt="QGIS render of {esc(card['name'])}"><figcaption>QGIS</figcaption></figure>
    <figure><img src="{esc(card['id'])}_browser.png" alt="Browser render of {esc(card['name'])}"><figcaption>Browser (MapLibre)</figcaption></figure>
    <figure><img src="{esc(card['id'])}_diff.png" alt="Difference"><figcaption>Difference</figcaption></figure>
  </div>
  <ul class="diags">{diags or '<li class="ok">No diagnostics</li>'}</ul>
</section>""")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Style gallery</title>
<style>
:root{{--bg:#fff;--fg:#1f2328;--muted:#59636e;--line:#d1d9e0;--card:#f6f8fa;--err:#b42318;--warn:#9a6700}}
@media (prefers-color-scheme: dark){{:root{{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;--line:#3d444d;--card:#161b22;--err:#ff7b72;--warn:#d29922}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0;padding:16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:0 0 16px}}
.card h2{{font-size:16px;margin:0 0 8px}} small{{color:var(--muted);font-weight:normal}}
.imgs{{display:flex;flex-wrap:wrap;gap:8px}} figure{{margin:0}} img{{width:260px;max-width:100%;border:1px solid var(--line);background:#fff}}
figcaption{{color:var(--muted);font-size:12px}} .diags{{margin:8px 0 0;padding-left:18px}}
.error{{color:var(--err)}} .warning{{color:var(--warn)}}
</style></head><body><h1>Style gallery</h1><p>{esc(summary)}</p>{''.join(rows)}</body></html>"""
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--style-db")
    parser.add_argument("--tags", default="")
    parser.add_argument("--qml", nargs="*")
    parser.add_argument("--out", required=True)
    parser.add_argument("--zoom", type=float, default=16.25)
    parser.add_argument("--size", type=int, default=400)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    global _APP
    _APP = init_qgis()  # keep a reference: QGIS must outlive the export
    from qgis.core import (QgsCoordinateReferenceSystem, QgsProcessingFeedback, QgsProject,
                           QgsRectangle)
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles

    out = os.path.abspath(args.out)
    shutil.rmtree(out, ignore_errors=True)
    data_dir = os.path.join(out, "data")
    os.makedirs(data_dir)
    items = load_items(args)
    project = QgsProject.instance()
    project.clear()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    project.setEllipsoid("EPSG:7019")
    layers, centers = [], []
    for index, item in enumerate(items):
        layer, center = build_layer(item, index, data_dir)
        item["id"] = f"i{index:03d}"
        layers.append(layer)
        centers.append(center)
    for layer in reversed(layers):
        project.addMapLayer(layer)
    tree_index = {node.layer().id(): i for i, node in
                  enumerate(project.layerTreeRoot().findLayers())}
    xs = [c[0] for c in centers]
    ys = [c[1] for c in centers]
    extent = QgsRectangle(min(xs) - CELL, min(ys) - CELL, max(xs) + CELL, max(ys) + CELL)

    class Feedback(QgsProcessingFeedback):
        def pushInfo(self, info):  # noqa: N802
            print(info, flush=True)

    started = time.time()
    exporter = QGIS2VectorTiles(min_zoom=14, max_zoom=int(math.ceil(args.zoom)), extent=extent,
                                output_dir=os.path.join(out, "export"), feedback=Feedback(),
                                serve=False, background_type=2)
    export_dir = exporter.convert_project_to_vector_tiles()
    print(f"Export: {time.time() - started:.0f} s -> {export_dir}", flush=True)
    diagnostics = exporter.diagnostics.items

    img_dir = os.path.join(out, "images")
    os.makedirs(img_dir)
    views = []
    for item, layer, center in zip(items, layers, centers):
        qgis_render(layer, center, args.zoom, args.size, os.path.join(img_dir, f"{item['id']}_qgis.png"))
        from qgis.core import QgsCoordinateTransform
        to_wgs = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:3857"),
                                        QgsCoordinateReferenceSystem("EPSG:4326"), project)
        point = to_wgs.transform(center[0], center[1])
        views.append({"id": item["id"], "lon": point.x(), "lat": point.y(), "zoom": args.zoom,
                      "width": args.size, "height": args.size})
    with open(os.path.join(out, "views.json"), "w", encoding="utf-8") as f:
        json.dump(views, f)

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
                             capture_output=True, text=True,
                             cwd=os.path.join(ROOT, "tests", "browser"))
        print(run.stdout[-2000:], run.stderr[-2000:], flush=True)
    finally:
        server.kill()

    # Sprite/pattern names -> style layer ids, to attribute sprite diagnostics.
    with open(os.path.join(export_dir, "style", "style.json"), encoding="utf-8") as f:
        style_layers = json.load(f)["layers"]
    image_owner = {}
    for style_layer in style_layers:
        for section, key in (("layout", "icon-image"), ("paint", "fill-pattern"),
                             ("paint", "line-pattern")):
            value = (style_layer.get(section) or {}).get(key)
            if isinstance(value, str):
                image_owner[value] = style_layer["id"]
    cards = []
    for item, layer in zip(items, layers):
        index = tree_index[layer.id()]
        prefix = f"l{index:02d}t"

        def belongs(d):
            owner = image_owner.get(d.component or "", d.component or "")
            return (d.layer_id == layer.id() or (d.layer_id or "").startswith(prefix)
                    or owner.startswith(prefix))
        own = [d.to_dict() for d in diagnostics if belongs(d)]
        result = score(os.path.join(img_dir, f"{item['id']}_qgis.png"),
                       os.path.join(img_dir, f"{item['id']}_browser.png"))
        cards.append({"id": item["id"], "name": item["name"], "kind": item["kind"],
                      "score": result, "diagnostics": own})
    cards.sort(key=lambda c: -c["score"]["shape"])
    with open(os.path.join(img_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(cards, f, indent=1, ensure_ascii=False)
    mean = sum(c["score"]["shape"] for c in cards) / max(1, len(cards))
    write_html(img_dir, cards, f"{len(cards)} styles, zoom {args.zoom}, mean shape mismatch "
               f"{mean:.1%}. Worst first.")
    print(f"Gallery: {os.path.join(img_dir, 'index.html')}")


if __name__ == "__main__":
    main()
