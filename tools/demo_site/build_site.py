"""Assemble the GitHub Pages demo site from the exported showcase map.

Usage: python3 build_site.py <work dir> [ffmpeg]
Writes <work dir>/site:
  index.html     the landing page (tools/demo_site/site/index.html)
  demo/          the published web map (<work dir>/out/swellendam)
  media/         the showcase video (docs/media), plus a WebM (VP9) copy for
                 browsers without H.264 when ffmpeg is given or on the PATH
  img/           the icon, the video poster and the close-up gallery (JPEG)
  .nojekyll      serve the files as they are
"""
import os
import shutil
import subprocess
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def main(work, ffmpeg=None):
    publication = os.path.join(work, "out", "swellendam")
    if not os.path.isfile(os.path.join(publication, "current.json")):
        sys.exit(f"No published map in {publication}: run export_showcase.py first.")
    site = os.path.join(work, "site")
    shutil.rmtree(site, ignore_errors=True)
    os.makedirs(os.path.join(site, "img"))
    os.makedirs(os.path.join(site, "media"))
    shutil.copy(os.path.join(HERE, "site", "index.html"), site)
    shutil.copytree(publication, os.path.join(site, "demo"))
    open(os.path.join(site, ".nojekyll"), "w").close()

    shutil.copy(os.path.join(REPO, "icon.svg"), os.path.join(site, "img", "icon.svg"))
    for source, name in (("qgis-vs-qwebmap.png", "poster.jpg"), ("symbology-gallery.png", "gallery.jpg")):
        Image.open(os.path.join(REPO, "docs", "images", source)).convert("RGB").save(
            os.path.join(site, "img", name), quality=85, optimize=True, progressive=True)

    video = os.path.join(site, "media", "qwebmap-showcase.mp4")
    shutil.copy(os.path.join(REPO, "docs", "media", "qwebmap-showcase.mp4"), video)
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    if ffmpeg:
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", video,
                        "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "34", "-row-mt", "1",
                        "-deadline", "good", "-cpu-used", "3", "-pix_fmt", "yuv420p", "-an",
                        video[:-4] + ".webm"], check=True)
    else:
        print("ffmpeg not found: no WebM copy (the page then offers the MP4 only).")
    print("site:", site)


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    main(os.path.abspath(sys.argv[1]), sys.argv[2] if len(sys.argv) == 3 else None)
