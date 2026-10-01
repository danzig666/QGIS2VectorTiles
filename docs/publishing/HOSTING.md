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

1. **Bucket**: create an R2 bucket (Cloudflare dashboard → R2 → Create bucket).
2. **Public address**: bucket → *Settings* → *Custom Domains* → connect a domain you manage
   in Cloudflare, e.g. `maps.example.com`. (`r2.dev` addresses are rate limited and meant for
   development.)
3. **API token**: R2 → *Manage R2 API Tokens* → create a token with *Object Read & Write*
   restricted to this bucket. Note the *Access Key ID*, the *Secret Access Key* and your
   *Account ID*.
4. **QGIS credentials**: Settings → Options → Authentication → add a *Basic* configuration
   with user name = Access Key ID and password = Secret Access Key (stored encrypted, master
   password protected). Or type the keys in the window for this session only.
5. **Publish window → Destination**: *Cloudflare R2*, account id, bucket, public base URL
   (`https://maps.example.com`), the saved credentials. *Test connection* checks bucket
   access.
6. **CORS**: not needed when the viewer and the data are on the same domain (the default).
   For a viewer on another domain, *Show CORS policy* prints a policy to merge in the bucket
   settings; the plugin never changes CORS, domains or public access itself.

Each *Publish* exports locally, uploads a new release, checks it through the public domain
(content types, real byte ranges, a tile read through the archive) and only then switches
`current.json` with a conditional write. If anything fails, the previous map stays online.

**Costs**: storage of the archive and kept releases, plus requests (a map view makes many
small range requests). R2 has no egress fees; usage above the free tier and domain costs
are billed by Cloudflare — see https://developers.cloudflare.com/r2/pricing/ (the review
tab shows sizes, not a bill).

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
