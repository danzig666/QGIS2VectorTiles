"""4.28: interval marker positions computed in Python (core.marker_points)
are the very numbers of the QGIS expression chain they replace
(geometrybyexpression → multiparttosingleparts → angle = z → dropmzvalues):
random lines, open and closed, duplicate vertices, offsets along the line
outside it, averaged angles, the project CRS different from the export's."""

import random

import pytest
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsExpressionContext,
                       QgsExpressionContextUtils, QgsFeature, QgsFeatureRequest, QgsGeometry,
                       QgsProcessingContext, QgsProcessingFeedback, QgsProject, QgsVectorLayer,
                       QgsWkbTypes)

from q2vt_fixtures import reset_project


def _context(project):
    context = QgsProcessingContext()
    expressions = QgsExpressionContext()
    expressions.appendScope(QgsExpressionContextUtils.globalScope())
    expressions.appendScope(QgsExpressionContextUtils.projectScope(project))
    context.setExpressionContext(expressions)
    context.setInvalidGeometryCheck(QgsFeatureRequest.InvalidGeometryCheck.GeometryNoCheck)
    return context


def _run(name, params, context):
    algorithm = QgsApplication.processingRegistry().createAlgorithmById(name)
    results, ok = algorithm.run(dict(params, OUTPUT="TEMPORARY_OUTPUT"), context, QgsProcessingFeedback(), {}, False)
    assert ok, name
    return context.takeResultLayer(results["OUTPUT"])


def _chain(mat, lines, recipe, context):
    multipoints = _run("native:geometrybyexpression", dict(
        INPUT=lines, OUTPUT_GEOMETRY=2, WITH_Z=True,
        EXPRESSION=mat.interval_points_expression(recipe, "EPSG:3857")), context)
    points = _run("native:multiparttosingleparts", dict(INPUT=multipoints), context)
    angled = _run("native:fieldcalculator", dict(INPUT=points, FIELD_NAME=mat.ANGLE_FIELD, FIELD_TYPE=0,
                                                  FORMULA="z(@geometry)"), context)
    return _run("native:dropmzvalues", dict(INPUT=angled, DROP_M_VALUES=True, DROP_Z_VALUES=True), context)


def _rows(layer):
    rows = [(QgsWkbTypes.displayString(layer.wkbType()),
             [(f.name(), f.type(), f.typeName(), f.length(), f.precision()) for f in layer.fields()])]
    for feature in layer.getFeatures():
        geometry = feature.geometry()
        rows.append((feature.id(), None if geometry.isNull() else bytes(geometry.asWkb()),
                     [repr(value) for value in feature.attributes()]))
    return rows


def _line(rng, real):
    x, y = (2119000 + rng.uniform(-5e4, 5e4), 6019000 + rng.uniform(-5e4, 5e4)) if real \
        else (rng.uniform(-50, 50), rng.uniform(-50, 50))
    points = [(x, y)]
    for _ in range(rng.choice([1, 1, 2, 3, 7, 20])):
        if rng.random() < 0.1:
            points.append(points[-1])  # a repeated vertex
        else:
            step = rng.choice([0.5, 3, 10, 40, 200])
            points.append((points[-1][0] + rng.uniform(-step, step), points[-1][1] + rng.uniform(-step, step)))
    if rng.random() < 0.3:
        points.append(points[0])  # a ring
    if rng.random() < 0.1:
        points = [(round(px), round(py)) for px, py in points]  # markers exactly on vertices
    return "LINESTRING(" + ", ".join(f"{px!r} {py!r}" for px, py in points) + ")"


def test_direct_interval_markers_equal_the_expression(plugin):
    from q2vt_plugin.src.core import marker_points  # pylint: disable=import-error
    from q2vt_plugin.src.core.fidelity import materialize as mat  # pylint: disable=import-error
    project = reset_project()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:23700"))
    rng = random.Random(28)
    for _ in range(60):
        real = rng.random() < 0.5
        lines = QgsVectorLayer(f"LineString?crs=EPSG:3857&field=k:integer&field={mat.COUNT_FIELD}:integer",
                               "l", "memory")
        wkts = [_line(rng, real) for _ in range(rng.randint(1, 5))] + [None, "LINESTRING(5 5, 5 5)"]
        features = []
        for number, wkt in enumerate(wkts):
            feature = QgsFeature(lines.fields())
            feature.setAttributes([number, 0])
            if wkt:
                feature.setGeometry(QgsGeometry.fromWkt(wkt))
            features.append(feature)
        lines.dataProvider().addFeatures(features)
        recipe = mat.interval_points(rng.choice([0.7, 2.5, 7.3, 20.0]), rng.choice([0.0, 1.5, -2.0, 30.0]),
                                     crs="EPSG:23700" if real and rng.random() < 0.7 else "")
        if rng.random() < 0.3:
            recipe = mat.Recipe(recipe.kind, recipe.placements,
                                recipe.params + (("average", rng.choice([0.5, 3.0, 12.0])),))
        context = _context(project)
        expected = _rows(_chain(mat, lines, recipe, context))
        # expressionContext() is a reference into its context: keep that alive.
        direct_context = _context(project)
        direct = marker_points.interval_points_layer(
            lines, recipe, "EPSG:3857", direct_context.expressionContext(),
            mat.interval_points_expression(recipe, "EPSG:3857"))
        assert _rows(direct) == expected, (recipe, wkts)


@pytest.mark.parametrize("wkt, distance, expected", [
    ("LINESTRING(0 0, 10 0, 10 10)", 10.0, 45.0),   # on a vertex: the two segments' mean
    ("LINESTRING(0 0, 10 0, 10 10)", 0.0, 90.0),
    ("LINESTRING(0 0, 10 0, 10 10)", 20.0, 0.0),
    ("LINESTRING(0 0, 0 0, 3 4)", 0.0, 90.0),       # a zero-length first segment
])
def test_line_angles(plugin, wkt, distance, expected):
    from q2vt_plugin.src.core.marker_points import Line, linestring_coords  # pylint: disable=import-error
    line = Line(*linestring_coords(bytes(QgsGeometry.fromWkt(wkt).asWkb())))
    assert line.angle(distance) == pytest.approx(expected)
    assert QgsGeometry.fromWkt(wkt).interpolateAngle(distance) * 180 / 3.141592653589793 == pytest.approx(expected)
