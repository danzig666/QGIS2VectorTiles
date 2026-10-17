"""Join style layers that are one layer cut into zoom ranges.

Map-unit markers get a sprite per zoom band (maplibre_converter
``_convert_marker_symbol``): a layer per zoom, ``<id>_z<zoom>``, which is
often the same layer at every zoom (a marker too small to need a bigger
image), or the same but for its image. A city map had about 450 such
layers out of 800; each style layer costs the browser per tile (its own
bucket) and in every copy of the style (main thread and worker).

Neighbours in the layer order (nothing drawn between them) whose zoom
ranges meet and that differ at most in a constant ``icon-image`` become
one layer; differing images become a step on the zoom (layout is
evaluated at the tile's zoom, so only at whole zooms).
"""

import copy
import json
import re
from typing import List

_SPLIT_ID = re.compile(r"_z\d+$")


def _same_except(a: dict, b: dict) -> bool:
    for key in set(a) | set(b):
        if key in ("id", "minzoom", "maxzoom", "layout"):
            continue
        if json.dumps(a.get(key), sort_keys=True) != json.dumps(b.get(key), sort_keys=True):
            return False
    la, lb = a.get("layout") or {}, b.get("layout") or {}
    for key in set(la) | set(lb):
        if key == "icon-image":
            continue
        if json.dumps(la.get(key), sort_keys=True) != json.dumps(lb.get(key), sort_keys=True):
            return False
    return True


def _image_steps(layer: dict) -> list:
    """[image, zoom, image, ...] of a layer's icon-image (a step built here
    or a constant), None when it is anything else."""
    image = (layer.get("layout") or {}).get("icon-image")
    if isinstance(image, str):
        return [image]
    if isinstance(image, list) and len(image) >= 3 and image[0] == "step" and image[1] == ["zoom"] \
            and all(isinstance(v, str) for v in image[2::2]):
        return list(image[2:])
    return None


def _joinable(a: dict, b: dict) -> bool:
    if not _SPLIT_ID.search(a.get("id", "")) or not _SPLIT_ID.search(b.get("id", "")):
        return False
    if _SPLIT_ID.sub("", a["id"]) != _SPLIT_ID.sub("", b["id"]):
        return False
    if "minzoom" not in b or "maxzoom" not in a or a["maxzoom"] != b["minzoom"]:
        return False
    if not _same_except(a, b):
        return False
    ia, ib = _image_steps(a), _image_steps(b)
    if ia == ib:
        return True
    if ia is None or ib is None or len(ib) != 1:
        return False
    return float(b["minzoom"]).is_integer()  # a step only at a whole (tile) zoom


def _join(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    out["maxzoom"] = b["maxzoom"] if "maxzoom" in b else out.pop("maxzoom")
    ia, ib = _image_steps(a), _image_steps(b)
    if ia is not None and ib is not None and ia != ib and ib[0] != ia[-1]:
        steps = ia + [int(b["minzoom"]), ib[0]]
        out["layout"]["icon-image"] = ["step", ["zoom"], *steps]
    return out


def join_zoom_split_layers(layers: List[dict]) -> List[dict]:
    """``layers`` with the neighbouring pieces of one zoom-split layer joined."""
    out: List[dict] = []
    for layer in layers:
        if out and _joinable(out[-1], layer):
            out[-1] = _join(out[-1], layer)
        else:
            out.append(layer)
    return out
