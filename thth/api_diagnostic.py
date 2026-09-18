"""Small, non-text API diagnostics safe for persisted notifications."""
import json

MAX_ERROR_BYTES = 16384
TYPES = frozenset({"OAuthException", "GraphMethodException", "Exception"})


def clean(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("http_status", "code", "error_subcode"):
        item = value.get(key)
        if type(item) is int and 0 <= item <= 2147483647:
            if key != "http_status" or 100 <= item <= 599:
                result[key] = item
    if type(value.get("is_transient")) is bool:
        result["is_transient"] = value["is_transient"]
    if isinstance(value.get("type"), str) and value["type"] in TYPES:
        result["type"] = value["type"]
    return result


def read_http_error(error):
    """One bounded read; raw message is returned only for existing permission classification."""
    detail = clean({"http_status": error.code})
    err, message = None, ""
    try:
        raw = error.read(MAX_ERROR_BYTES + 1)
        if len(raw) > MAX_ERROR_BYTES:
            return detail, err, message
        body = json.loads(raw or b"{}")
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            detail.update(clean({k: v for k, v in err.items() if k != "http_status"}))
            message = err.get("message", "")
            message = message[:170] if isinstance(message, str) else ""
    except (ValueError, UnicodeError, OSError, TypeError):
        pass
    return detail, err, message


def format_detail(value):
    return "; ".join(f"{key}={str(item).lower() if type(item) is bool else item}"
                     for key, item in clean(value).items())


def suffix(value):
    text = format_detail(value)
    return f"; {text}" if text else ""
