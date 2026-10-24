"""Template (de)serialization.

Templates are JSON with NumPy arrays as base64 - never pickle, so decoding
cannot execute code even if the encryption layer were bypassed.
"""

import base64
import json

import numpy as np

from .errors import TemplateError

_DTYPES = {"<f4", "<f8", "|u1", "<i4", "<i8"}


def _encode(obj):
    if isinstance(obj, np.ndarray):
        return {"__nd__": obj.dtype.str, "shape": list(obj.shape),
                "data": base64.b64encode(np.ascontiguousarray(obj).tobytes()).decode()}
    if isinstance(obj, dict):
        return {str(k): _encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    return obj


def _decode(obj):
    if isinstance(obj, dict):
        if "__nd__" in obj:
            if obj["__nd__"] not in _DTYPES:
                raise TemplateError(f"Unsupported array dtype {obj['__nd__']}")
            arr = np.frombuffer(base64.b64decode(obj["data"]), dtype=np.dtype(obj["__nd__"]))
            return arr.reshape(obj["shape"]).copy()
        return {k: _decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode(v) for v in obj]
    return obj


def serialize(record: dict) -> bytes:
    return json.dumps(_encode(record), separators=(",", ":")).encode()


def deserialize(data: bytes) -> dict:
    try:
        return _decode(json.loads(data))
    except (ValueError, TypeError, KeyError) as e:
        raise TemplateError(f"Malformed template payload: {e}") from e
