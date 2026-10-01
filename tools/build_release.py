"""
Build the installable plugin zip: releases/QGIS2VectorTilesFork-<version>.zip.

The zip holds the plugin files tracked by git (no tests, tools, docs or
caches) in a ``QGIS2VectorTilesFork`` folder, so QGIS installs the fork next to
the official QGIS2VectorTiles plugin instead of replacing it.

Usage::

    python3 tools/build_release.py          # version from metadata.txt
    python3 tools/build_release.py --out /tmp/plugin.zip
    python3 tools/build_release.py --variant variant.json

A variant branch (e.g. hu-hesz, see docs/BRANCHES.md) has a ``variant.json``
at the repository root: ``{"suffix": "hu", "name": "QGIS2VectorTiles (fork, HU)"}``.
Its zip is ``QGIS2VectorTilesFork-<version>-<suffix>.zip`` and the ``name`` in
the zipped metadata.txt is replaced; the folder and the version stay the same
(installing one replaces the other).
"""

import configparser
import json
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = "QGIS2VectorTilesFork"
CONTENT = ["__init__.py", "metadata.txt", "LICENSE", "icon.png", "icon.svg", "resources", "src"]


def read_variant(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        variant = json.load(handle)
    if not str(variant.get("suffix", "")).replace("-", "").isalnum():
        raise SystemExit(f"{path}: 'suffix' must be letters/digits")
    return variant


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    options = dict(zip(argv[::2], argv[1::2]))
    metadata = configparser.ConfigParser()
    metadata.read(os.path.join(ROOT, "metadata.txt"), encoding="utf-8")
    version = metadata["general"]["version"]
    variant = read_variant(options.get("--variant", os.path.join(ROOT, "variant.json")))
    if variant:
        version = f"{version}-{variant['suffix']}"
    files = subprocess.run(["git", "ls-files", "-z", *CONTENT], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split("\0")
    files = [path for path in files if path]
    out = os.path.abspath(options["--out"]) if "--out" in options else \
        os.path.join(ROOT, "releases", f"{FOLDER}-{version}.zip")
    folders = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo(f"{FOLDER}/"), "")
        for path in sorted(files):
            parts = path.split("/")[:-1]
            for depth in range(1, len(parts) + 1):
                folder = f"{FOLDER}/{'/'.join(parts[:depth])}/"
                if folder not in folders:
                    folders.add(folder)
                    archive.writestr(zipfile.ZipInfo(folder), "")
            if path == "metadata.txt" and variant and variant.get("name"):
                with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
                    text = handle.read()
                text = "\n".join(f"name={variant['name']}" if line.startswith("name=") else line
                                  for line in text.split("\n"))
                archive.writestr(f"{FOLDER}/{path}", text)
                continue
            archive.write(os.path.join(ROOT, path), f"{FOLDER}/{path}")
    print(f"{out}: {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
