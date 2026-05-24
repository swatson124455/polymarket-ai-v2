"""
Fast JSON parsing for hot paths (e.g. WebSocket messages).
Uses orjson when available (3-10x faster), otherwise stdlib json.
"""
from typing import Any, Union

try:
    import orjson
    _ORJSON_AVAILABLE = True
except ImportError:
    orjson = None
    _ORJSON_AVAILABLE = False

import json


def loads(raw: Union[str, bytes]) -> Any:
    """Parse JSON from str or bytes. Prefers orjson when available."""
    if _ORJSON_AVAILABLE and orjson is not None:
        return orjson.loads(raw)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)
