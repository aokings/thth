import copy
import json

import pytest

from thth.x_metrics import MAX_COUNT, XMetricsError, normalize_owned_metrics

NOW = "2026-09-18T12:00:00Z"
POSTED = "2026-09-17T12:00:00Z"


def row(**kw):
    return {"id": "123", "author_id": "456", "created_at": POSTED,
            "public_metrics": {"impression_count": 0, "like_count": 5,
                               "reply_count": 2, "repost_count": 3,
                               "quote_count": 4, "bookmark_count": 6}, **kw}


def run(data, **kw):
    return normalize_owned_metrics({"data": data}, **{
        "connected_user_id": "456", "observed_at": NOW, "now": NOW, **kw})


@pytest.mark.parametrize("as_list", [False, True])
def test_projection_is_owned_public_only_and_does_not_mutate(as_list):
    item = row(text="BODY_CANARY", username="NAME_CANARY", extra="EXTRA_CANARY",
               non_public_metrics={"url_link_clicks": 900})
    data = [item] if as_list else item
    before = copy.deepcopy(data)
    result = run(data)
    assert data == before
    assert all(c not in json.dumps(result) for c in ["BODY_CANARY", "NAME_CANARY", "EXTRA_CANARY", "url_link_clicks"])
    assert result["records"][0]["metrics"]["views"]["value"] == 0
    assert result["records"][0]["ownership"]["author_id"] == "456"
    assert result["coverage"]["complete"] is True


@pytest.mark.parametrize("value,reason", [
    (True, "invalid_type"), (None, "invalid_type"), (float("nan"), "invalid_type"),
    (float("inf"), "invalid_type"), (1.0, "invalid_type"), ("8", "invalid_type"),
    ({}, "invalid_type"), (-1, "negative"), (10**1000, "outside_safe_integer_range"),
    (MAX_COUNT+1, "outside_safe_integer_range")])
def test_invalid_metrics_are_null_with_reason(value, reason):
    result = run(row(public_metrics={"like_count": value}))
    metrics = result["records"][0]["metrics"]
    assert metrics["likes"]["value"] is None
    assert metrics["likes"]["reason"] == reason
    assert metrics["views"]["reason"] == "missing"
    json.dumps(result, allow_nan=False)


def test_safe_integer_boundary():
    assert run(row(public_metrics={"like_count": MAX_COUNT}))["records"][0]["metrics"]["likes"]["value"] == MAX_COUNT


@pytest.mark.parametrize("value", [None, [], "BODY_CANARY", True])
def test_bad_metric_object_not_zero(value):
    metrics = run(row(public_metrics=value))["records"][0]["metrics"]
    assert all(m["value"] is None and m["reason"] == "invalid_metrics_object" for m in metrics.values())


@pytest.mark.parametrize("fields,expected,reason", [
    ({"retweet_count": 7}, 7, None), ({"repost_count": 7}, 7, None),
    ({"retweet_count": 7, "repost_count": 7}, 7, None),
    ({"retweet_count": 7, "repost_count": 8}, None, "conflicting_aliases"),
    ({"retweet_count": True, "repost_count": 7}, None, "invalid_type")])
def test_documented_aliases(fields, expected, reason):
    metric = run(row(public_metrics=fields))["records"][0]["metrics"]["reposts"]
    assert (metric["value"], metric["reason"]) == (expected, reason)


def test_mixed_batch_rejects_without_leaking_foreign_identity():
    result = run([row(), row(id="222", author_id="789"), None,
                  row(id="333", created_at="bad")])
    assert len(result["records"]) == 1
    assert result["rejected"] == [{"index": 1, "reason": "owner_mismatch"},
                                  {"index": 2, "reason": "invalid_record"},
                                  {"index": 3, "reason": "invalid_posted_at"}]
    assert "789" not in json.dumps(result)
    assert result["coverage"]["complete"] is False


def test_duplicate_ids_invalidate_all_occurrences_even_foreign_owner():
    result = run([row(), row(author_id="789")])
    assert result["records"] == []
    assert [r["reason"] for r in result["rejected"]] == ["duplicate_post_id"] * 2


@pytest.mark.parametrize("posted", ["2026-09-18T12:00:00.000001Z", "2026-09-19T12:00:00Z"])
def test_posted_after_observation(posted):
    assert run(row(created_at=posted))["rejected"][0]["reason"] == "posted_after_observation"


def test_equal_time_and_timezone_equivalence():
    record = run(row(created_at="2026-09-18T21:00:00+09:00"))["records"][0]
    assert record["posted_at"] == NOW


@pytest.mark.parametrize("field,value,reason", [
    ("provider", "buffer", "unsupported_provider"), ("provider", None, "unsupported_provider"),
    ("connected_user_id", True, "invalid_connected_user_id"),
    ("observed_at", "2026-09-18T12:00:00", "invalid_observed_at"),
    ("observed_at", "2026-09-18T12:00:00.000001Z", "future_observation"),
    ("now", "BODY_CANARY", "invalid_now")])
def test_bad_call_bounded_errors(field, value, reason):
    with pytest.raises(XMetricsError, match="^" + reason + "$"):
        run(row(), **{field: value})


@pytest.mark.parametrize("value", ["BODY_CANARY", "１２３", "0", "1"*21, 123, None])
def test_bad_id_is_not_echoed(value):
    result = run(row(id=value))
    assert result["rejected"] == [{"index": 0, "reason": "invalid_post_id"}]


def test_partial_api_errors_counted_without_raw_details():
    result = normalize_owned_metrics({"data": row(), "errors": [{"detail": "ERROR_CANARY"}]},
                                    connected_user_id="456", observed_at=NOW, now=NOW)
    assert result["coverage"]["api_error_count"] == 1
    assert result["coverage"]["complete"] is False
    assert "ERROR_CANARY" not in json.dumps(result)


@pytest.mark.parametrize("response", [{}, {"data": None}, {"data": [row()]*101},
                                      {"data": [], "errors": "CANARY"}, "CANARY"])
def test_bad_response_rejected(response):
    with pytest.raises(XMetricsError):
        normalize_owned_metrics(response, connected_user_id="456", observed_at=NOW, now=NOW)


@pytest.mark.parametrize("value", ["2026-09-18T12:00:00+00:99",
                                   "2026-09-18T12:00:00+24:00",
                                   "2026-09-18 12:00:00Z", "2026-09-18"])
def test_invalid_timestamp_forms(value):
    assert run(row(created_at=value))["rejected"][0]["reason"] == "invalid_posted_at"


@pytest.mark.parametrize("metrics", [None, {}, {"like_count": True}])
def test_coverage_distinguishes_missing_metrics_from_accepted_records(metrics):
    result = run([row(), row(id="124", public_metrics=metrics)])
    assert result["coverage"]["complete"] is True
    assert result["coverage"]["accepted_records"] == 2
    assert result["coverage"]["metrics_missing_records"] == 1
    assert run(row())["coverage"]["metrics_missing_records"] == 0


def test_errors_only_is_explicit_invalid_data_contract():
    error = {"detail": "PRIVATE_ERROR_CANARY"}
    with pytest.raises(XMetricsError, match="^invalid_data$"):
        normalize_owned_metrics({"errors": [error]}, connected_user_id="456", observed_at=NOW, now=NOW)
    result = normalize_owned_metrics({"data": [], "errors": [error]},
                                    connected_user_id="456", observed_at=NOW, now=NOW)
    assert result["coverage"]["accepted_records"] == 0
    assert result["coverage"]["api_error_count"] == 1
    assert result["coverage"]["complete"] is False
    assert "PRIVATE_ERROR_CANARY" not in json.dumps(result)
