"""Typed messages exchanged by the Python runtime and Live2D renderers.

The bridge deliberately carries commands and lifecycle facts only.  It does not
choose motions and it does not infer motion state from an unrelated queue.
"""

import json
import time
import uuid
from typing import Any, Dict, Optional

PROTOCOL_VERSION = 1
Message = Dict[str, Any]


def create_message(
    msg_type: str,
    data: Any,
    *,
    event_id: Optional[str] = None,
    session_id: Optional[str] = None,
    source: str = "python",
    seq: Optional[int] = None,
) -> str:
    """Create a versioned envelope suitable for a WebSocket text frame."""
    message: Message = {
        "v": PROTOCOL_VERSION,
        "type": str(msg_type),
        "event_id": event_id or uuid.uuid4().hex,
        "session_id": session_id or "",
        "source": source,
        "timestamp": time.time(),
        "data": data,
    }
    if seq is not None:
        message["seq"] = int(seq)
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def parse_message(raw: Any) -> Optional[Message]:
    """Parse and minimally validate an incoming protocol envelope."""
    try:
        message = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(message, dict):
        return None
    if message.get("v") != PROTOCOL_VERSION or not isinstance(message.get("type"), str):
        return None
    if not isinstance(message.get("data", {}), dict):
        return None
    return message
