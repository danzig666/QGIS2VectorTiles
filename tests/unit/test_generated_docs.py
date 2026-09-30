import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_capabilities_document_is_generated_from_registry():
    result = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "generate_capabilities.py"),
                             "--check"], capture_output=True)
    assert result.returncode == 0, "Run: python tools/generate_capabilities.py"
