from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def stable_payload_hash(payload: Any, *, digest_size: int = 16) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[: max(8, digest_size)]
