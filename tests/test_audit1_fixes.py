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
