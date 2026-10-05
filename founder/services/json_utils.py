"""Reject oversized/deep untrusted JSON before allocating its object tree."""
import json


def bounded_json_loads(text, *, max_chars=256_000, max_depth=64):
    if not isinstance(text, str) or len(text) > max_chars:
        raise ValueError("JSON is too large")
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > max_depth:
                raise ValueError("JSON is nested too deeply")
        elif char in "]}":
            depth -= 1
    return json.loads(text)
