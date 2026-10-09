"""
Field disclosure of a publication (pure Python).

Field classes (plan §5.2):

1. transient: read by QGIS expressions on the export side only, never written;
2. rendering: computed ``q2vt_*`` values the style needs (label text, sizes,
   angles, draw order, helper ids, the feature key);
3. popup: approved fields shown on click (feature-lookup records, not tiles);
4. search / filter / deep-link: approved fields in search records; filter
   fields are also written to the tiles of their layer.

Publishing writes *required fields only* to tiles unless the profile
explicitly includes all fields (then the review lists every field). The
check below decodes the published tiles and fails on any property that is
neither generated (``q2vt_``) nor approved for that tile layer.
"""

from typing import Dict, Iterable, List, Set

from . import mvt
from .errors import PublishingError
from .validation import open_pmtiles

GENERATED_PREFIX = "q2vt_"


def tile_field_violations(pmtiles_path: str, approved: Dict[str, Set[str]],
                          allow_all: bool = False, limit: int = 10) -> List[str]:
    """Properties in the archive that are not generated or approved.

    ``approved``: {source layer: approved raw fields}. Every tile is checked
    (keys only; features are not decoded): read from the file, or, for the
    very archive built in this session (same SHA-256), the keys of every
    tile gathered as it was written."""
    if allow_all:
        return []
    from .pmtiles_builder import built_layer_keys, sha256_file  # pylint: disable=import-outside-toplevel
    problems: List[str] = []
    seen: Set[str] = set()
    known = built_layer_keys(sha256_file(pmtiles_path))
    with open_pmtiles(pmtiles_path) as archive:
        summaries = [known] if known is not None else \
            (mvt.layer_summary(data) for _, data in archive.tiles())
        for summary in summaries:
            for layer, keys in summary.items():
                allowed = approved.get(layer, set())
                for key in keys:
                    if key.startswith(GENERATED_PREFIX) or key in allowed:
                        continue
                    marker = f"{layer}.{key}"
                    if marker not in seen:
                        seen.add(marker)
                        problems.append(marker)
                        if len(problems) >= limit:
                            return problems
    return problems


def assert_disclosure(pmtiles_path: str, approved: Dict[str, Set[str]], allow_all: bool = False):
    problems = tile_field_violations(pmtiles_path, approved, allow_all)
    if problems:
        raise PublishingError("Q2VT_PUB_FIELD_DISCLOSURE",
                              "Fields that were not approved are in the tiles: " + ", ".join(problems))


def public_record(attributes: dict, fields: Iterable[str]) -> dict:
    """Only the approved fields of a feature (None kept as JSON null)."""
    return {name: attributes.get(name) for name in fields}
