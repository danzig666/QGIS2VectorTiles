"""
Minimal Mapbox Vector Tile (protobuf) decoder.

Decodes layers, feature ids, types and properties (geometry coordinates
only on request): enough to prove that an archive holds vector tiles, to
check source-layer names and fields, to scan published tiles for values
that must not be there, and to read the basemap's named streets.
Independent of the fidelity package's tile reader.
"""

import gzip
import struct
import zlib
from typing import Dict, Iterator, List, Tuple

GEOMETRY_TYPES = {0: "Unknown", 1: "Point", 2: "LineString", 3: "Polygon"}


class MvtDecodeError(ValueError):
    """The bytes are not a valid vector tile."""


def _varint(data: bytes, i: int) -> Tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(data):
            raise MvtDecodeError("truncated varint")
        byte = data[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7
        if shift > 70:
            raise MvtDecodeError("varint too long")


def _fields(data: bytes) -> Iterator[Tuple[int, int, object]]:
    """(field number, wire type, value) of a protobuf message."""
    i = 0
    while i < len(data):
        key, i = _varint(data, i)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise MvtDecodeError("field number 0")
        if wire == 0:
            value, i = _varint(data, i)
        elif wire == 1:
            if i + 8 > len(data):
                raise MvtDecodeError("truncated fixed64")
            value, i = data[i:i + 8], i + 8
        elif wire == 2:
            length, i = _varint(data, i)
            if i + length > len(data):
                raise MvtDecodeError("truncated bytes")
            value, i = data[i:i + length], i + length
        elif wire == 5:
            if i + 4 > len(data):
                raise MvtDecodeError("truncated fixed32")
            value, i = data[i:i + 4], i + 4
        else:
            raise MvtDecodeError(f"unsupported wire type {wire}")
        yield number, wire, value


def _packed(data: bytes) -> List[int]:
    out, i = [], 0
    while i < len(data):
        value, i = _varint(data, i)
        out.append(value)
    return out


def _zigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def _value(data: bytes):
    for number, wire, value in _fields(data):
        if number == 1 and wire == 2:
            return value.decode("utf-8")
        if number == 2 and wire == 5:
            return struct.unpack("<f", value)[0]
        if number == 3 and wire == 1:
            return struct.unpack("<d", value)[0]
        if number == 4 and wire == 0:
            return value - (1 << 64) if value >= 1 << 63 else value
        if number == 5 and wire == 0:
            return value
        if number == 6 and wire == 0:
            return _zigzag(value)
        if number == 7 and wire == 0:
            return bool(value)
    return None


def payload(data: bytes) -> bytes:
    """The raw protobuf of a (possibly gzip-compressed) tile."""
    data = bytes(data)
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except (OSError, EOFError, zlib.error) as error:  # a damaged stream: zlib.error
            raise MvtDecodeError(f"bad gzip: {error}") from error
    return data


def decode(data: bytes, properties: bool = True, geometry: bool = False,
           layers_wanted=None) -> Dict[str, dict]:
    """``{layer name: {"version", "extent", "features": [...], "keys"}}``.

    Each feature is ``{"id", "type", "properties", "geometry_ints"}``, plus
    ``"geometry"`` (the command integers, see ``lines``) when asked.
    ``layers_wanted``: decode only these layers (others are skipped).
    Raises MvtDecodeError for anything that is not a vector tile.
    """
    raw = payload(data)
    layers: Dict[str, dict] = {}
    for number, wire, value in _fields(raw):
        if number != 3 or wire != 2:
            raise MvtDecodeError(f"unexpected tile field {number}")
        name, version, extent = None, 1, 4096
        keys: List[str] = []
        values: list = []
        features_raw: List[bytes] = []
        for l_number, l_wire, l_value in _fields(value):
            if l_number == 1 and l_wire == 2:
                name = l_value.decode("utf-8")
            elif l_number == 15 and l_wire == 0:
                version = l_value
            elif l_number == 5 and l_wire == 0:
                extent = l_value
            elif l_number == 3 and l_wire == 2:
                keys.append(l_value.decode("utf-8"))
            elif l_number == 4 and l_wire == 2:
                values.append(_value(l_value))
            elif l_number == 2 and l_wire == 2:
                features_raw.append(l_value)
        if name is None:
            raise MvtDecodeError("layer without a name")
        if layers_wanted is not None and name not in layers_wanted:
            continue
        if version not in (1, 2):
            raise MvtDecodeError(f"layer {name}: unsupported version {version}")
        features = []
        for raw_feature in features_raw:
            feature = {"id": None, "type": 0, "properties": {}, "geometry_ints": 0}
            for f_number, f_wire, f_value in _fields(raw_feature):
                if f_number == 1 and f_wire == 0:
                    feature["id"] = f_value
                elif f_number == 3 and f_wire == 0:
                    feature["type"] = f_value
                elif f_number == 2 and f_wire == 2 and properties:
                    tags = _packed(f_value)
                    if len(tags) % 2:
                        raise MvtDecodeError(f"layer {name}: odd tag count")
                    for k, v in zip(tags[::2], tags[1::2]):
                        if k >= len(keys) or v >= len(values):
                            raise MvtDecodeError(f"layer {name}: tag index out of range")
                        feature["properties"][keys[k]] = values[v]
                elif f_number == 4 and f_wire == 2:
                    ints = _packed(f_value)
                    feature["geometry_ints"] = len(ints)
                    if geometry:
                        feature["geometry"] = ints
            features.append(feature)
        if name in layers:  # same name twice: merge (legal but unusual)
            layers[name]["features"].extend(features)
        else:
            layers[name] = {"version": version, "extent": extent, "keys": keys,
                            "features": features}
    return layers


def lines(ints: List[int]) -> List[List[Tuple[int, int]]]:
    """The parts of a line or polygon geometry in tile coordinates (MoveTo,
    LineTo, ClosePath commands; a closed ring repeats its first point)."""
    parts: List[List[Tuple[int, int]]] = []
    x = y = 0
    i = 0
    while i < len(ints):
        command, count = ints[i] & 7, ints[i] >> 3
        i += 1
        if command in (1, 2):
            if i + 2 * count > len(ints):
                raise MvtDecodeError("truncated geometry")
            for _ in range(count):
                x += _zigzag(ints[i])
                y += _zigzag(ints[i + 1])
                i += 2
                if command == 1:
                    parts.append([(x, y)])
                elif parts:
                    parts[-1].append((x, y))
        elif command == 7:
            if parts and parts[-1]:
                parts[-1].append(parts[-1][0])
        else:
            raise MvtDecodeError(f"unknown geometry command {command}")
    return [part for part in parts if len(part) >= 2]


def layer_names(data: bytes) -> List[str]:
    return list(layer_summary(data))


def layer_summary(data: bytes) -> Dict[str, set]:
    """``{layer name: set of property keys}`` without decoding features
    (cheap enough to run on every tile of an archive)."""
    raw = payload(data)
    out: Dict[str, set] = {}
    for number, wire, value in _fields(raw):
        if number != 3 or wire != 2:
            raise MvtDecodeError(f"unexpected tile field {number}")
        name, keys = None, set()
        for l_number, l_wire, l_value in _fields(value):
            if l_number == 1 and l_wire == 2:
                name = l_value.decode("utf-8")
            elif l_number == 3 and l_wire == 2:
                keys.add(l_value.decode("utf-8"))
        if name is None:
            raise MvtDecodeError("layer without a name")
        out.setdefault(name, set()).update(keys)
    return out
