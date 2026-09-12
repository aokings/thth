"""観測の `audience` に、誰が書いたかを添える（運用セッション指摘 2026-09-12）。

観測は「**誰がいるかは共有の事実**」として account を分けずに 1 つの棚に置く。
**その意図は正しい。** 穴は **`audience` に何を書いてよいかを決めていない**こと。

    中学受験［行動］ — 受験親のやりとり。**自アカウントの既存 6 投稿が
    全部この語で views 202〜574**

**account 固有の実績が共有の棚に乗り**、しかも**最後の行が勝つ**ので、別の
account が書き直すと入れ替わる。**文面から機械で見分けることはできない**ので、
**せめて出どころを必ず見せる。**
"""
from __future__ import annotations

import argparse
import json

from thth import cli as cli_mod, topics as topics_mod


def _出す(account, as_json):
    cli_mod._advise(account, as_json=as_json)


def test_他accountが書いた観測には出どころが付く(thth_root, capsys, monkeypatch):
    topics_mod.record("中学受験", verdict="alive", kind="行動",
                       audience="受験親のやりとり。自アカウントの既存 6 投稿が 202〜574",
                       by="kanto", account="asmon-kanto-threads")
    topics_mod.record("中学受験", verdict="alive", kind="行動",
                       audience="", by="自分", account="kopicha-threads")
    monkeypatch.setattr(cli_mod.account_report_mod, "topic_plan",
                         lambda *_a, **_k: {"topics": []})
    _出す("kopicha-threads", False)
    out = capsys.readouterr().out
    # **`if ... in out:` で逃がさない。** 出ていなければ、そもそもテストに
    # なっていない（今日 何度も踏んだ型）。
    assert "202〜574" in out, f"**前提が崩れている**（観測が出ていない）:\n{out}"
    assert "asmon-kanto-threads" in out, \
        f"**他 account の実績が、出どころ無しで共有の事実として出ている**:\n{out}"


def test_自分が書いた観測には出どころを付けない(thth_root, capsys, monkeypatch):
    """**自分のものにまで付けると、画面が読みにくくなるだけ。**"""
    topics_mod.record("お茶", verdict="alive", kind="一般名詞",
                       audience="茶葉の話", by="自分", account="kopicha-threads")
    monkeypatch.setattr(cli_mod.account_report_mod, "topic_plan",
                         lambda *_a, **_k: {"topics": []})
    _出す("kopicha-threads", False)
    out = capsys.readouterr().out
    assert "茶葉の話" in out, f"**前提が崩れている**:\n{out}"
    assert "kopicha-threads** の観測" not in out, out


def test_accountの記録が無い観測はそう言う(thth_root, capsys, monkeypatch):
    """**2026-09-10 までの 46 件は account を持たない。** 「誰が書いたか分からない」
    ことを、**分からないまま出す。**"""
    topics_mod.record("精製", verdict="alive", kind="専門語",
                       audience="鉱物精製の場", by="昔の記録")     # account なし
    monkeypatch.setattr(cli_mod.account_report_mod, "topic_plan",
                         lambda *_a, **_k: {"topics": []})
    _出す("kopicha-threads", False)
    out = capsys.readouterr().out
    assert "鉱物精製の場" in out, f"**前提が崩れている**:\n{out}"
    assert "account の記録なし" in out, out


def test_jsonにも出どころが出る(thth_root, capsys, monkeypatch):
    topics_mod.record("中学受験", verdict="alive", kind="行動",
                       audience="受験親のやりとり", by="kanto",
                       account="asmon-kanto-threads")
    monkeypatch.setattr(cli_mod.account_report_mod, "topic_plan",
                         lambda *_a, **_k: {"topics": []})
    _出す("kopicha-threads", True)
    payload = json.loads(capsys.readouterr().out)
    行 = [r for r in payload["observed_only"] + payload["proven"] + payload["avoid"]
          if r["topic"] == "中学受験"]
    assert 行, payload
    # **旧鍵は廃止した**（設計 v1.0.0 §1 規則 4）。観測は観測者ごとに並ぶ。
    assert "audience" not in 行[0] and "audience_account" not in 行[0]
    観測 = 行[0]["observations"]
    assert [o["account"] for o in 観測] == ["asmon-kanto-threads"]
    assert [o["by"] for o in 観測] == ["kanto"]
    assert 観測[0]["audience"] == "受験親のやりとり"
    assert 観測[0]["note_id"].startswith("sha256:")

def test_空のaudienceで前の観測を消さない(thth_root, capsys, monkeypatch):
    """**`kind` と同じ穴が `audience` にもあった**（2026-09-12・このテストを
    書いていて見つけた）。

    `--audience` を付けずに verdict だけ記録すると、**最後の行が勝つので、
    前に書いた「誰がいたか」が消える。** 共有の事実が、書き足しで消えていた。
    """
    topics_mod.record("中学受験", verdict="alive", kind="行動",
                       audience="受験親のやりとり", by="kanto",
                       account="asmon-kanto-threads")
    # **`--audience` を付けずに**、別の account が verdict だけ記録する
    topics_mod.record("中学受験", verdict="alive", kind="行動",
                       audience="", by="自分", account="kopicha-threads")

    monkeypatch.setattr(cli_mod.account_report_mod, "topic_plan",
                         lambda *_a, **_k: {"topics": []})
    _出す("kopicha-threads", True)
    payload = json.loads(capsys.readouterr().out)
    行 = [r for r in payload["proven"] if r["topic"] == "中学受験"][0]
    # **観測者ごとに並ぶので、両方が残る**（設計 v1.0.0 §1 規則 1）。
    書いた = {o["account"]: o["audience"] for o in 行["observations"]}
    assert 書いた["asmon-kanto-threads"] == "受験親のやりとり", "**観測が消えている**"
    assert 書いた["kopicha-threads"] is None, "**書いていないものを書いたことにしない**"
