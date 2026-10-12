// Coordinate and measurement helpers (pure, no DOM).
//
// EOV (EPSG:23700): the same operation PROJ uses by default for
// WGS 84 -> EOV ("Inverse of HD72 to WGS 84 (4)" + "Egyseges Orszagos
// Vetuleti"): a 3-parameter geocentric shift WGS 84 -> HD72 (GRS 67)
// followed by the oblique Mercator (somerc) projection. The datum shift is
// an approximation of about 1 m; the result is labelled approximate and is
// not survey grade. Outside Hungary's area of use it returns null.
//
// Measurements: Vincenty distances on the WGS 84 ellipsoid; areas on the
// authalic sphere (equal-area latitude) — approximate, stated in the UI.

const WGS84 = { a: 6378137.0, f: 1 / 298.257223563 };
const GRS67 = { a: 6378160.0, f: 1 / 298.247167427 };
const SHIFT = [-52.17, 71.82, 14.9]; // WGS 84 -> HD72 geocentric (metres)
const EOV = { lat0: 47.1443937222222, lon0: 19.0485717777778, k0: 0.99993, x0: 650000, y0: 200000 };
export const EOV_AREA = { west: 16.11, south: 45.74, east: 22.9, north: 48.58 };
const D2R = Math.PI / 180;

function toCartesian(lon, lat, h, ell) {
  const e2 = ell.f * (2 - ell.f);
  const sin = Math.sin(lat), cos = Math.cos(lat);
  const n = ell.a / Math.sqrt(1 - e2 * sin * sin);
  return [(n + h) * cos * Math.cos(lon), (n + h) * cos * Math.sin(lon), (n * (1 - e2) + h) * sin];
}

function toGeodetic([x, y, z], ell) {
  const e2 = ell.f * (2 - ell.f);
  const p = Math.hypot(x, y);
  let lat = Math.atan2(z, p * (1 - e2));
  for (let i = 0; i < 10; i++) {
    const sin = Math.sin(lat);
    const n = ell.a / Math.sqrt(1 - e2 * sin * sin);
    const next = Math.atan2(z + e2 * n * sin, p);
    if (Math.abs(next - lat) < 1e-14) { lat = next; break; }
    lat = next;
  }
  return [Math.atan2(y, x), lat];
}

const SOMERC = (() => {
  const es = GRS67.f * (2 - GRS67.f);
  const e = Math.sqrt(es);
  const phi0 = EOV.lat0 * D2R;
  const cp = Math.cos(phi0) ** 2;
  const c = Math.sqrt(1 + es * cp * cp / (1 - es));
  let sp = Math.sin(phi0);
  const sinp0 = sp / c;
  const phip0 = Math.asin(sinp0);
  const cosp0 = Math.cos(phip0);
  sp *= e;
  const K = Math.log(Math.tan(Math.PI / 4 + 0.5 * phip0))
    - c * (Math.log(Math.tan(Math.PI / 4 + 0.5 * phi0)) - 0.5 * e * Math.log((1 + sp) / (1 - sp)));
  const kR = EOV.k0 * Math.sqrt(1 - es) / (1 - sp * sp);
  return { e, c, K, kR, sinp0, cosp0 };
})();

// [easting (EOV Y), northing (EOV X)] in metres, or null outside the area.
export function wgs84ToEov(lon, lat) {
  if (!(lon >= EOV_AREA.west && lon <= EOV_AREA.east && lat >= EOV_AREA.south && lat <= EOV_AREA.north)) return null;
  const xyz = toCartesian(lon * D2R, lat * D2R, 0, WGS84).map((v, i) => v + SHIFT[i]);
  const [lam, phi] = toGeodetic(xyz, GRS67);
  const { e, c, K, kR, sinp0, cosp0 } = SOMERC;
  const sp = e * Math.sin(phi);
  const phip = 2 * Math.atan(Math.exp(c * (Math.log(Math.tan(Math.PI / 4 + 0.5 * phi))
    - 0.5 * e * Math.log((1 + sp) / (1 - sp))) + K)) - Math.PI / 2;
  const lamp = c * (lam - EOV.lon0 * D2R);
  const cp = Math.cos(phip);
  const phipp = Math.asin(cosp0 * Math.sin(phip) - sinp0 * cp * Math.cos(lamp));
  const lampp = Math.asin(cp * Math.sin(lamp) / Math.cos(phipp));
  return [GRS67.a * kR * lampp + EOV.x0, GRS67.a * kR * Math.log(Math.tan(Math.PI / 4 + 0.5 * phipp)) + EOV.y0];
}

// Vincenty inverse distance (metres) on WGS 84.
export function distance([lon1, lat1], [lon2, lat2]) {
  const { a, f } = WGS84;
  const b = a * (1 - f);
  const L = (lon2 - lon1) * D2R;
  const U1 = Math.atan((1 - f) * Math.tan(lat1 * D2R));
  const U2 = Math.atan((1 - f) * Math.tan(lat2 * D2R));
  const sinU1 = Math.sin(U1), cosU1 = Math.cos(U1), sinU2 = Math.sin(U2), cosU2 = Math.cos(U2);
  let lambda = L, previous, sinSigma, cosSigma, sigma, cos2Alpha, cos2SigmaM;
  for (let i = 0; i < 200; i++) {
    const sinL = Math.sin(lambda), cosL = Math.cos(lambda);
    sinSigma = Math.hypot(cosU2 * sinL, cosU1 * sinU2 - sinU1 * cosU2 * cosL);
    if (sinSigma === 0) return 0;
    cosSigma = sinU1 * sinU2 + cosU1 * cosU2 * cosL;
    sigma = Math.atan2(sinSigma, cosSigma);
    const sinAlpha = cosU1 * cosU2 * sinL / sinSigma;
    cos2Alpha = 1 - sinAlpha * sinAlpha;
    cos2SigmaM = cos2Alpha ? cosSigma - 2 * sinU1 * sinU2 / cos2Alpha : 0;
    const C = f / 16 * cos2Alpha * (4 + f * (4 - 3 * cos2Alpha));
    previous = lambda;
    lambda = L + (1 - C) * f * sinAlpha * (sigma + C * sinSigma * (cos2SigmaM + C * cosSigma * (-1 + 2 * cos2SigmaM ** 2)));
    if (Math.abs(lambda - previous) < 1e-12) break;
  }
  const u2 = cos2Alpha * (a * a - b * b) / (b * b);
  const A = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)));
  const B = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)));
  const deltaSigma = B * sinSigma * (cos2SigmaM + B / 4 * (cosSigma * (-1 + 2 * cos2SigmaM ** 2)
    - B / 6 * cos2SigmaM * (-3 + 4 * sinSigma ** 2) * (-3 + 4 * cos2SigmaM ** 2)));
  return b * A * (sigma - deltaSigma);
}

export function lineLength(points) {
  let total = 0;
  for (let i = 1; i < points.length; i++) total += distance(points[i - 1], points[i]);
  return total;
}

// Polygon area (m²) on the authalic sphere of WGS 84 (equal-area latitude).
export function polygonArea(points) {
  if (points.length < 3) return 0;
  const { a, f } = WGS84;
  const e2 = f * (2 - f), e = Math.sqrt(e2);
  const q = (phi) => {
    const s = Math.sin(phi);
    return (1 - e2) * (s / (1 - e2 * s * s) - Math.log((1 - e * s) / (1 + e * s)) / (2 * e));
  };
  const qp = q(Math.PI / 2);
  const R = a * Math.sqrt(qp / 2);
  const beta = (lat) => Math.asin(q(lat * D2R) / qp);
  let sum = 0;
  for (let i = 0; i < points.length; i++) {
    const [lon1, lat1] = points[i];
    const [lon2, lat2] = points[(i + 1) % points.length];
    sum += (lon2 - lon1) * D2R * (2 + Math.sin(beta(lat1)) + Math.sin(beta(lat2)));
  }
  return Math.abs(sum * R * R / 2);
}
