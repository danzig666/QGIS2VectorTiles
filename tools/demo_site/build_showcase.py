"""Showcase project for the README video: complex QGIS symbology on the QGIS
training data (Swellendam, GPL-2.0, from OpenStreetMap).

Fills: gradient, shapeburst, line-pattern hatches, point-pattern fills with
SVG icons, geometry-generator shadows. Lines: road casings with symbol
levels, lineburst rivers with flow arrows, dashed/dotted paths, one-way
arrows, an ArrowLine walking route, an offset park boundary band. Markers:
SVG icons on circles. Labels: curved, buffered, letter-spaced, background
shapes."""
import os
import shutil
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from qgis.core import (Qgis, QgsApplication, QgsArrowSymbolLayer, QgsCategorizedSymbolRenderer,
                       QgsCentroidFillSymbolLayer, QgsCoordinateReferenceSystem, QgsFeature, QgsField,
                       QgsFillSymbol, QgsGeometry, QgsGeometryGeneratorSymbolLayer,
                       QgsGradientFillSymbolLayer, QgsLineburstSymbolLayer, QgsLinePatternFillSymbolLayer,
                       QgsLineSymbol, QgsMarkerLineSymbolLayer, QgsMarkerSymbol, QgsPalLayerSettings,
                       QgsPointPatternFillSymbolLayer, QgsPointXY, QgsProject, QgsProperty,
                       QgsRendererCategory, QgsRuleBasedLabeling, QgsRuleBasedRenderer,
                       QgsShapeburstFillSymbolLayer, QgsSimpleFillSymbolLayer, QgsSimpleLineSymbolLayer,
                       QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer, QgsSvgMarkerSymbolLayer,
                       QgsTextBackgroundSettings, QgsTextBufferSettings, QgsTextFormat,
                       QgsUnitTypes, QgsVectorFileWriter, QgsVectorLayer,
                       QgsVectorLayerSimpleLabeling, QgsCoordinateTransform, QgsSymbolLayer)
from qgis.PyQt.QtCore import QPointF, QSizeF, Qt, QVariant
from qgis.PyQt.QtGui import QColor, QFont

# Usage: python3 build_showcase.py <QGIS-Training-Data/exercise_data> <work dir>
# Writes <work dir>/showcase/showcase.qgs with copies of the data it uses.
if len(sys.argv) != 3:
    sys.exit(__doc__.split("\n\n")[0] + "\nUsage: build_showcase.py <exercise_data dir> <work dir>")
SRC = os.path.abspath(sys.argv[1])
OUT = os.path.join(os.path.abspath(sys.argv[2]), "showcase")

app = QgsApplication([], False)
app.initQgis()
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT + "/shapefile")
for name in ("training_data.gpkg", "landuse.sqlite"):
    shutil.copy(f"{SRC}/{name}", OUT)
for name in os.listdir(SRC + "/shapefile"):
    shutil.copy(f"{SRC}/shapefile/{name}", OUT + "/shapefile")
shutil.copy(f"{SRC}/symbols/orchard.svg", OUT)
SVG = os.path.join(QgsApplication.pkgDataPath(), "svg")
FONT = "Open Sans"
MM = QgsUnitTypes.RenderMillimeters if hasattr(QgsUnitTypes, "RenderMillimeters") else Qgis.RenderUnit.Millimeters
MAPU = Qgis.RenderUnit.MapUnits

p = QgsProject.instance()
p.clear()
p.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
p.setTitle("Swellendam")
p.setBackgroundColor(QColor("#f3efe6"))


def layer(path, name):
    lyr = QgsVectorLayer(path, name, "ogr")
    assert lyr.isValid(), path
    return lyr


def solid(color, outline=None, width=0.2):
    return QgsSimpleFillSymbolLayer.create({"color": color, "outline_color": outline or "0,0,0,0",
                                           "outline_width": str(width),
                                           "outline_style": "solid" if outline else "no"})


def fill_symbol(*layers):
    sym = QgsFillSymbol()
    sym.deleteSymbolLayer(0)
    for lyr in layers:
        sym.appendSymbolLayer(lyr)
    return sym


def line_symbol(*layers):
    sym = QgsLineSymbol()
    sym.deleteSymbolLayer(0)
    for lyr in layers:
        sym.appendSymbolLayer(lyr)
    return sym


def simple_line(color, width, cap=Qt.PenCapStyle.RoundCap, dash=None, offset=0.0, rendering_pass=0):
    lyr = QgsSimpleLineSymbolLayer(QColor(color), width)
    lyr.setPenCapStyle(cap)
    lyr.setPenJoinStyle(Qt.PenJoinStyle.RoundJoin)
    if dash:
        lyr.setUseCustomDashPattern(True)
        lyr.setCustomDashVector(dash)
    if offset:
        lyr.setOffset(offset)
    lyr.setRenderingPass(rendering_pass)
    return lyr


def hatch(color, angle, distance, width=0.25):
    lyr = QgsLinePatternFillSymbolLayer()
    lyr.setLineAngle(angle)
    lyr.setDistance(distance)
    lyr.setLineWidth(width)
    lyr.setColor(QColor(color))
    lyr.subSymbol().symbolLayer(0).setColor(QColor(color))
    lyr.subSymbol().symbolLayer(0).setWidth(width)
    return lyr


def point_pattern(marker, dx, dy, displace_x=0.0):
    lyr = QgsPointPatternFillSymbolLayer()
    lyr.setDistanceX(dx)
    lyr.setDistanceY(dy)
    lyr.setDisplacementX(displace_x)
    lyr.setSubSymbol(marker)
    return lyr


def svg_marker(path, size, color=None, stroke=None, angle=0.0):
    lyr = QgsSvgMarkerSymbolLayer(path, size)
    if color:
        lyr.setFillColor(QColor(color))
    if stroke:
        lyr.setStrokeColor(QColor(stroke))
    lyr.setAngle(angle)
    sym = QgsMarkerSymbol()
    sym.deleteSymbolLayer(0)
    sym.appendSymbolLayer(lyr)
    return sym


def simple_marker(shape, size, color, stroke="0,0,0,0", stroke_width=0.0):
    lyr = QgsSimpleMarkerSymbolLayer(shape, size)
    lyr.setColor(QColor(color))
    lyr.setStrokeColor(QColor(stroke))
    lyr.setStrokeWidth(stroke_width)
    return lyr


def text(size, color="#3a3a3a", weight=QFont.Weight.Normal, italic=False, halo=1.0,
         halo_color="#ffffff", spacing=0.0, family=FONT):
    fmt = QgsTextFormat()
    font = QFont(family)
    font.setWeight(weight)
    font.setItalic(italic)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    fmt.setFont(font)
    fmt.setSize(size)
    fmt.setColor(QColor(color))
    buf = QgsTextBufferSettings()
    buf.setEnabled(halo > 0)
    buf.setSize(halo)
    buf.setColor(QColor(halo_color))
    fmt.setBuffer(buf)
    return fmt


root = p.layerTreeRoot()
g_poi = root.addGroup("Places to go")
g_town = root.addGroup("Town")
g_land = root.addGroup("Landscape")

# --- Land use: one fill technique per class ------------------------------------------
land = layer(f"{OUT}/landuse.sqlite|layername=landuse", "Land use")
forest_tree = svg_marker(f"{SVG}/symbol/landuse_coniferous.svg", 3.2, "#3f7d3a", "#3f7d3a")
orchard_tree = svg_marker(f"{OUT}/orchard.svg", 3.4, "#6b8f2a", "#4f6d1d")
vine = QgsMarkerSymbol()
vine.deleteSymbolLayer(0)
vine.appendSymbolLayer(simple_marker(Qgis.MarkerShape.Circle, 0.9, "#8e5aa8"))
cross = QgsMarkerSymbol()
cross.deleteSymbolLayer(0)
cross_layer = simple_marker(Qgis.MarkerShape.Cross2, 1.8, "#6b7f66", "#6b7f66", 0.3)
cross.appendSymbolLayer(cross_layer)
radial = QgsGradientFillSymbolLayer(QColor("#cfe8b6"), QColor("#a9d48f"))
radial.setGradientType(Qgis.GradientType.Radial)
radial.setCoordinateMode(Qgis.SymbolCoordinateReference.Feature)
radial.setReferencePoint1(QPointF(0.5, 0.5))
radial.setReferencePoint2(QPointF(1.0, 1.0))
classes = {
    "residential": ("Residential", [solid("#ebe2d4")]),
    "farmland": ("Farmland", [solid("#f3edc9"), hatch("#ddd09a", 0, 1.6, 0.3)]),
    "forest": ("Forest", [solid("#cfe3bf"), point_pattern(forest_tree, 5.5, 5.0, 2.75)]),
    "orchard": ("Orchard", [solid("#e3edc0"), point_pattern(orchard_tree, 5.0, 5.0, 2.5)]),
    "vineyard": ("Vineyard", [solid("#ecdff2"), point_pattern(vine, 2.4, 2.4)]),
    "grass": ("Grass", [radial.clone()]),
    "village_green": ("Village green", [radial.clone()]),
    "cemetery": ("Cemetery", [solid("#d5e2cf"), point_pattern(cross, 3.6, 3.6, 1.8)]),
    "industrial": ("Industrial", [solid("#e7dfee"), hatch("#bba8cf", 45, 2.2), hatch("#bba8cf", -45, 2.2)]),
    "conservation": ("Conservation", [solid("#d6ead0"), hatch("#7db477", 45, 2.6, 0.35)]),
}
categories = []
for key, (label, layers_) in classes.items():
    categories.append(QgsRendererCategory(key, fill_symbol(*layers_), label, True))
land.setRenderer(QgsCategorizedSymbolRenderer("landuse", categories))

# --- Water: shapeburst ------------------------------------------------------------------
water = layer(f"{OUT}/shapefile/water.shp", "Water")
burst = QgsShapeburstFillSymbolLayer(QColor("#5f9fd2"), QColor("#d3e9f7"))
burst.setUseWholeShape(False)
burst.setMaxDistance(2.2)
burst.setDistanceUnit(MM)
burst.setColorType(Qgis.GradientColorSource.SimpleTwoColor)
water.setRenderer(QgsSingleSymbolRenderer(fill_symbol(
    burst, QgsSimpleFillSymbolLayer.create({"color": "0,0,0,0", "style": "no", "outline_color": "#5a92c2",
                                            "outline_width": "0.25"}))))

# --- Rivers: lineburst + flow arrows + curved italic labels --------------------------
rivers = layer(f"{OUT}/shapefile/rivers.shp", "Rivers")
lineburst = QgsLineburstSymbolLayer(QColor("#4b8ec6"), QColor("#a8d2f0"))
lineburst.setWidth(1.5)
lineburst.setPenCapStyle(Qt.PenCapStyle.RoundCap)
arrow_marker = QgsMarkerSymbol()
arrow_marker.deleteSymbolLayer(0)
arrow_marker.appendSymbolLayer(simple_marker(Qgis.MarkerShape.ArrowHeadFilled, 1.6, "#2f6fa8"))
flow = QgsMarkerLineSymbolLayer(True, 22)
flow.setPlacements(Qgis.MarkerLinePlacement.Interval)
flow.setOffsetAlongLine(11)
flow.setSubSymbol(arrow_marker)
rivers.setRenderer(QgsSingleSymbolRenderer(line_symbol(lineburst, flow)))
river_labels = QgsPalLayerSettings()
river_labels.fieldName = "name"
river_labels.placement = Qgis.LabelPlacement.Curved
river_labels.setFormat(text(9, "#245f95", QFont.Weight.Medium, italic=True, halo=1.0, spacing=1.2))
river_labels.repeatDistance = 70
river_labels.repeatDistanceUnit = MM
rivers.setLabeling(QgsVectorLayerSimpleLabeling(river_labels))
rivers.setLabelsEnabled(True)

# --- Protected areas: an inside band, a dashed edge -------------------------------------
prot = layer(f"{OUT}/shapefile/protected_areas.shp", "Protected areas")
band = simple_line("#5aa864", 3.2, cap=Qt.PenCapStyle.FlatCap, offset=-1.6)
band.setColor(QColor(90, 168, 100, 110))
edge = simple_line("#2f7a3d", 0.45, cap=Qt.PenCapStyle.FlatCap, dash=[2.2, 1.0])
prot_sym = fill_symbol(QgsSimpleFillSymbolLayer.create({"color": "0,0,0,0", "style": "no", "outline_style": "no"}))
prot_sym.deleteSymbolLayer(0)
prot_sym.appendSymbolLayer(QgsSimpleFillSymbolLayer.create({"color": "90,168,100,22", "outline_style": "no"}))
# Outlines as line layers on the polygon (QgsFillSymbol accepts line symbol layers).
prot_sym.appendSymbolLayer(band)
prot_sym.appendSymbolLayer(edge)
prot.setRenderer(QgsSingleSymbolRenderer(prot_sym))
prot_labels = QgsPalLayerSettings()
prot_labels.fieldName = "name"
prot_labels.placement = Qgis.LabelPlacement.Horizontal
prot_labels.setFormat(text(10, "#2f6b37", QFont.Weight.DemiBold, italic=True, halo=1.0, spacing=1.5))
prot.setLabeling(QgsVectorLayerSimpleLabeling(prot_labels))
prot.setLabelsEnabled(True)

# --- Roads: rule-based, casings below fills (symbol levels) ---------------------------
roads = layer(f"{OUT}/training_data.gpkg|layername=roads", "Roads")
ROAD = [  # filter, label, casing colour, casing width, fill colour, fill width
    ("\"highway\" IN ('trunk','trunk_link','primary')", "Main road", "#b4661c", 2.5, "#f7be55", 1.85),
    ("\"highway\" IN ('secondary','tertiary')", "Secondary road", "#a8843f", 2.1, "#fde49a", 1.5),
    ("\"highway\" IN ('residential','unclassified','road','living_street')", "Street", "#bdb2a2", 1.55,
     "#ffffff", 1.05),
    ("\"highway\" = 'service'", "Service road", "#c9c0b3", 1.05, "#ffffff", 0.6),
]
root_rule = QgsRuleBasedRenderer.Rule(None)
for flt, label, cc, cw, fc, fw in ROAD:
    sym = line_symbol(simple_line(cc, cw, rendering_pass=0), simple_line(fc, fw, rendering_pass=1))
    root_rule.appendChild(QgsRuleBasedRenderer.Rule(sym, 0, 0, flt, label))
track = line_symbol(simple_line("#9a6a3a", 0.55, cap=Qt.PenCapStyle.FlatCap, dash=[2.6, 1.1], rendering_pass=1))
root_rule.appendChild(QgsRuleBasedRenderer.Rule(track, 0, 0, "\"highway\" = 'track'", "Track"))
path = line_symbol(simple_line("#c4453f", 0.5, cap=Qt.PenCapStyle.RoundCap, dash=[0.01, 1.1], rendering_pass=1))
root_rule.appendChild(QgsRuleBasedRenderer.Rule(path, 0, 0, "\"highway\" IN ('path','footway','steps')",
                                                "Path"))
one_way_marker = QgsMarkerSymbol()
one_way_marker.deleteSymbolLayer(0)
one_way_marker.appendSymbolLayer(simple_marker(Qgis.MarkerShape.ArrowHead, 1.4, "#7d7468", "#7d7468", 0.25))
one_way = QgsMarkerLineSymbolLayer(True, 14)
one_way.setPlacements(Qgis.MarkerLinePlacement.Interval)
one_way.setOffsetAlongLine(7)
one_way.setSubSymbol(one_way_marker)
one_way.setRenderingPass(2)
root_rule.appendChild(QgsRuleBasedRenderer.Rule(line_symbol(one_way), 0, 0, "\"oneway\" = 'yes'", "One way"))
road_renderer = QgsRuleBasedRenderer(root_rule)
road_renderer.setUsingSymbolLevels(True)
roads.setRenderer(road_renderer)
label_root = QgsRuleBasedLabeling.Rule(None)
for flt, size, color, weight in (
        ("\"highway\" IN ('trunk','primary','secondary','tertiary')", 8.5, "#6b3c0f", QFont.Weight.Bold),
        ("\"highway\" IN ('residential','unclassified','road')", 7.0, "#5a554e", QFont.Weight.Normal)):
    settings = QgsPalLayerSettings()
    settings.fieldName = "name"
    settings.placement = Qgis.LabelPlacement.Curved
    settings.setFormat(text(size, color, weight, halo=0.9, spacing=0.3))
    settings.repeatDistance = 60
    settings.repeatDistanceUnit = MM
    rule = QgsRuleBasedLabeling.Rule(settings)
    rule.setFilterExpression(flt)
    label_root.appendChild(rule)
roads.setLabeling(QgsRuleBasedLabeling(label_root))
roads.setLabelsEnabled(True)

# --- Buildings: a geometry-generator shadow under a two-tone fill --------------------
build = layer(f"{OUT}/training_data.gpkg|layername=buildings", "Buildings")
shadow = QgsGeometryGeneratorSymbolLayer.create({"geometryModifier": "translate($geometry, 1.6, -1.6)",
                                                 "SymbolType": "Fill"})
shadow.setSubSymbol(fill_symbol(solid("#c9bba6")))
house = fill_symbol(shadow, solid("#dcc5a6", "#a88c69", 0.2))
special = fill_symbol(shadow.clone(), solid("#e7b9a0", "#9c5a3c", 0.25))
glass = fill_symbol(solid("#d6ecee", "#7fa9ad", 0.25), hatch("#9cc6ca", 90, 0.9, 0.15))
build.setRenderer(QgsCategorizedSymbolRenderer("building", [
    QgsRendererCategory("church", special, "Church", True),
    QgsRendererCategory("hospital", special.clone(), "Hospital", True),
    QgsRendererCategory("hotel", special.clone(), "Hotel", True),
    QgsRendererCategory("greenhouse", glass, "Greenhouse", True),
    QgsRendererCategory(None, house, "Building", True),
]))

# --- A walking route: QGIS curved arrows ------------------------------------------------
route_points = [(20.4425, -34.0275), (20.4440, -34.0250), (20.4462, -34.0236), (20.4490, -34.0229),
                (20.4518, -34.0218), (20.4535, -34.0200)]
to_web = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                QgsCoordinateReferenceSystem("EPSG:3857"), p)
route = QgsVectorLayer("LineString?crs=EPSG:3857&field=name:string", "Heritage walk", "memory")
feat = QgsFeature(route.fields())
feat.setAttributes(["Heritage walk"])
feat.setGeometry(QgsGeometry.fromPolylineXY([to_web.transform(QgsPointXY(x, y)) for x, y in route_points])
                 .smooth(3, 0.25))
route.dataProvider().addFeatures([feat])
QgsVectorFileWriter.writeAsVectorFormatV3(route, f"{OUT}/route.gpkg", p.transformContext(),
                                          QgsVectorFileWriter.SaveVectorOptions())
route = layer(f"{OUT}/route.gpkg", "Heritage walk")
arrow = QgsArrowSymbolLayer.create({"arrow_width": "1.2", "arrow_start_width": "0.5", "head_length": "3",
                                    "head_thickness": "2.2", "head_type": "0", "arrow_type": "0",
                                    "is_curved": "1", "is_repeated": "1"})
arrow.setSubSymbol(fill_symbol(solid("196,48,43,200", "#7a1c18", 0.2)))
route.setRenderer(QgsSingleSymbolRenderer(line_symbol(arrow)))

# --- Restaurants and schools: icons on shapes, labels with backgrounds ---------------
rest = layer(f"{OUT}/training_data.gpkg|layername=restaurants", "Restaurants")
rest_marker = QgsMarkerSymbol()
rest_marker.deleteSymbolLayer(0)
rest_marker.appendSymbolLayer(simple_marker(Qgis.MarkerShape.Circle, 5.2, "#c0392b", "#ffffff", 0.5))
icon = QgsSvgMarkerSymbolLayer(f"{SVG}/food/food_restaurant.svg", 3.0)
icon.setFillColor(QColor("#ffffff"))
icon.setStrokeColor(QColor("#ffffff"))
icon.setStrokeWidth(0)
rest_marker.appendSymbolLayer(icon)
centroid = QgsCentroidFillSymbolLayer()
centroid.setPointOnSurface(True)
centroid.setSubSymbol(rest_marker)
rest.setRenderer(QgsSingleSymbolRenderer(fill_symbol(solid("#f2c7ad", "#c0392b", 0.3), centroid)))
rest_labels = QgsPalLayerSettings()
rest_labels.fieldName = "name"
rest_labels.placement = Qgis.LabelPlacement.AroundPoint
rest_labels.dist = 3.4
rest_fmt = text(7.5, "#8e2a20", QFont.Weight.DemiBold, halo=0.8)
rest_labels.setFormat(rest_fmt)
rest.setLabeling(QgsVectorLayerSimpleLabeling(rest_labels))
rest.setLabelsEnabled(True)

schools = layer(f"{OUT}/training_data.gpkg|layername=schools", "Schools")
school_marker = QgsMarkerSymbol()
school_marker.deleteSymbolLayer(0)
background = QgsSvgMarkerSymbolLayer(f"{SVG}/backgrounds/background_square_rounded.svg", 6.0)
background.setFillColor(QColor("#d68910"))
background.setStrokeColor(QColor("#ffffff"))
background.setStrokeWidth(0.4)
school_marker.appendSymbolLayer(background)
school_icon = QgsSvgMarkerSymbolLayer(f"{SVG}/symbol/education_school.svg", 3.6)
school_icon.setFillColor(QColor("#ffffff"))
school_icon.setStrokeColor(QColor("#ffffff"))
school_marker.appendSymbolLayer(school_icon)
school_centroid = QgsCentroidFillSymbolLayer()
school_centroid.setPointOnSurface(True)
school_centroid.setSubSymbol(school_marker)
schools.setRenderer(QgsSingleSymbolRenderer(fill_symbol(solid("#f8dba0", "#b9770e", 0.35), school_centroid)))
school_labels = QgsPalLayerSettings()
school_labels.fieldName = "name"
school_labels.placement = Qgis.LabelPlacement.AroundPoint
school_labels.dist = 4.2
fmt = text(7.5, "#6e4a07", QFont.Weight.DemiBold, halo=0)
bg = QgsTextBackgroundSettings()
bg.setEnabled(True)
bg.setType(QgsTextBackgroundSettings.ShapeType.ShapeRectangle)
bg.setSizeType(QgsTextBackgroundSettings.SizeType.SizeBuffer)
bg.setSize(QSizeF(1.4, 0.7))
bg.setRadii(QSizeF(1.0, 1.0))
bg.setFillColor(QColor(255, 248, 230, 235))
bg.setStrokeColor(QColor("#d68910"))
bg.setStrokeWidth(0.3)
fmt.setBackground(bg)
school_labels.setFormat(fmt)
schools.setLabeling(QgsVectorLayerSimpleLabeling(school_labels))
schools.setLabelsEnabled(True)

# --- Places: the town name -----------------------------------------------------------
places = layer(f"{OUT}/shapefile/places.shp", "Places")
dot = QgsMarkerSymbol()
dot.deleteSymbolLayer(0)
dot.appendSymbolLayer(simple_marker(Qgis.MarkerShape.Circle, 2.2, "#ffffff", "#3a3a3a", 0.5))
dot.appendSymbolLayer(simple_marker(Qgis.MarkerShape.Circle, 0.9, "#3a3a3a"))
places.setRenderer(QgsSingleSymbolRenderer(dot))
place_labels = QgsPalLayerSettings()
place_labels.fieldName = "upper(\"name\")"
place_labels.isExpression = True
place_labels.placement = Qgis.LabelPlacement.OverPoint
place_labels.yOffset = -4.0
place_labels.setFormat(text(15, "#2b2b2b", QFont.Weight.Bold, halo=1.6, spacing=3.0))
places.setLabeling(QgsVectorLayerSimpleLabeling(place_labels))
places.setLabelsEnabled(True)

for group, layers_ in ((g_poi, [places, rest, schools, route]),
                       (g_town, [roads, build]),
                       (g_land, [rivers, prot, water, land])):
    for lyr in layers_:
        p.addMapLayer(lyr, False)
        group.addLayer(lyr)
p.write(os.path.join(OUT, "showcase.qgs"))
print("saved", os.path.join(OUT, "showcase.qgs"), [l.name() for l in p.mapLayers().values()])
