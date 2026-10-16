"""
Build the MapLibre GL JS bundle of the viewer (resources/ml_viewer/
maplibre-gl.mjs and maplibre-gl-worker.mjs): MapLibre's readable build,
patched so that QGIS symbology draws as QGIS draws it, then minified.

MapLibre and esbuild are pinned in package.json / package-lock.json (npm
checks their checksums). The patches are in patches/, one file per fix:
each lists exact text edits of the readable build (old text, new text, how
many times the old text occurs) and says why. A patch whose text is no
longer found stops the build and names it.

    cd tools/maplibre && npm ci           # once, and after changing a pin
    python3 tools/maplibre/build.py       # patch, minify, write the bundle
    python3 tools/maplibre/build.py --check     # bundle up to date?
    python3 tools/maplibre/build.py --readable  # unminified bundle (debugging)

Upgrading MapLibre: change its version in package.json, run ``npm install``
there, run this script and fix each patch it reports (README.md).
"""

import glob
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "resources", "ml_viewer")
DIST = os.path.join(HERE, "node_modules", "maplibre-gl", "dist")
ESBUILD = os.path.join(HERE, "node_modules", ".bin", "esbuild")
# Readable build -> bundle file name (MapLibre loads the worker next to
# the main module by this name).
FILES = {"main": ("maplibre-gl-dev.mjs", "maplibre-gl.mjs"),
         "worker": ("maplibre-gl-worker-dev.mjs", "maplibre-gl-worker.mjs")}


def _tabs(text: str) -> str:
    """Patches are written with 4-space indents; the readable build uses tabs."""
    lines = []
    for line in text.split("\n"):
        stripped = line.lstrip(" ")
        lines.append("\t" * ((len(line) - len(stripped)) // 4) + stripped)
    return "\n".join(lines)


def load_patches():
    patches = []
    for path in sorted(glob.glob(os.path.join(HERE, "patches", "[0-9]*.py"))):
        spec = importlib.util.spec_from_file_location(os.path.basename(path)[:-3], path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        patches.append((os.path.basename(path)[:-3], module.EDITS))
    return patches


def apply_patches(texts: dict) -> dict:
    """``texts``: {"main": source, "worker": source}. Each edit is (target,
    old, new, count), target "main", "worker" or "both" (count per file)."""
    texts = dict(texts)
    errors = []
    for name, edits in load_patches():
        for number, (target, old, new, count) in enumerate(edits, 1):
            old, new = _tabs(old), _tabs(new)
            for key in (("main", "worker") if target == "both" else (target,)):
                found = texts[key].count(old)
                if found != count:
                    first = old.strip().split("\n")[0][:80]
                    errors.append(f"{name} edit {number} ({key}): found {found}x, expected {count}x: {first}")
                    continue
                texts[key] = texts[key].replace(old, new)
    if errors:
        raise SystemExit("Patches that no longer fit MapLibre's code:\n  " + "\n  ".join(errors))
    return texts


def _pinned_version() -> str:
    with open(os.path.join(HERE, "package.json"), encoding="utf-8") as handle:
        return json.load(handle)["dependencies"]["maplibre-gl"]


def _check_install():
    try:
        with open(os.path.join(DIST, "..", "package.json"), encoding="utf-8") as handle:
            installed = json.load(handle)["version"]
    except OSError:
        raise SystemExit("MapLibre is not installed: run `npm ci` in tools/maplibre") from None
    if installed != _pinned_version():
        raise SystemExit(f"node_modules has MapLibre {installed}, package.json pins "
                         f"{_pinned_version()}: run `npm ci` in tools/maplibre")


def build(out_dir: str, minify: bool = True):
    _check_install()
    texts = {}
    for key, (source, _) in FILES.items():
        with open(os.path.join(DIST, source), encoding="utf-8") as handle:
            texts[key] = handle.read()
    names = [name for name, _ in load_patches()]
    texts = apply_patches(texts)
    banner = f"/*! QWebMap patches: {', '.join(names)} (tools/maplibre) */"
    with tempfile.TemporaryDirectory() as work:
        for key, (source, target) in FILES.items():
            patched = os.path.join(work, source)
            with open(patched, "w", encoding="utf-8") as handle:
                handle.write(texts[key])
            command = [ESBUILD, patched, "--format=esm", "--legal-comments=inline",
                       "--log-level=warning", f"--banner:js={banner}",
                       f"--outfile={os.path.join(out_dir, target)}"]
            if minify:
                command.append("--minify")
            subprocess.run(command, check=True)
    shutil.copyfile(os.path.join(DIST, "maplibre-gl.css"), os.path.join(out_dir, "maplibre-gl.css"))
    shutil.copyfile(os.path.join(DIST, "..", "LICENSE.txt"), os.path.join(out_dir, "MAPLIBRE-LICENSE.txt"))


BUILT = ["maplibre-gl.mjs", "maplibre-gl-worker.mjs", "maplibre-gl.css", "MAPLIBRE-LICENSE.txt"]


def main() -> int:
    if "--check" in sys.argv:
        with tempfile.TemporaryDirectory() as fresh:
            build(fresh)
            stale = []
            for name in BUILT:
                with open(os.path.join(fresh, name), "rb") as a:
                    try:
                        with open(os.path.join(OUT, name), "rb") as b:
                            same = a.read() == b.read()
                    except OSError:
                        same = False
                if not same:
                    stale.append(name)
        print("up to date" if not stale else "OUT OF DATE: " + ", ".join(stale))
        return 1 if stale else 0
    build(OUT, minify="--readable" not in sys.argv)
    print(f"MapLibre {_pinned_version()} built into {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
