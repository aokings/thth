"""Pure, offline projection of X direct owned-post metrics (public, and the
non-public ones of the last 30 days when asked).

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
# Non-public metrics (docs.x.com/x-api/fundamentals/metrics, read 2026-11-06):
# only for the authenticated author's own posts created within the last 30 days,
# user-context auth only. `impression_count` appears in that page's example
# response under non_public_metrics but not in its field table; it is kept apart
# from the public `views` so the two are never mixed.
_PRIVATE_FIELDS = {
    "link_clicks": ("url_link_clicks",), "profile_clicks": ("user_profile_clicks",),
    "engagements": ("engagements",), "private_impressions": ("impression_count",),
}
# Video (`includes.media[]` via `expansions=attachments.media_keys`): the public
# `view_count` and the non-public playback quartiles.
_VIDEO_PUBLIC = {"video_views": ("view_count",)}
_VIDEO_PRIVATE = {
    "video_playback_0": ("playback_0_count",), "video_playback_25": ("playback_25_count",),
    "video_playback_50": ("playback_50_count",), "video_playback_75": ("playback_75_count",),
    "video_playback_100": ("playback_100_count",),
}
PRIVATE_METRICS = tuple(_PRIVATE_FIELDS)
VIDEO_METRICS = tuple(_VIDEO_PUBLIC) + tuple(_VIDEO_PRIVATE)
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


def _metric(metrics, fields, prefix="public_metrics."):
    present = [field for field in fields if field in metrics]
    if not present:
        return {"value": None, "reason": "missing", "source_fields": []}
    values = [_value(metrics[field]) for field in present]
    reason = next((reason for _, reason in values if reason), None)
    if reason is None and len({value for value, _ in values}) != 1:
        reason = "conflicting_aliases"
    return {"value": None if reason else values[0][0], "reason": reason,
            "source_fields": [prefix + field for field in present]}


def _absent(table, reason):
    return {name: {"value": None, "reason": reason, "source_fields": []} for name in table}


def _block(raw, table, prefix):
    if raw is None:
        return _absent(table, "missing")
    if type(raw) is not dict:
        return _absent(table, "invalid_metrics_object")
    return {name: _metric(raw, fields, prefix) for name, fields in table.items()}


def _media_index(response):
    """`includes.media[]` by media_key; malformed or duplicated entries are absent."""
    includes = response.get("includes")
    media = includes.get("media") if type(includes) is dict else None
    if type(media) is not list:
        return {}
    keys = Counter(m.get("media_key") for m in media
                   if type(m) is dict and type(m.get("media_key")) is str)
    return {m["media_key"]: m for m in media
            if type(m) is dict and type(m.get("media_key")) is str and keys[m["media_key"]] == 1}


def _private_metrics(row, media_by_key):
    """Non-public counts of one accepted row. A missing or malformed block gives
    value None (`missing`/`invalid_metrics_object`), never 0. Video counts are
    added only when the post has exactly one attached video in `includes.media`."""
    out = _block(row.get("non_public_metrics"), _PRIVATE_FIELDS, "non_public_metrics.")
    attachments = row.get("attachments")
    keys = attachments.get("media_keys") if type(attachments) is dict else None
    if type(keys) is not list:
        return out
    videos = [media_by_key[key] for key in keys
              if type(key) is str and key in media_by_key and media_by_key[key].get("type") == "video"]
    if len(videos) != 1:
        return out
    video = videos[0]
    out.update(_block(video.get("public_metrics"), _VIDEO_PUBLIC, "includes.media.public_metrics."))
    out.update(_block(video.get("non_public_metrics"), _VIDEO_PRIVATE,
                      "includes.media.non_public_metrics."))
    return out


def normalize_owned_metrics(response, *, connected_user_id, observed_at, now,
                            provider="x_direct", private=False):
    """Normalize one direct X lookup response without side effects.

    Invalid records are excluded by input index and bounded reason, including all
    occurrences of duplicate IDs. Top-level failures raise XMetricsError. Partial
    API errors are represented by counts only; no raw error or text is returned.
    `now` is explicit to keep the function deterministic and independently testable.
    With `private=True` (the request asked for non_public_metrics and the media
    expansion) each record's `metrics` also carries PRIVATE_METRICS, plus
    VIDEO_METRICS for a post with exactly one video; with False nothing changes.
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
    media_by_key = _media_index(response) if private else {}
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
        if private:
            metrics.update(_private_metrics(row, media_by_key))
        accepted.append({"post_id": row["id"], "posted_at": _iso(posted),
                         "observed_at": _iso(observed),
                         "ownership": {"author_id": row["author_id"],
                                       "connected_user_id": connected_user_id,
                                       "basis": "response_author_matches_caller_identity"},
                         "metrics": metrics})
    definitions = {"views": "public_post_impressions_not_unique_people",
                   "likes": "public_post_likes", "replies": "public_post_replies",
                   "reposts": "public_post_reposts", "quotes": "public_post_quotes",
                   "bookmarks": "public_post_bookmarks"}
    if private:
        definitions.update({"link_clicks": "non_public_url_link_clicks",
                            "profile_clicks": "non_public_user_profile_clicks",
                            "engagements": "non_public_engagements",
                            "private_impressions": "non_public_impressions_not_mixed_with_views",
                            "video_views": "public_video_view_count",
                            **{name: "non_public_video_" + fields[0]
                               for name, fields in _VIDEO_PRIVATE.items()}})
    return {"schema_version": 1, "report_type": "owned_public_metrics",
            "provider": "x_direct", "observed_at": _iso(observed),
            "records": accepted, "rejected": rejected,
            "coverage": {"input_records": len(rows), "accepted_records": len(accepted),
                         "rejected_records": len(rejected), "api_error_count": len(errors),
                         "complete": not rejected and not errors,
                         "metrics_missing_records": sum(
                             any(metric["value"] is None for metric in record["metrics"].values())
                             for record in accepted)},
            "metric_definitions": definitions,
            "limitations": ["caller_identity_and_clock_not_authenticated",
                            "lookup_subset_not_account_inventory",
                            "root_reply_classification_not_performed",
                            "no_24_hour_measurement_claim",
                            "retention_and_external_export_permission_not_established"]}
