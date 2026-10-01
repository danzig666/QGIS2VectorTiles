"""
Vendor the S3 SDK (boto3 + botocore + s3transfer + dependencies) into
src/publishing/vendor/s3, trimmed to the S3 service, from pinned,
hash-checked wheels. Development tool; the plugin never runs pip.

    python3 tools/publishing/vendor_s3.py

Pinned wheels (PyPI) and their SHA-256 are listed in WHEELS; the script
refuses a wheel whose hash differs. Licences are copied next to the code.
"""

import hashlib
import io
import os
import shutil
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TARGET = os.path.join(ROOT, "src", "publishing", "vendor", "s3")
WHEELS = {
    "boto3-1.43.106-py3-none-any.whl": None,
    "botocore-1.43.106-py3-none-any.whl": None,
    "s3transfer-0.19.2-py3-none-any.whl": None,
    "jmespath-1.1.0-py3-none-any.whl": None,
    "python_dateutil-2.9.0.post0-py2.py3-none-any.whl": None,
    "six-1.17.0-py2.py3-none-any.whl": None,
    "urllib3-2.8.0-py3-none-any.whl": None,
}
HASHES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor_s3.sha256")
KEEP_BOTOCORE_DATA = {"s3", "endpoints.json", "partitions.json", "sdk-default-configuration.json",
                      "_retry.json"}
KEEP_BOTO3_DATA = {"s3"}


def _pypi_url(name: str) -> str:
    project = name.split("-")[0].replace("_", "-")
    data = urllib.request.urlopen(f"https://pypi.org/pypi/{project}/json", timeout=60).read()
    import json  # pylint: disable=import-outside-toplevel
    for files in json.loads(data)["releases"].values():
        for item in files:
            if item["filename"] == name:
                return item["url"]
    raise SystemExit(f"{name} not on PyPI")


def main() -> int:
    expected = {}
    if os.path.exists(HASHES_FILE):
        for line in open(HASHES_FILE, encoding="utf-8"):
            digest, name = line.split()
            expected[name] = digest
    shutil.rmtree(TARGET, ignore_errors=True)
    os.makedirs(os.path.join(TARGET, "licenses"))
    recorded = []
    for name in WHEELS:
        data = urllib.request.urlopen(_pypi_url(name), timeout=120).read()
        digest = hashlib.sha256(data).hexdigest()
        if name in expected and expected[name] != digest:
            raise SystemExit(f"{name}: SHA-256 {digest} != pinned {expected[name]}")
        recorded.append(f"{digest}  {name}")
        with zipfile.ZipFile(io.BytesIO(data)) as wheel:
            for member in wheel.namelist():
                parts = member.split("/")
                if parts[0].endswith(".dist-info"):
                    if parts[-1].upper().startswith(("LICENSE", "NOTICE", "COPYING")) or \
                            (len(parts) > 2 and parts[1] == "licenses"):
                        dest = os.path.join(TARGET, "licenses", f"{name.split('-')[0]}-{parts[-1]}")
                        with open(dest, "wb") as handle:
                            handle.write(wheel.read(member))
                    continue
                if parts[0] == "botocore" and len(parts) > 2 and parts[1] == "data" \
                        and parts[2] not in KEEP_BOTOCORE_DATA:
                    continue
                if parts[0] == "boto3" and len(parts) > 2 and parts[1] == "data" \
                        and parts[2] not in KEEP_BOTO3_DATA:
                    continue
                if member.endswith("/") or "/tests/" in member or member.endswith(".pyc") \
                        or parts[0].endswith(".data"):
                    continue
                dest = os.path.join(TARGET, *parts)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as handle:
                    handle.write(wheel.read(member))
    if not expected:
        with open(HASHES_FILE, "w", encoding="utf-8") as handle:
            handle.write("\n".join(recorded) + "\n")
    print("\n".join(recorded))
    return 0


if __name__ == "__main__":
    sys.exit(main())
