"""3.1.1 件 7: 毎朝の一枚の「返していないもの」の窓を台帳の `collect_days` に合わせる。

以前は 7 日固定で、145 時間前の行があと 1 日で窓から落ちるところだった。ここでは:
- `collect_days: 14` の台帳で 10 日前の未回答が出る（7 日固定なら出ない）。
- 窓の残りが 24 時間を切った行（13.5 日前）に `window_edge: true`、人向けに「⚠ 窓まで <n>h」。
- `window` に `basis` と `days`（と出所）。`collect_days` を変えれば窓も動く。
"""
import datetime
import json
from pathlib import Path

from thth import accounts, jst, morning, sent

NOW = jst.parse("2026-09-23T07:00:00+09:00")


def ago(days):
    return jst.iso(NOW - datetime.timedelta(days=days))


def setup(isolated_account_factory, collect_days):
    isolated_account_factory("one", handle="owner", collect_days=collect_days)
    sent.write(accounts.state_dir_for("one"), post_id="ROOT", text="本文", body_hash="hash", sent_at=ago(21))
    directory = Path(accounts.data_dirs(accounts.load_account("one"), "one")["replies"])
    directory.mkdir(parents=True, exist_ok=True)
    rows = [{"id": rid, "username": "outside", "text": "返信", "timestamp": ago(days), "post_id": "ROOT"}
            for rid, days in (("R10", 10), ("R135", 13.5), ("R20", 20))]
    rows.append({"kind": "fetch", "post_id": "ROOT", "collected_at": jst.iso(NOW)})
    (directory / "ROOT.ndjson").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return accounts.load_account("one")


def test_window_follows_collect_days_and_marks_the_edge(isolated_account_factory):
    cfg = setup(isolated_account_factory, 14)
    value = morning._unanswered_rows("one", NOW, None, cfg)
    by_id = {row["reply_id"]: row for row in value["items"]}
    assert set(by_id) == {"R10", "R135"}, "10 日前は 14 日の窓の中・20 日前は外"
    assert value["n"] == 2
    assert by_id["R135"]["window_edge"] is True and by_id["R135"]["hours_to_window_edge"] == 12.0
    assert by_id["R10"]["window_edge"] is False and by_id["R10"]["hours_to_window_edge"] == 96.0
    window = value["window"]
    assert window["days"] == 14 and window["days_source"] == "collect_days"
    assert window["basis"] == "reply_timestamp" and window["since"] == ago(14)
    lines = []
    morning._render_unanswered("one", {"medium": "threads", "replies": morning.cell(value),
                                       "mentions": morning.cell(cannot_say="not_supported")}, lines.append)
    assert any("R135" in line and "⚠ 窓まで 12.0h" in line for line in lines)
    assert not any("R10" in line and "⚠" in line for line in lines)
    assert any("窓 14 日" in line for line in lines)


def test_a_shorter_collect_days_shrinks_the_window(isolated_account_factory):
    cfg = setup(isolated_account_factory, 3)
    value = morning._unanswered_rows("one", NOW, None, cfg)
    assert value["items"] == [] and value["window"]["days"] == 3


def test_missing_or_bad_collect_days_uses_the_default(isolated_account_factory):
    for raw in (None, "abc", 0, True):
        assert morning._unanswered_days({"collect_days": raw}) == (14, "default")
    assert morning._unanswered_days({}) == (14, "default")
    assert morning._unanswered_days({"collect_days": "21"}) == (21, "collect_days")
