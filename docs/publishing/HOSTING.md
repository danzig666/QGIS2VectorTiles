# Hosting a published web map

The map is a folder of static files. The map data is one PMTiles archive read with HTTP
byte ranges, so any static host that answers `Range` requests correctly works; no tile
server, database or Docker is needed.

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

## Other S3-compatible storage

Choose *Other S3-compatible storage*, enter the S3 API endpoint, bucket, region and the
public base URL. Only enable *conditional writes* when the service supports `If-Match` /
`If-None-Match` on PutObject; without it, activation is refused (share versioned links).

## Any static web server

Copy the publication folder to the server (e.g. with *Destination: Local* into a web root).
The server must answer `Range` requests with `206 Partial Content`, must not gzip
`.pmtiles` responses, and must serve `.mjs` as `text/javascript`. nginx and Apache do this
by default for static files (add `types { text/javascript mjs; }` to old nginx versions).

## Troubleshooting (viewer error codes)

| Code | Meaning |
|---|---|
| `Q2VT_PUB_RANGE_UNSUPPORTED` | The server returned the whole file for a range request. |
| `Q2VT_PUB_CORS` | Data on another origin without a matching CORS rule. |
| `Q2VT_PUB_NOT_MVT` | The archive is not vector tiles (or a raster source was added). |
| `Q2VT_PUB_SCHEMA_UNSUPPORTED` | Published by a newer plugin version than the viewer. |
| `Q2VT_PUB_WEBGL` | The browser/device cannot draw WebGL. |
