"""Pure, offline projection of X direct owned-post public metrics.

No transport, authentication, persistence or permission to export is supplied.
The caller must supply trustworthy connection identity and observation times.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import re

_FIELDS = {
    "views": ("impression_count",), "likes": ("like_count",),
    "replies": ("reply_count",), "reposts": ("repost_count", "retweet_count"),
    "quotes": ("quote_count",), "bookmarks": ("bookmark_count",),
}
# Local interoperability bound, not an asserted X API maximum.
MAX_COUNT = 2**53 - 1


class XMetricsError(ValueError):
    """Bounded reason code; never includes response contents."""


def _id(value):
    return (type(value) is str and re.fullmatch(r"[1-9][0-9]{0,19}", value)
            is not None)


def _time(value):
    if type(value) is not str or len(value) > 40:
        return None
    # Require an explicit full datetime and timezone, not Python's loose ISO forms.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value):
        return None
    if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def _value(value):
    if type(value) is not int:
        return None, "invalid_type"
    if value < 0:
        return None, "negative"
    if value > MAX_COUNT:
        return None, "outside_safe_integer_range"
    return value, None


def _metric(metrics, fields):
    present = [field for field in fields if field in metrics]
    if not present:
        return {"value": None, "reason": "missing", "source_fields": []}
    values = [_value(metrics[field]) for field in present]
    reason = next((reason for _, reason in values if reason), None)
    if reason is None and len({value for value, _ in values}) != 1:
        reason = "conflicting_aliases"
    return {"value": None if reason else values[0][0], "reason": reason,
            "source_fields": ["public_metrics." + field for field in present]}


def normalize_owned_metrics(response, *, connected_user_id, observed_at, now,
                            provider="x_direct"):
    """Normalize one direct X lookup response without side effects.

    Invalid records are excluded by input index and bounded reason, including all
    occurrences of duplicate IDs. Top-level failures raise XMetricsError. Partial
    API errors are represented by counts only; no raw error or text is returned.
    `now` is explicit to keep the function deterministic and independently testable.
    """
    if provider != "x_direct" or type(provider) is not str:
        raise XMetricsError("unsupported_provider")
    if not _id(connected_user_id):
        raise XMetricsError("invalid_connected_user_id")
    current, observed = _time(now), _time(observed_at)
    if current is None:
        raise XMetricsError("invalid_now")
    if observed is None:
        raise XMetricsError("invalid_observed_at")
    if observed > current:
        raise XMetricsError("future_observation")
    if type(response) is not dict:
        raise XMetricsError("invalid_response")
    data = response.get("data")
    if type(data) is dict:
        rows = [data]
    elif type(data) is list and len(data) <= 100:
        rows = data
    else:
        raise XMetricsError("invalid_data")
    errors = response.get("errors", [])
    if type(errors) is not list or len(errors) > 100 or any(type(e) is not dict for e in errors):
        raise XMetricsError("invalid_errors")
    ids = Counter(row["id"] for row in rows
                  if type(row) is dict and _id(row.get("id")))
    accepted, rejected = [], []
    for index, row in enumerate(rows):
        reason = None
        posted = None
        if type(row) is not dict:
            reason = "invalid_record"
        elif not _id(row.get("id")):
            reason = "invalid_post_id"
        elif ids[row["id"]] > 1:
            reason = "duplicate_post_id"
        elif not _id(row.get("author_id")):
            reason = "invalid_author_id"
        elif row["author_id"] != connected_user_id:
            reason = "owner_mismatch"
        else:
            posted = _time(row.get("created_at"))
            if posted is None:
                reason = "invalid_posted_at"
            elif posted > observed:
                reason = "posted_after_observation"
        if reason:
            rejected.append({"index": index, "reason": reason})
            continue
        raw_metrics = row.get("public_metrics", {})
        invalid_container = type(raw_metrics) is not dict
        metrics = {name: _metric({} if invalid_container else raw_metrics, fields)
                   for name, fields in _FIELDS.items()}
        if invalid_container:
            for metric in metrics.values():
                metric["reason"] = "invalid_metrics_object"
        accepted.append({"post_id": row["id"], "posted_at": _iso(posted),
                         "observed_at": _iso(observed),
                         "ownership": {"author_id": row["author_id"],
                                       "connected_user_id": connected_user_id,
                                       "basis": "response_author_matches_caller_identity"},
                         "metrics": metrics})
    return {"schema_version": 1, "report_type": "owned_public_metrics",
            "provider": "x_direct", "observed_at": _iso(observed),
            "records": accepted, "rejected": rejected,
            "coverage": {"input_records": len(rows), "accepted_records": len(accepted),
                         "rejected_records": len(rejected), "api_error_count": len(errors),
                         "complete": not rejected and not errors},
            "metric_definitions": {"views": "public_post_impressions_not_unique_people",
                                   "likes": "public_post_likes", "replies": "public_post_replies",
                                   "reposts": "public_post_reposts", "quotes": "public_post_quotes",
                                   "bookmarks": "public_post_bookmarks"},
            "limitations": ["caller_identity_and_clock_not_authenticated",
                            "lookup_subset_not_account_inventory",
                            "root_reply_classification_not_performed",
                            "no_24_hour_measurement_claim",
                            "retention_and_external_export_permission_not_established"]}
