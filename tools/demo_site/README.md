# Demo site (GitHub Pages)

The live demo at <https://danzig666.github.io/QGIS2VectorTiles/> is built from these files and served
from the `gh-pages` branch. It shows the Swellendam showcase project (the
[QGIS training data](https://github.com/qgis/QGIS-Training-Data), GPL-2.0, from OpenStreetMap)
published with the plugin, the showcase video and code for embedding the map.

| File | Step |
|---|---|
| `build_showcase.py` | Builds the QGIS project with the showcase symbology from the training data. |
| `export_showcase.py` | Publishes it with the plugin of this checkout (like *Export locally* in the Publish window). |
| `package_project.py` | Zips the QGIS project with its data and fonts for download (`download/` on the site). |
| `build_site.py` | Puts together the site: landing page, the published map in `demo/`, the project zip, video, images. |
| `site/index.html` | The landing page. |
| `publish_site.sh` | Pushes the site as the `gh-pages` branch. |

Run the scripts with a work folder **outside the repository**, because QGIS writes files into the
current folder. They need QGIS (PyQGIS) and Pillow; ffmpeg is optional and adds the WebM copy of
the video. Without a font folder the project zip has no fonts.

```sh
git clone --depth 1 https://github.com/qgis/QGIS-Training-Data ~/qgis-training-data
W=~/qwebmap-demo
mkdir -p $W && cd $W
python3 <repo>/tools/demo_site/build_showcase.py ~/qgis-training-data/exercise_data $W
python3 <repo>/tools/demo_site/export_showcase.py $W
python3 <repo>/tools/demo_site/package_project.py $W <folder with the Open Sans .ttf files>
python3 <repo>/tools/demo_site/build_site.py $W
sh <repo>/tools/demo_site/publish_site.sh $W
```

Before publishing, check the site locally with a server that answers HTTP range requests (the map
reads its PMTiles file in ranges, as GitHub Pages serves it), for example
`npx http-server $W/site`.

`publish_site.sh` replaces the whole `gh-pages` branch with one commit of the new site. GitHub
Pages publishes it a minute or so later.

The landing page opens the map at the town centre in the viewer's embedded mode
(`demo/?embed#v=1&map=14.7/-34.0232/20.4455`). Its embed code uses the same `?embed` address.
