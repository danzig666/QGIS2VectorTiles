# Web publishing — security and privacy

Published maps are **public**: anyone with the address can download the archive and every
file of the release. Hiding a field from a popup does not hide it from someone reading the
archive; what is not approved is simply not written.

## What becomes public

* Geometry of the published layers inside the export extent, as drawn by the exported rules.
* Generated rendering values (`q2vt_*`: label text, sizes, angles, draw order, feature key).
* Fields approved in the Interaction tab: popup fields (feature-lookup files), search fields
  (search index), filter fields (also written to the tiles of that layer).
* With *Publish ALL attribute fields* (not recommended): every attribute.
* Published **raster layers**: their image inside the export extent at the chosen zooms, as
  QGIS draws it. For online services (WMS, XYZ, ArcGIS) check that their licence allows
  republishing; the review lists them.
* The optional basemap is OpenStreetMap data (ODbL); its attribution is shown automatically.
* **Parcel report**: per parcel its computed areas, zone codes, the chosen parcel/zone fields,
  restriction titles/explanations/references and the chosen restriction name field. Owner
  names or other fields are only published if you tick them; the review lists the report.

The Review tab lists this per layer; the first publication and every change of this scope
need an explicit approval (stored with the settings as a fingerprint).

## Enforced before anything is written or uploaded

* Every tile is decoded; a property that is neither generated nor approved stops the
  release (`Q2VT_PUB_FIELD_DISCLOSURE`).
* Only allowlisted files are published (release.json inventory); projects, QLR, source
  data, MBTiles, logs, fidelity reports, temporary and script files are refused.
* Absolute local paths, known secret values and canaries are searched in every text file
  (and in decoded tiles for canaries) before upload (`Q2VT_PUB_SECRET_LEAK`).

## Credentials

* Profiles (saved in the project) hold only an authentication configuration id; keys are in
  the QGIS authentication database (encrypted, master password), or typed for one session.
* The S3 client receives explicit keys; environment variables, `~/.aws` files and instance
  roles are never used. TLS certificates are always verified.
* Errors and logs are redacted; keys never appear in Processing history, the project,
  public files, URLs or the upload journal.
* Use a bucket-scoped token with object read/write only. Bucket, domain, CORS and public
  access settings are changed by you in the Cloudflare dashboard, never by the plugin.
* **SSH / SFTP server**: the profile holds the host, user, folder and the *path* of a key
  file, never a key or password. A password (or key passphrase) reaches ssh only through a
  temporary askpass helper that reads it from the sftp process's environment; it is never on
  a command line, in a file, log or settings file, and the helper's folder is removed after
  each session; no shared connection (`ControlMaster`) outlives the upload with it. The saved
  login of R2 / S3 is never used for SSH, nor the other way round. A new server's host key is
  accepted on first use and remembered; a changed key stops the upload. Only files listed in
  the folder's `.q2vt-files.json` (safe relative paths inside the folder) are ever deleted.

## Export cache

The export cache (`<output folder>/.q2vt-cache`) holds intermediate datasets and tiles of the
published layers (the fields the tiles need), next to the local release folders. It is never
uploaded — uploads send only the files listed in a validated release inventory — and it holds
no credentials. *Output → Clear cache…* deletes it; it is pruned after 30 days unused or
beyond 4 GB.

## Viewer

* No third-party requests (no CDN, no fonts, no telemetry). The basemap is copied into the
  release when exporting (only the export machine contacts the Protomaps build server, and
  only when that source is chosen); visitors never load it from elsewhere.
* Exceptions you switch on: web (XYZ) basemaps load from their tile servers, and **Google
  Street View** (*Interaction → Street View*) loads Google's Maps JavaScript API and the
  Street View coverage tiles, only after a visitor presses the Street View button. The
  page's CSP then allows Google's hosts, and the page sends its origin as referrer. The
  Google API key is necessarily **public** in the manifest: restrict it in Google Cloud to
  your site's address (HTTP referrers) and to the Maps JavaScript API and the Map Tiles API.
  The key is left out of the manifest while Street View is off.
* Attribution is plain text (not MapLibre's HTML attribution control).
* Content-Security-Policy meta tag: scripts only from the release (plus Google's hosts with
  Street View on), no `eval`, no inline scripts.
* All titles, descriptions, attribution and attribute values are inserted as text
  (`textContent`); links only for `http(s)`/`mailto` URL fields, with `noopener`.
* URL state is parsed strictly (known ids, ranges, size limits); nothing from the URL is
  evaluated as code or as a style expression.
* There is no password protection: a client-side password in front of a public archive
  would not protect anything. Private hosting needs server-side access control of every
  file (future extension).
