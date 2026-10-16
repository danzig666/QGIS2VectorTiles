"""
Run build_gallery.py in batches: each batch of styles is exported and
rendered by its own process, so one style that hangs or runs out of memory
costs only its batch. Finished batches are kept and skipped when the run is
started again (resume); a batch that fails or passes its time limit is
recorded and tried again on the next run. The merged gallery (worst first,
over every finished batch) is rewritten after each batch, so it can be
looked at while the rest runs.

Usage (QGIS Python)::

    python3 tools/gallery/batch_gallery.py --style-db styles.db --tags hub \
        --out /tmp/gallery --batch-size 50 --timeout-min 20

Every other build_gallery.py option (--qml, --hard, --zooms, --size...) is
passed on to each batch. Output: <out>/batch_NNN/ per batch, <out>/index.html
merged, <out>/batches.json with each batch's state and time.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_gallery  # noqa: E402  pylint: disable=wrong-import-position


def count_styles(args) -> int:
    """How many styles build_gallery.py would take (same selection)."""
    build_gallery._APP = build_gallery.init_qgis()  # pylint: disable=protected-access  (QGIS must stay alive)
    probe = argparse.Namespace(style_db=args.style_db, tags=args.tags, qml=args.qml,
                               names=args.names, limit=0, offset=0)
    return len(build_gallery.load_items(probe))


def run_batch(index, offset, size, args, extra, out) -> dict:
    """One batch in its own process group (a timeout ends its ogr2ogr and
    worker processes too)."""
    folder = os.path.join(out, f"batch_{index:03d}")
    log = os.path.join(out, f"batch_{index:03d}.log")
    command = [sys.executable, os.path.join(HERE, "build_gallery.py"), "--out", folder,
               "--offset", str(offset), "--limit", str(size),
               "--port", str(args.port + index % 20)] + extra
    started = time.time()
    with open(log, "w", encoding="utf-8") as handle:
        process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            code = process.wait(timeout=args.timeout_min * 60)
            state = "done" if code == 0 and os.path.exists(
                os.path.join(folder, "images", "results.json")) else f"failed (exit {code})"
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            state = f"timeout ({args.timeout_min} min)"
    return {"batch": index, "offset": offset, "size": size, "state": state,
            "seconds": round(time.time() - started), "log": os.path.basename(log)}


def merge(out, batches, total):
    """One gallery page over every finished batch, worst first; image paths
    point into the batch folders."""
    cards = []
    for entry in batches:
        results = os.path.join(out, f"batch_{entry['batch']:03d}", "images", "results.json")
        if entry["state"] != "done" or not os.path.exists(results):
            continue
        prefix = f"batch_{entry['batch']:03d}/images/"
        with open(results, encoding="utf-8") as handle:
            for card in json.load(handle):
                for view in card.get("views") or []:
                    view["id"] = prefix + view["id"]
                card["id"] = prefix + card["id"]
                cards.append(card)
    cards.sort(key=lambda c: -c["score"]["shape"])
    done = [b for b in batches if b["state"] == "done"]
    failed = [b for b in batches if b["state"] != "done"]
    mean = sum(c["score"]["shape"] for c in cards) / max(1, len(cards))
    summary = (f"{len(cards)} of {total} styles in {len(done)} finished batches, mean shape "
               f"mismatch {mean:.1%}. Worst first.")
    if failed:
        summary += " Not finished: " + "; ".join(
            f"batch {b['batch']} (styles {b['offset']}-{b['offset'] + b['size'] - 1}): {b['state']}"
            for b in failed) + "."
    build_gallery.write_html(out, cards, summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--style-db")
    parser.add_argument("--tags", default="")
    parser.add_argument("--qml", nargs="*")
    parser.add_argument("--names", default="")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--timeout-min", type=float, default=6)
    parser.add_argument("--port", type=int, default=9200)
    args, extra = parser.parse_known_args()
    for flag, value in (("--style-db", args.style_db), ("--tags", args.tags), ("--names", args.names)):
        if value:
            extra += [flag, value]
    if args.qml:
        extra += ["--qml", *args.qml]

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    total = count_styles(args)
    state_path = os.path.join(out, "batches.json")
    previous = {}
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as handle:
            previous = {b["batch"]: b for b in json.load(handle)}
    batches = []
    count = (total + args.batch_size - 1) // args.batch_size
    for index in range(count):
        offset = index * args.batch_size
        size = min(args.batch_size, total - offset)
        old = previous.get(index)
        if old and old["state"] == "done" and old["offset"] == offset and old["size"] == size \
                and os.path.exists(os.path.join(out, f"batch_{index:03d}", "images", "results.json")):
            batches.append(old)
            print(f"BATCH {index + 1}/{count} kept (styles {offset}-{offset + size - 1})", flush=True)
            continue
        entry = run_batch(index, offset, size, args, extra, out)
        batches.append(entry)
        print(f"BATCH {index + 1}/{count} {entry['state']} in {entry['seconds']} s "
              f"(styles {offset}-{offset + size - 1})", flush=True)
        with open(state_path, "w", encoding="utf-8") as handle:
            json.dump(batches + [previous[i] for i in sorted(previous) if i > index], handle, indent=1)
        merge(out, batches, total)
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(batches, handle, indent=1)
    merge(out, batches, total)
    failed = [b for b in batches if b["state"] != "done"]
    print(f"GALLERY {len(batches) - len(failed)}/{len(batches)} batches done: "
          f"{os.path.join(out, 'index.html')}", flush=True)


if __name__ == "__main__":
    main()
