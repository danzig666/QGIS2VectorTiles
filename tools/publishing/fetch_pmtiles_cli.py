"""
Download the official go-pmtiles CLI for *tests only* (independent PMTiles reader).

    python3 tools/publishing/fetch_pmtiles_cli.py   # prints the binary path
    Q2VT_PMTILES_CLI=<path> pytest tests/unit/test_publishing_pmtiles.py

The plugin never downloads or runs this tool; it is a developer/CI dependency.
"""

import hashlib
import io
import os
import platform
import sys
import tarfile
import urllib.request
import zipfile

VERSION = "1.28.0"
ASSETS = {
    ("Linux", "x86_64"): f"go-pmtiles_{VERSION}_Linux_x86_64.tar.gz",
    ("Darwin", "arm64"): f"go-pmtiles-{VERSION}_Darwin_arm64.zip",
    ("Darwin", "x86_64"): f"go-pmtiles-{VERSION}_Darwin_x86_64.zip",
    ("Windows", "AMD64"): f"go-pmtiles_{VERSION}_Windows_x86_64.zip",
}


def main() -> int:
    asset = ASSETS.get((platform.system(), platform.machine()))
    if asset is None:
        print("No go-pmtiles build known for this platform.", file=sys.stderr)
        return 1
    cache = os.path.join(os.path.expanduser("~"), ".cache", "q2vt", f"go-pmtiles-{VERSION}")
    binary = os.path.join(cache, "pmtiles.exe" if os.name == "nt" else "pmtiles")
    if not os.path.exists(binary):
        url = f"https://github.com/protomaps/go-pmtiles/releases/download/v{VERSION}/{asset}"
        data = urllib.request.urlopen(url, timeout=120).read()
        print(f"{asset}: sha256 {hashlib.sha256(data).hexdigest()}", file=sys.stderr)
        os.makedirs(cache, exist_ok=True)
        if asset.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                archive.extract(os.path.basename(binary), cache)
        else:
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                archive.extract("pmtiles", cache)
        os.chmod(binary, 0o755)
    print(binary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
