"""棚の読み出しを**観測者ごと**にする（設計 v1.0.0 §1・受け入れ T-A1〜T-A4）。

**壊れているのは保存ではなく読み出しだった。** `topics.observation()` と
`topic_store.legacy_observations()` の両方が「語ごとに最後の 1 行」で潰していたので、

- **別の account が同じ語を書くと、前の観測が画面から消える**（穴 2・穴 4）
- しかも `audience` に account 固有の実績が乗っていたので、**他所の実績が
  入れ替わって見えなくなる**（穴 1）
- 上書きでしか訂正できないので、**誤記録を消す口が無い**（穴 3）

観測者ごとに並べると穴 1・2・4 は閉じるが、**穴 3 はむしろ悪くなる**——誤記録が
「その観測者の最新」として残り続ける。だから**打ち消しの口**を一緒に入れる。
"""
from __future__ import annotations

import json

from thth import cli as cli_mod, topic_store as store, topics as topics_mod


def _plan無し(monkeypatch):
    monkeypatch.setattr(cli_mod.account_report_mod, "topic_plan",
                         lambda *_a, **_k: {"topics": []})


def _advise(monkeypatch, capsys, account="kopicha-threads", *, as_json=False):
    _plan無し(monkeypatch)
    cli_mod._advise(account, as_json=as_json)
    out = capsys.readouterr().out
    return json.loads(out) if as_json else out


def _語(payload, topic):
    行 = [r for r in payload["proven"] + payload["avoid"] + payload["observed_only"]
          if r["topic"] == topic]
    assert 行, payload
    return 行[0]


# --- T-A1 -------------------------------------------------------------------

def test_TA1_別accountが同じ語を書いても前の観測が消えない(thth_root, capsys, monkeypatch):
    """**画面と `--json` の両方で確かめる。** 片方だけ直しても穴は塞がらない。"""
    topics_mod.record("コーヒー", verdict="alive", kind="一般名詞",
                       audience="焙煎と抽出の話", by="kanto",
                       account="asmon-kanto-threads")
    topics_mod.record("コーヒー", verdict="alive", kind="一般名詞",
                       audience="豆屋と喫茶の話", by="自分",
                       account="kopicha-threads")

    out = _advise(monkeypatch, capsys)
    assert "焙煎と抽出の話" in out, f"**前の観測が画面から消えた**:\n{out}"
    assert "豆屋と喫茶の話" in out, out
    assert "asmon-kanto-threads" in out, f"**出どころが無い**:\n{out}"

    payload = _advise(monkeypatch, capsys, as_json=True)
    書いた = {o["account"]: o["audience"] for o in _語(payload, "コーヒー")["observations"]}
    assert 書いた == {"asmon-kanto-threads": "焙煎と抽出の話",
                       "kopicha-threads": "豆屋と喫茶の話"}, 書いた


def test_TA1_同じ観測者の古い行は代表にならない(thth_root):
    """**観測者ごとの「最新」**——同じ人の古い行まで並べると画面が埋まる。
    古い行は消えず、`history` で読める。"""
    topics_mod.record("お茶", verdict="alive", audience="古い見立て", by="kanto",
                       account="asmon-kanto-threads")
    topics_mod.record("お茶", verdict="alive", audience="新しい見立て", by="kanto",
                       account="asmon-kanto-threads")
    assert [r["audience"] for r in topics_mod.observation("お茶")] == ["新しい見立て"]
    assert [r["audience"] for r in topics_mod.history("お茶")] == \
        ["新しい見立て", "古い見立て"]


def test_TA1_accountが無ければbyが観測者になる(thth_root):
    """観測者の鍵は `account` → `by` → `"(記録なし)"`（規則 1）。"""
    topics_mod.record("精製", verdict="mismatch", audience="鉱物精製", by="統括")
    topics_mod.record("精製", verdict="alive", audience="別の見立て", by="kanto")
    assert sorted(topics_mod.observer_of(r) for r in topics_mod.observation("精製")) \
        == ["kanto", "統括"]


# --- T-A2 -------------------------------------------------------------------

def test_TA2_打ち消した行はどの読み口にも出ないが行は残る(thth_root, capsys, monkeypatch):
    """**消さない。読むときに飛ばす。**

    `--advise`（人向け・`--json`）・`legacy_observations()` から消え、
    `history` には**「打ち消し済み」の印つきで**残る。`topics.json` の行数は
    **減らない**（打ち消しの行が増えるので、むしろ増える）。
    """
    誤り = topics_mod.record("テスト", verdict="dead", audience="値域を試しただけ",
                              by="運用", account="kopicha-threads")
    行数 = len(topics_mod.load()["checks"])

    assert any(r["topic"] == "テスト" for r in store.legacy_observations())
    assert "値域を試しただけ" in _advise(monkeypatch, capsys)

    topics_mod.retract_note(誤り["note_id"], reason="本番に打った誤記録", by="運用")

    out = _advise(monkeypatch, capsys)
    assert "値域を試しただけ" not in out, f"**打ち消しが効いていない**:\n{out}"
    assert "テスト" not in out, out
    payload = _advise(monkeypatch, capsys, as_json=True)
    assert not [r for r in payload["proven"] + payload["avoid"]
                + payload["observed_only"] if r["topic"] == "テスト"], payload
    assert not [r for r in store.legacy_observations() if r["topic"] == "テスト"]
    assert topics_mod.observation("テスト") == []
    assert topics_mod.notes("テスト") == []

    # **行は消えていない。** 打ち消しの行が 1 本増えている。
    assert len(topics_mod.load()["checks"]) == 行数 + 1
    履歴 = topics_mod.history("テスト")
    assert len(履歴) == 1 and 履歴[0]["retracted"], 履歴
    assert 履歴[0]["retracted"]["reason"] == "本番に打った誤記録"


def test_TA2_打ち消しても判断や型には残らない(thth_root):
    """`judgment` / `kind_of` / `other_accounts` も同じ読み口を通る。"""
    行 = topics_mod.record("テスト", verdict="alive", kind="自作", by="運用",
                            account="kopicha-threads")
    assert topics_mod.judgment("テスト", "kopicha-threads")
    assert topics_mod.kind_of("テスト") == "自作"
    topics_mod.retract_note(行["note_id"], reason="誤記録", by="運用")
    assert topics_mod.judgment("テスト", "kopicha-threads") == {}
    assert topics_mod.kind_of("テスト") is None
    assert topics_mod.other_accounts("テスト", account="other") == []


def test_TA2_打ち消しは理由と名乗りを要る(thth_root):
    import pytest
    行 = topics_mod.record("テスト", verdict="alive", by="運用")
    for reason, by in (("", "運用"), ("誤記録", "")):
        with pytest.raises(ValueError):
            topics_mod.retract_note(行["note_id"], reason=reason, by=by)
    with pytest.raises(ValueError):
        topics_mod.retract_note("sha256:" + "0" * 64, reason="無い行", by="運用")
    with pytest.raises(ValueError):
        topics_mod.retract_note("でたらめ", reason="形が違う", by="運用")
    # 二度は打てない（**「無い」と「もう下げてある」を混ぜない**）
    topics_mod.retract_note(行["note_id"], reason="誤記録", by="運用")
    with pytest.raises(ValueError):
        topics_mod.retract_note(行["note_id"], reason="もう一度", by="運用")


def test_TA2_note_idは旧行にも同じ規則で付く(thth_root):
    """**旧行は `note_id` を持たない。** 読むときに計算して、同じ ID で打ち消せる。"""
    topics_mod.record("旧", verdict="alive", audience="昔の観測", by="昔の記録")
    生 = topics_mod.load()
    for row in 生["checks"]:
        row.pop("note_id", None)                       # 旧行の形に戻す
    import json as _json, os
    with open(topics_mod.path(), "w", encoding="utf-8") as f:
        _json.dump(生, f, ensure_ascii=False)
    assert "note_id" not in topics_mod.load()["checks"][0]

    行 = topics_mod.observation("旧")[0]
    assert 行["note_id"].startswith("sha256:")
    topics_mod.retract_note(行["note_id"], reason="旧行も打ち消せる", by="運用")
    assert topics_mod.observation("旧") == []


def test_TA2_打ち消しの行は観測として数えない(thth_root):
    """打ち消しの行は `topic` を持たない。**観測の一覧に化けて入らない。**"""
    行 = topics_mod.record("お茶", verdict="alive", by="運用")
    topics_mod.retract_note(行["note_id"], reason="誤記録", by="運用")
    assert topics_mod.observation() == {}
    assert store.legacy_observations() == []


# --- T-A3 -------------------------------------------------------------------

def test_TA3_人向け画面は1語2件とほかk件(thth_root, capsys, monkeypatch):
    """**観測者の数だけ行が増える**ので上限が要る（前任の指摘）。
    **出さなかった分は件数で言い、`history` へ送る。**"""
    for who, 何 in (("asmon-kanto-threads", "受験親のやりとり"),
                     ("kopicha-threads", "茶の話も混ざる"),
                     ("nigamilab-threads", "苦味の話は無い")):
        topics_mod.record("中学受験", verdict="alive", kind="行動",
                           audience=何, by=who, account=who)
    out = _advise(monkeypatch, capsys)
    行 = [l for l in out.splitlines() if any(x in l for x in
          ("受験親のやりとり", "茶の話も混ざる", "苦味の話は無い"))]
    assert len(行) == 2, f"**2 件に絞れていない**:\n{out}"
    assert "ほか 1 件" in out, f"**出さなかった分を隠している**:\n{out}"
    assert "thth topics history 中学受験" in out, out


def test_TA3_jsonは最大5件と残りの件数(thth_root, capsys, monkeypatch):
    for i in range(7):
        topics_mod.record("中学受験", verdict="alive", kind="行動",
                           audience=f"観測 {i}", by=f"who{i}", account=f"acct{i}")
    payload = _advise(monkeypatch, capsys, as_json=True)
    行 = _語(payload, "中学受験")
    assert len(行["observations"]) == 5
    assert 行["observations_more"] == 2
    # **新しい順。** 追記順が時系列なので、最後に書いた 5 件が並ぶ。
    assert [o["account"] for o in 行["observations"]] == \
        ["acct6", "acct5", "acct4", "acct3", "acct2"]


def test_TA3_historyは全件返す(thth_root, capsys):
    for i in range(7):
        topics_mod.record("中学受験", verdict="alive", audience=f"観測 {i}",
                           by=f"who{i}", account=f"acct{i}")
    assert cli_mod.main(["topics", "history", "中学受験"]) == 0
    out = capsys.readouterr().out
    for i in range(7):
        assert f"観測 {i}" in out, f"**{i} 件目が history に無い**:\n{out}"


def test_TA3_historyとretract_noteはCLIから打てる(thth_root, capsys):
    行 = topics_mod.record("テスト", verdict="dead", audience="誤記録",
                            by="運用", account="kopicha-threads")
    assert cli_mod.main(["topics", "retract-note", 行["note_id"],
                          "--reason", "本番に打った", "--by", "運用"]) == 0
    assert "打ち消しました" in capsys.readouterr().out

    assert cli_mod.main(["topics", "history", "テスト", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["notes"]) == 1
    assert payload["notes"][0]["retracted"]["reason"] == "本番に打った"

    # 語も note_id も無いときは**黙って成功しない**
    assert cli_mod.main(["topics", "history"]) == 2
    assert cli_mod.main(["topics", "retract-note"]) == 2
    # 余分な引数を黙って捨てない
    assert cli_mod.main(["topics", "kopicha-threads", "よけいなもの"]) == 2


# --- 規則 5: legacy_observations() も観測者別 -------------------------------

def test_規則5_旧棚の読み出しも観測者ごとに引ける(thth_root):
    """**`observation_refs` に書ける ID を、観測者ごとに用意する。**

    語ごとに 1 件しか返していなかったので、**同じ語を別の観測者が書くと、
    先に書いた人の観測は `suggest` の証拠から消え、ID でも引けなかった**
    ——「観測が足りない」と言われても、自分の観測を指す手段が無い。

    **`observation_id` の計算は変えていない**（記録の中身から決まるまま）。
    変えたのは**潰す単位**だけ。
    """
    topics_mod.record("コーヒー", verdict="alive", audience="焙煎と抽出",
                       by="kanto", account="asmon-kanto-threads")
    topics_mod.record("コーヒー", verdict="alive", audience="豆屋と喫茶",
                       by="自分", account="kopicha-threads")

    行 = [r for r in store.legacy_observations() if r["topic"] == "コーヒー"]
    assert len(行) == 2, f"**観測者ごとに引けない**: {行}"
    assert {r["account"] for r in 行} == {"asmon-kanto-threads", "kopicha-threads"}
    assert {r["audience"] for r in 行} == {"焙煎と抽出", "豆屋と喫茶"}
    assert len({r["observation_id"] for r in 行}) == 2, "ID が衝突している"
    for r in 行:
        assert r["observation_id"].startswith("sha256:")
        # **中身から決まる ID のまま**（保存側と同じ計算）。
        from thth import topic_models as models
        assert r["observation_id"] == models.content_id(
            r, exclude=("observation_id",))


def test_規則5_記録するといま書いた行のIDが返る(thth_root, capsys):
    """**語だけで引くと、他人の観測の ID が返る。** いま書いた行のものを返す。

    **先に書いた観測者が、あとから書き直す**のが現実の順序（棚は観測者ごとに
    並ぶので、書き直しても並びの位置は変わらない）。**語だけで引くと、
    そのあいだに割り込んだ別の観測者の ID が返る。**
    """
    topics_mod.record("コーヒー", verdict="alive", audience="古い見立て",
                       by="自分", account="kopicha-threads")
    topics_mod.record("コーヒー", verdict="alive", audience="焙煎と抽出",
                       by="kanto", account="asmon-kanto-threads")
    assert cli_mod.main(["topics", "kopicha-threads", "--note", "コーヒー",
                          "--verdict", "alive", "--audience", "豆屋と喫茶",
                          "--by", "自分"]) == 0
    out = capsys.readouterr().out
    出た = [l.split(": ", 1)[1].strip() for l in out.splitlines()
             if l.strip().startswith("観測 ID:")]
    assert 出た, out
    引いた = [r for r in store.legacy_observations()
              if r["observation_id"] == 出た[0]]
    assert len(引いた) == 1, out
    assert 引いた[0]["account"] == "kopicha-threads", \
        f"**他人の観測の ID を返している**: {引いた[0]}"
    assert 引いた[0]["audience"] == "豆屋と喫茶"
    # 記録 ID も返る（打ち消せる形で返す）
    assert "記録 ID: sha256:" in out, out


# --- T-A4 -------------------------------------------------------------------

def test_TA4_jsonに旧3鍵が無くobservationsがある(thth_root, capsys, monkeypatch):
    """**名前を変えないと古い読み手が旧意味で読む**（規約 5・設計 §1 規則 4）。"""
    topics_mod.record("お茶", verdict="alive", kind="一般名詞", status="ok",
                       audience="茶葉の話", by="自分", account="kopicha-threads")
    payload = _advise(monkeypatch, capsys, as_json=True)
    行 = _語(payload, "お茶")
    for 旧 in ("audience", "audience_account", "audience_by"):
        assert 旧 not in 行, f"**旧鍵が残っている**: {旧}"
    assert set(行["observations"][0]) == {
        "note_id", "audience", "account", "by", "checked_at", "status", "kind"}
    assert 行["observations_more"] == 0
    # `kind` は**自分の記録を優先する現行規則のまま**（規則 4）
    assert 行["kind"] == "一般名詞"
