"""Package the showcase project for download: Swellendam.qgz with its data
beside it (relative paths), the Open Sans fonts it uses and a README, in
<work dir>/Swellendam_QWebMap_demo.zip.

Usage: python3 package_project.py <work dir> [Open Sans font folder]
Reads <work dir>/showcase (from build_showcase.py and export_showcase.py).
The Publish window settings stay in the project, without the export folder
and project file of this machine."""
import os
import shutil
import sys
import zipfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(REPO, "tests")]
if len(sys.argv) not in (2, 3):
    sys.exit(__doc__)
WORK = os.path.abspath(sys.argv[1])
FONTS = sys.argv[2] if len(sys.argv) == 3 else None
NAME = "Swellendam_QWebMap_demo"
FONT_FILES = ("OpenSans-Regular.ttf", "OpenSans-Italic.ttf", "OpenSans-Bold.ttf",
              "OpenSans-Semibold.ttf", "OpenSans-SemiboldItalic.ttf")
README = """QWebMap demo project: Swellendam
================================

The QGIS project behind the live demo at https://danzig666.github.io/QWebMap/
Open it in QGIS next to the web map to compare the two.

1. Unzip everything into one folder (the project finds its data next to it).
2. Install the Open Sans fonts from the fonts folder (Windows: select the .ttf files,
   right click > Install) and restart QGIS. Without them the labels use another font.
3. Open Swellendam.qgz in QGIS 3.44 or newer.

To publish it yourself, install the QWebMap plugin
(https://github.com/danzig666/QWebMap/releases/latest) and open
Web > QWebMap: Publish Web Map... The settings of the demo (popups, search, zoom range)
are saved in the project. Choose an export folder on the Destination tab, then Export locally.

Data: Swellendam from the QGIS training data (https://github.com/qgis/QGIS-Training-Data,
GPL-2.0), derived from OpenStreetMap data. Open Sans font: Apache License 2.0.
"""

from q2vt_support import register_plugin_package  # noqa: E402

register_plugin_package()
from qgis.core import QgsProject  # noqa: E402
from q2vt_plugin.src.gui import publication_profiles as store  # noqa: E402

package = os.path.join(WORK, NAME)
shutil.rmtree(package, ignore_errors=True)
shutil.copytree(os.path.join(WORK, "showcase"), package,
                ignore=shutil.ignore_patterns("*.qgs~", "*_attachments.zip"))
project = QgsProject.instance()
# Read the copy, so that its data paths are saved relative to the package.
copy = os.path.join(package, "showcase.qgs")
if not project.read(copy):
    sys.exit("Cannot read showcase/showcase.qgs: run build_showcase.py first.")
found = store.active_profile(project)
if found:
    profile, _ = found
    profile.output.local_directory = ""
    project.setFileName("")  # no project file of this machine in the settings
    store.save_profile(project, profile)
if not project.write(os.path.join(package, "Swellendam.qgz")):
    sys.exit("Cannot write Swellendam.qgz")
os.remove(copy)
if FONTS:
    os.makedirs(os.path.join(package, "fonts"))
    for name in FONT_FILES:
        shutil.copy(os.path.join(FONTS, name), os.path.join(package, "fonts", name))
with open(os.path.join(package, "README.txt"), "w", encoding="utf-8") as handle:
    handle.write(README)

archive = os.path.join(WORK, NAME + ".zip")
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as out:
    for folder, _, files in os.walk(package):
        for name in sorted(files):
            path = os.path.join(folder, name)
            out.write(path, os.path.relpath(path, WORK))
print("package:", archive)
