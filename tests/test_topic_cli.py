"""工程 6（P3）の受け入れ: T15〜T18・T27・T28。

設計 §6（CLI 互換性・JSON と終了コード）・§7（MCP）。

**新しい入口を足して、既存の呼び方を壊さないこと。** それと、
**提案は原稿にも Git にも触らないこと**——ここが本体との境界。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tests.conftest import run_thth, write_queue_file
from tests.test_topic_advice import (ARTICLE_TEXT, candidate, ids,
                                      make_article, make_observation)
from thth import topic_cli

BODY = ("## threads\n\n"
        "同じ豆でも精製で香りが変わる話。https://example.test/coffee\n")


def _queue(account, body=BODY, name="t.md"):
    return write_queue_file(account["queue_dir"], name, body=body,
                             fm_overrides={"status": "draft"})


def _suggest(path, payload=None, extra=None):
    args = ["topics", "suggest", path]
    if payload is not None:
        args.append("--input-json-stdin")
    args += extra or []
    proc = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "bin", "thth"), *args],
        capture_output=True, text=True, env=dict(os.environ),
        input=None if payload is None else json.dumps(payload, ensure_ascii=False))
    return proc


# --- T15 既存 CLI が誤 dispatch されない -----------------------------------

@pytest.mark.parametrize("argv,new_style", [
    (["topics"], False),
    (["topics", "nigamilab"], False),
    (["topics", "nigamilab", "--advise"], False),
    (["topics", "--advise"], False),
    (["topics", "--note", "コーヒー", "--verdict", "alive"], False),
    (["topics", "--account", "kopicha", "--learned"], False),
    (["topics", "suggest", "a.md"], True),
    (["topics", "observe", "--input", "o.json"], True),
    (["topics", "record-decision"], True),
    (["topics", "decision", "sha256:x"], True),
    (["topics", "profile", "kopicha"], True),
    (["queue", "suggest"], False),           # 別コマンドの引数は触らない
    (["posts", "kopicha", "--limit", "3"], False),
])
def test_T15_入口の振り分けは直後の語だけで決まる(argv, new_style):
    assert topic_cli.is_new_style(argv) is new_style


def test_T15_既存の呼び方がそのまま動く(isolated_account):
    """**既存の parser に届いていること**を見る。

    `--account <name>` だけの形は token が無ければ exit 1 で終わる——それは
    前からの振る舞いで、振り分けとは関係ない。ここで落としたいのは
    「新しい parser に吸われて `invalid choice` になる」形。
    """
    for args in (["topics", isolated_account["name"], "--advise"],
                  ["topics", isolated_account["name"], "--learned"]):
        proc = run_thth(args)
        assert proc.returncode == 0, f"{args}: {proc.stderr}"
    proc = run_thth(["topics", "--account", isolated_account["name"]])
    assert "invalid choice" not in proc.stderr
    assert "unrecognized arguments" not in proc.stderr
    assert "token が無いので引けません" in proc.stderr, proc.stderr


def test_T15_同名のaccountがあれば黙って隠さず導入エラー(isolated_account_factory,
                                                          monkeypatch, tmp_path):
    """`suggest` という account があるなら `thth topics suggest` は曖昧。"""
    isolated_account_factory("suggest")
    proc = run_thth(["topics", "suggest", str(tmp_path / "x.md")])
    assert proc.returncode == 2, proc.stdout
    out = json.loads(proc.stdout)
    assert out["error"]["code"] == "subcommand_collides_with_account"


# --- T17 提案は何も書き換えない ---------------------------------------------

def _repo_state(repo_dir):
    def git(*a):
        return subprocess.run(["git", "-C", repo_dir, *a],
                               capture_output=True, text=True).stdout
    return (git("rev-parse", "HEAD"), git("status", "--porcelain"),
            git("diff", "--cached", "--name-only"))


def test_T17_suggestは原稿もGitもinflightもsentも変えない(isolated_account, thth_root):
    path = _queue(isolated_account)
    before_bytes = open(path, "rb").read()
    before_repo = _repo_state(isolated_account["repo_dir"])
    state_before = sorted(os.listdir(os.path.join(thth_root, "state"))) \
        if os.path.isdir(os.path.join(thth_root, "state")) else []

    article = make_article()
    proc = _suggest(path, {"article": article})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["status"] == "needs_proposal", out

    proposal = {
        "context_id": out["context_id"], "prompt_version": "t",
        "intended_reader": "コーヒーを淹れる人", "article_value": "精製の違い",
        "post_angle": "同じ豆でも変わる",
        "candidates": [candidate("コーヒー", [])],
        "selected_topic": "コーヒー", "selection_reason": "記事の主題",
    }
    proc = _suggest(path, {"article": article, "proposal": proposal})
    assert proc.returncode == 0, proc.stderr + proc.stdout

    assert open(path, "rb").read() == before_bytes, "原稿を書き換えた"
    assert _repo_state(isolated_account["repo_dir"]) == before_repo, "Git を動かした"
    after = sorted(os.listdir(os.path.join(thth_root, "state"))) \
        if os.path.isdir(os.path.join(thth_root, "state")) else []
    assert [d for d in after if d not in state_before] in ([], ["topic_advice"]), \
        f"提案が state に余計なものを作った: {after}"


# --- T16 MCP は委譲するだけ -------------------------------------------------

def _load_mcp():
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "thth_mcp_server", os.path.join(root, "mcp", "server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_T16_MCPは偽物のCLIの出力をそのまま返す(monkeypatch):
    """**MCP に判断を持たせない**（設計 §3.7・§7）。中身を作り変えないこと。"""
    mcp = _load_mcp()
    seen = {}

    class Fake:
        returncode = 0
        stdout = '{"status": "provisional", "selected_topic": "コーヒー"}'
        stderr = ""

    def fake_run(args, *, stdin_text=None):
        seen["args"] = args
        seen["stdin"] = stdin_text
        return Fake()

    monkeypatch.setattr(mcp, "run_cli", fake_run)
    result = mcp.call_tool("thth_topic_evaluate", {
        "file": "a.md", "article": {"x": 1}, "proposal": {"y": 2}})
    assert result["content"][0]["text"] == Fake.stdout, "MCP が中身を作り変えた"
    assert result["isError"] is False
    assert seen["args"][:3] == ["topics", "suggest", "a.md"]
    assert "--input-json-stdin" in seen["args"]
    # **任意のサーバファイルへ書いて受け渡していない**（設計 §7）。
    assert json.loads(seen["stdin"]) == {"article": {"x": 1}, "proposal": {"y": 2}}


def test_T16_新しい道具はexit1もisErrorにする(monkeypatch):
    """lint の exit 1（検査結果）とは意味が違う。既存の扱いは変えない。"""
    mcp = _load_mcp()

    class Fake:
        returncode = 1
        stdout = '{"ok": false, "error": {"code": "stale_context"}}'
        stderr = ""

    monkeypatch.setattr(mcp, "run_cli", lambda *a, **k: Fake())
    assert mcp.call_tool("thth_topic_context", {"file": "a.md"})["isError"] is True
    assert mcp.call_tool("thth_lint", {"file": "a.md"})["isError"] is False


def test_T16_道具の説明に使う順序が書いてある():
    mcp = _load_mcp()
    names = {t["name"]: t for t in mcp.TOOLS}
    assert {"thth_topic_context", "thth_topic_evaluate",
            "thth_topic_decision"} <= set(names)
    assert "context" in names["thth_topic_context"]["description"]
    assert "evaluate" in names["thth_topic_context"]["description"]


# --- T27 既定でない queue_dir・Unicode パス --------------------------------

def test_T27_台帳のqueue_dirに従い日本語のパスでも読める(isolated_account_factory,
                                                          nigamilab_repo):
    """**`docs/sns/queue` 固定にしない。**"""
    queue_dir = os.path.join(nigamilab_repo, "記事", "投稿予定")
    account = isolated_account_factory(queue_dir=queue_dir)
    path = write_queue_file(queue_dir, "コーヒーの精製.md", body=BODY,
                             fm_overrides={"status": "draft"})
    proc = _suggest(path, {})
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = json.loads(proc.stdout)
    assert out["account"] == account["name"]
    assert out["status"] == "needs_article"


# --- T28 複数 URL・query 違い ----------------------------------------------

def test_T28_URLが複数なら主対象を決めずに指定を求める(isolated_account):
    body = ("## threads\n\n前の記事 https://example.test/a と "
            "今回 https://example.test/b の話。\n")
    path = _queue(isolated_account, body=body, name="two.md")
    out = json.loads(_suggest(path, {}).stdout)
    assert out["status"] == "needs_article"
    assert out["main_article_url"] is None
    assert out["urls_in_post"] == ["https://example.test/a",
                                    "https://example.test/b"]
    assert "--article-url" in out["required_actions"][-1]["reason"]


def test_T28_主対象を指定すれば残りは対象外として出る(isolated_account):
    body = ("## threads\n\n前の記事 https://example.test/a と "
            "今回 https://example.test/b の話。\n")
    path = _queue(isolated_account, body=body, name="two2.md")
    proc = _suggest(path, {}, extra=["--article-url", "https://example.test/b"])
    out = json.loads(proc.stdout)
    assert out["main_article_url"] == "https://example.test/b"
    assert out["ignored_urls"] == ["https://example.test/a"]


def test_T28_queryが違うURLを同じ記事と見なさない(isolated_account):
    """`?utm_source=` が付いただけでも**別の記事証拠**として扱われる。

    記事の同一性は URL ではなく `content_sha256` で決まるが、
    `requested_url` が違えば `article_id` も違う（取り違えを見つけられる形）。
    """
    a = make_article()
    b = dict(a)
    b.pop("article_id"); b.pop("content_sha256"); b.pop("schema_version")
    b["requested_url"] = "https://example.test/coffee?utm_source=threads"
    from thth import topic_models as models
    assert models.build_article(b)["article_id"] != a["article_id"]
    assert models.build_article(b)["content_sha256"] == a["content_sha256"]


# --- 入力の上限（設計 §7） --------------------------------------------------

def test_大きすぎる入力は構造化エラーで断る(isolated_account):
    path = _queue(isolated_account, name="big.md")
    huge = {"article": {"content_text": "あ" * 400000}}
    proc = _suggest(path, huge)
    assert proc.returncode == 2, proc.stdout
    assert json.loads(proc.stdout)["error"]["code"] == "input_too_large"


def test_stdoutはJSONだけ(isolated_account):
    path = _queue(isolated_account, name="only.md")
    proc = _suggest(path, {})
    json.loads(proc.stdout)          # 例外が出なければ JSON だけ


# --- T18 トピックを採り入れた後の承認 ---------------------------------------

def test_T18_トピックを変えたら前の承認は使い回せない(isolated_account):
    """採用したトピックは**承認の確認対象に入る**。

    トピックは本文と同じくらい結果を変える（実測で約 400 倍）。本文だけ確認して
    トピックを後から差し替えられるなら、**masaru が見ていないものが出る**。
    """
    from tests.conftest import approve_via_cli
    from thth import approval, queuefile

    path = _queue(isolated_account, name="approve.md")
    first = approve_via_cli(path)
    assert first.returncode == 0, first.stderr

    before = queuefile.parse(path).front_matter["approved_sha"]

    # 承認のあとでトピックだけを差し替える
    text = open(path, encoding="utf-8").read()
    assert "topic:" in text
    open(path, "w", encoding="utf-8").write(
        text.replace("topic:", "topic: コーヒー", 1) if "topic: \n" not in text
        else text.replace("topic: \n", "topic: コーヒー\n", 1))

    fm = queuefile.parse(path).front_matter
    section = queuefile.extract_section(queuefile.parse(path).body, "threads")
    after = approval.compute_approved_sha(
        section=section, account=fm["account"], reply_to=fm.get("reply_to"),
        topic=fm.get("topic"), publish_at=fm["publish_at"])
    assert after != before, "トピックを変えても digest が同じ（承認が使い回せる）"


# --- 保存の往復（observe → record-decision → decision） ---------------------

def test_観測と判断を残して読み戻せる(isolated_account, thth_root):
    path = _queue(isolated_account, name="round.md")
    article = make_article()

    obs = make_observation("コーヒー")
    proc = run_thth(["topics", "observe", "--json-stdin", "--by", "test"],
                     env=None)
    assert proc.returncode == 2, "入力が無いのに通った"

    proc = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "bin", "thth"),
         "topics", "observe", "--json-stdin", "--by", "test"],
        capture_output=True, text=True, env=dict(os.environ),
        input=json.dumps(obs, ensure_ascii=False))
    assert proc.returncode == 0, proc.stderr
    observation_id = json.loads(proc.stdout)["observation_id"]

    # **再送しても増えない**（内容アドレス）。
    proc2 = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "bin", "thth"),
         "topics", "observe", "--json-stdin", "--by", "test"],
        capture_output=True, text=True, env=dict(os.environ),
        input=json.dumps(obs, ensure_ascii=False))
    assert json.loads(proc2.stdout)["observation_id"] == observation_id
    assert json.loads(proc2.stdout)["stored"] is False

    out = json.loads(_suggest(path, {"article": article}).stdout)
    assert observation_id in out["context"]["observation_ids"]

    decision_input = {
        "draft_path": path, "context_id": out["context_id"],
        "article": {k: v for k, v in article.items()
                    if k not in ("article_id", "content_sha256", "schema_version")},
        "proposal": {
            "context_id": out["context_id"], "prompt_version": "t",
            "intended_reader": "コーヒーを淹れる人", "article_value": "精製の違い",
            "post_angle": "同じ豆でも変わる",
            "candidates": [candidate("コーヒー", [observation_id])],
            "selected_topic": "コーヒー", "selection_reason": "記事の主題",
        },
    }
    proc = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "bin", "thth"),
         "topics", "record-decision", "--json-stdin", "--by", "test"],
        capture_output=True, text=True, env=dict(os.environ),
        input=json.dumps(decision_input, ensure_ascii=False))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    saved = json.loads(proc.stdout)
    # profile をまだ確定していない account なので暫定。**何が足りないかを言う。**
    assert saved["status"] == "provisional", saved
    assert any("profile" in s for s in saved["shortfalls"]), saved["shortfalls"]
    decision_id = saved["decision_id"]

    proc = run_thth(["topics", "decision", decision_id, "--json"])
    assert proc.returncode == 0, proc.stderr
    read_back = json.loads(proc.stdout)
    assert read_back["selected_topic"] == "コーヒー"
    assert read_back["freshness"] == {"draft_readable": True,
                                       "draft_unchanged": True}

    # **原稿が変われば、判断は書き換えずに freshness で言う**（設計 §6）。
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n追記。\n")
    again = json.loads(run_thth(["topics", "decision", decision_id, "--json"]).stdout)
    assert again["selected_topic"] == "コーヒー", "保存済みの判断を書き換えた"
    assert again["freshness"]["draft_unchanged"] is False


def test_原稿が読み取り時から変わっていれば判断を登録しない(isolated_account):
    """設計 §6。**入力 JSON が「検証済み」と自称しても信用しない。**"""
    path = _queue(isolated_account, name="stale.md")
    article = make_article()
    out = json.loads(_suggest(path, {"article": article}).stdout)

    with open(path, "a", encoding="utf-8") as f:
        f.write("\n別の人が足した行。\n")

    decision_input = {
        "draft_path": path, "context_id": out["context_id"],
        "article": {k: v for k, v in article.items()
                    if k not in ("article_id", "content_sha256", "schema_version")},
        "proposal": {
            "context_id": out["context_id"], "prompt_version": "t",
            "intended_reader": "x", "article_value": "x", "post_angle": "x",
            "candidates": [candidate("コーヒー", [])],
            "selected_topic": "コーヒー", "selection_reason": "x",
        },
    }
    proc = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "bin", "thth"),
         "topics", "record-decision", "--json-stdin", "--by", "test"],
        capture_output=True, text=True, env=dict(os.environ),
        input=json.dumps(decision_input, ensure_ascii=False))
    assert proc.returncode == 1, proc.stdout
    assert json.loads(proc.stdout)["error"]["code"] == "stale_context"


def test_profileは確認者と根拠が要る(isolated_account):
    account = isolated_account["name"]
    proc = run_thth(["topics", "profile", account, "--json"])
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["profile"] is None

    row = {"language": "ja", "primary_goal": "relevant_conversation",
           "editorial_scope": "淹れ方・豆・器具", "intended_interests": ["コーヒー"],
           "avoid_misrepresentation": ["専門家を装わない"],
           "status": "confirmed", "basis": []}
    proc = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "bin", "thth"),
         "topics", "profile", account, "--json-stdin", "--by", "kopicha-session"],
        capture_output=True, text=True, env=dict(os.environ),
        input=json.dumps(row, ensure_ascii=False))
    assert proc.returncode == 2, "根拠なしで confirmed にできてしまった"

    row["basis"] = ["docs/kopicha/編集方針.md"]
    proc = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "bin", "thth"),
         "topics", "profile", account, "--json-stdin", "--by", "kopicha-session"],
        capture_output=True, text=True, env=dict(os.environ),
        input=json.dumps(row, ensure_ascii=False))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    out = json.loads(proc.stdout)
    assert out["profile"]["confirmed_by"] == "kopicha-session"
    assert "masaru に確認" in out["notice"]


# --- 手順書に書いたコマンドが実在すること ------------------------------------

def test_手順書のコマンドが実際に存在する():
    """**動かないコマンドを配らない**（2026-09-11 に `--account` でやった失敗）。

    手順書は各セッションが読んで**そのまま打つ**。存在しない語を書いたら、
    そこで止まる。ドキュメントの側から実装を縛る。
    """
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    doc = open(os.path.join(root, "docs", "手順_LLM_トピック選定.md"),
                encoding="utf-8").read()
    used = set(re.findall(r"thth topics ([a-z][a-z-]*)", doc))
    unknown = used - set(topic_cli.SUBCOMMANDS)
    assert not unknown, f"手順書に無いコマンドが書いてある: {unknown}"
    assert used, "手順書にコマンドが 1 つも出てこない"

    parser = topic_cli.build_parser()
    for name in used:
        assert parser.parse_args([name] + {
            "suggest": ["x.md"], "decision": ["sha256:x"],
            "profile": ["kopicha"]}.get(name, [])).sub == name


def test_知らないaccountはexit2で構造化エラー(tmp_path, isolated_account):
    """設計 §6。**exit 2 は実行エラー**（判断の結果ではない）。"""
    path = os.path.join(str(tmp_path), "x.md")
    open(path, "w", encoding="utf-8").write(
        "---\naccount: そんなaccountはない\nstatus: draft\n---\n\n## threads\n\n本文。\n")
    proc = _suggest(path, {})
    assert proc.returncode == 2, proc.stdout
    assert json.loads(proc.stdout)["error"]["code"] == "unknown_account"
