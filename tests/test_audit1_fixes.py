"""独立監査 1（Opus・まっさらな文脈・2026-09-12）の指摘を閉じる。

番号は監査 1 の報告のまま。

- **P1-1** 台帳が読めないと「観測なし」と偽り、次の `record` で全行が消える
- **P2-3** 旧行・壊れた行で `verdict_line()` が KeyError
- **P2-4** `retracts` が文字列でないと全読み口が TypeError／数値だと黙って通る
- **P2-5** `topic_cli._legacy_notes()` が account 無しの観測者を 1 人に潰す
- **P2-6** `thth topics history <語> --note X` が `--note` を黙って捨てる
- **P2-7** `retract-note` / `history` という account があると打ち消せない
- **P3-8** `--note ""` が無関係な文言で rc=1・`--note "   "` が空白の語を記録する
- **P3-9** 同じ `note_id` を同時に打ち消すと打ち消し行が 2 本
- **P3-11** `doctor --json` が app.env を 600 に直した事実を JSON に書かない

**共通の筋**は 1 つ: **判らなかったこと・できなかったことを、判った形／できた形で
残さない。** `topic_store` が最初から守っていた作法（設計 §8・受け入れ T14
「壊れた記録を観測なしと偽らない」）を、`topics.py` 側にも通す。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.conftest import run_thth, write_queue_file
from thth import topics as topics_mod


def 台帳を壊す(thth_root: str, *, 中身: str) -> str:
    """`state/topics.json` を「あるのに読めない」状態にする。"""
    path = os.path.join(thth_root, "state", "topics.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(中身)
    return path


def 壊れた台帳(thth_root: str) -> tuple:
    """**中身のある台帳の末尾 3 文字を落とす**（書き込み中断・手編集の打ち間違い相当）。

    監査 1 の `repro1.sh` と同じ壊し方。返すのは `(path, 元の行数)`。
    """
    topics_mod.record("お茶", verdict="alive", audience="茶葉の話", by="統括")
    topics_mod.record("精製", verdict="mismatch", audience="レアアース", by="研究者")
    path = topics_mod.path()
    with open(path, encoding="utf-8") as f:
        text = f.read()
    行数 = len(json.loads(text)["checks"])
    with open(path, "w", encoding="utf-8") as f:
        f.write(text[:-3])
    return path, 行数


# --- P1-1 -------------------------------------------------------------------

def test_P1_1_壊れた台帳を観測なしと偽らない(thth_root):
    """**無い**ときだけ空を返す。**あるのに読めない**ときは名指しで断る。"""
    assert topics_mod.load() == {"checks": []}, "まだ 1 度も記録していない＝空でよい"

    path, _ = 壊れた台帳(thth_root)
    with pytest.raises(topics_mod.ShelfBroken) as e:
        topics_mod.load()
    assert e.value.path == path
    assert "JSON として読めません" in e.value.detail
    assert "トピックの台帳が壊れています" in str(e.value)
    assert "直すまで読み書きしません" in str(e.value)


def test_P1_1_recordは壊れた台帳に書かず行を減らさない(thth_root):
    """**いちばん重い穴。** 以前はここで 46 件が消えていた。"""
    path, 行数 = 壊れた台帳(thth_root)
    前 = os.path.getsize(path)

    with pytest.raises(topics_mod.ShelfBroken):
        topics_mod.record("コーヒー", verdict="alive", by="監査1")

    assert os.path.getsize(path) == 前, "**壊れた台帳を上書きした**（既存の行が消える）"
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "お茶" in text and "精製" in text, "**壊れた台帳の中身まで消えた**"
    # **note_id を持つ行が 2 本そのまま残っている**（＝1 行も失っていない）。
    assert text.count('"note_id"') == 行数 == 2


def test_P1_1_checksが配列でない台帳も壊れているとして断る(thth_root):
    path = 台帳を壊す(thth_root, 中身='{"checks": {"お茶": "alive"}}')
    with pytest.raises(topics_mod.ShelfBroken) as e:
        topics_mod.load()
    assert e.value.path == path
    assert "checks が配列ではありません" in e.value.detail


def test_P1_1_いちばん外側がobjectでない台帳も断る(thth_root):
    台帳を壊す(thth_root, 中身='["お茶"]')
    with pytest.raises(topics_mod.ShelfBroken) as e:
        topics_mod.load()
    assert "object ではありません" in e.value.detail


def test_P1_1_adviseはrc2で壊れていると言う(thth_root, isolated_account):
    """`--advise` は LLM が毎回読む口。**「記録はありません」と言わせない。**"""
    壊れた台帳(thth_root)
    r = run_thth(["topics", isolated_account["name"], "--advise"])
    assert r.returncode == 2, f"rc={r.returncode}\nout={r.stdout}\nerr={r.stderr}"
    assert "トピックの台帳が壊れています" in r.stderr, r.stderr
    assert "直すまで読み書きしません" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_P1_1_advise_jsonは機械が読める形で断る(thth_root, isolated_account):
    壊れた台帳(thth_root)
    r = run_thth(["topics", isolated_account["name"], "--advise", "--json"])
    assert r.returncode == 2, r.stdout + r.stderr
    payload = json.loads(r.stdout)
    assert payload["error"] == "topics_shelf_broken"
    assert payload["path"].endswith(os.path.join("state", "topics.json"))
    assert payload["detail"]


def test_P1_1_historyとretract_noteも黙らない(thth_root):
    壊れた台帳(thth_root)
    for argv in (["topics", "history", "お茶"],
                 ["topics", "retract-note", "sha256:" + "0" * 64,
                  "--reason", "誤り", "--by", "監査1"]):
        r = run_thth(argv)
        assert r.returncode == 2, f"{argv}: rc={r.returncode}\n{r.stdout}{r.stderr}"
        assert "トピックの台帳が壊れています" in r.stderr, (argv, r.stderr)


def test_P1_1_記録の口も黙らない(thth_root, isolated_account):
    壊れた台帳(thth_root)
    r = run_thth(["topics", isolated_account["name"], "--note", "コーヒー",
                  "--verdict", "alive", "--by", "監査1"])
    assert r.returncode == 2, f"rc={r.returncode}\n{r.stdout}{r.stderr}"
    assert "トピックの台帳が壊れています" in r.stderr, r.stderr


def test_P1_1_承認の一段目はtracebackでなくrc2(thth_root, isolated_account):
    """**承認の入口で traceback を出さない。** 一段目は `verdict_line()` を呼ぶ。"""
    壊れた台帳(thth_root)
    path = write_queue_file(isolated_account["queue_dir"], "2026-09-09-t.md",
                            fm_overrides={"status": "draft", "approved_sha": None,
                                           "topic": "お茶"})
    r = run_thth(["approve", str(path)])
    assert r.returncode == 2, f"rc={r.returncode}\nout={r.stdout}\nerr={r.stderr}"
    assert "トピックの台帳が壊れています" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_P1_1_doctorは壊れた台帳に気づく(thth_root, isolated_account):
    """診断の道具が黙っていたのが穴（`repro1.sh` の「doctor は気づくか」）。"""
    壊れた台帳(thth_root)
    r = run_thth(["doctor", isolated_account["name"]])
    out = r.stdout + r.stderr
    assert r.returncode == 2, f"rc={r.returncode}\n{out}"
    assert "トピックの台帳が壊れています" in out, out


def test_P1_1_topic_cliも黙らない(thth_root, isolated_account):
    """`thth topics suggest`（`_legacy_notes` を読む口）。

    stdout は JSON だけ（設計 §6）なので、**断るときも JSON で断る。**
    """
    壊れた台帳(thth_root)
    path = write_queue_file(isolated_account["queue_dir"], "s.md",
                            body="## threads\n\n本文 https://example.test/a です。\n",
                            fm_overrides={"status": "draft", "approved_sha": None})
    r = run_thth(["topics", "suggest", str(path),
                  "--article-url", "https://example.test/a"])
    assert r.returncode == 2, f"rc={r.returncode}\n{r.stdout}{r.stderr}"
    payload = json.loads(r.stdout)
    assert payload["error"] == "topics_shelf_broken", payload
    assert payload["path"] and payload["detail"]
    assert "Traceback" not in r.stderr or "topics_shelf_broken" in r.stdout


# --- P2-3 -------------------------------------------------------------------

# 監査 1 の `legacy.py` が並べた 14 の行の形。**1 行の形が違うだけで、
# 承認の一段目・`--advise`・`--learned` が丸ごと落ちていた。**
旧行の形 = {
    "checked_at 無し(account 有り)": [
        {"topic": "A", "verdict": "alive", "by": "u", "account": "acc"},
    ],
    "checked_at 無し(account 無し=旧46件の形)": [
        {"topic": "B", "verdict": "mismatch", "by": "u"},
    ],
    "checked_at が数値": [
        {"topic": "C", "verdict": "alive", "by": "u", "account": "acc", "checked_at": 1234},
        {"topic": "C", "verdict": "alive", "by": "v", "account": "acc2",
         "checked_at": "2026-09-10T00:00:00+09:00"},
    ],
    "topic が空文字": [
        {"topic": "", "verdict": "alive", "by": "u", "account": "acc",
         "checked_at": "2026-09-10T00:00:00+09:00"},
    ],
    "by も account も無い": [
        {"topic": "D", "verdict": "alive", "checked_at": "2026-09-10T00:00:00+09:00"},
    ],
    "verdict が無い": [
        {"topic": "E", "by": "u", "account": "acc", "checked_at": "2026-09-10T00:00:00+09:00"},
    ],
    "verdict が知らない値": [
        {"topic": "F", "verdict": "maybe", "by": "u", "account": "acc",
         "checked_at": "2026-09-10T00:00:00+09:00"},
    ],
    "verdict 知らない値(他 account)": [
        {"topic": "G", "verdict": "maybe", "by": "u", "account": "other",
         "checked_at": "2026-09-10T00:00:00+09:00"},
    ],
    "status が知らない値": [
        {"topic": "H", "verdict": "alive", "status": "zzz", "by": "u", "account": "acc",
         "checked_at": "2026-09-10T00:00:00+09:00"},
    ],
    "retracts が list": [
        {"topic": "I", "verdict": "alive", "by": "u", "account": "acc",
         "checked_at": "2026-09-10T00:00:00+09:00"},
        {"retracts": ["x"], "reason": "r", "by": "u", "checked_at": "2026-09-11T00:00:00+09:00"},
    ],
    "retracts が dict": [
        {"topic": "J", "verdict": "alive", "by": "u", "account": "acc",
         "checked_at": "2026-09-10T00:00:00+09:00"},
        {"retracts": {"a": 1}, "reason": "r", "by": "u", "checked_at": "2026-09-11T00:00:00+09:00"},
    ],
    "retracts が数値": [
        {"topic": "K", "verdict": "alive", "by": "u", "account": "acc",
         "checked_at": "2026-09-10T00:00:00+09:00"},
        {"retracts": 7, "reason": "r", "by": "u", "checked_at": "2026-09-11T00:00:00+09:00"},
    ],
    "retracts と topic の両方を持つ行": [
        {"topic": "L", "verdict": "alive", "by": "u", "account": "acc",
         "retracts": "sha256:" + "0" * 64,
         "checked_at": "2026-09-10T00:00:00+09:00"},
    ],
    "legacy_verdict 無し + audience 有り(旧46件)": [
        {"topic": "M", "verdict": "alive", "audience": "受験親", "by": "統括"},
    ],
}


def 台帳を置く(thth_root: str, checks: list) -> None:
    path = os.path.join(thth_root, "state", "topics.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"checks": checks}, f, ensure_ascii=False)


@pytest.mark.parametrize("形", sorted(旧行の形))
def test_P2_3_旧行と壊れた行でどの読み口も落ちない(形, thth_root):
    """**14 の形 × 主な読み口。** 例外が 1 つも出ないこと。

    ここが落ちると、承認の一段目（`verdict_line`）が traceback になる
    ——**人が本文を確かめる画面そのものが出なくなる。**
    """
    from thth import topic_cli as topic_cli_mod, topic_store as store_mod

    checks = 旧行の形[形]
    台帳を置く(thth_root, checks)
    topic = checks[0].get("topic") or ""

    読み口 = {
        "notes()": lambda: topics_mod.notes(),
        "observation()": lambda: topics_mod.observation(),
        "observation(t)": lambda: topics_mod.observation(topic),
        "history(t)": lambda: topics_mod.history(topic),
        "newest(t)": lambda: topics_mod.newest(topic),
        "kind_of(t,acc)": lambda: topics_mod.kind_of(topic, "acc"),
        "judgment(t,acc)": lambda: topics_mod.judgment(topic, "acc"),
        "latest(t,account=acc)": lambda: topics_mod.latest(topic, account="acc"),
        "verdict_line(t,acc)": lambda: topics_mod.verdict_line(topic, account="acc"),
        "verdict_line(t,None)": lambda: topics_mod.verdict_line(topic),
        "other_accounts(t,acc)": lambda: topics_mod.other_accounts(topic, account="acc"),
        "learned({})": lambda: topics_mod.learned({}, account="acc"),
        "broken_rows()": lambda: topics_mod.broken_rows(),
        "legacy_observations()": lambda: store_mod.legacy_observations(),
        "_legacy_notes(acc)": lambda: topic_cli_mod._legacy_notes("acc"),
    }
    落ちた = []
    for 名, 呼ぶ in 読み口.items():
        try:
            呼ぶ()
        except Exception as e:            # noqa: BLE001  ——**どの例外も許さない**
            落ちた.append(f"{名}: {type(e).__name__}: {e}")
    assert not 落ちた, f"[{形}] で落ちた読み口:\n" + "\n".join(落ちた)


@pytest.mark.parametrize("形", ["checked_at 無し(account 有り)",
                                 "by も account も無い",
                                 "verdict が無い",
                                 "verdict が知らない値",
                                 "status が知らない値",
                                 "checked_at が数値"])
def test_P2_3_形の変な行は黙って通さず印を添える(形, thth_root):
    """**落ちないだけでは足りない。** 整った 1 行に見せると、読み手は信じる。"""
    checks = 旧行の形[形]
    台帳を置く(thth_root, checks)
    line = topics_mod.verdict_line(checks[0]["topic"], account="acc")
    assert "記録の形が古い・欠けあり" in line, line


def test_P2_3_知らない判定と取得状態を消さずに出す(thth_root):
    台帳を置く(thth_root, [
        {"topic": "F", "verdict": "maybe", "status": "zzz", "by": "u", "account": "acc",
         "checked_at": "2026-09-10T00:00:00+09:00", "audience": "誰か"},
    ])
    line = topics_mod.verdict_line("F", account="acc")
    assert "maybe" in line, f"**知らない判定を黙って消した**:\n{line}"
    assert "不明な取得状態 zzz" in line, f"**知らない取得状態を黙って消した**:\n{line}"


def test_P2_3_ちゃんとした記録には印を付けない(thth_root):
    """**印が常に出るなら、印は何も言っていないのと同じ。**"""
    topics_mod.record("お茶", verdict="alive", audience="茶葉", by="統括",
                       status="ok", account="acc")
    line = topics_mod.verdict_line("お茶", account="acc")
    assert "記録の形が古い・欠けあり" not in line, line


# --- P2-4 -------------------------------------------------------------------

def test_P2_4_形の合わない打ち消しは数えて捨てる(thth_root):
    """`retracts` が list / dict だと全読み口が TypeError、数値だと黙って通る。"""
    台帳を置く(thth_root, [
        {"topic": "K", "verdict": "alive", "by": "u", "account": "acc",
         "checked_at": "2026-09-10T00:00:00+09:00"},
        {"retracts": ["x"], "reason": "r", "by": "u", "checked_at": "2026-09-11T00:00:00+09:00"},
        {"retracts": {"a": 1}, "reason": "r", "by": "u", "checked_at": "2026-09-11T00:00:00+09:00"},
        {"retracts": 7, "reason": "r", "by": "u", "checked_at": "2026-09-11T00:00:00+09:00"},
    ])
    assert topics_mod.broken_rows() == 3
    assert [r["topic"] for r in topics_mod.notes()] == ["K"], "観測まで落とした"


def test_P2_4_historyが捨てた行の件数を言う(thth_root):
    台帳を置く(thth_root, [
        {"topic": "K", "verdict": "alive", "by": "u", "account": "acc",
         "checked_at": "2026-09-10T00:00:00+09:00"},
        {"retracts": 7, "reason": "r", "by": "u", "checked_at": "2026-09-11T00:00:00+09:00"},
    ])
    r = run_thth(["topics", "history", "K", "--json"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["shelf_broken_rows"] == 1, r.stdout

    人向け = run_thth(["topics", "history", "K"])
    assert "使えなかった行が 1 行" in 人向け.stdout, 人向け.stdout


def test_P2_4_adviseのjsonも捨てた行の件数を言う(thth_root, isolated_account):
    台帳を置く(thth_root, [
        {"topic": "K", "verdict": "alive", "by": "u", "account": "acc",
         "checked_at": "2026-09-10T00:00:00+09:00"},
        {"retracts": ["x"], "reason": "r", "by": "u", "checked_at": "2026-09-11T00:00:00+09:00"},
    ])
    r = run_thth(["topics", isolated_account["name"], "--advise", "--json"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["shelf_broken_rows"] == 1, r.stdout


def test_P2_4_正しい形の打ち消しはこれまでどおり効く(thth_root):
    """**捨てる側を厳しくしたせいで、正しい打ち消しまで効かなくなっていないか。**"""
    row = topics_mod.record("お茶", verdict="alive", by="統括", account="acc")
    topics_mod.retract_note(row["note_id"], reason="誤り", by="統括")
    assert topics_mod.notes("お茶") == []
    assert topics_mod.broken_rows() == 0
    assert [r["retracted"] is not None for r in topics_mod.history("お茶")] == [True]


# --- P2-5 -------------------------------------------------------------------

def test_P2_5_accountを持たない観測者が2人いても片方が消えない(thth_root, isolated_account):
    """監査 1 の `legacynotes3.py` と同じ形（2026-09-10 までの 46 件は account 無し）。

    `_legacy_notes()` は `観測[0]` で 1 語 1 行に潰していたので、**`thth topics
    suggest` を読む LLM には片方の観測しか届かなかった。** `--advise --json` は
    直っていたのに、**同じ棚を読む別の口が取り残されていた。**
    """
    import datetime

    from thth import jst
    from thth import topic_cli as topic_cli_mod

    def at(d):
        return datetime.datetime(2026, 9, d, 10, 0, 0, tzinfo=jst.JST)

    topics_mod.record("精製", verdict="alive", audience="【統括】茶の精製の話が中心だった",
                       by="統括", status="ok", now=at(1))
    topics_mod.record("精製", verdict="mismatch", audience="【研究者】レアアース・重加工",
                       by="研究者", status="ok", now=at(10))

    行 = [r for r in topic_cli_mod._legacy_notes(isolated_account["name"])
          if r["topic"] == "精製"][0]
    blob = json.dumps(行, ensure_ascii=False)
    assert "【統括】" in blob, f"**古いほうの観測が消えた**:\n{blob}"
    assert "【研究者】" in blob, f"**新しいほうの観測が消えた**:\n{blob}"

    # `--advise --json` と同じ形・新しい順・最大 5 件。
    assert [o["by"] for o in 行["observations"]] == ["研究者", "統括"], 行["observations"]
    assert 行["observations_more"] == 0
    assert set(行["observations"][0]) == {"note_id", "audience", "account", "by",
                                           "checked_at", "status", "kind"}
    # **代表 1 件であることを鍵名で言う**（`observation` は残す）。
    assert 行["observation"]["audience_representative"] == 行["observation"]["audience"]
    assert 行["observation"]["audience"] == "【研究者】レアアース・重加工"


def test_P2_5_observationsは5件までで残りは件数で言う(thth_root, isolated_account):
    import datetime

    from thth import jst
    from thth import topic_cli as topic_cli_mod

    for i in range(1, 8):
        topics_mod.record("精製", verdict="alive", audience=f"観測{i}", by=f"人{i}",
                           now=datetime.datetime(2026, 9, i, 10, 0, 0, tzinfo=jst.JST))
    行 = [r for r in topic_cli_mod._legacy_notes(isolated_account["name"])
          if r["topic"] == "精製"][0]
    assert len(行["observations"]) == 5
    assert 行["observations_more"] == 2, "**出さなかった件数を隠した**"


def test_P2_5_構造化棚のschemaは変えていない(thth_root, isolated_account):
    """**足すだけ。** 既存の鍵は 1 つも消していない（設計 §6）。"""
    from thth import topic_cli as topic_cli_mod

    topics_mod.record("精製", verdict="alive", audience="茶の精製", by="統括")
    行 = [r for r in topic_cli_mod._legacy_notes(isolated_account["name"])
          if r["topic"] == "精製"][0]
    for 鍵 in ("topic", "observation_id", "observation", "own_judgment",
               "legacy_judgment", "other_judgments", "notice"):
        assert 鍵 in 行, f"既存の鍵 {鍵} が消えた"
    for 鍵 in ("observation_id", "kind", "audience", "status", "checked_at",
               "recorded_by", "account"):
        assert 鍵 in 行["observation"], f"observation の既存の鍵 {鍵} が消えた"


# --- P2-6 -------------------------------------------------------------------

@pytest.mark.parametrize("余分", [
    ["--note", "X"],
    ["--verdict", "alive"],
    ["--audience", "誰か"],
    ["--kind", "行動"],
    ["--status", "ok"],
    ["--advise"],
    ["--plan"],
    ["--learned"],
])
def test_P2_6_historyの枝は使えない引数を黙って捨てない(余分, thth_root):
    """`thth topics history <語> --note X --verdict alive` が rc=0 で履歴を出していた。

    **打った人は記録したつもりでいる。** 記録は 1 行も増えていない。
    """
    topics_mod.record("お茶", verdict="alive", by="統括")
    r = run_thth(["topics", "history", "お茶", *余分])
    assert r.returncode == 2, f"{余分}: rc={r.returncode}\n{r.stdout}{r.stderr}"
    assert "この枝ではその引数は使えません" in r.stderr, r.stderr
    assert 余分[0] in r.stderr, r.stderr
    assert len(topics_mod.notes()) == 1, "**黙って記録が増えた／減った**"


def test_P2_6_retract_noteの枝も同じ(thth_root):
    row = topics_mod.record("お茶", verdict="alive", by="統括")
    r = run_thth(["topics", "retract-note", row["note_id"],
                  "--reason", "誤り", "--by", "統括", "--note", "X"])
    assert r.returncode == 2, f"rc={r.returncode}\n{r.stdout}{r.stderr}"
    assert "この枝ではその引数は使えません" in r.stderr, r.stderr
    assert topics_mod.notes("お茶"), "**断ったのに打ち消していた**"


def test_P2_6_reasonとbyは打ち消しの枝で使える(thth_root):
    """**締めすぎない。** `--reason` と `--by` は打ち消しが実際に使う。"""
    row = topics_mod.record("お茶", verdict="alive", by="統括")
    r = run_thth(["topics", "retract-note", row["note_id"],
                  "--reason", "誤り", "--by", "統括"])
    assert r.returncode == 0, f"rc={r.returncode}\n{r.stdout}{r.stderr}"
    assert topics_mod.notes("お茶") == []


# --- P2-7 -------------------------------------------------------------------

def test_P2_7_historyという名のaccountがあってもフラグで打てる(thth_root,
                                                                isolated_account_factory):
    """**account 名も機能の語も、利用者には変えられない。** 逃げ道を用意する。"""
    isolated_account_factory("history")
    isolated_account_factory("retract-note")
    row = topics_mod.record("お茶", verdict="alive", by="統括")

    # 位置引数の形は曖昧なので断る。**そのとき打てる形を案内する。**
    塞がった = run_thth(["topics", "history", "お茶"])
    assert 塞がった.returncode == 2, 塞がった.stdout + 塞がった.stderr
    assert "--history <語>" in 塞がった.stderr, 塞がった.stderr
    assert "この機能の語を変えて" not in 塞がった.stderr, (
        "**利用者にできないことを案内している**: " + 塞がった.stderr)

    # フラグの形なら通る。
    見る = run_thth(["topics", "--history", "お茶"])
    assert 見る.returncode == 0, 見る.stdout + 見る.stderr
    assert "お茶" in 見る.stdout and row["note_id"] in 見る.stdout, 見る.stdout

    塞がった2 = run_thth(["topics", "retract-note", row["note_id"],
                          "--reason", "誤り", "--by", "統括"])
    assert 塞がった2.returncode == 2, 塞がった2.stdout + 塞がった2.stderr
    assert "--retract-note <note_id>" in 塞がった2.stderr, 塞がった2.stderr

    打ち消す = run_thth(["topics", "--retract-note", row["note_id"],
                          "--reason", "誤り", "--by", "統括"])
    assert 打ち消す.returncode == 0, 打ち消す.stdout + 打ち消す.stderr
    assert topics_mod.notes("お茶") == []


def test_P2_7_フラグの形は衝突が無くても使える(thth_root):
    topics_mod.record("お茶", verdict="alive", by="統括")
    r = run_thth(["topics", "--history", "お茶", "--json"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["topic"] == "お茶"


# --- P3-8 -------------------------------------------------------------------

def test_P3_8_recordは空の語を拒む(thth_root):
    for 語 in ("", "   ", "　"):
        with pytest.raises(ValueError, match="語が空です"):
            topics_mod.record(語, verdict="alive", by="統括")
    assert topics_mod.notes() == []


@pytest.mark.parametrize("語", ["", "   "])
def test_P3_8_空のnoteはrc2で語が空ですと言う(語, thth_root, isolated_account):
    """空文字は記録の枝を素通りして**無関係な文言で rc=1** になっていた。"""
    r = run_thth(["topics", isolated_account["name"], "--note", 語,
                  "--verdict", "alive", "--by", "統括"])
    assert r.returncode == 2, f"rc={r.returncode}\n{r.stdout}{r.stderr}"
    assert "語が空です" in r.stderr, r.stderr
    assert topics_mod.notes() == [], "**空白だけの語が台帳に入った**"
