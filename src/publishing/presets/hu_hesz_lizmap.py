"""
Lizmap HÉSZ texts -> the regulation text table of the parcel report.

A Lizmap Web Client project shows a parcel's zone regulations from HTML files
in the project's ``media`` folder: expression fields of a layer give the
paths (e.g. ``'media/html/' + szerk_ov + '.html'``), and the layer's Lizmap
popup template (``<project>.qgs.cfg``) shows them one after the other. Here
those expressions are evaluated on the zoning layer's features (it carries
the fields they read: zone kind, special mark …), the files joined in the
template's order, and the result written as the table the preset uses for
the full regulation texts ("HÉSZ övezeti előírások szövege": zone code,
HTML), keyed by the zoning layer's own zone code - what the parcel report
shows.

Variant branch hu-hesz only (see docs/BRANCHES.md).
"""

import json
import os
import re
from typing import Dict, List, Optional, Tuple

TABLE_NAME = "HÉSZ övezeti előírások szövege (Lizmap)"
FILE_NAME = "hesz_eloirasok_szovege_lizmap.gpkg"
CODE_FIELD = "szab_ov"
TEXT_FIELD = "eloiras_html"
MEDIA = "media"
_MEDIA_HTML = re.compile(r"media/[^'\"]*\.html?", re.IGNORECASE)


def _is_html(expression: str) -> bool:
    return "media/" in expression and ".htm" in expression.lower()


def _project_expressions(project) -> Dict[str, List[Tuple[str, str]]]:
    """{layer id: [(field, expression)]} of the expression fields in the
    project file. QGIS drops an expression field whose name the data source
    also has (a layer saved with the paths filled in): its expression is
    still in the file."""
    import xml.etree.ElementTree as ET  # pylint: disable=import-outside-toplevel
    import zipfile  # pylint: disable=import-outside-toplevel
    path = project.fileName() or ""
    try:
        if path.lower().endswith(".qgz"):
            with zipfile.ZipFile(path) as archive:
                name = next(item for item in archive.namelist() if item.lower().endswith(".qgs"))
                root = ET.fromstring(archive.read(name))
        else:
            root = ET.parse(path).getroot()
    except (OSError, ET.ParseError, StopIteration, zipfile.BadZipFile):
        return {}
    found: Dict[str, List[Tuple[str, str]]] = {}
    for layer in root.iter("maplayer"):
        fields = layer.find("expressionfields")
        if fields is None:
            continue
        found[layer.findtext("id") or ""] = [(field.get("name") or "", field.get("expression") or "")
                                             for field in fields if field.get("name")]
    return found


def _html_fields(layer, saved: Dict[str, List[Tuple[str, str]]]) -> List[Tuple[str, str]]:
    """(field, expression) of the layer's expression fields giving media/*.html
    paths: those QGIS loaded, and those in the project file."""
    from qgis.core import QgsFields  # pylint: disable=import-outside-toplevel
    found = []
    fields = layer.fields()
    for index in range(fields.count()):
        if fields.fieldOrigin(index) == QgsFields.OriginExpression:
            expression = layer.expressionField(index) or ""
            if _is_html(expression):
                found.append((fields.at(index).name(), expression))
    names = {name for name, _ in found}
    found += [(name, expression) for name, expression in saved.get(layer.id(), [])
              if name not in names and _is_html(expression)]
    return found


def _template_order(project, layer, names: List[str]) -> List[str]:
    """``names`` in the order the layer's Lizmap popup template uses them
    (``{$field}``): only those it shows; without a template all of them."""
    config = (project.fileName() or "") + ".cfg"
    order: List[str] = []
    try:
        with open(config, encoding="utf-8") as handle:
            layers = json.load(handle).get("layers", {})
        entry = layers.get(layer.name()) or next(
            (item for item in layers.values() if item.get("name") == layer.name()), {})
        if entry.get("popupSource") == "lizmap":
            order = [name for name in re.findall(r"\{\$(\w+)\}", entry.get("popupTemplate") or "")
                     if name in names]
    except (OSError, ValueError, AttributeError):
        pass
    seen = []
    for name in order or names:
        if name not in seen:
            seen.append(name)
    return seen


def _source_layer(project):
    """The layer with the most media/*.html expression fields, and those fields in order."""
    from qgis.core import QgsVectorLayer  # pylint: disable=import-outside-toplevel
    best = None
    saved = _project_expressions(project)
    for layer in project.mapLayers().values():
        if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            continue
        fields = _html_fields(layer, saved)
        if fields and (best is None or len(fields) > len(best[1])):
            best = (layer, fields)
    if best is None:
        return None, []
    layer, fields = best
    expressions = dict(fields)
    return layer, [(name, expressions[name]) for name in _template_order(project, layer, list(expressions))]


def _read(path: str) -> str:
    with open(path, "rb") as handle:
        data = handle.read()
    for encoding in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def collect(project, zoning, code_field: str) -> Tuple[Optional[object], Dict[str, str], List[str]]:
    """(source layer, {zone code: HTML}, notes): the texts of every zone code of
    ``zoning``. Nothing (no source layer) when the project has no Lizmap
    media HTML fields or no media folder."""
    from qgis.core import (QgsExpression, QgsExpressionContext,  # pylint: disable=import-outside-toplevel
                           QgsExpressionContextUtils, QgsFeatureRequest)
    base = project.absolutePath()
    media = os.path.realpath(os.path.join(base, MEDIA)) if base else ""
    source, fields = _source_layer(project)
    if source is None or not media or not os.path.isdir(media):
        return None, {}, []
    notes: List[str] = []
    zone_fields = {field.name().lower() for field in zoning.fields()}
    expressions = []
    for name, text in fields:
        expression = QgsExpression(text)
        missing = [column for column in expression.referencedColumns()
                   if column != QgsFeatureRequest.ALL_ATTRIBUTES and column.lower() not in zone_fields]
        if missing:
            notes.append(f"A(z) „{name}” Lizmap-mező kimaradt: az övezeti rétegben nincs "
                         f"{', '.join(sorted(missing))} mező.")
            continue
        expressions.append((name, expression))
    if not expressions:
        return source, {}, notes
    context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(zoning))
    for _name, expression in expressions:
        expression.prepare(context)
    paths: Dict[str, List[str]] = {}
    variants: Dict[str, set] = {}
    missing_files = set()
    for feature in zoning.getFeatures():
        code = feature[code_field]
        if code is None or str(code).strip() == "" or str(code) == "NULL":
            continue
        code = str(code).strip()
        # Empty text as NULL: the Lizmap layer's fields are NULL where the
        # zoning layer's are "" ("if(csak_szab is null, szerk_ov, csak_szab)").
        attributes = feature.attributes()
        feature.setAttributes([None if isinstance(value, str) and not value.strip() else value
                               for value in attributes])
        context.setFeature(feature)
        found = []
        for _name, expression in expressions:
            value = expression.evaluate(context)
            if not isinstance(value, str) or not _MEDIA_HTML.search(value):
                continue
            path = os.path.realpath(os.path.join(base, value.strip()))
            if not path.startswith(media + os.sep):  # only the project's media folder
                continue
            if not os.path.isfile(path):
                missing_files.add(value.strip())
                continue
            found.append(path)
        listed = paths.setdefault(code, [])
        listed.extend(path for path in found if path not in listed)
        variants.setdefault(code, set()).add(tuple(found))
    texts = {}
    cache: Dict[str, str] = {}
    for code, listed in paths.items():
        if not listed:
            continue
        parts = [cache.setdefault(path, _read(path).strip()) for path in listed]
        texts[code] = "\n<hr>\n".join(part for part in parts if part)
    mixed = sorted(code for code, seen in variants.items() if len(seen) > 1)
    if mixed:
        notes.append("Ezeknél az övezeti jeleknél a telekdarabok más-más Lizmap-szöveget kapnak, "
                     "mindegyikük szövege bekerült: " + ", ".join(mixed[:10]) + (" …" if len(mixed) > 10 else ""))
    real_missing = sorted(path for path in missing_files if not re.search(r"/(sajatos-)?\.html?$", path))
    if real_missing:
        notes.append("Hiányzó Lizmap-szövegfájlok: " + ", ".join(real_missing[:10])
                     + (" …" if len(real_missing) > 10 else ""))
    return source, texts, notes


def write_table(project, texts: Dict[str, str]) -> Optional[object]:
    """The texts as a table (no geometry) next to the project, added to the
    project (replacing the one made before); the layer, or None."""
    from qgis.core import (QgsFeature, QgsField, QgsFields,  # pylint: disable=import-outside-toplevel
                           QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes)
    from qgis.PyQt.QtCore import QVariant  # pylint: disable=import-outside-toplevel
    path = os.path.join(project.absolutePath(), FILE_NAME)
    fields = QgsFields()
    fields.append(QgsField(CODE_FIELD, QVariant.String))
    fields.append(QgsField(TEXT_FIELD, QVariant.String))
    for layer in list(project.mapLayers().values()):  # the table made before: replaced
        if os.path.normcase(os.path.abspath(layer.source().split("|")[0])) == os.path.normcase(os.path.abspath(path)):
            project.removeMapLayer(layer.id())
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = "hesz_eloirasok_szovege"
    options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
    writer = QgsVectorFileWriter.create(path, fields, QgsWkbTypes.NoGeometry, project.crs(),
                                        project.transformContext(), options)
    if writer is None or writer.hasError() != QgsVectorFileWriter.NoError:
        return None
    for code in sorted(texts):
        feature = QgsFeature(fields)
        feature.setAttributes([code, texts[code]])
        writer.addFeature(feature)
    del writer
    layer = QgsVectorLayer(f"{path}|layername=hesz_eloirasok_szovege", TABLE_NAME, "ogr")
    if not layer.isValid():
        return None
    project.addMapLayer(layer)
    return layer
