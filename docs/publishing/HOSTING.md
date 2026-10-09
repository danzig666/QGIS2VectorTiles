# Hosting a published web map

The map is a folder of static files. The map data is one PMTiles archive read with HTTP
byte ranges, so any static host that answers `Range` requests correctly works; no tile
server, database or Docker is needed. The search index, the feature records (popups, links)
and the parcel report are one `.pack` file each, read the same way, so even a city's map is
a few hundred files to upload, not tens of thousands.

```text
<publication>/index.html        stable address (follows current.json)
<publication>/current.json      which release is current (never cached)
<publication>/releases/<id>/    one immutable release, also openable directly
```

Share `…/<publication>/index.html` for "always the current map", or
`…/releases/<id>/index.html` for "exactly this version". Object storage does not turn
`/maps/name/` into `index.html` by itself; use the explicit `index.html` address unless
you configure routing.

## Local preview

*Publish Web Map → Export locally → Preview* serves the folder on `127.0.0.1` with byte
ranges and opens the browser. Opening `index.html` as a `file://` page does not work
(browsers block modules and range reads there).

## Cloudflare R2 (one-time setup)

The Publish window has the same steps built in: **Destination → Step-by-step: set up
Cloudflare R2…**, and every field explains itself in its tooltip.

1. **Open R2**: dash.cloudflare.com → *R2 Object Storage* (activate it the first time; there
   is a free monthly allowance, Cloudflare may ask for a payment method).
2. **Account id**: R2 overview → *Account Details* → *Account ID* (32 characters), or copy the
   *S3 API* address `https://<account id>.r2.cloudflarestorage.com` — the plugin takes the id
   (and a bucket name after the slash) from it, also from a dashboard page address. Leave
   *S3 API endpoint* empty unless the bucket is in a jurisdiction (EU, FedRAMP).
3. **Bucket**: *Create bucket* → e.g. `maps` (lowercase letters, digits, hyphens).
4. **Prefix**: a folder in the bucket per map, e.g. `maps/arlo` (empty: `maps/<slug>`).
   Several maps can share one bucket; the plugin writes only inside its prefix.
5. **Public address**: bucket → *Settings* → *Custom Domains* → *Add* → a subdomain of a
   domain on Cloudflare in the same account, e.g. `maps.example.com` → *Connect Domain*; wait
   until it is *Active*. Enter `https://maps.example.com` as *Public base URL*; the window
   shows the resulting *Map address*. (`r2.dev` addresses are rate limited and meant for
   development.)
6. **API token**: R2 overview → *Account Details* → *API Tokens* → *Manage* → *Create API
   token* (an *Account* token keeps working when your user leaves the account) →
   *Object Read & Write*, *Specify bucket(s)*: only this bucket → copy the *Access Key ID* and
   the *Secret Access Key* (the secret is shown only once).
7. **Keys in QGIS**: paste both into *Or keys for this session only* and click *Save keys in
   QGIS…* — they are stored encrypted in QGIS's authentication database (master password
   protected) and selected under *Saved credentials*. (Or create a *Basic* configuration by
   hand: user name = Access Key ID, password = Secret Access Key.)
8. **Test connection**: every line should show ✔.
9. **CORS**: not needed when the viewer and the data are on the same domain (the default).
   For a viewer on another domain, *Show CORS policy* prints a policy to merge in the bucket
   settings; the plugin never changes CORS, domains or public access itself.

Each *Publish* exports locally, uploads a new release, checks it through the public domain
(content types, real byte ranges, a tile read through the archive) and only then switches
`current.json` with a conditional write. If anything fails, the previous map stays online.

**Costs**: storage of the archive and kept releases, plus requests (a map view makes many
small range requests). R2 has no egress fees; usage above the free tier and domain costs
are billed by Cloudflare — see https://developers.cloudflare.com/r2/pricing/ (the review
tab shows sizes, not a bill).

## Faster re-exports and uploads

* **Export cache** (*Output → Reuse unchanged layers from earlier exports*, on by default):
  layers whose data files, style and the export settings did not change since an earlier
  export in the same output folder are not processed again — their datasets, vector tiles
  and the parcel report are reused from `<output folder>/.q2vt-cache`. Editing one layer
  redoes only that layer. Database and web layers (PostGIS, WFS, memory layers) are always
  exported. The cache is pruned automatically (unused for 30 days, or beyond 4 GB);
  *Clear cache…* deletes it. It stays on your computer and is never uploaded.
* **Uploads**: a new release copies the files that did not change (same SHA-256) from the
  current release inside the bucket instead of uploading them again; only changed files —
  usually the map archive and a few small JSON files — are sent.
* **Parallel export** (4.28, automatic): from 150 datasets on, worker processes (headless QGIS
  instances started from QGIS's own Python, as many as *Output → CPU limit* allows) read the
  file layers and prepare the datasets in parallel; the vector tiles of each layer are cut by
  their own `ogr2ogr`, several at a time, while the records, legend, rasters and basemap are
  prepared. The result is the same as in one process. Database, web and virtual layers, and
  rules whose expressions read other layers of the project, are still done by QGIS itself.
  `Q2VT_WORKERS=0` (environment variable) turns the workers off, `Q2VT_WORKERS=n` sets their
  number.
* **Fast marker lines** (*Output*, off by default): marker lines whose spacing is in screen
  units (points, millimetres, pixels) are normally computed at QGIS's positions for every zoom
  level — on a plan with many such lines most of the export. With this option the browser
  places them along the lines instead: a much faster export, but the markers are not exactly
  where QGIS draws them (the fidelity report says so).
* **Network output folder** (a Windows share, a mapped network drive, an NFS/SMB mount): the
  work files and the export cache stay on this computer (system temp folder); only the
  finished release is written to the network folder, and the export log and fidelity report
  are copied next to it (`.q2vt-work/<slug>/<export>/`).
* The export log (`export_log.txt`) names the **slowest layers** (seconds for their datasets
  and for their tiles): where to look first when an export is slow. A big layer's tiles are
  made in several pieces side by side ("in 26 parallel pieces"): their seconds add up to more
  than the tiles took.
* A layer drawn at every scale (no *scale-dependent visibility*), such as every parcel of a
  city, is in the tiles of every zoom level: zoomed out, one tile holds all of it (several MB).
  A scale range in QGIS (e.g. parcels from 1:25,000) makes the export faster and the web map
  lighter, and the web map then matches QGIS at those scales too. Or, for the web map only:
  *Map* tab → **Zoomed-out load…** estimates each layer's part of the heaviest tile and
  suggests a zoom from which its features, or only its labels, are drawn (labels zoomed far
  out are slow and mostly cannot be placed anyway); the checked suggestions go into the
  *Scales* column with one click.

## Other S3-compatible storage

Choose *Other S3-compatible storage*, enter the S3 API endpoint, bucket, region and the
public base URL. Only enable *conditional writes* when the service supports `If-Match` /
`If-None-Match` on PutObject; without it, activation is refused (share versioned links).

## Any static web server

Copy the publication folder to the server (e.g. with *Destination: Local only (no upload)* into a web root).
The server must answer `Range` requests with `206 Partial Content`, must not gzip
`.pmtiles` and `.pack` responses, and must serve `.mjs` as `text/javascript`. nginx and Apache
do this by default for static files (add `types { text/javascript mjs; }` to old nginx versions).

Range support is required: a server that answers a range request with the whole file
(`200 OK`) cannot serve the map, and the viewer shows `Q2VT_PUB_RANGE_UNSUPPORTED` instead of
it. Python's `python -m http.server`, for example, does not support ranges; use the plugin's
*Preview* to look at an export locally. (The `.pack` files are read even from a server that
sometimes sends a whole file, but the map archive is not.)

## Troubleshooting (viewer error codes)

| Code | Meaning |
|---|---|
| `Q2VT_PUB_RANGE_UNSUPPORTED` | The server returned the whole file for a range request. |
| `Q2VT_PUB_CORS` | Data on another origin without a matching CORS rule. |
| `Q2VT_PUB_NOT_MVT` | The archive is not vector tiles (or a raster source was added). |
| `Q2VT_PUB_SCHEMA_UNSUPPORTED` | Published by a newer plugin version than the viewer. |
| `Q2VT_PUB_WEBGL` | The browser/device cannot draw WebGL. |
