"""Bencode encoding/decoding - the serialization format used by BitTorrent.

Spec: https://en.wikipedia.org/wiki/Bencode
Strings: <length>:<string>
Integers: i<number>e
Lists: l<items>e
Dicts: d<key><value>...e
"""

from io import BytesIO
from typing import Any


def encode(obj: Any) -> bytes:
    """Encode a Python object to bencoded bytes."""
    if isinstance(obj, bytes):
        return str(len(obj)).encode() + b":" + obj
    elif isinstance(obj, str):
        encoded = obj.encode("utf-8")
        return str(len(encoded)).encode() + b":" + encoded
    elif isinstance(obj, int):
        return b"i" + str(obj).encode() + b"e"
    elif isinstance(obj, list):
        result = [b"l"]
        for item in obj:
            result.append(encode(item))
        result.append(b"e")
        return b"".join(result)
    elif isinstance(obj, dict):
        result = [b"d"]
        for key in sorted(obj.keys(), key=lambda k: encode(k)):
            result.append(encode(key))
            result.append(encode(obj[key]))
        result.append(b"e")
        return b"".join(result)
    else:
        raise ValueError(f"Unsupported type: {type(obj)}")


def _decode_next(data: BytesIO) -> Any:
    """Read and decode the next bencoded object from the stream."""
    c = data.read(1)
    if not c:
        raise ValueError("Unexpected end of data")

    if c == b"i":
        # Integer: i<number>e
        buf = []
        while True:
            ch = data.read(1)
            if not ch:
                raise ValueError("Unexpected end of integer")
            if ch == b"e":
                break
            buf.append(ch)
        return int(b"".join(buf))

    elif c == b"l":
        # List: l<items>e
        items = []
        while True:
            peek = data.read(1)
            if not peek:
                raise ValueError("Unexpected end of list")
            if peek == b"e":
                break
            # Put back the byte and decode next item
            data.seek(-1, 1)
            items.append(_decode_next(data))
        return items

    elif c == b"d":
        # Dictionary: d<key><value>...e
        result = {}
        while True:
            peek = data.read(1)
            if not peek:
                raise ValueError("Unexpected end of dict")
            if peek == b"e":
                break
            data.seek(-1, 1)
            key = _decode_next(data)
            if not isinstance(key, bytes):
                raise ValueError(f"Dict key must be bytes, got {type(key)}")
            value = _decode_next(data)
            result[key] = value
        return result

    elif c == b"0" or c.isdigit():
        # String: <length>:<data>
        buf = [c]
        while True:
            ch = data.read(1)
            if not ch:
                raise ValueError("Unexpected end of string length")
            if ch == b":":
                break
            buf.append(ch)
        length = int(b"".join(buf))
        result = data.read(length)
        if len(result) != length:
            raise ValueError(f"Expected {length} bytes, got {len(result)}")
        return result

    else:
        raise ValueError(f"Unexpected byte: {c!r}")


def decode(data: bytes) -> Any:
    """Decode bencoded bytes into a Python object.

    Returns bytes for strings, int for integers, list for lists, dict for dicts.
    """
    stream = BytesIO(data)
    result = _decode_next(stream)
    # Check for trailing data
    remaining = stream.read()
    if remaining:
        raise ValueError(f"Trailing data after decode: {remaining[:20]!r}")
    return result
