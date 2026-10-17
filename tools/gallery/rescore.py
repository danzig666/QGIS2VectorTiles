"""
Score an existing gallery again (after a change to the scoring), without
exporting or rendering: the QGIS and browser images, the layers (data/) and
the views stay as they are.

Usage (QGIS Python)::

    python3 tools/gallery/rescore.py /tmp/gallery        # build_gallery.py --out
    python3 tools/gallery/rescore.py /tmp/hub-gallery    # batch_gallery.py --out
"""

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import batch_gallery  # noqa: E402  pylint: disable=wrong-import-position
import build_gallery  # noqa: E402  pylint: disable=wrong-import-position
from along import view_paths  # noqa: E402  pylint: disable=wrong-import-position

RADIUS = build_gallery.EARTH / (2 * math.pi)


def mercator(lon, lat):
    return (RADIUS * math.radians(lon),
            RADIUS * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def rescore_run(out):
    """Score one build_gallery.py output folder again; rewrites its
    results.json and index.html."""
    from qgis.core import QgsVectorLayer
    img_dir = os.path.join(out, "images")
    with open(os.path.join(out, "views.json"), encoding="utf-8") as handle:
        views = {v["id"]: v for v in json.load(handle)}
    with open(os.path.join(img_dir, "results.json"), encoding="utf-8") as handle:
        cards = json.load(handle)
    for card in cards:
        card_views = card.get("views") or [{"id": card["id"], "zoom": None}]
        first = views[card_views[0]["id"]]
        layer = QgsVectorLayer(os.path.join(out, "data", f"{first['layer']}.gpkg"), first["layer"], "ogr")
        map_paths = build_gallery.layer_paths(layer)
        for view in card_views:
            meta = views[view["id"]]
            center = mercator(meta["lon"], meta["lat"])
            paths = view_paths(map_paths, center, meta["zoom"], meta["width"], build_gallery.EARTH)
            view["score"] = build_gallery.score(os.path.join(img_dir, f"{view['id']}_qgis.png"),
                                                os.path.join(img_dir, f"{view['id']}_browser.png"),
                                                paths, card["geometry"])
            view["zoom"] = meta["zoom"]
        card["views"] = card_views
        card["score"] = build_gallery.mean_score(card_views)
    build_gallery.sort_cards(cards)
    with open(os.path.join(img_dir, "results.json"), "w", encoding="utf-8") as handle:
        json.dump(cards, handle, indent=1, ensure_ascii=False)
    zooms = sorted({v["zoom"] for v in views.values()})
    build_gallery.write_html(img_dir, cards, f"{len(cards)} styles, zoom "
                             f"{', '.join(f'{z:g}' for z in zooms)}, "
                             f"{build_gallery.summarize(cards)}")
    return len(cards)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", help="output folder of build_gallery.py or batch_gallery.py")
    args = parser.parse_args()
    out = os.path.abspath(args.out)
    app = build_gallery.init_qgis()  # noqa: F841  keep QGIS alive
    state = os.path.join(out, "batches.json")
    if not os.path.exists(state):
        print(f"{rescore_run(out)} styles scored again: {os.path.join(out, 'images', 'index.html')}")
        return
    with open(state, encoding="utf-8") as handle:
        batches = json.load(handle)
    for entry in batches:
        folder = os.path.join(out, f"batch_{entry['batch']:03d}")
        if entry["state"] == "done" and os.path.exists(os.path.join(folder, "images", "results.json")):
            print(f"batch {entry['batch']}: {rescore_run(folder)} styles", flush=True)
    batch_gallery.merge(out, batches, sum(b["size"] for b in batches))
    print(f"Gallery scored again: {os.path.join(out, 'index.html')}")


if __name__ == "__main__":
    main()
