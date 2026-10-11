#!/bin/sh
# Publish <work dir>/site (from build_site.py) as the gh-pages branch, which
# GitHub Pages serves at https://danzig666.github.io/QGIS2VectorTiles/.
# The branch holds only the latest site: each run replaces it with one commit.
# Usage: sh publish_site.sh <work dir> [remote url]
set -e
[ -n "$1" ] || { echo "Usage: sh publish_site.sh <work dir> [remote url]"; exit 1; }
SITE="$1/site"
REMOTE="${2:-https://github.com/danzig666/QGIS2VectorTiles}"
[ -f "$SITE/index.html" ] || { echo "No site in $SITE: run build_site.py first."; exit 1; }
cd "$SITE"
rm -rf .git
git init -q -b gh-pages
git add -A
git commit -q -m "QWebMap live demo: Swellendam web map, video and embed code"
git push -f "$REMOTE" gh-pages
rm -rf .git
