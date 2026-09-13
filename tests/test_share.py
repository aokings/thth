"""`thth share on|off|status|log`（設計 v2 §3・裁定 §7-3「既定 off」）。

ここで確かめるのは**振る舞い**（既定・積む・読める・止まる）。**落ちないもの**の
機械検査は `tests/test_share_safety.py`（§2）。

**送る先はまだ無い。** ネットワークに触る口が 1 つも無いことも見る。
"""
from __future__ import annotations

import json
import os

import pytest

from thth import cli as cli_mod
from thth import share as share_mod
from thth import topics as topics_mod

from tests.helpers.share_ledger import ROOT, build_reply_ledger


@pytest.fixture(autouse=True)
def _実物の台帳を読ませない(isolated_account):
    """**`accounts/` を隔離してから share を触る。**

    `share.sync()` は `accounts.list_account_names()` を歩いて
    `threadshape.load()` を呼ぶ。`THTH_APP_DIR` を隔離しないと、**repo に
    commit されている masaru の 4 本の台帳**と、そこに書かれた**実の
    `repo_dir`** を読みに行く（台帳を repo の外へ出すのは v2-2a・設計 v2 §3 で、
    まだ済んでいない）。テストが実データに触る経路をここで塞ぐ。
    """
    return isolated_account


# ------------------------------------------------------------------ 既定 off

def test_設定が無ければoff(thth_root):
    """**`config.json` が無ければ off**（裁定 §7-3）。「作られていない」が既定。"""
    assert share_mod.is_on() is False
    assert share_mod.status()["enabled"] is False


def test_offのあいだは1バイトも積まない(thth_root):
    """**off なら `state/share/` の下に何も作らない。**

    「積むけど送らない」ではなく「**積まない**」。ファイルが 1 つでも出来ると、
    「何も起きていない」と言えなくなる。
    """
    assert share_mod.enqueue_observation(topic="中学受験", audience="受験する家庭",
                                          kind="行動", status="ok") is None
    assert share_mod.enqueue_thread_shape({"post_id": "17912345678901234",
                                            "medium": "threads"}) is None
    結果 = share_mod.sync()
    assert 結果["enabled"] is False and 結果["added"] == 0

    assert share_mod.bytes_on_disk() == 0
    assert not os.path.exists(share_mod.root()), (
        f"off なのに置き場が出来ています: "
        f"{os.listdir(share_mod.root()) if os.path.isdir(share_mod.root()) else ''}")


def test_設定が壊れていたらoffのまま落ちる(thth_root):
    """**読めないものを on にしない。** 黙って off にもしない（loud reject）。"""
    os.makedirs(share_mod.root(), exist_ok=True)
    with open(share_mod.config_path(), "w", encoding="utf-8") as f:
        f.write("{壊れている")
    with pytest.raises(share_mod.ShareError):
        share_mod.is_on()


# --------------------------------------------------------------- on にする

def test_onにすると仮名が1つ出来て安定する(thth_root):
    """観測者の名前は**仮名**（設計 v2 §2）。16 桁・毎回同じ・全部が数字ではない。"""
    share_mod.set_enabled(True, by="masaru")
    仮名 = share_mod.observer_id()
    assert len(仮名) == share_mod.OBSERVER_ID_HEX
    assert not 仮名.isdigit(), "生の post_id と見分けが付かない形にしない"
    assert share_mod.observer_id() == 仮名, "呼ぶたびに変わると刻みが結べない"
    assert share_mod.status()["observer"] == 仮名


def test_onにしても送り先はまだ無いと毎回言う(thth_root):
    """**黙っていると「送った」と読まれる**（§3「黙って何かを送った瞬間に信用が消える」）。"""
    share_mod.set_enabled(True)
    st = share_mod.status()
    assert st["destination"] is None
    assert "送っていません" in st["destination_note"]


# ----------------------------------------------------------------- 観測を積む

def test_観測は語とaudienceと型と取得状態と仮名と日時だけが積まれる(thth_root):
    """§2 の表「観測 …… 語・誰がいたか・型・取得状態・観測者の仮名・日時」。

    **`verdict`（自分の判断）と `by`（誰が確かめたか）は積まない。**
    """
    topics_mod.record("中学受験2027", verdict="alive", audience="受験する家庭",
                       kind="年度付き", status="ok", by="masaru",
                       account="nigamilab-threads", note="画面で 10 人以上")
    share_mod.set_enabled(True)
    share_mod.sync()

    rows, broken = share_mod.log_rows()
    assert broken == []
    obs = [r for r in rows if r["schema"] == share_mod.SCHEMA_OBSERVATION]
    assert len(obs) == 1, rows
    row = obs[0]
    assert row["topic"] == "中学受験2027"
    assert row["audience"] == "受験する家庭"
    assert row["kind"] == "年度付き"
    assert row["obs_status"] == "ok"
    assert row["observer"] == share_mod.observer_id()
    assert row["observed_at"].startswith("20")
    # **落ちないもの**（ここは代表 2 つだけ。全部は test_share_safety.py）。
    assert "verdict" not in row and "by" not in row and "account" not in row


def test_同じ観測を2度積まない(thth_root):
    share_mod.set_enabled(True)
    topics_mod.record("コーヒー", verdict="alive", audience="喫茶", kind="一般名詞",
                       status="ok", by="masaru")
    一度目 = share_mod.sync()
    二度目 = share_mod.sync()
    assert 一度目["observations"] == 1
    assert 二度目["observations"] == 0, "同じ `note_id` は 1 回だけ"
    rows, _ = share_mod.log_rows()
    assert len([r for r in rows if r["schema"] == share_mod.SCHEMA_OBSERVATION]) == 1


def test_打ち消された観測は積まれず_積んだあとなら打ち消しが1行増える(thth_root):
    """`thth/topics.py` の `retract_note()` と同じ流儀——**消さずに 1 行足す。**"""
    row = topics_mod.record("消える語", verdict="dead", audience="", kind="カテゴリ",
                             status="empty", by="masaru")
    topics_mod.retract_note(row["note_id"], reason="語を間違えた", by="masaru")
    share_mod.set_enabled(True)
    share_mod.sync()
    rows, _ = share_mod.log_rows()
    assert [r for r in rows if r.get("topic") == "消える語"] == [], \
        "打ち消し済みの観測は最初から積まない"

    # 積んだあとで打ち消されたら、打ち消しの行が 1 本増える。
    row2 = topics_mod.record("あとで消す語", verdict="alive", audience="場",
                              kind="行動", status="ok", by="masaru")
    share_mod.sync()
    topics_mod.retract_note(row2["note_id"], reason="見直した", by="masaru")
    share_mod.sync()
    rows, _ = share_mod.log_rows()
    ret = [r for r in rows if r["schema"] == share_mod.SCHEMA_RETRACTION]
    assert len(ret) == 1 and ret[0]["retracts"] == row2["note_id"]
    # **理由（自由文）は積まない。**
    assert "reason" not in ret[0]


# ------------------------------------------------------- スレッドの形を積む

def test_スレッドの形はpost_idのハッシュで積まれる(thth_root, isolated_account):
    """§2「post_id は**ハッシュ**にして落とす（permalink に戻せない）」。"""
    build_reply_ledger(isolated_account)
    share_mod.set_enabled(True)
    share_mod.sync()

    rows, _ = share_mod.log_rows()
    shapes = [r for r in rows if r["schema"] == share_mod.SCHEMA_THREAD_SHAPE]
    assert len(shapes) == 1, rows
    row = shapes[0]
    assert row["post_hash"] == share_mod.post_hash(ROOT)
    assert len(row["post_hash"]) == 64 and row["post_hash"] != ROOT
    assert ROOT not in json.dumps(row, ensure_ascii=False)

    assert row["medium"] == "threads"
    assert row["branches"] == 3 and row["depth"] == 3
    assert row["replies_total"] == 5 and row["author_replies"] == 2
    assert row["participants_count"] == 2
    assert row["hour_band"] == "朝"
    # **取れていない刻みは `null`。`0` ではない**（規約 12）。
    assert row["growth"]["1"]["replies"] == 1
    assert row["growth"]["168"]["replies"] is None
    assert row["growth"]["168"]["covered"] is False


def test_ハッシュは塩つきで_塩は積まれない(thth_root):
    """塩を積むと総当たりで戻せる（Threads の post_id は数字で空間が狭い）。"""
    share_mod.set_enabled(True)
    import hashlib
    素のsha = hashlib.sha256(b"17912345678901234").hexdigest()
    assert share_mod.post_hash("17912345678901234") != 素のsha
    塩 = open(share_mod._salt_path(), encoding="utf-8").read().strip()
    share_mod.enqueue_thread_shape({"post_id": "17912345678901234",
                                     "medium": "threads"})
    積んだ = open(share_mod.outbox_path(), encoding="utf-8").read()
    assert 塩 not in 積んだ, "塩が outbox に出ています"


# --------------------------------------------------------------- log と off

def test_logで積んだ全部が読める(thth_root):
    """**1 行残らず。** 端折ると「何を出したか読める」と言えなくなる。"""
    share_mod.set_enabled(True)
    for i in range(7):
        topics_mod.record(f"語{i}", verdict="alive", audience="場", kind="行動",
                           status="ok", by="masaru")
    share_mod.sync()
    rows, broken = share_mod.log_rows()
    assert len(rows) == 7 and broken == []

    生の行 = [ln for ln in open(share_mod.outbox_path(), encoding="utf-8")
              if ln.strip()]
    assert len(生の行) == len(rows), "ファイルにある行が log から漏れている"


def test_offに戻すとそこから1バイトも増えない(thth_root):
    share_mod.set_enabled(True)
    topics_mod.record("前の語", verdict="alive", audience="場", kind="行動",
                       status="ok", by="masaru")
    share_mod.sync()
    前 = share_mod.bytes_on_disk()
    assert 前 > 0

    share_mod.set_enabled(False, by="masaru")
    topics_mod.record("あとの語", verdict="alive", audience="場", kind="行動",
                       status="ok", by="masaru")
    share_mod.sync()
    share_mod.enqueue_observation(topic="直に積もうとした語")
    assert share_mod.bytes_on_disk() == 前, "off にしたのに増えています"
    # **すでに積んだものは消さない**（消すのは人の手）。
    rows, _ = share_mod.log_rows()
    assert len(rows) == 1


# -------------------------------------------------------------------- CLI

def _share(*argv) -> int:
    """`build_parser()` を通す（`share_cli.register()` が 1 行で刺さっている確認）。"""
    args = cli_mod.build_parser().parse_args(["share", *argv])
    return args.func(args)


def test_CLIの既定はstatusでoffと言う(thth_root, capsys):
    assert _share() == 0
    out = capsys.readouterr().out
    assert "share: off（既定）" in out
    assert "まだ作っていません" in out, "off のうちは仮名も作らない"


def test_CLIのonとlogとoff(thth_root, capsys):
    topics_mod.record("お茶", verdict="alive", audience="茶葉", kind="一般名詞",
                       status="ok", by="masaru")
    assert _share("on", "--by", "masaru") == 0
    assert "share: on" in capsys.readouterr().out

    assert _share("log") == 0
    log_out = capsys.readouterr().out
    assert "お茶" in log_out and "これで全部です" in log_out

    assert _share("off") == 0
    assert "1 バイトも積みません" in capsys.readouterr().out
    assert share_mod.is_on() is False


def test_CLIのjsonは機械が読める形(thth_root, capsys):
    assert _share("status", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["enabled"] is False and payload["queued"] == 0


def test_設定が壊れていたらCLIは理由を言って非ゼロで終わる(thth_root, capsys):
    os.makedirs(share_mod.root(), exist_ok=True)
    with open(share_mod.config_path(), "w", encoding="utf-8") as f:
        f.write("[]")
    assert _share("status") == 2
    assert "share:" in capsys.readouterr().out


# ----------------------------------- 数字だけの語で詰まらない（監査 1・P2-5）

def test_数字だけの語は生のpost_idと間違われない(thth_root, capsys):
    """**人が選んだ語は ID ではない**（監査 1・P2-5）。

    `_assert_clean()` の `^\\d{10,}$` は生の post_id を弾くためのものだが、
    `topic` にも効いていた。語が 10 桁以上の数字だけ（年度番号・品番）だと
    `share on` が **rc=2 なのに on のまま残り**、以後 `sync` が恒久的に rc=2 に
    なる——語を消さない限り抜けられない。
    """
    topics_mod.record("2026091300", verdict="alive", audience="品番の話",
                       kind="固有名", status="ok", by="masaru")
    assert _share("on", "--by", "masaru") == 0, capsys.readouterr().out
    capsys.readouterr()

    rows, broken = share_mod.log_rows()
    assert broken == [], broken
    assert "2026091300" in [r.get("topic") for r in rows], rows
    # 2 回目も通る（恒久的に詰まらない）。
    assert _share("sync") == 0


def test_audienceに数字が並んでいても詰まらない(thth_root, capsys):
    topics_mod.record("お茶", verdict="alive", audience="12345678901 番地の人たち",
                       kind="一般名詞", status="ok", by="masaru")
    assert _share("on", "--by", "masaru") == 0, capsys.readouterr().out
    capsys.readouterr()
    assert _share("sync") == 0


def test_生のpost_idはID欄では今までどおり弾く(thth_root):
    """**緩めたのは自由文だけ。** ID の欄に生の post_id が入れば落ちる。"""
    with pytest.raises(share_mod.ShareError) as e:
        share_mod._assert_clean({"post_hash": "17916074118445631"})
    assert "生の post_id" in str(e.value)

    with pytest.raises(share_mod.ShareError) as e:
        share_mod._assert_clean({"root": "at://did:plc:x/app.bsky.feed.post/y"})
    assert "生の post_id" in str(e.value)

    # 自由文でも**長すぎれば**落ちる（原稿本文が回ってきていないか）。
    with pytest.raises(share_mod.ShareError) as e:
        share_mod._assert_clean({"audience": "あ" * (share_mod.MAX_FREE_TEXT + 1)})
    assert "長すぎます" in str(e.value)


def test_onが失敗したらonのまま残さない(thth_root, capsys, monkeypatch):
    """**失敗したら on にしない**（監査 1・P2-5 の後半）。

    前は config を先に書いていたので、最初の `sync` が落ちると「on にできな
    かった」と思っている人の手元に**積む設定だけが残った**。
    """
    def 落ちる(*a, **kw):
        raise share_mod.ShareError("わざと落とす（試験）")
    monkeypatch.setattr(share_mod, "sync", 落ちる)

    assert _share("on", "--by", "masaru") == 2
    assert "わざと落とす" in capsys.readouterr().out
    assert share_mod.is_on() is False, "失敗したのに on のまま残っている"


def test_shareはネットワークに触る口を持たない():
    """**送る先はまだ無い**（§6 v2-5）。import だけで確かめる。"""
    src = open(share_mod.__file__, encoding="utf-8").read()
    for 語 in ("urllib", "http.client", "requests", "socket", "curl"):
        assert 語 not in src, f"share が {語} を持っています（送る口はまだ作らない）"
