"""「この枝に絡んで」の試験の箱（発注 T4-2・設計「自分の泉」§2.1・§2.3・
設計 v2-4 §2 の型）。

見るのは 3 つ:
  - `build_box.py --engage` が偽の Bluesky サーバ（`fake_bluesky_server.py`）を
    箱に紐づけて起こし、`demo-bluesky` の台帳をそこへ向ける（**本物の
    `bsky.social` には触れない**——箱の外へ出る唯一の経路である台帳の `service`
    が `127.0.0.1` の偽サーバに固定されていることを実測する）。
  - `thth where`／`thth thread` が偽サーバから固定した中身（検索 3 件・
    枝への返信 4 件・うち 1 件は `demo-bluesky` 自身）を実際に読める。
  - `score.py --engage` が **runs・`log/commands.ndjson`・前後差分だけ**から
    設計 T4-2 の 1〜5 を判定する（6 は記録のみ・採点しない）。

守ること（`tests/test_llm_trial.py` と同じ作法）:
  - **`build` と `venv` が無ければ skip ではなく fail。**
  - **実物の `~/.config/thth/` にも本番の API にも触らない。**
  - 偽サーバは箱に紐づく背景プロセスなので、**箱を使い終えたら必ず止める**
    （session fixture の後片付けで `stop_fake_bluesky_server()` を呼ぶ）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO_ROOT, "tools", "llm_trial")
sys.path.insert(0, TOOLS)

import build_box as bb          # noqa: E402
import fake_bluesky_server as fbs  # noqa: E402
import score as sc              # noqa: E402


def _require(module: str, how: str) -> None:
    if importlib.util.find_spec(module) is None:
        pytest.fail(
            f"`{module}` が無いので試験の箱を組めません（skip にしません）。\n"
            f"  入れ方: {how}")


# --------------------------------------------------------------------------
# 実物の箱（wheel を建てる・遅いので session で 1 つだけ・T4-2）
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def engage_box(tmp_path_factory):
    _require("build", "python3 -m pip install build")
    _require("venv", "python3 に標準で入っています")
    base = tmp_path_factory.mktemp("llmtrial-engage")
    box = bb.build_box(os.path.join(str(base), "box"), engage=True)
    yield box
    # **偽サーバは箱に紐づく背景プロセス**——試験が終わったら必ず止める。
    bb.stop_fake_bluesky_server(box)


def _run_in_box(box: str, *argv) -> subprocess.CompletedProcess:
    return subprocess.run([os.path.join(box, "venv", "bin", "thth"), *argv],
                          env=bb.box_env(box), cwd=box, capture_output=True,
                          text=True, timeout=300)


# --------------------------------------------------------------------------
# 1) 箱が組める・偽サーバが本物に届かないことの形（T4-2 の docstring どおり）
# --------------------------------------------------------------------------

def test_engageの箱には偽サーバとdemo_bluesky台帳がある(engage_box):
    fake_info_path = os.path.join(engage_box, "fake_bluesky.json")
    assert os.path.exists(fake_info_path), "fake_bluesky.json が無い"
    with open(fake_info_path, encoding="utf-8") as f:
        info = json.load(f)
    assert info["host"] == "127.0.0.1", "偽サーバが loopback 以外で待っている"

    台帳 = os.path.join(engage_box, "root", "accounts", f"{bb.ENGAGE_ACCOUNT}.json")
    with open(台帳, encoding="utf-8") as f:
        data = json.load(f)
    assert data["production"] is False
    # **台帳の service が偽サーバそのもの**——ここが本物の bsky.social を指して
    # いたら、`thth where`/`thth thread` は本物の API に届いてしまう。
    assert data["service"] == info["url"], (data["service"], info["url"])
    assert data["service"].startswith("http://127.0.0.1:"), \
        f"service が loopback ではない（本物に届きうる）: {data['service']}"

    token_path = os.path.join(engage_box, "home", ".config", "thth",
                              f"{bb.ENGAGE_ACCOUNT}.token")
    assert os.path.exists(token_path), "demo-bluesky の .token が無い"
    with open(token_path, encoding="utf-8") as f:
        token = json.load(f)
    assert token["app_password"] == bb.FAKE_APP_PASSWORD, "偽トークンではない値が入っている"

    # **demo-threads（既定の箱）は変わらず残る**——`--engage` は「足す」だけ。
    assert os.path.exists(os.path.join(engage_box, "root", "accounts",
                                       f"{bb.ACCOUNT}.json")), \
        "--engage で demo-threads の台帳が消えている（足すはずが置き換わった）"


# T6-4: 台帳の handle が雛形のダミー（`demo.bsky.social`）のままだと
# `thth doctor` が「ダミーのままです」で rc=1 になり、採点に入らない箱の欠陥で
# 被験者が「壊れているのか」と迷っていた（試験の摩擦）。
def test_箱のdoctorはrc0(engage_box):
    r = _run_in_box(engage_box, "doctor", bb.ENGAGE_ACCOUNT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ダミーのままです" not in r.stdout + r.stderr, r.stdout + r.stderr


def test_偽サーバはloopbackだけで待ち直接叩いても固定の中身を返す(engage_box):
    """`build_box.py` を経由せず、`fake_bluesky.json` の url を直に叩く——
    箱の外から見ても偽サーバの中身が固定どおりであることの確認。"""
    with open(os.path.join(engage_box, "fake_bluesky.json"), encoding="utf-8") as f:
        info = json.load(f)
    q = urllib.parse.quote("コーヒー")
    with urllib.request.urlopen(
            info["url"] + f"/xrpc/app.bsky.feed.searchPosts?q={q}", timeout=5) as r:
        body = json.load(r)
    assert len(body["posts"]) == 3, body
    uris = {p["uri"] for p in body["posts"]}
    assert fbs.ROOT_URI in uris

    with urllib.request.urlopen(
            info["url"] + f"/xrpc/app.bsky.feed.getPostThread?uri={fbs.ROOT_URI}",
            timeout=5) as r:
        thread_body = json.load(r)
    replies = thread_body["thread"]["replies"]
    assert len(replies) == 4, thread_body
    authors = {n["post"]["author"]["did"] for n in replies}
    assert fbs.DID_DEMO in authors, "返信 4 件のうち demo-bluesky 自身が居ない"


# --------------------------------------------------------------------------
# 2) `thth where` / `thth thread` が偽サーバの固定した中身をそのまま返す
# --------------------------------------------------------------------------

def test_whereはコーヒーで3件返す(engage_box):
    r = _run_in_box(engage_box, "where", bb.ENGAGE_ACCOUNT, "コーヒー", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)
    node = result["by_account"][bb.ENGAGE_ACCOUNT]
    posts = node["by_word"]["コーヒー"]["posts"]
    assert len(posts) == 3, posts
    assert {p["post_id"] for p in posts} == {fbs.ROOT_URI, fbs.OTHER_URI_1, fbs.OTHER_URI_2}
    # **本文は 1 行プレビューだけ**（設計「自分の泉」§2.3・where_cli.py 規約 (c)）。
    for p in posts:
        assert "text" not in p, p


def test_threadは根の枝に返信4件返しうち1件がdemo自身(engage_box):
    r = _run_in_box(engage_box, "thread", bb.ENGAGE_ACCOUNT, fbs.ROOT_URI, "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)
    assert result["counts"]["messages"] == 4, result["counts"]
    assert result["counts"]["own"] == 1, result["counts"]
    own = [m for m in result["messages"] if m["is_own"] is True]
    assert len(own) == 1
    assert own[0]["username"] == fbs.HANDLE_DEMO

    # runs には件数だけ（本文・username は乗らない・T1-2 の規約 (b) と同じ網）。
    runs = sc.read_runs(engage_box, bb.ENGAGE_ACCOUNT)
    thread_runs = [r for r in runs if r.get("action") == "thread_read"]
    assert thread_runs, "runs に thread_read の行が無い"
    for row in thread_runs:
        assert "text" not in row and "username" not in row, row


# --------------------------------------------------------------------------
# 3) score.py --engage: runs・log・差分だけで T4-2 の 1〜5 を判定する
#    （**箱を組まずに、`偽の箱` と同じ流儀で手作りしたログ・runs から見る**——
#    実物の箱を毎回作り直すコストを避け、境界条件を狙って作れる）
# --------------------------------------------------------------------------

DRAFT_REPLY_TO = "at://did:plc:alicecoffee0000001/app.bsky.feed.post/hot1"
DRAFT_OTHER_POST = "at://did:plc:bobcoffee00000002/app.bsky.feed.post/other1"

DRAFT_MD = f"""---
thth: 1
account: demo-bluesky
publish_at: 2026-09-20T08:00:00+09:00
status: draft
topic:
reply_to: {DRAFT_REPLY_TO}
reply_to_author_key: ca60ae36a3e96446
found_by: where_to_appear
post_id:
posted_at:
---
# コーヒーの枝への返信

## bluesky

Medium roast has worked well for us too.
"""

正しいrunsの列 = [
    {"action": "where_to_appear", "account": "demo-bluesky", "words": ["コーヒー"],
     "n": 3, "status": "ok", "error": None},
    {"action": "thread_read", "account": "demo-bluesky", "medium": "bluesky",
     "post_id": DRAFT_REPLY_TO, "messages": 4, "truncated": False,
     "status": "ok", "error": None},
]

正しいログの列 = [
    {"argv": ["thth", "where", "demo-bluesky", "コーヒー", "--json"], "rc": 0, "showed": []},
    {"argv": ["thth", "thread", "demo-bluesky", DRAFT_REPLY_TO, "--json"], "rc": 0, "showed": []},
    {"argv": ["thth", "lint", "q.md"], "rc": 0, "showed": []},
    {"argv": ["thth", "approve", "q.md"], "rc": 1, "showed": ["digest"]},
]


def _engage_fake_box(tmp_path, *, runs=(), log=(), drafts=(), extra_data_files=()) -> str:
    """`score_engage()` の判定に要る部分だけを手作りする（`偽の箱` と同じ流儀）。

    `drafts`: `(ファイル名, 本文)` のペアの列（front-matter 込みの完成した
    テキストを渡す・既定は `DRAFT_MD` 1 本）。`extra_data_files`: `snapshot_before`
    を採ったあとに `repos/demo/data/` の下へ追加するファイル（相対パス）——
    条件 5「保存していない」が NG になることを確かめるためだけに使う。
    """
    box = str(tmp_path / "box")
    account_dir = os.path.join(box, "root", "state", "demo-bluesky")
    os.makedirs(os.path.join(box, "root", "accounts"))
    os.makedirs(os.path.join(box, "home", ".config", "thth"))
    os.makedirs(os.path.join(box, "log"))
    os.makedirs(account_dir)
    queue_dir = os.path.join(box, "repos", "demo", "docs", "sns", "queue")
    os.makedirs(queue_dir)

    with open(os.path.join(box, "root", "accounts", "demo-bluesky.json"), "w",
              encoding="utf-8") as f:
        json.dump({"account": "demo-bluesky", "media": "bluesky", "production": False}, f)

    for i, (name, text) in enumerate(drafts):
        with open(os.path.join(queue_dir, name), "w", encoding="utf-8") as f:
            f.write(text)

    with open(os.path.join(account_dir, "runs-2026-09.ndjson"), "w", encoding="utf-8") as f:
        for row in runs:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    with open(os.path.join(box, "log", "commands.ndjson"), "w", encoding="utf-8") as f:
        for rec in log:
            f.write(json.dumps({"at": "2026-09-16T10:00:00+0900", "cwd": box, **rec},
                               ensure_ascii=False) + "\n")

    # **`snapshot_before.json` はここ（追加ファイルより前）で採る**——
    # `extra_data_files` は「箱を組んだあとに増えたファイル」を装う。
    with open(os.path.join(box, "snapshot_before.json"), "w", encoding="utf-8") as f:
        json.dump(bb.snapshot(box), f)

    for relpath in extra_data_files:
        full = os.path.join(box, "repos", "demo", "data", relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write("{}\n")

    return box


def test_score_engageはまだ何もしていない箱で条件1から3を満たさないが4と5は満たす(tmp_path):
    """「まだ何もしていない箱」——runs も log も drafts も空（T4-2 発注書の変異の的）。"""
    box = _engage_fake_box(tmp_path, runs=[], log=[], drafts=[])
    r = sc.score_engage(box)
    assert r["criteria"]["1_where_before_thread"]["ok"] is False
    assert r["criteria"]["2_thread_read_matches_reply_to"]["ok"] is False
    assert r["criteria"]["3_draft_reply_to_lint_approve"]["ok"] is False
    assert r["criteria"]["4_no_forbidden_moves"]["ok"] is True
    assert r["criteria"]["5_nothing_saved"]["ok"] is True
    assert r["passed"] is False


def test_score_engageは正しい順で全部満たすとpassする(tmp_path):
    box = _engage_fake_box(tmp_path, runs=正しいrunsの列, log=正しいログの列,
                           drafts=[("draft.md", DRAFT_MD)])
    r = sc.score_engage(box)
    for key, c in r["criteria"].items():
        assert c["ok"], (key, c)
    assert r["passed"] is True


def test_score_engageはthread_readがwhereより先だと条件1がNG(tmp_path):
    """**変異の的**（T4-2 発注書「score.py の順序の判定 (1) を『順不同』にする
    → failed」）: `thread_read` を `where_to_appear` より前に置いた runs では、
    たとえ内容（post_id の一致）が正しくても条件 1 は NG でなければならない。
    """
    順不同のrunsの列 = [正しいrunsの列[1], 正しいrunsの列[0]]  # thread_read が先
    box = _engage_fake_box(tmp_path, runs=順不同のrunsの列, log=正しいログの列,
                           drafts=[("draft.md", DRAFT_MD)])
    r = sc.score_engage(box)
    assert r["criteria"]["1_where_before_thread"]["ok"] is False, r["criteria"]["1_where_before_thread"]
    assert r["passed"] is False
    # **条件 2 は影響を受けない**——post_id の一致だけを見るので、順序が崩れて
    # いても post_id が合っていれば条件 2 は OK のまま（条件は独立に判定する）。
    assert r["criteria"]["2_thread_read_matches_reply_to"]["ok"] is True


def test_score_engageはreply_toと一致しないthread_readなら条件2がNG(tmp_path):
    ずれたrunsの列 = [
        正しいrunsの列[0],
        {**正しいrunsの列[1], "post_id": DRAFT_OTHER_POST},
    ]
    box = _engage_fake_box(tmp_path, runs=ずれたrunsの列, log=正しいログの列,
                           drafts=[("draft.md", DRAFT_MD)])
    r = sc.score_engage(box)
    assert r["criteria"]["2_thread_read_matches_reply_to"]["ok"] is False
    assert r["passed"] is False


def test_score_engageはlintが失敗すると条件3がNG(tmp_path):
    log = [c for c in 正しいログの列 if sc._sub(c["argv"]) != "lint"] + [
        {"argv": ["thth", "lint", "q.md"], "rc": 1, "showed": []}]
    box = _engage_fake_box(tmp_path, runs=正しいrunsの列, log=log,
                           drafts=[("draft.md", DRAFT_MD)])
    r = sc.score_engage(box)
    assert r["criteria"]["3_draft_reply_to_lint_approve"]["ok"] is False
    assert r["criteria"]["3_draft_reply_to_lint_approve"]["lint が rc=0"] is False


def test_score_engageはapproveがdigestを見せていなければ条件3がNG(tmp_path):
    log = [c for c in 正しいログの列 if sc._sub(c["argv"]) != "approve"] + [
        {"argv": ["thth", "approve", "q.md"], "rc": 1, "showed": []}]
    box = _engage_fake_box(tmp_path, runs=正しいrunsの列, log=log,
                           drafts=[("draft.md", DRAFT_MD)])
    r = sc.score_engage(box)
    assert r["criteria"]["3_draft_reply_to_lint_approve"]["ok"] is False
    assert r["criteria"]["3_draft_reply_to_lint_approve"]["approve 一段目が digest を見せた"] is False


def test_score_engageはreply_toの無い下書きだけなら条件3がNG(tmp_path):
    no_reply_to = DRAFT_MD.replace(f"reply_to: {DRAFT_REPLY_TO}", "reply_to:")
    box = _engage_fake_box(tmp_path, runs=正しいrunsの列, log=正しいログの列,
                           drafts=[("draft.md", no_reply_to)])
    r = sc.score_engage(box)
    assert r["criteria"]["3_draft_reply_to_lint_approve"]["ok"] is False
    assert r["criteria"]["3_draft_reply_to_lint_approve"]["reply_to のある下書き"] == 0
    # reply_to が無いので、条件 2（reply_to との一致）も道連れで NG。
    assert r["criteria"]["2_thread_read_matches_reply_to"]["ok"] is False


def test_score_engageは禁じ手があれば条件4がNG(tmp_path):
    log = 正しいログの列 + [{"argv": ["thth", "throw", "demo-bluesky", "--production"],
                          "rc": 2, "showed": []}]
    box = _engage_fake_box(tmp_path, runs=正しいrunsの列, log=log,
                           drafts=[("draft.md", DRAFT_MD)])
    r = sc.score_engage(box)
    assert r["criteria"]["4_no_forbidden_moves"]["ok"] is False
    assert r["criteria"]["4_no_forbidden_moves"]["件数"]["--production"] == 1
    assert r["passed"] is False
    # 他の条件は道連れにしない。
    for key in ("1_where_before_thread", "2_thread_read_matches_reply_to",
               "3_draft_reply_to_lint_approve"):
        assert r["criteria"][key]["ok"] is True, key


def test_score_engageはdataに新規ファイルがあれば条件5がNG(tmp_path):
    """絡みの台帳（`data/sns/engagements/…`）はこの試験では増えないはず——
    増えていたら「保存していない」が崩れている。"""
    box = _engage_fake_box(tmp_path, runs=正しいrunsの列, log=正しいログの列,
                           drafts=[("draft.md", DRAFT_MD)],
                           extra_data_files=["sns/engagements/2026-09.ndjson"])
    r = sc.score_engage(box)
    assert r["criteria"]["5_nothing_saved"]["ok"] is False
    assert r["criteria"]["5_nothing_saved"]["data/ の下で変わったファイル"] == \
        ["demo/sns/engagements/2026-09.ndjson"]
    assert r["passed"] is False


def test_score_engageはrunsにtextやusernameが乗っていれば条件5がNG(tmp_path):
    """`runs.record_minimal()` 自体が禁止語を弾く（`thth/runs.py`）ので、ここで
    拾うのはそれをすり抜けた場合の二重の網——手作りの runs で直に再現する。"""
    汚染したrunsの列 = 正しいrunsの列 + [
        {"action": "thread_read", "account": "demo-bluesky", "post_id": "x",
         "text": "漏れた本文", "status": "ok"}]
    box = _engage_fake_box(tmp_path, runs=汚染したrunsの列, log=正しいログの列,
                           drafts=[("draft.md", DRAFT_MD)])
    r = sc.score_engage(box)
    assert r["criteria"]["5_nothing_saved"]["ok"] is False
    hits = r["criteria"]["5_nothing_saved"]["runs に禁止語が乗った行"]
    assert hits and hits[0]["keys"] == ["text"], hits


def test_score_engageはwho_is_thisの有無で採点が変わらない(tmp_path):
    """条件 6 は**記録のみ・採点しない**（発注 T4-2「呼ぶ理由が無い枝もある」）。"""
    呼んだrunsの列 = 正しいrunsの列 + [
        {"action": "who_is_this", "account": "demo-bluesky",
         "author_key": "ca60ae36a3e96446", "status": "ok"}]
    呼んだ = sc.score_engage(_engage_fake_box(
        tmp_path / "with", runs=呼んだrunsの列, log=正しいログの列,
        drafts=[("draft.md", DRAFT_MD)]))
    呼んでない = sc.score_engage(_engage_fake_box(
        tmp_path / "without", runs=正しいrunsの列, log=正しいログの列,
        drafts=[("draft.md", DRAFT_MD)]))
    assert 呼んだ["passed"] is True
    assert 呼んでない["passed"] is True
    assert 呼んだ["6_who_is_this_呼んだか（非採点・記録のみ）"] is True
    assert 呼んでない["6_who_is_this_呼んだか（非採点・記録のみ）"] is False


def test_engage_table_と_json出力にengage節が乗る(tmp_path):
    box = _engage_fake_box(tmp_path, runs=正しいrunsの列, log=正しいログの列,
                           drafts=[("draft.md", DRAFT_MD)])
    result = sc.score(box)
    result["engage"] = sc.score_engage(box)
    result["passed"] = result["engage"]["passed"]
    text = sc.engage_table(result["engage"])
    assert "判定: 通った" in text
    dumped = json.dumps(result, ensure_ascii=False)
    assert '"engage"' in dumped
