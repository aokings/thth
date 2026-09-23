"""3.6.0 つまずきの年表（設計 3.6.0 §B・§C）。

- 報告の口の記録（置いた日・種類・題・閉じた版）とリリースノートの「報告 id」
  「〜の実測から」を日付で並べる。材料は道具の中の記録と repo の docs だけ。
- **project の範囲だけ**——他の持ち主の報告は行にも id にも出さない（リリースノートに
  書かれた他の持ち主の id も拾わない）。
- 集計: 最初の報告から数えた日数ごとの件数・始めて 2 週間の件数・閉じるまでの日数の
  中央値と n。新しい持ち主の最初の 2 週間は、open に参加した持ち主の年表の中央値
  （件数と日数だけ・題は出さない）。
"""
from __future__ import annotations

import datetime
import json

import pytest

from tests.conftest import run_thth
from thth import jst, plaza, report_inbox, report_timeline

NOW = jst.parse("2026-09-24T12:00:00+09:00")


def _file(account, project, title, days_ago, *, kind="bug"):
    at = NOW - datetime.timedelta(days=days_ago)
    return report_inbox.file_report(account, kind=kind, title=title, body=f"{title} の本文",
                                    by="セッション", project=project, medium="threads",
                                    now=at, trusted=True)["report_id"]


def _close(report_id, days_ago, version):
    report_inbox.close(report_id, by="実装側", reason="fixed", version=version,
                       now=NOW - datetime.timedelta(days=days_ago))


@pytest.fixture
def store(thth_root, tmp_path):
    ids = {
        "k1": _file("kopicha-threads", "kopicha", "止まったのに気づかない", 10),
        "k2": _file("kopicha-bsky", "kopicha", "断りの理由が分からない", 9, kind="friction"),
        "k3": _file("kopicha-threads", "kopicha", "目的で比べたい", 1, kind="request"),
        "o1": _file("other-threads", "other", "他の持ち主の秘密の題", 8),
        "o2": _file("other-threads", "other", "他の持ち主の二つ目", 2),
    }
    _close(ids["k1"], 7, "3.3.0")
    _close(ids["k2"], 4, "3.5.0")
    _close(ids["o1"], 5, "3.4.0")
    notes = tmp_path / "docs"
    notes.mkdir()
    (notes / "リリースノート_3.3.0_2026-09-17.md").write_text(
        f"# 3.3.0 止まったら知らせる\n\n材料の報告: **{ids['k1']}**・{ids['o1']}。\n", encoding="utf-8")
    (notes / "リリースノート_3.4.0_2026-09-19.md").write_text(
        f"# 3.4.0 広場\n\n材料: {ids['o1']}。\n", encoding="utf-8")
    (notes / "リリースノート_3.4.1_2026-09-20.md").write_text(
        "# 3.4.1 直し\n\n件 2 は kopicha セッションの実測から追加。件 3 は other の実測から。\n",
        encoding="utf-8")
    (notes / "リリースノート_3.5.0_2026-09-20.md").write_text(
        "# 3.5.0 地図\n\n報告 id の無い版。\n", encoding="utf-8")
    (notes / "メモ.md").write_text(f"{ids['k1']}\n", encoding="utf-8")
    return ids, notes


def _timeline(target, notes, **kwargs):
    return report_timeline.timeline(report_inbox.scope_for_account(target), now=NOW,
                                    notes_dir=notes, **kwargs)


def test_報告とリリースノートを日付で並べる(store, monkeypatch):
    ids, notes = store
    monkeypatch.setattr(report_inbox.accounts, "list_account_names", lambda: [])
    payload = _timeline("kopicha", notes)
    assert [(row["kind"], row["id"]) for row in payload["rows"]] == [
        ("report", ids["k1"]), ("report", ids["k2"]), ("release", "3.3.0"),
        ("release", "3.4.1"), ("report", ids["k3"])]
    k1 = payload["rows"][0]
    assert k1 == {"date": "2026-09-14", "kind": "report", "id": ids["k1"],
                  "title": "止まったのに気づかない", "report_kind": "bug", "status": "closed",
                  "closed_in_version": "3.3.0", "days_to_close": 3, "mentioned_in": ["3.3.0"]}
    release = payload["rows"][2]
    assert release["title"] == "3.3.0 止まったら知らせる"
    assert release["reports"] == [ids["k1"]] and release["measured_from"] == 0
    assert payload["rows"][3]["reports"] == [] and payload["rows"][3]["measured_from"] == 1
    assert payload["rows"][4]["days_to_close"] is None and payload["rows"][4]["closed_in_version"] is None


def test_他の持ち主の報告は出さない_リリースノートの他のidも拾わない(store, monkeypatch):
    ids, notes = store
    monkeypatch.setattr(report_inbox.accounts, "list_account_names", lambda: [])
    text = json.dumps(_timeline("kopicha", notes), ensure_ascii=False)
    for key in ("o1", "o2"):
        assert ids[key] not in text
    assert "他の持ち主の秘密の題" not in text and "他の持ち主の二つ目" not in text
    assert "3.4.0" not in [row["id"] for row in json.loads(text)["rows"]]


def test_集計_日数ごとの件数と閉じるまでの日数の中央値(store, monkeypatch):
    _ids, notes = store
    monkeypatch.setattr(report_inbox.accounts, "list_account_names", lambda: [])
    summary = _timeline("kopicha", notes)["summary"]
    assert summary["n_reports"] == 3 and summary["n_open"] == 1 and summary["n_closed"] == 2
    assert summary["first_report_date"] == "2026-09-14"
    assert summary["by_day_since_first"] == [{"day": 0, "n": 1}, {"day": 1, "n": 1},
                                             {"day": 9, "n": 1}]
    assert summary["first_days"] == {"window_days": 14, "n": 3}
    assert summary["days_to_close"] == {"median": 4.0, "n": 2, "denominator": 3}


def test_sinceは行だけを絞り集計は全部(store, monkeypatch):
    ids, notes = store
    monkeypatch.setattr(report_inbox.accounts, "list_account_names", lambda: [])
    payload = _timeline("kopicha", notes, since="2026-09-20")
    assert [row["id"] for row in payload["rows"]] == ["3.4.1", ids["k3"]]
    assert payload["summary"]["n_reports"] == 3
    with pytest.raises(report_inbox.ReportError, match="^invalid_since$"):
        _timeline("kopicha", notes, since="昨日")


def test_新しい持ち主の最初の2週間に先輩の中央値_件数と日数だけ(store, monkeypatch):
    ids, notes = store
    monkeypatch.setattr(report_inbox.accounts, "list_account_names", lambda: [])
    new_id = _file("newbie-threads", "newbie", "新人の最初の報告", 3)
    monkeypatch.setattr(plaza, "members", lambda: frozenset({"newbie", "kopicha", "other"}))
    payload = _timeline("newbie", notes)
    assert payload["peers"] == {"basis": "plaza_open_members", "n_owners": 2,
                                "median_first_days_reports": 2.5, "window_days": 14,
                                "median_days_to_close": 3, "n_closed": 3, "titles": "not_shown"}
    text = json.dumps(payload["peers"], ensure_ascii=False)
    assert "kopicha" not in text and "other" not in text
    assert all(ids[key] not in json.dumps(payload, ensure_ascii=False) for key in ids)
    assert [row["id"] for row in payload["rows"]] == [new_id]
    # 参加していない持ち主には出さない（open と同じ線）。
    monkeypatch.setattr(plaza, "members", lambda: frozenset({"kopicha", "other"}))
    assert _timeline("newbie", notes)["peers_reason"] == "plaza_not_joined"
    # 2 週間を過ぎた持ち主には出さない（kopicha の最初の報告から 15 日目）。
    monkeypatch.setattr(plaza, "members", lambda: frozenset({"newbie", "kopicha", "other"}))
    later = report_timeline.timeline(report_inbox.scope_for_account("kopicha"), notes_dir=notes,
                                     now=NOW + datetime.timedelta(days=5))
    assert later["peers"] is None and later["peers_reason"] == "not_in_first_14_days"


def test_リリースノートが読めなければ理由(store, monkeypatch, tmp_path):
    _ids, _notes = store
    monkeypatch.setattr(report_inbox.accounts, "list_account_names", lambda: [])
    payload = _timeline("kopicha", tmp_path / "無い")
    assert payload["sources"]["release_notes_reason"] == "notes_directory_unavailable"
    assert all(row["kind"] == "report" for row in payload["rows"])


def test_repoのdocsのリリースノートから報告idを拾える():
    notes, reason = report_timeline.release_notes()
    assert reason is None
    by_version = {version: text for version, _date, _title, text in notes}
    assert "r20260923-f199078c" in by_version["3.3.0"]
    assert "r20260923-f199078c" in report_timeline.REPORT_ID_IN_TEXT.findall(by_version["3.3.0"])


def test_CLIのreport_timeline(isolated_account_factory):
    account = isolated_account_factory("kopicha-threads", project="kopicha")
    report_id = report_inbox.file_report(account["name"], kind="bug", title="CLI の年表",
                                         body="本文", by="セッション")["report_id"]
    result = run_thth(["report", "timeline", account["name"], "--json"])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["report_type"] == "report_timeline"
    assert [row["id"] for row in payload["rows"] if row["kind"] == "report"] == [report_id]
    text = run_thth(["report", "timeline", account["name"]])
    assert "つまずきの年表  報告 1 件" in text.stdout
    bad = run_thth(["report", "timeline", account["name"], "--since", "x"])
    assert bad.returncode != 0 and "invalid_since" in bad.stderr


def test_MCPのthth_report_timelineはcredentialの範囲だけ(tmp_path, monkeypatch):
    from tests.test_v340_plaza_mcp import _tenant, _text
    _root, server = _tenant(tmp_path, monkeypatch)
    mine = report_inbox.file_report("first", kind="bug", title="私の報告", body="本文", by="s",
                                    project="kopicha", medium="threads", trusted=True)["report_id"]
    theirs = report_inbox.file_report("second", kind="bug", title="他人の報告", body="本文", by="s",
                                      project="other", medium="threads", trusted=True)["report_id"]
    names = {tool["name"] for tool in server.server_tools(server.authenticated_context())}
    assert "thth_report_timeline" in names
    result = server.call_tool("thth_report_timeline", {})
    assert not result.get("isError"), result
    payload = json.loads(_text(result))
    assert mine in _text(result) and theirs not in _text(result)
    assert payload["summary"]["n_reports"] == 1
    refused = server.call_tool("thth_report_timeline", {"since": "x"})
    assert refused.get("isError") and _text(refused) == "invalid_since"
