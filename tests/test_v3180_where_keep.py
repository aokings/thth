"""3.18.0 A2・A3 `thth where … --aggregate|--also … --keep` と `thth where-log`。

- 既定は残さない。`--keep` のときだけ、語ごとの**数だけ**を `state/_map/<project>/where-<月>.ndjson`
  に日付つきで残す（本文・post_id・username・author_key は残さない）。
- 5 未満の数（0 を含む）は欠測（null）。
- 同じ日・媒体・種類・語は最後の 1 回で置き換える。保持は地図と同じ日数で日単位に削る。
- 削除の依頼（`map_store.purge`）・退出（`purge_for_leave`）でも消える。
- `thth where-log <project>` は期間の和の当たり率と、`--budget N --medium M` の比例の割り当て。
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from thth import accounts, adapters, cli, jst, map_store, map_world, where_cli, where_keep

NOW = jst.parse("2026-10-13T09:00:00+09:00")
STAMP = "2026-10-12T20:00:00Z"


def _row(i, text):
    return {"message_id": f"POSTID{i:03d}", "author_key": f"{i:016x}", "username": f"USERNAME{i}",
            "timestamp": STAMP, "text": text}


COFFEE = [_row(1, "BODY 珈琲の豆はどれ？"), _row(2, "BODY 珈琲の豆が好き？"),
          _row(3, "BODY 珈琲の豆が苦い"), _row(4, "BODY 珈琲の豆が甘い"),
          _row(5, "BODY 珈琲の豆が酸っぱい"), _row(6, "BODY 珈琲の豆が濃い"),
          _row(7, "BODY 珈琲と紅茶")]
TEA = [_row(8, "BODY 紅茶の豆"), _row(9, "BODY 紅茶")]
SECRETS = ("BODY", "POSTID", "USERNAME", "0000000000000001", "どれ", "好き")


@pytest.fixture
def account(isolated_account_factory, monkeypatch, thth_root):
    isolated_account_factory("one", media="threads", project="cafe")
    monkeypatch.setattr(accounts, "load_token", lambda cfg: {"access_token": "FAKE"})

    class Fake:
        def keyword_search(self, word, **kwargs):
            return [dict(row) for row in (COFFEE if word == "珈琲" else TEA)]
    monkeypatch.setattr(adapters, "make_adapter", lambda *a: Fake())
    return thth_root


def _lexicon(tmp_path):
    path = tmp_path / "lexicon.json"
    path.write_text(json.dumps({"味": ["苦い", "甘い", "酸っぱい", "濃い"], "豆": ["豆"]},
                               ensure_ascii=False), encoding="utf-8")
    return where_cli.load_lexicon(str(path))


def _files(root):
    out = []
    for base, _dirs, names in os.walk(root):
        out.extend(os.path.join(base, n) for n in names if n.startswith("where-"))
    return out


def test_既定は残さない(account, tmp_path):
    where_cli.answer(account_name="one", words=["珈琲"], aggregate_mode=True, also=["豆"], now=NOW)
    assert _files(account) == []


def test_keepは数だけ残し5未満は欠測(account, tmp_path):
    result = where_cli.answer(account_name="one", words=["珈琲", "紅茶"], aggregate_mode=True,
                              lexicon=_lexicon(tmp_path), also=["豆"], keep=True, now=NOW)
    assert result["storage"] == "kept"
    node = result["by_account"]["one"]
    assert node["kept"]["rows"] == 4 and node["kept"]["project"] == "cafe"
    [path] = _files(account)
    assert path.endswith(os.path.join("state", "_map", "cafe", "where-2026-10.ndjson"))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    raw = open(path, encoding="utf-8").read()
    for secret in SECRETS:
        assert secret not in raw, secret
    rows = {(r["kind"], r["word"]): r for r in map(json.loads, raw.splitlines())}
    agg = rows[("aggregate", "珈琲")]
    # also（豆）に合った 6 本。問いの形は 2 本 → 欠測。味は 4 本 → 欠測。豆は 6 本。
    assert agg["n"] == 6 and agg["date"] == "2026-10-13" and agg["medium"] == "threads"
    assert agg["ends_with_question_mark"] is None and agg["contains_question_mark"] is None
    assert agg["lexicon"] == {"味": None, "豆": 6}
    hits = rows[("also_hits", "珈琲")]
    assert hits["n_fetched"] == 7 and hits["n_matched"] == 6 and hits["also"] == ["豆"]
    # 紅茶は 2 本 → 件数ごと欠測・内訳も欠測。
    assert rows[("aggregate", "紅茶")]["n"] is None
    assert rows[("aggregate", "紅茶")]["reason"] == "small_sample"
    assert rows[("also_hits", "紅茶")]["n_fetched"] is None
    for row in rows.values():
        assert where_keep.valid_row(row)


def test_keepは同じ日の同じ語を置き換える(account):
    for _ in range(2):
        where_cli.answer(account_name="one", words=["珈琲"], also=["豆"], keep=True, now=NOW)
    [path] = _files(account)
    assert len(open(path, encoding="utf-8").read().splitlines()) == 1


def test_keepはaggregateかalsoと一緒にだけ(account, capsys):
    assert cli.main(["where", "one", "珈琲", "--keep"]) == 2
    assert "--keep は --aggregate か --also" in capsys.readouterr().err


def test_projectの無いaccountは残せないと言う(isolated_account_factory, monkeypatch, thth_root):
    isolated_account_factory("solo", media="threads", project=None)
    monkeypatch.setattr(accounts, "load_token", lambda cfg: {"access_token": "FAKE"})

    class Fake:
        def keyword_search(self, word, **kwargs):
            return [dict(row) for row in COFFEE]
    monkeypatch.setattr(adapters, "make_adapter", lambda *a: Fake())
    result = where_cli.answer(account_name="solo", words=["珈琲"], also=["豆"], keep=True, now=NOW)
    node = result["by_account"]["solo"]
    assert node["kept"] == {"rows": 0, "cannot_say": "no_project"}
    assert any("project がありません" in line for line in node["cannot_say"])
    assert _files(thth_root) == []


def test_valid_rowは5未満の値と許可リスト外の鍵を断る():
    base = {"date": "2026-10-13", "medium": "x", "kind": "also_hits", "word": "珈琲",
            "search_type": "RECENT", "requested": 10, "filters": [], "reason": None,
            "also": ["豆"], "n_fetched": 10, "n_matched": 5}
    assert where_keep.valid_row(base)
    for bad in ({"n_matched": 4}, {"n_matched": 0}, {"n_fetched": 1}, {"text": "本文"},
                {"post_id": "1"}, {"filters": ["本文"]}, {"medium": "plaza"}):
        assert not where_keep.valid_row({**base, **bad}), bad


def test_保持を過ぎた日は書くたびに消える_collectのpruneでも消える(account):
    old = jst.parse("2026-03-01T09:00:00+09:00")
    where_cli.answer(account_name="one", words=["珈琲"], also=["豆"], keep=True, now=old)
    where_cli.answer(account_name="one", words=["珈琲"], also=["豆"], keep=True, now=NOW)
    rows, _ = where_keep.read("cafe")
    assert [r["date"] for r in rows] == ["2026-10-13"]
    assert not any(p.endswith("where-2026-03.ndjson") for p in _files(account))
    # 地図の保持（retention）を 1 日に縮めると、collect と同じ prune で消える。
    later = jst.parse("2026-10-20T09:00:00+09:00")
    map_store.set_retention("cafe", 1, by="運用者", now=NOW)
    assert map_world.prune("cafe", now=later) == 1
    assert where_keep.read("cafe") == ([], 0)


def test_削除の依頼と退出で消える(account):
    where_cli.answer(account_name="one", words=["珈琲"], also=["豆"], keep=True, now=NOW)
    out = map_store.purge("cafe", by="運用者", date_from="2026-10-13", date_to="2026-10-13")
    assert out["rows_removed"] == 1 and where_keep.read("cafe") == ([], 0)
    where_cli.answer(account_name="one", words=["珈琲"], also=["豆"], keep=True, now=NOW)
    out = map_store.purge("cafe", by="運用者")
    assert out["scope"] == "whole_map" and _files(account) == []
    where_cli.answer(account_name="one", words=["珈琲"], also=["豆"], keep=True, now=NOW)
    assert map_world.purge_for_leave("one", "cafe", "threads")["scope"] == "whole_map"
    assert _files(account) == []


def test_where_logは期間の和の当たり率とbudgetの割り当て(account, capsys, monkeypatch):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    rows = [{"date": "2026-10-12", "medium": "x", "kind": "also_hits", "word": w,
             "search_type": "RECENT", "requested": 10, "filters": [], "reason": None,
             "also": ["豆"], "n_fetched": f, "n_matched": m}
            for w, f, m in (("珈琲", 20, 15), ("紅茶", 20, 5), ("緑茶", 20, None))]
    where_keep.write("cafe", rows, now=NOW)
    assert cli.main(["where-log", "cafe", "--medium", "x", "--budget", "20", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    rates = {c["word"]: c["hit_rate"] for c in result["also_hits"]}
    assert rates == {"珈琲": 0.75, "紅茶": 0.25, "緑茶": None}
    assert result["allocation"]["by_word"] == {"珈琲": 15, "紅茶": 5, "緑茶": 0}
    assert cli.main(["where-log", "cafe"]) == 0
    out = capsys.readouterr().out
    assert "当たり率 75%" in out
    assert cli.main(["where-log", "cafe", "--budget", "20"]) == 2


def test_privacyは残す数とwhere_logを英日で書く():
    """build_site の文が実態（--keep のときだけ・数だけ・5 未満は 0 も欠測・地図と同じ決まり）と合う。"""
    import pathlib
    page = (pathlib.Path(__file__).resolve().parents[1] / "callback" / "public" / "privacy"
            / "index.html").read_text(encoding="utf-8")
    for must in ("thth where … --keep", "Numbers below 5, including zero, are saved as missing",
                 "thth where-log", "Without <code>--keep</code> nothing from the search is saved",
                 "a search that returned no posts is recorded as zero",
                 "5 未満の数（0 を含む）は値を保存せず", "何も返らなかった検索は 0 と記録します",
                 "<code>--keep</code> を付けなければ、検索から何も保存しません",
                 "求めに応じて残した検索の語ごとの数"):
        assert must in page, must
