"""
feature_order.py

QGIS's feature order across the rules of one layer ("feature-order strata").

Without symbol levels QGIS draws a categorized, graduated or single-symbol
layer feature by feature in request order, each feature with its own symbol
(a rule-based renderer: feature by feature within each rendering pass). The
web style draws one style layer per rule, rule after rule, so wherever
features of two rules overlap the later rule covers the earlier one, even
where QGIS draws a later feature of the earlier rule on top.

Each feature gets a stratum: the highest of stratum(g) + (1 if rule(g) is
later than rule(f), else 0) over the features g that QGIS draws before f and
that overlap it (a feature's other rules count like overlapping features).
Style layers drawn by (pass, stratum, rule, symbol layer) then put every
feature above the overlapping features QGIS draws before it. A feature above
stratum 0 is drawn by a copy of its rule's style layers filtered to the
lifted features (RulesExporter._keep_feature_order, "_kNN" style names); the
rule's own style layers leave them out. Touching polygons (a partition) and
overlaps narrower than the margin (data simplification) do not count; lines
that touch do (a stroke has a width: a street ending on a main road covers
half of it), as do lines that meet within the margin.

Geometries are QgsGeometry objects; QGIS is imported only when ``strata`` runs.
"""

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Lifted (feature, rule) pairs and strata per layer. Each lifted feature id is
# written twice into the style (the copy's filter and the rule's own); each
# stratum is one more copy of every lifted rule's style layers. Beyond these
# the layer keeps rule order and Q2VT_FEATURE_ORDER_ACROSS_RULES is reported
# (a line layer first tries without lines that only touch: crossings only).
MAX_LIFTED = 5000
MAX_STRATA = 8
# Dataset rows of one layer read for the check (one spatial pass over them,
# about 0.1-0.3 ms per polygon); a larger layer keeps rule order.
MAX_FEATURES = 100_000

ID_FIELD = "q2vt_orig_id"


def strata(items: Sequence[Tuple[int, int, object]], margin: float = 0.0,
           touching_lines: bool = True) -> Dict[Tuple[int, int], int]:
    """{(feature id, rule): stratum} of the pairs above stratum 0.

    ``items``: (feature id, rule sequence, QgsGeometry) in QGIS's drawing
    order (by feature, then by rule). Polygons overlap only where their
    overlap is wider than twice ``margin``; a line must reach deeper than
    ``margin`` into a polygon; lines overlap where they meet (touching
    included) or come within ``margin`` of each other. ``touching_lines``
    False: lines overlap only where they cross or share a stretch (far fewer
    strata on a network whose ways end at the junctions).
    """
    from qgis.core import QgsGeometry, QgsSpatialIndex  # pylint: disable=import-outside-toplevel

    index = QgsSpatialIndex()
    level: List[int] = []
    cores: Dict[int, object] = {}
    lifted: Dict[Tuple[int, int], int] = {}

    def prepared(geometry):
        engine = QgsGeometry.createGeometryEngine(geometry.constGet())
        engine.prepareGeometry()
        return engine

    def polygon(n: int) -> bool:
        geometry = items[n][2]
        return int(getattr(geometry.type(), "value", geometry.type())) == 2

    def strokes(n: int, other: int) -> bool:
        """Two lines that overlap where they touch (a stroke has a width)."""
        return touching_lines and not polygon(n) and not polygon(other)

    ends: Dict[int, object] = {}

    def endpoints(n: int):
        """The first and last points of a line's parts (a multipoint)."""
        if n not in ends:
            from qgis.core import QgsMultiPoint  # pylint: disable=import-outside-toplevel
            points = QgsMultiPoint()
            for part in items[n][2].constParts():
                if part.nCoordinates():
                    points.addGeometry(part.startPoint().clone())
                    points.addGeometry(part.endPoint().clone())
            ends[n] = QgsGeometry(points)
        return ends[n]

    def core(n: int):
        """A polygon without its outer ``margin``; None: not shrunk (a line)."""
        if n not in cores:
            cores[n] = items[n][2].buffer(-margin, 2) if polygon(n) and margin > 0 else None
        return cores[n]

    for n, (fid, seq, geometry) in enumerate(items):
        # The feature's earlier rules: drawn below this one, as QGIS does.
        own = level[n - 1] if n and items[n - 1][0] == fid else 0
        candidates = []
        box = geometry.boundingBox()
        if touching_lines and margin > 0 and not polygon(n):
            box.grow(margin)  # a line ending within the margin of another
        for other in index.intersects(box):
            ofid, oseq, _ = items[other]
            need = level[other] + (1 if oseq > seq else 0)
            if ofid != fid and need > own:
                candidates.append((need, other))
        engine = core_engine = None
        for need, other in sorted(candidates, reverse=True):
            if need <= own:
                break
            if engine is None:
                engine = prepared(geometry)
            other_geometry = items[other][2]
            if not engine.intersects(other_geometry.constGet()):
                # Lines that meet within the margin: a junction vertex moved
                # by the data simplification (an end of one line near the
                # other; a full line-to-line distance cost minutes on dense
                # contours that never meet).
                if not strokes(n, other) or margin <= 0 or (
                        engine.distance(endpoints(other).constGet()) > margin and
                        endpoints(n).distance(other_geometry) > margin):
                    continue
            if core(n) is not None or core(other) is not None:
                # A polygon: the overlap must reach beyond the margin (which
                # also leaves out touching neighbours).
                if core_engine is None:
                    core_engine = prepared(geometry if core(n) is None else core(n))
                other_core = other_geometry if core(other) is None else core(other)
                if not core_engine.intersects(other_core.constGet()):
                    continue
            elif not strokes(n, other) and engine.touches(other_geometry.constGet()):
                continue  # neighbours that only touch
            own = need  # the highest need left: the rest cannot raise it
        level.append(own)
        index.addFeature(n, geometry.boundingBox())
        if own:
            lifted[(fid, seq)] = own
    return lifted


def only(ids: Iterable[int]) -> list:
    """MapLibre filter: the features ``ids`` (a match is a hash lookup)."""
    return ["match", ["get", ID_FIELD], sorted(set(ids)), True, False]


def without(ids: Iterable[int]) -> list:
    """MapLibre filter: every feature but ``ids`` (also those without id)."""
    return ["match", ["get", ID_FIELD], sorted(set(ids)), False, True]


_COPY = re.compile(r"^(.+)_k\d{2,}$")


def copy_name(name: str, stratum: int) -> str:
    """Style name of the copy of rule component ``name`` in ``stratum``
    (publishing.provenance.owning_style_name maps it back by its prefix)."""
    return f"{name}_k{stratum:02d}"


def copied_from(name: str) -> Optional[str]:
    """The style name a stratum copy was made from; None: not a copy."""
    match = _COPY.match(name)
    return match.group(1) if match else None
