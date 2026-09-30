"""Regenerate docs/fidelity/CAPABILITIES.md from the capability registry.

Usage: python tools/generate_capabilities.py [--check]
``--check`` exits non-zero when the committed document is out of date.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "core"))

from fidelity.capabilities import capabilities_markdown  # noqa: E402  pylint: disable=wrong-import-position

TARGET = os.path.join(ROOT, "docs", "fidelity", "CAPABILITIES.md")


def main() -> int:
    content = capabilities_markdown()
    if "--check" in sys.argv:
        with open(TARGET, encoding="utf-8") as handle:
            return 0 if handle.read() == content else 1
    with open(TARGET, "w", encoding="utf-8") as handle:
        handle.write(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
