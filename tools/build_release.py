"""
Build the installable plugin zip: releases/QGIS2VectorTilesFork-<version>.zip.

The zip holds the plugin files tracked by git (no tests, tools, docs or
caches) in a ``QGIS2VectorTilesFork`` folder, so QGIS installs the fork next to
the official QGIS2VectorTiles plugin instead of replacing it.

Usage::

    python3 tools/build_release.py          # version from metadata.txt
    python3 tools/build_release.py --out /tmp/plugin.zip
"""

import configparser
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = "QGIS2VectorTilesFork"
CONTENT = ["__init__.py", "metadata.txt", "LICENSE", "icon.png", "icon.svg", "resources", "src"]


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    metadata = configparser.ConfigParser()
    metadata.read(os.path.join(ROOT, "metadata.txt"), encoding="utf-8")
    version = metadata["general"]["version"]
    files = subprocess.run(["git", "ls-files", "-z", *CONTENT], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split("\0")
    files = [path for path in files if path]
    out = os.path.join(ROOT, "releases", f"{FOLDER}-{version}.zip")
    if len(argv) == 2 and argv[0] == "--out":
        out = os.path.abspath(argv[1])
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
            archive.write(os.path.join(ROOT, path), f"{FOLDER}/{path}")
    print(f"{out}: {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
