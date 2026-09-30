"""
assets.py

Deterministic sprite-atlas packing from completed images.

The packer never renders anything: renderers produce one image per pixel
ratio, and the packer lays them out (sorted, shelf packing, fixed gutters) so
that the same inputs always produce the same sheets and JSON. Each entry
keeps its own ``pixelRatio`` per sheet, so a pattern rendered at true 1x and
2x resolution is described correctly in both indexes.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class AtlasEntry:
    name: str
    # {sheet_ratio: (PIL image, declared pixelRatio)}
    images: Dict[int, Tuple[object, float]] = field(default_factory=dict)
    # Extra sprite metadata in *logical* pixels: stretchX / stretchY (lists
    # of [from, to]) and content ([left, top, right, bottom]). Scaled by each
    # image's pixelRatio when written.
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass
class Atlas:
    sheets: Dict[int, object]          # sheet ratio -> PIL image
    index: Dict[int, Dict[str, dict]]  # sheet ratio -> sprite JSON dict


def pack(entries: List[AtlasEntry], ratios=(1, 2), gutter: int = 2,
         max_row_width: int = 1024) -> Atlas:
    """Pack entries into one sheet per ratio with shared slot layout."""
    from PIL import Image  # pylint: disable=import-outside-toplevel

    ordered = sorted(entries, key=lambda e: e.name)

    def slot(entry: AtlasEntry) -> Tuple[int, int]:
        width = height = 1
        for ratio in ratios:
            img, _ = entry.images[ratio]
            width = max(width, math.ceil(img.width / ratio))
            height = max(height, math.ceil(img.height / ratio))
        return width, height

    positions: Dict[str, Tuple[int, int]] = {}
    x = y = gutter
    row_height = 0
    sheet_w = sheet_h = 0
    for entry in ordered:
        w, h = slot(entry)
        if x > gutter and x + w + gutter > max_row_width:
            x = gutter
            y += row_height + gutter
            row_height = 0
        positions[entry.name] = (x, y)
        x += w + gutter
        row_height = max(row_height, h)
        sheet_w = max(sheet_w, x)
        sheet_h = max(sheet_h, y + row_height + gutter)

    sheets: Dict[int, object] = {}
    index: Dict[int, Dict[str, dict]] = {}
    for ratio in ratios:
        sheet = Image.new("RGBA", (max(1, sheet_w * ratio), max(1, sheet_h * ratio)),
                          (0, 0, 0, 0))
        entries_json: Dict[str, dict] = {}
        for entry in ordered:
            img, pixel_ratio = entry.images[ratio]
            px, py = positions[entry.name]
            sheet.paste(img, (px * ratio, py * ratio))
            entries_json[entry.name] = {
                "x": px * ratio, "y": py * ratio,
                "width": img.width, "height": img.height,
                "pixelRatio": pixel_ratio,
            }
            for key, value in entry.metadata.items():
                entries_json[entry.name][key] = _scale_metadata(value, pixel_ratio)
        sheets[ratio] = sheet
        index[ratio] = entries_json
    return Atlas(sheets, index)


def _scale_metadata(value, factor):
    if isinstance(value, (list, tuple)):
        return [_scale_metadata(v, factor) for v in value]
    return round(value * factor)


def symmetric_crop_box(bbox, width: int, height: int):
    """Crop box around the image centre that contains ``bbox``.

    Cropping to the tight bounding box shifts the icon anchor whenever the
    drawing is not centred on the symbol origin. Expanding the box
    symmetrically about the render centre keeps the origin at the centre, so
    ``icon-anchor: center`` stays correct.
    """
    left, top, right, bottom = bbox
    cx, cy = width / 2.0, height / 2.0
    half_w = max(cx - left, right - cx)
    half_h = max(cy - top, bottom - cy)
    return (max(0, math.floor(cx - half_w)), max(0, math.floor(cy - half_h)),
            min(width, math.ceil(cx + half_w)), min(height, math.ceil(cy + half_h)))
