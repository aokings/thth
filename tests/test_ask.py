"""`before_you_post`（`thth/ask.py`・`thth/ask_cli.py`・MCP）。設計 v2 §1・§6 v2-1。

**泉はまだ無い。** ここで確かめるのは「泉と同じ**契約**を、手元の account の
水だけで守れているか」——数は `n`・期間・揃えた条件つきでしか出ない、閾値に
満たない群は中央値を返さない、媒体をまたがない、本文と `username` はどこにも
出ない、指図はしない。

**偽の台帳は本番と同じ形で組む**（規約 11）。`thth/collect.py` が実際に書く
行の形（`data/sns/insights/posts/*.ndjson` と `data/sns/replies/*.ndjson`）を
そのまま作る——fixture だけ別の形をしていると、テストは通るのに本番では
動かない。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
import sys

import pytest

from thth import ask as ask_mod
from thth import jst as jst_mod
from thth import topics as topics_mod
from tests.conftest import BIN_THTH, REPO_ROOT

# **本文と観測者の名前は、どこにも出てはいけない**（設計 v2 §1 規約 4）。
# 偽の台帳にこの 2 つを埋めておいて、答えの JSON 全体を grep する。
SECRET_BODY = "SECRET-BODY-本文は泉に落ちない"
SECRET_USER = "SECRET-USER-観測者の名前"

ACCOUNT = "nigamilab-threads"
TOPIC = "コーヒー"

# conftest の `frozen_now_jst` が固定する「いま」。期間（既定 30 日）の内側に
# 投稿を置くための基準。
NOW = datetime.datetime(2026, 9, 9, 10, 0, 0, tzinfo=jst_mod.JST)


# ---------------------------------------------------------------- 台帳を組む

def _write_ndjson(path: str, rows: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _utc(dt: datetime.datetime) -> str:
    """返信の `timestamp` は Threads API の生の形（UTC・`+0000`）。"""
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def write_post(account, post_id: str, *, posted_at: datetime.datetime,
               topic: str = TOPIC, branches: int = 2,
               first_reply_min: int = 30, views: int = 300,
               medium: str = "threads", reply_to=None,
               covered: bool = True, measured: bool = True) -> None:
    """投稿 1 本ぶんの台帳（実測 1 行 ＋ 返信の台帳）を本番と同じ形で書く。

    `branches` 本の**他人**の返信を根の直下に置く。最初の 1 本は
    `first_reply_min` 分後、以降は 10 分ずつ後ろ（どれも 24 時間以内）。
    """
    repo = account["repo_dir"]
    if measured:
        _write_ndjson(
            os.path.join(repo, "data", "sns", "insights", "posts",
                         f"{post_id}.ndjson"),
            [{
                "post_id": post_id,
                "file": f"{post_id}.md",
                "account": account["name"],
                # **採取時点の媒体**（設計 v2 §2.1）。台帳の `media` ではない。
                "medium": medium,
                "topic": topic,
                "reply_to": reply_to,
                # 本文は台帳に落ちない（長さだけ）。**それでも**偽の台帳には
                # 本文を混ぜておく——出ないことを確かめるため。
                "text": SECRET_BODY,
                "text_length": len(SECRET_BODY),
                "has_link": False,
                "collected_at": jst_mod.iso(posted_at + datetime.timedelta(hours=25)),
                "posted_at": jst_mod.iso(posted_at),
                "age_hours": 25.0,
                "marks": [24],
                "metrics": {"views": views, "likes": 0, "replies": branches,
                            "reposts": 0, "quotes": 0, "shares": 0},
            }])

    rows = []
    if covered:
        rows.append({
            "kind": "fetch", "post_id": post_id,
            "collected_at": jst_mod.iso(posted_at + datetime.timedelta(hours=25)),
            "age_hours": 25.0, "marks": [24], "trigger": "marks",
            "replies": branches, "id_missing": 0,
        })
    for i in range(branches):
        minutes = first_reply_min + i * 10
        rows.append({
            "kind": "reply", "post_id": post_id, "id": f"{post_id}-r{i}",
            "is_reply": True,
            # **身内ではない**（account の handle は "nigamilab"）。
            "username": f"{SECRET_USER}-{i}",
            "text": SECRET_BODY,
            "timestamp": _utc(posted_at + datetime.timedelta(minutes=minutes)),
            "replied_to": {"id": post_id}, "root_post": {"id": post_id},
            "collected_at": jst_mod.iso(posted_at + datetime.timedelta(hours=25)),
        })
    _write_ndjson(os.path.join(repo, "data", "sns", "replies",
                               f"{post_id}.ndjson"), rows)


def write_many(account, n: int, *, hour: int = 8, day0: int = 15,
               prefix: str = "P", **kwargs) -> list:
    """同じ条件の投稿を `n` 本。**日付だけずらす**（期間 30 日の内側に収める）。"""
    ids = []
    for i in range(n):
        post_id = f"1800000000000{prefix}{i:03d}"
        posted_at = datetime.datetime(2026, 8, day0, hour, 0, 0,
                                      tzinfo=jst_mod.JST) + datetime.timedelta(days=i % 20)
        write_post(account, post_id, posted_at=posted_at, **kwargs)
        ids.append(post_id)
    return ids


@pytest.fixture
def account(isolated_account):
    return isolated_account


# ---------------------------------------------------------------- (a) 答える

def test_a_25本あれば中央値が件数と期間つきで返る(account):
    write_many(account, 25, branches=3, first_reply_min=42, views=310)

    answer = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, now=NOW)

    assert answer["comparable"] == {"n": 25, "window_days": 30,
                                    "aligned_on": "age_hours=24",
                                    "medium": "threads"}
    # **数は単独で返らない**（規約 1）。どの指標にも `n` が付いている。
    for metric in ("branches_24h", "first_reply_min", "views_24h"):
        stat = answer["expected"][metric]
        assert set(stat) == {"median", "p25", "p75", "n"}
        assert stat["n"] == 25
        assert stat["median"] is not None, metric
    assert answer["expected"]["branches_24h"]["median"] == 3
    assert answer["expected"]["first_reply_min"]["median"] == 42
    assert answer["expected"]["views_24h"]["median"] == 310
    # 閾値を越えた群には「n が足りない」は出ない。
    assert not [line for line in answer["cannot_say"] if "20 未満" in line]
    assert answer["provenance"] == {"source": "local", "observers": 0,
                                    "updated": "2026-09-03", "schema": 1}
    # 要約は**そのまま引用できる形**（規約 5）——語・媒体・期間・n が入る。
    for 断片 in (TOPIC, "threads", "直近 30 日", "n=25"):
        assert 断片 in answer["summary"]


def test_a2_閾値は引数で動く(account):
    """`min_n` は既定 20 だが、渡せばそこで切り替わる（設計 v2 §7 裁定 4）。"""
    write_many(account, 25)
    厳しい = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, min_n=26, now=NOW)
    assert 厳しい["expected"]["branches_24h"]["median"] is None
    assert 厳しい["expected"]["branches_24h"]["n"] == 25


def test_a3_1件でも落ちない(account):
    """`--min-n 1` で 1 件しかない群に当たっても**口ごと落ちない**（回帰）。

    四分位は 2 件から計算できる。1 件のときは中央値だけ返し、`p25`・`p75` は
    「言えない」——例外を投げるのではなく。
    """
    write_post(account, "18000000000000001",
               posted_at=datetime.datetime(2026, 9, 1, 8, 0, tzinfo=jst_mod.JST))
    answer = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, min_n=1, now=NOW)
    stat = answer["expected"]["branches_24h"]
    assert stat["n"] == 1 and stat["median"] is not None
    assert stat["p25"] is None and stat["p75"] is None


# ---------------------------------------------------------------- (b) 言えない

def test_b_19本なら中央値を返さずcannot_sayに理由が出る(account):
    write_many(account, 19)

    answer = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, now=NOW)

    assert answer["comparable"]["n"] == 19
    for metric in ("branches_24h", "first_reply_min", "views_24h"):
        stat = answer["expected"][metric]
        assert stat["median"] is None, metric
        assert stat["p25"] is None and stat["p75"] is None
        # **`n` は出す**——「言えない」と「1 件も無い」を読み分けるため。
        assert stat["n"] == 19
    # 理由に `n=…` が入っている（設計 v2 §1 規約 2）。
    理由 = [line for line in answer["cannot_say"] if "n=19" in line]
    assert len(理由) == 3, answer["cannot_say"]
    assert all("20 未満" in line for line in 理由)
    # **売るために断定しない。** 要約も言い切らない。
    assert "言える中央値がありません" in answer["summary"]


def test_b2_1本も無ければn0で言えないと出る(account):
    answer = ask_mod.before_you_post(ACCOUNT, topic="まだ試していない語", now=NOW)
    assert answer["comparable"]["n"] == 0
    assert answer["expected"]["branches_24h"] == {"median": None, "p25": None,
                                                  "p75": None, "n": 0}
    assert any("n=0" in line for line in answer["cannot_say"])


# ---------------------------------------------------------------- (c) 媒体

def test_c_媒体違いは混ぜず数えた事実だけ出す(account):
    """**媒体をまたいで比べない**（設計 v2 §2.1）。

    同じ語の投稿でも、採取時点の `medium` が違えば群に入らない。だが
    **黙って捨てない**——何件外したかを `cannot_say` に出す。
    """
    write_many(account, 25, prefix="T")                      # threads
    write_many(account, 8, prefix="B", medium="bluesky")     # bluesky

    answer = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, now=NOW)

    # bluesky の 8 本は 1 件も混ざっていない。
    assert answer["comparable"]["n"] == 25
    assert answer["comparable"]["medium"] == "threads"
    assert all(stat["n"] == 25 for stat in answer["expected"].values())
    混ぜなかった = [line for line in answer["cannot_say"] if "媒体違い" in line]
    assert len(混ぜなかった) == 1
    assert "8 件" in 混ぜなかった[0]

    # 逆向きも同じ（bluesky を聞けば threads は入らない）。
    逆 = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, medium="bluesky", now=NOW)
    assert 逆["comparable"]["n"] == 8 and 逆["comparable"]["medium"] == "bluesky"


def test_c2_期間外と媒体違いの件数はこの語のものだけ(account):
    """`cannot_say` の件数は「**この語について**数えられなかったもの」。

    先に媒体で切ってから語で絞ると、`コーヒー` を聞いた人の `cannot_say` に
    別の語の投稿が混ざる。
    """
    write_many(account, 5, prefix="T")
    write_many(account, 4, prefix="X", medium="bluesky", topic="別の語")

    answer = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, now=NOW)
    assert not [line for line in answer["cannot_say"] if "媒体違い" in line]


# ------------------------------------- (c3) 壊れて数えられない ≠ 無かった

def test_c3_返信台帳が壊れていたらcannot_sayに出る(account):
    """**「壊れて数えられない」を「無かった」に化けさせない**（監査 1・P2-3）。

    返信の `<post_id>.ndjson` が JSON として読めないと、`replies.load()` は
    **そのファイルを丸ごと落とす**（壊れと不存在を混ぜない流儀）。落ちた分だけ
    枝が少なく出るのに、`before_you_post` は何も言わずに n=0 を返していた。
    観測の棚が壊れたときは `cannot_say` に出るのに、返信台帳だけ抜けていた。
    """
    ids = write_many(account, 25, branches=3)
    replies_dir = os.path.join(account["repo_dir"], "data", "sns", "replies")
    壊す = ids[:4]
    for post_id in 壊す:
        with open(os.path.join(replies_dir, f"{post_id}.ndjson"), "w",
                  encoding="utf-8") as f:
            f.write('{"kind": "reply", これはJSONではない\n')

    answer = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, now=NOW)

    理由 = [line for line in answer["cannot_say"] if "返信台帳" in line]
    assert 理由, answer["cannot_say"]
    assert f"{len(壊す)} 本が読めません" in 理由[0], 理由[0]
    assert 壊す[0] in 理由[0], 理由[0]
    # **枝が黙って 0 になっていない**ことも言う（少なく出ている、と添える）。
    assert "少なく出ています" in 理由[0], 理由[0]


def test_c3b_返信台帳が全部読めれば余計なことを言わない(account):
    """**言わなくてよいときは言わない**（`cannot_say` を水増ししない）。"""
    write_many(account, 25, branches=3)
    answer = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, now=NOW)
    assert not [line for line in answer["cannot_say"] if "返信台帳" in line], \
        answer["cannot_say"]


# ------------------------------------------------- (d) 1 つだけ・指図はしない

# **指図の語**（設計 v2 §1 規約 3）。答えは事実の形で書く。
指図の語 = ("すべき", "べきです", "しましょう", "してください", "おすすめ",
            "推奨", "した方がいい", "したほうがいい", "回しましょう")


def test_d_差があるときだけ1個で指図の語を含まない(account):
    """朝（30 分）と深夜（180 分）で `first_reply_min` が 6 倍違う。

    枝と views は両帯で同じにしてあるので、**差が出るのは 1 指標だけ**。
    """
    write_many(account, 6, prefix="M", hour=8, first_reply_min=30,
               branches=3, views=300)
    write_many(account, 6, prefix="N", hour=23, first_reply_min=180,
               branches=3, views=300)

    answer = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, min_n=6, now=NOW)

    one = answer["one_thing_to_change"]
    # **1 個**（文字列 1 本。並べた表ではない）。
    assert isinstance(one, str) and one
    assert "深夜" in one and "朝" in one
    assert "180" in one and "30" in one
    # **件数つき**（規約 1）。
    assert "n=6" in one
    for 語 in 指図の語:
        assert 語 not in one, f"指図の語が混ざっている: {語} / {one}"


def test_d2_差が無ければnull(account):
    """帯が 2 つあっても中央値が同じなら `null`。**無理に 1 個ひねり出さない。**"""
    write_many(account, 6, prefix="M", hour=8, first_reply_min=30)
    write_many(account, 6, prefix="N", hour=23, first_reply_min=30)
    answer = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, min_n=6, now=NOW)
    assert answer["one_thing_to_change"] is None


def test_d3_比べられる帯が1つならnull(account):
    """帯が 1 つしか無ければ差は言えない（規約 3「2 つ以上」）。"""
    write_many(account, 12, prefix="M", hour=8, first_reply_min=30)
    answer = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, min_n=6, now=NOW)
    assert answer["one_thing_to_change"] is None


def test_d4_片方がminn未満ならnull(account):
    """一方の帯が閾値に満たなければ、差があっても言わない（規約 2 が先）。"""
    write_many(account, 12, prefix="M", hour=8, first_reply_min=30)
    write_many(account, 3, prefix="N", hour=23, first_reply_min=180)
    answer = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, min_n=6, now=NOW)
    assert answer["one_thing_to_change"] is None


def test_d5_要約にも指図の語は出ない(account):
    write_many(account, 25)
    answer = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, now=NOW)
    for 語 in 指図の語:
        assert 語 not in answer["summary"]


# ------------------------------------------------ (e) 本文・username は出ない

def test_e_本文もusernameも答えのどこにも出ない(account):
    """設計 v2 §1 規約 4。**JSON 全体を文字列にして grep する。**

    鍵の名前でなく**中身**を見る——新しい欄が足された日に、名前だけ見る
    テストは黙って通ってしまう。
    """
    write_many(account, 25)
    topics_mod.record(TOPIC, verdict="alive", audience="コーヒー好きのやりとり",
                      by=SECRET_USER, account=ACCOUNT, kind="一般名詞", now=NOW)

    answer = ask_mod.before_you_post(ACCOUNT, topic=TOPIC, now=NOW)
    text = json.dumps(answer, ensure_ascii=False)

    assert SECRET_BODY not in text
    assert SECRET_USER not in text
    # 観測は出る（自由文・人数・日付だけ）。
    assert answer["audience"] == [{"topic": TOPIC, "who": "コーヒー好きのやりとり",
                                   "observers": 1, "latest": "2026-09-09"}]
    assert set(answer["audience"][0]) == {"topic", "who", "observers", "latest"}
    assert answer["provenance"]["observers"] == 1


def test_e2_CLIの人向け出力にも出ない(account, thth_root):
    write_many(account, 25)
    topics_mod.record(TOPIC, verdict="alive", audience="コーヒー好きのやりとり",
                      by=SECRET_USER, account=ACCOUNT, now=NOW)
    proc = run_ask(["ask", "before-you-post", ACCOUNT, "--topic", TOPIC])
    assert proc.returncode == 0, proc.stderr
    assert SECRET_BODY not in proc.stdout and SECRET_USER not in proc.stdout


def test_e3_本文は問いにも入らない():
    """**原稿本文を渡す口が無い**（設計 v2 §1「原稿本文は渡さない」）。

    引数に body / text / draft が生えたら落ちる。
    """
    import inspect
    params = set(inspect.signature(ask_mod.before_you_post).parameters)
    assert params == {"account", "medium", "topic", "kind", "hour_band",
                      "is_reply", "window_days", "min_n", "now"}


# ---------------------------------------------------------------- (f) CLI

def run_ask(args, env=None) -> subprocess.CompletedProcess:
    full = dict(os.environ)
    full.update(env or {})
    return subprocess.run([sys.executable, BIN_THTH, *args],
                          capture_output=True, text=True, env=full)


def test_f_CLIの人向けは表とcannot_sayと1行を出す(account, thth_root):
    write_many(account, 25, branches=3, first_reply_min=42, views=310)
    proc = run_ask(["ask", "before-you-post", ACCOUNT, "--topic", TOPIC])
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "before_you_post" in out
    # 要約 1 行・表・言えないこと・1 つだけ・出所 1 行。
    assert "24h の枝" in out and "最初の枝まで" in out and "views（24h）" in out
    assert "言えないこと:" in out
    assert "1 つだけ挙げるなら: 差が言える群がありません" in out
    assert "出所 local" in out and "schema 1" in out
    assert "直近 30 日" in out and "age_hours=24" in out


def test_f2_CLIのJSONは契約の形そのまま(account, thth_root):
    write_many(account, 25)
    proc = run_ask(["ask", "before-you-post", ACCOUNT, "--topic", TOPIC, "--json"])
    assert proc.returncode == 0, proc.stderr
    answer = json.loads(proc.stdout)
    assert set(answer) == {"summary", "expected", "comparable", "cannot_say",
                           "one_thing_to_change", "audience", "provenance"}
    assert set(answer["expected"]) == {"branches_24h", "first_reply_min",
                                       "views_24h"}
    assert set(answer["comparable"]) == {"n", "window_days", "aligned_on",
                                          "medium"}
    assert set(answer["provenance"]) == {"source", "observers", "updated",
                                          "schema"}
    assert answer["provenance"]["source"] == "local"


def test_f3_CLIの引数が効く(account, thth_root):
    write_many(account, 25, hour=8)
    # 時刻帯で絞れば群が変わる。
    朝 = json.loads(run_ask(["ask", "before-you-post", ACCOUNT, "--topic", TOPIC,
                             "--hour-band", "朝", "--json"]).stdout)
    深夜 = json.loads(run_ask(["ask", "before-you-post", ACCOUNT, "--topic", TOPIC,
                               "--hour-band", "深夜", "--json"]).stdout)
    assert 朝["comparable"]["n"] == 25 and 深夜["comparable"]["n"] == 0
    # 期間・閾値も効く。
    短い = json.loads(run_ask(["ask", "before-you-post", ACCOUNT, "--topic", TOPIC,
                               "--window-days", "1", "--json"]).stdout)
    assert 短い["comparable"]["window_days"] == 1
    assert 短い["comparable"]["n"] < 25


def test_f4_問いが受け取れなければrc2で一覧を添えて断る(account, thth_root):
    未知の型 = run_ask(["ask", "before-you-post", ACCOUNT, "--topic", TOPIC,
                        "--kind", "そんな型は無い"])
    assert 未知の型.returncode == 2
    assert "知らない型" in 未知の型.stderr and "行動" in 未知の型.stderr

    未知の帯 = run_ask(["ask", "before-you-post", ACCOUNT, "--topic", TOPIC,
                        "--hour-band", "未明"])
    assert 未知の帯.returncode == 2 and "知らない時刻帯" in 未知の帯.stderr

    空の語 = run_ask(["ask", "before-you-post", ACCOUNT, "--topic", "   "])
    assert 空の語.returncode == 2 and "語" in 空の語.stderr


def test_f5_台帳が無ければrc1(account, thth_root):
    proc = run_ask(["ask", "before-you-post", "そんなaccountは無い",
                    "--topic", TOPIC])
    assert proc.returncode == 1
    assert proc.stdout == ""


def _tree_fingerprint(root: str) -> dict:
    """`root` の下**全部**の (相対パス → 大きさ・更新時刻・中身の sha256)。

    **なぜ `os.listdir` では足りないか**（監査 2・2026-09-13）: 前はここが
    `os.listdir(state)` のトップ階層の**名前だけ**だった。`state/share/outbox/
    2026-09.ndjson` に 1 行積んでも、`state/topics.json` を書き換えても、
    `state/share/observer_id` を新しく作っても——**名前の一覧は変わらないので
    緑のまま**。「読むだけ」を名前だけで見張ると、中身の書き換えを取り逃がす。

    ディレクトリも数える（空のディレクトリが増えるのも書き込み——`$THTH_ROOT/
    accounts` が出来た瞬間に台帳の解決順が変わる、という形で実害が出る）。
    """
    out: dict = {}
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(dirnames):
            p = os.path.join(dirpath, name)
            out[os.path.relpath(p, root) + "/"] = "dir"
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            st = os.lstat(p)
            if os.path.islink(p):
                out[rel] = ("link", os.readlink(p))
                continue
            with open(p, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            out[rel] = (st.st_size, st.st_mtime_ns, digest)
    return out


def test_f6_読むだけで何も書かない(account, thth_root):
    """**読むだけの口**。呼んでも repo にも state にも書き込みが起きない。

    見るのは `$THTH_ROOT` の**ツリー全体**と原稿 repo の**ツリー全体**——
    パス・大きさ・更新時刻・中身の sha256 を前後で丸ごと比べる。`.git/` は
    除く（`git status` を挟むと index の `mtime` が動くため。代わりに
    `git status --porcelain` と `rev-parse HEAD` を見る）。
    """
    write_many(account, 25)
    repo = account["repo_dir"]

    def 原稿repo():
        return {k: v for k, v in _tree_fingerprint(repo).items()
                if not k.startswith(".git/") and k != ".git/"}

    def gitの状態():
        return (subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                               capture_output=True, text=True).stdout,
                subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                               capture_output=True, text=True).stdout)

    台帳の置き場 = account["accounts_dir"]
    root_before = _tree_fingerprint(thth_root)
    台帳_before = _tree_fingerprint(台帳の置き場)
    repo_before = 原稿repo()
    git_before = gitの状態()
    # **`$THTH_ROOT` はこの時点で空**（`state/` はまだ 1 つも無い）。空のまま
    # であることこそ見たいものなので、前提は「在ること」だけ確かめる。
    assert os.path.isdir(thth_root), "この試験の前提が崩れている（$THTH_ROOT が無い）"
    assert 台帳_before, "この試験の前提が崩れている（台帳が 1 本も無い）"
    assert repo_before, "この試験の前提が崩れている（原稿 repo が空）"

    assert run_ask(["ask", "before-you-post", ACCOUNT,
                    "--topic", TOPIC]).returncode == 0

    root_after = _tree_fingerprint(thth_root)
    repo_after = 原稿repo()

    # 差分は**名指しで**出す（「違う」だけでは、何を書いたのか分からない）。
    def 差分(before: dict, after: dict) -> list:
        return sorted(
            [f"増えた: {k}" for k in after if k not in before]
            + [f"消えた: {k}" for k in before if k not in after]
            + [f"変わった: {k}" for k in before if k in after and before[k] != after[k]])

    assert 差分(root_before, root_after) == [], \
        "`ask` が $THTH_ROOT に書き込んだ:\n" + "\n".join(差分(root_before, root_after))
    台帳_after = _tree_fingerprint(台帳の置き場)
    assert 差分(台帳_before, 台帳_after) == [], \
        "`ask` が台帳を書き換えた:\n" + "\n".join(差分(台帳_before, 台帳_after))
    assert 差分(repo_before, repo_after) == [], \
        "`ask` が原稿 repo に書き込んだ:\n" + "\n".join(差分(repo_before, repo_after))
    assert gitの状態() == git_before


# ---------------------------------------------------------------- (g) MCP

# **説明文は設計 v2 §1 規約 6 の正本そのまま。** LLM は説明文で呼ぶかを決める
# ので、ここを黙って書き換えられないようにテストで固定する。
MCP_DESCRIPTION = ("投稿する前に呼ぶ。この語とこの型で、スレッドがどう伸びたかの"
                   "実績を、件数と期間つきで返す")


def test_g_MCPのtool一覧に固定の説明文で載っている():
    sys.path.insert(0, os.path.join(REPO_ROOT, "mcp"))
    try:
        import server as mcp_server  # noqa: PLC0415
    finally:
        sys.path.pop(0)

    tools = {t["name"]: t for t in mcp_server.TOOLS}
    assert "before_you_post" in tools
    tool = tools["before_you_post"]
    assert tool["description"] == MCP_DESCRIPTION
    # 引数は CLI と同じ（`account` と `topic` だけ必須）。**本文は受け取らない。**
    props = tool["inputSchema"]["properties"]
    assert set(props) == {"account", "topic", "kind", "hour_band", "is_reply",
                          "window_days", "min_n"}
    assert tool["inputSchema"]["required"] == ["account", "topic"]


def test_g2_MCP越しに呼べてcannot_sayだらけでもisErrorにならない(account, thth_root):
    """**「言えない」は正常な答え**（設計 v2 §1 規約 2）。error ではない。"""
    sys.path.insert(0, os.path.join(REPO_ROOT, "mcp"))
    try:
        import server as mcp_server  # noqa: PLC0415
    finally:
        sys.path.pop(0)

    write_many(account, 3)   # 閾値に満たない
    result = mcp_server.call_tool("before_you_post",
                                  {"account": ACCOUNT, "topic": TOPIC})
    assert not result.get("isError"), result
    answer = json.loads(result["content"][0]["text"])
    assert answer["expected"]["branches_24h"]["median"] is None
    assert answer["cannot_say"]
    assert SECRET_BODY not in result["content"][0]["text"]
    assert SECRET_USER not in result["content"][0]["text"]


# ------------------------------------------------------------ 本物の台帳

def _real_root(tmp_path) -> str | None:
    """本物の利用者 repo（`~/Developer/kopicha`）を指す `THTH_ROOT` を組む。

    **読むだけ**（symlink を張るだけで、利用者 repo には一切書かない）。
    手元にその repo が無い機械（CI・他人の clone）では `None`。
    """
    real = os.path.expanduser("~/Developer/kopicha")
    if not os.path.isdir(os.path.join(real, "data", "sns", "insights", "posts")):
        return None
    root = tmp_path / "real_root"
    (root / "repos").mkdir(parents=True)
    os.symlink(real, str(root / "repos" / "kopicha"))
    return str(root)


def test_本物の台帳でrc0でcannot_sayが出る(tmp_path):
    """**本物で落ちないこと**を見る（手元に kopicha が無ければ skip）。

    手元の水では、ほとんど全部 `cannot_say` になる——**それが正直な答え。**
    """
    root = _real_root(tmp_path)
    if root is None:
        pytest.skip("本物の利用者 repo（~/Developer/kopicha）がありません")
    proc = run_ask(["ask", "before-you-post", "kopicha-threads",
                    "--topic", "コーヒー", "--json"], env={"THTH_ROOT": root})
    assert proc.returncode == 0, proc.stderr
    answer = json.loads(proc.stdout)
    assert answer["provenance"]["source"] == "local"
    assert answer["cannot_say"], "手元の水で言い切ってしまっている"
    assert answer["expected"]["branches_24h"]["median"] is None
