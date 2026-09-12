"""監査 2（2026-09-12・v1.0.0）の指摘を直したことを固定する。

1. `docs/….md` の全角三点リーダをプレースホルダとして扱う（主張の文書自身が
   リンク検査を割った）。
2. `--advise` の「参考: 他 account の判断」の列挙に上限（観測を 2 件で切っても、
   ここが無制限なら画面は伸びる）。
3. `--plan` で自 account の判断が無い語の観測に、誰の観測かを添える（他人の観測が
   無記名で出ていた）。
"""
from __future__ import annotations

import json

from thth import cli as cli_mod, topics as topics_mod
from tests import test_docs_links as links


def test_全角三点リーダはプレースホルダ():
    assert links._looks_like_placeholder("docs/….md")
    assert links._looks_like_placeholder("docs/記録/検収_….md")
    assert not links._looks_like_placeholder("docs/README.md")


def _advise(monkeypatch, capsys, account="kopicha-threads", *, as_json=False):
    monkeypatch.setattr(cli_mod.account_report_mod, "topic_plan",
                         lambda *_a, **_k: {"topics": []})
    cli_mod._advise(account, as_json=as_json)
    out = capsys.readouterr().out
    return json.loads(out) if as_json else out


def test_他accountの判断の列挙は3件で切りhistoryへ送る(thth_root, capsys, monkeypatch):
    for i in range(6):
        topics_mod.record("紅茶", verdict="alive", audience=f"観測{i}",
                           by=f"u{i}", account=f"acct{i}")
    out = _advise(monkeypatch, capsys)
    行 = [l for l in out.splitlines() if l.strip().startswith("紅茶")]
    assert 行, out
    assert "ほか 3 account（thth topics history 紅茶）" in 行[0], 行[0]
    assert 行[0].count("と判断") == 3, 行[0]
    # `--json` は切らない（機械の読み手には全部）
    payload = _advise(monkeypatch, capsys, as_json=True)
    item = [r for r in payload["observed_only"] if r["topic"] == "紅茶"][0]
    assert len(item["other_accounts"]) == 6


def test_planの観測には誰の観測かが付く(thth_root):
    topics_mod.record("抹茶", verdict="alive", audience="茶道の人",
                       by="kanto", account="asmon-kanto-threads")
    row = topics_mod.latest("抹茶", account="kopicha-threads")
    assert row["no_own_judgment"]
    assert row["audience"] == "茶道の人"
    assert row["audience_observer"] == "asmon-kanto-threads"
    # 自分の判断があれば観測者の欄は要らない（自分の記録がそのまま返る）
    topics_mod.record("抹茶", verdict="mismatch", by="自分", account="kopicha-threads")
    own = topics_mod.latest("抹茶", account="kopicha-threads")
    assert own["verdict"] == "mismatch" and "audience_observer" not in own
