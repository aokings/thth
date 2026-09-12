"""逆監査（2026-09-11）: 開発セッションの今日の主張に反証を取りに行く。

masaru の指示どおり、「正しいと確認する」のではなく「間違っている証拠を探す」。
ここに書いたテストが green のものは「反証できなかった」だけで「正しい」の
証明ではない。何を叩いて何が返ったかは、このファイルの各テストの docstring と
assert メッセージに残す。

`thth/` と `mcp/` は変更しない。ここに書くだけ。
"""
from __future__ import annotations

import http.server
import json
import os
import threading
import contextlib

import pytest

from tests.conftest import (init_git_pair, run_thth, write_queue_file,
                             approve_via_cli)
from tests.test_review_cli import (VOCABULARY, _entry, _finding, _json,
                                    _register_vocabulary, _review, _thth,
                                    _record)
from tests.test_form_spec_cli import _register_spec, FORM
from tests.test_thread_publish import (thread_account, FakeAdapter, publish,
                                        bundle_text, REL, NOW as TNOW)
from thth import threadrun, threadthrow
from thth import topic_models as models
from thth import topic_store as store
from thth.adapters import threads as threads_mod


# =============================================================================
# 主張 2: 「読めない実行記録があると連投は止まるが、単発は止まらない」
# =============================================================================

def _runs_dir(thth_root):
    path = os.path.join(thth_root, "state", "threads")
    os.makedirs(path, exist_ok=True)
    return path


def test_単発の承認は壊れた実行記録があっても止まらない(isolated_account, thth_root):
    """`thth approve` を単発原稿（v1）に対して実際に叩く。

    連投側（`_prepare_bundle`）は `unreadable_runs()` を見るが、単発側
    （`_prepare_one`）はそもそも `threadrun` を一切 import even しない。
    壊れた実行記録を置いた状態で、実際に承認が通るかを確かめる。
    """
    path = write_queue_file(isolated_account["queue_dir"], "solo.md",
                             fm_overrides={"status": "draft", "approved_sha": None})
    directory = _runs_dir(thth_root)
    with open(os.path.join(directory, "run-broken.json"), "w", encoding="utf-8") as f:
        f.write("{壊れた")
    assert threadrun.unreadable_runs(), "前提が崩れている: 壊れた実行記録が無い"

    proc = approve_via_cli(path)
    print("SOLO_APPROVE_WITH_BROKEN_RUN", proc.returncode, proc.stdout, proc.stderr)
    assert proc.returncode == 0, \
        f"単発の承認が壊れた実行記録で止まった（主張と食い違う）: {proc.stdout}{proc.stderr}"


def test_単発の投稿は壊れた実行記録があっても出る(isolated_account, thth_root):
    """`thth throw`（dry-run）を単発原稿に対して実際に叩く。"""
    write_queue_file(isolated_account["queue_dir"], "solo.md")   # 既定で status: approved
    directory = _runs_dir(thth_root)
    with open(os.path.join(directory, "run-broken.json"), "w", encoding="utf-8") as f:
        f.write("{壊れた")

    result = run_thth(["throw", isolated_account["name"]])
    print("SOLO_THROW_WITH_BROKEN_RUN", result.returncode, result.stdout, result.stderr)
    assert result.returncode == 0, \
        f"単発の投稿（dry-run）が壊れた実行記録で止まった: {result.stdout}{result.stderr}"
    combined = result.stdout + result.stderr
    assert "実行記録を読めません" not in combined, \
        f"壊れた実行記録メッセージで単発が止まっていた: {combined}"


# =============================================================================
# 主張 3: 「別 account の実行の続きとして連投を出せない」
#   → `claimed = threadrun.find_by_run_id(rid)` は account でスコープされて
#     いない（`find_latest` と違う）。ここが唯一の攻め口のはず。
# =============================================================================

def test_他accountのrun_idに便乗して連投を出せない(thth_root, thread_account,
                                          isolated_account_factory, tmp_path):
    """A account の実行を最後まで出し、その `run_id` を B account の原稿の
    1 段目に埋め込んで、B account で `publish_bundle` を叩く。

    `find_by_run_id()` は account を見ずに実行記録を引く。`find_latest()`
    （account でスコープ済み）だけに頼っていたら、ここで A の実行に
    B が「継続」として書き込みながら 1 段目を出せてしまうはず
    （`resolve_parent()` の account 照合は index>1 だけにしかない）。
    """
    api_a = FakeAdapter()
    results_a = publish(thread_account, api_a)
    assert [r.action for r in results_a] == ["published"] * 3, \
        [r.reason for r in results_a]
    run_a = threadrun.find_latest(thread_account["account"]["name"], REL)
    assert run_a is not None
    run_id = run_a["run_id"]
    print("RUN_A", run_id, run_a)

    pair_b = init_git_pair(tmp_path / "acct-b",
                            seed_content=bundle_text(
                                account="other-threads",
                                posts=[{"index": 1, "run_id": f'"{run_id}"'},
                                       {"index": 2}, {"index": 3}]),
                            seed_name="thread.md")
    isolated_account_factory(name="other-threads", repo_dir=pair_b["work"],
                              production=True, quiet_hours=None, min_interval_hours=0)

    api_b = FakeAdapter()
    results_b = threadthrow.publish_bundle(
        "other-threads", REL, adapter_factory=lambda *_: api_b, now=TNOW)
    print("RESULTS_B", [(r.action, r.reason) for r in results_b])
    print("API_B_CALLS", api_b.calls)

    assert len(api_b.calls) == 0, \
        f"他 account の run_id に便乗して公開してしまった: {api_b.calls}"
    assert any("別の account" in (r.reason or "") for r in results_b), results_b

    run_a_after = threadrun.find_latest(thread_account["account"]["name"], REL)
    assert run_a_after == run_a, "A の実行記録が汚された"


# =============================================================================
# 主張 4: 「採取の台帳は『取れて 0 件』と『取得の失敗』を区別できている」
#   → `_get()` は `body.get("error")` だけを見る。それ以外の「静かに 0 件」
#     形が残っていないかを、偽サーバで総当たりする。
# =============================================================================

class _QuietZeroHandler(http.server.BaseHTTPRequestHandler):
    body_bytes = b"{}"
    content_type = "application/json"

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.body_bytes)))
        self.end_headers()
        self.wfile.write(self.body_bytes)

    def log_message(self, *a):
        pass


@contextlib.contextmanager
def _quiet_server(body_bytes: bytes, content_type: str = "application/json"):
    handler = type("H", (_QuietZeroHandler,),
                    {"body_bytes": body_bytes, "content_type": content_type})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _adapter(base_url):
    return threads_mod.ThreadsAdapter(base_url=base_url, access_token="fake-token",
                                       user_id="12345", timeout=2.0)


@pytest.mark.parametrize("label,body_bytes,content_type", [
    ("empty_object", b"{}", "application/json"),
    ("data_null", b'{"data": null}', "application/json"),
    ("data_false", b'{"data": false}', "application/json"),
    ("html_error_page", b"<html><body>502 Bad Gateway</body></html>", "text/html"),
    ("empty_body", b"", "application/json"),
])
def test_静かに0件になる形が残っていないか(label, body_bytes, content_type):
    """`replies()` に対していろいろな「エラーとは書いていない不完全な応答」を
    投げて、例外が上がるか・`[]` を返して「0 件」として成立してしまうかを見る。

    `collect.py` は「例外なら記録を書かない・例外でなければ `kind: fetch` を
    書く」で成功と失敗を分けている。**ここで例外にならず `[]` に丸まる形が
    あれば、それがそのまま「取れて 0 件」として台帳に残る。**
    """
    with _quiet_server(body_bytes, content_type) as base_url:
        adapter = _adapter(base_url)
        try:
            result = adapter.conversation("POST1")
            outcome = ("no_exception", result)
        except Exception as e:  # noqa: BLE001 — 何が起きるかそのものを見たい
            outcome = ("exception", f"{type(e).__name__}: {e}")
    print(f"QUIET_ZERO[{label}]", outcome)
    # 断定の assert はしない。何が起きたかを出力に残すのがこのテストの目的
    # （「200 台帳」の項の報告に、この出力をそのまま貼る）。


def test_数値がゴミなら件数として数えない():
    """`data` が list でも None でもなく **文字列** だったとき。

    **逆監査が穴として記録し、その日のうちに塞いだ**（2026-09-11）。
    以前は `body.get("data") or []` が文字列も truthy として通したので、
    `replies()` が文字列をそのまま返し、**呼び出し側の `len()` が文字数を
    件数として数えた**（採取側は `for row in replies` で 1 文字ずつ回って落ちる）。

    **この test は「穴があること」から「塞がっていること」へ書き換えたもの。**
    元の版（穴を記録していたほう）は commit の履歴に残っている。
    """
    with _quiet_server(b'{"data": "not-a-list-but-truthy"}') as base_url:
        adapter = _adapter(base_url)
        with pytest.raises(RuntimeError) as e:
            adapter.conversation("POST1")
    assert "配列ではありません" in str(e.value)


# =============================================================================
# 主張 5: 「検収の根拠 ID は account の境界で絞られている」
#   → account を省略・空にして `_foreign_evidence` の判定自体を迂回できないか。
# =============================================================================

def test_accountを省いても他account根拠のすり抜けにはならない(isolated_account, thth_root):
    """`_known_ids()`/`_foreign_evidence()` は `account` が truthy なときしか
    動かない（`topic_cli.py` の `if account and isinstance(findings, list):`）。

    account を丸ごと省略すれば `_foreign_evidence` の呼び出し自体が
    スキップされる。**その状態で他 account の decision_id を根拠に通せるか**
    を実際に叩いて確かめる。
    """
    vocabulary_id = _register_vocabulary()
    decision = {"schema_version": models.SCHEMA_VERSION,
                "context": {"account": "kopicha-threads"}, "note": "test-fixture"}
    decision["decision_id"] = models.content_id(decision, exclude=("decision_id",))
    store.put("decisions", decision, id_key="decision_id")

    row = _review(vocabulary_id, draft_sha256="c" * 64,
                   findings=[_finding(evidence_refs=[decision["decision_id"]])])
    del row["account"]           # account を丸ごと省く

    proc = _record(row)
    print("ACCOUNT_OMITTED", proc.returncode, proc.stdout, proc.stderr)
    assert proc.returncode != 0, "account を省いた検収記録が保存できてしまった"
    out = _json(proc)
    # 期待: account 必須のスキーマ検査で落ちる（＝「他 account の判断」を
    # 通した結果ではなく、そもそも保存できない）。もし `unrelated_reference`
    # ではなく `stored: true` で通っていたら、それが本当の穴。
    assert out.get("error", {}).get("code") != "stored", out


def test_account空文字でも他account根拠のすり抜けにはならない(isolated_account, thth_root):
    """account が空文字（truthy チェックに引っかからない別の形）でも同じことを試す。"""
    vocabulary_id = _register_vocabulary()
    decision = {"schema_version": models.SCHEMA_VERSION,
                "context": {"account": "kopicha-threads"}, "note": "test-fixture-2"}
    decision["decision_id"] = models.content_id(decision, exclude=("decision_id",))
    store.put("decisions", decision, id_key="decision_id")

    row = _review(vocabulary_id, draft_sha256="d" * 64, account="",
                   findings=[_finding(evidence_refs=[decision["decision_id"]])])

    proc = _record(row)
    print("ACCOUNT_EMPTY", proc.returncode, proc.stdout, proc.stderr)
    assert proc.returncode != 0, "account が空文字の検収記録が保存できてしまった"


def test_supersedesで他accountの検収に処置を付けられない(isolated_account, thth_root):
    """`evidence_refs` ではなく `supersedes` 経由での他 account 参照も試す
    （`_foreign_evidence` は evidence_refs しか見ないので、鎖のほうは
    `cmd_record_review` 本体の別チェックに掛かっているはず）。"""
    vocabulary_id = _register_vocabulary()
    foreign = _json(_record(_review(vocabulary_id, account="kopicha-threads",
                                     draft_sha256="e" * 64)))
    assert foreign.get("stored") or foreign.get("review_id"), foreign

    proc = _record(_review(vocabulary_id, draft_sha256="f" * 64,
                            supersedes=foreign["review_id"],
                            disposition="dismissed", disposition_reason="test"))
    print("SUPERSEDES_CROSS_ACCOUNT", proc.returncode, proc.stdout, proc.stderr)
    assert proc.returncode != 0, "他 account の検収に supersedes で処置を付けられた"


# =============================================================================
# 主張 7: 「improvements は意味の版が違う記録を束ねない／改訂前後を独立2件にしない」
#   → グルーピングの key が (reason_id, meaning_version, role_id, spec_version)
#     で、vocabulary_id 自体は見ていない。**別語彙・鎖なしで meaning_version が
#     たまたま同じ値なら束ねてしまわないか**を確かめる。
# =============================================================================

def test_別語彙でも同じmeaning_versionなら束ねてしまうか(isolated_account, thth_root):
    """v1 と v2 は互いに `supersedes` で繋がっていない完全に独立な語彙。
    たまたま両方とも `unsupported_claim` の `meaning_version: 1` を名乗るが、
    **定義文は違う**（=本当は別の意味）。

    `improvement_candidates()` のグルーピング key は
    `(reason_id, meaning_version, role_id, spec_version)` で `vocabulary_id`
    を含まない。だから鎖も account 関係も無関係に、ここが同じ値なら
    束ねられてしまうはず——これは「意味の版が違う記録を束ねない」という
    主張の **裏側**（版が違わないのに意味が違う記録がある場合に区別できるか）
    を突く。
    """
    v1_payload = dict(VOCABULARY)
    v1_payload["entries"] = [_entry("unsupported_claim",
                                     definition="原資料から裏付けられない、という意味A"),
                              _entry("other", definition="既存分類で説明できない")]
    v1 = _register_vocabulary(v1_payload)

    v2_payload = dict(VOCABULARY)
    v2_payload["entries"] = [_entry("unsupported_claim",
                                     definition="まったく違う理由づけ、という意味B"),
                              _entry("other", definition="既存分類で説明できない")]
    v2 = _register_vocabulary(v2_payload)

    sid = _register_spec()

    r1 = _record(_review(v1, draft_sha256="1" * 64, form=FORM,
                          findings=[_finding(reason_id="unsupported_claim")]))
    assert r1.returncode == 0, r1.stdout + r1.stderr
    r2 = _record(_review(v2, draft_sha256="2" * 64, form=FORM,
                          findings=[_finding(reason_id="unsupported_claim")]))
    assert r2.returncode == 0, r2.stdout + r2.stderr

    out = _json(_thth(["topics", "improvements", "--form-spec", sid]))
    print("DIFFERENT_VOCAB_SAME_VERSION", json.dumps(out, ensure_ascii=False, indent=2))
    merged = [c for c in out["candidates"]
              if c["reason_id"] == "unsupported_claim" and c["meaning_version"] == 1]
    if merged:
        assert merged[0]["independent_cases"] >= 2, merged
        print("FINDING: 別語彙・別定義でも meaning_version が同じ値なら 1 候補に束ねられた",
              merged[0])
