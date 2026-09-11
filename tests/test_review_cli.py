"""検収と修正理由の CLI（Codex §7・§12 第 1 段階の出口条件）。

**実プロセスで確かめる**（規約 11: 引数を渡さない本番経路を検証する）。
`bin/thth` をサブプロセスで呼ぶので、dispatch・parser・棚まで通る。

出口条件（Codex §12）: 既存データを読んだ実出力と、**実際の原稿修正 1 往復**。
同じ記録の再送・対象変更・破損・他 account は `tests/test_review_records.py`。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

from tests.conftest import BIN_THTH, write_queue_file
from thth import topic_cli

NOW = "2026-09-11T18:00:00+09:00"
BODY = ("## threads\n\n"
        "浅煎りは酸が立つ。https://example.test/roast\n")


def _thth(args, payload=None, env=None):
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run(
        [sys.executable, BIN_THTH, *args], capture_output=True, text=True,
        env=full_env,
        input=None if payload is None else json.dumps(payload, ensure_ascii=False))


def _json(proc):
    assert proc.stdout, proc.stderr
    return json.loads(proc.stdout)


def _entry(reason_id, **over):
    row = {"reason_id": reason_id, "display": reason_id, "definition": "定義",
           "includes": [], "excludes": [], "scope": "すべての原稿",
           "judged_by": ["human", "session"], "state": "proposed",
           "meaning_version": 1}
    row.update(over)
    return row


VOCABULARY = {
    "name": "修正理由", "created_at": NOW, "supersedes": None,
    "entries": [_entry("missing_condition", definition="必要な条件が不足"),
                 _entry("unsupported_claim", definition="原資料から裏付けられない"),
                 _entry("other", definition="既存分類で説明できない")],
}


def _register_vocabulary(payload=None):
    proc = _thth(["topics", "record-vocabulary", "--json-stdin", "--by", "テスト"],
                  payload or VOCABULARY)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return _json(proc)["vocabulary_id"]


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _finding(reason_id="missing_condition", **over):
    row = {"reason_id": reason_id, "check_method": "human", "result": "problem",
           "evidence_refs": [], "note": ""}
    row.update(over)
    return row


def _review(vocabulary_id, **over):
    row = {"account": "nigamilab-threads", "vocabulary_id": vocabulary_id,
           "findings": [_finding()], "judged_by": {"kind": "human"},
           "judged_at": NOW, "disposition": "unresolved"}
    row.update(over)
    return row


def _record(payload, extra=None, by="masaru"):
    return _thth(["topics", "record-review", "--json-stdin", "--by", by]
                  + list(extra or []), payload)


# --- 出口条件: 実際の原稿修正 1 往復 ----------------------------------------

def test_原稿修正の1往復を記録できる(isolated_account, thth_root):
    """指摘 → 直す → 再検査。**直しただけで解消済みにしない**（Codex §6.2）。"""
    vocabulary_id = _register_vocabulary()
    path = str(write_queue_file(isolated_account["queue_dir"], "roast.md",
                                 body=BODY, fm_overrides={"status": "draft"}))
    before = _sha256(path)

    # 1. 指摘（未処置）。原稿はパスでなく中身から指す。
    first = _record(_review(vocabulary_id,
                             findings=[_finding(note="浅煎りの焙煎度の範囲が無い")]),
                     ["--draft", path])
    assert first.returncode == 0, first.stdout + first.stderr
    out = _json(first)
    assert out["stored"] is True and out["draft_sha256"] == before
    assert out["reasons"]["by_reason"] == {"missing_condition": 1}

    # 2. 直す。**処置は「直した」であって「正しくなった」ではない。**
    with open(path, "a", encoding="utf-8") as f:
        f.write("焙煎度は Agtron 70 前後。\n")
    after = _sha256(path)
    fixed = _record(_review(vocabulary_id, draft_sha256=before,
                             findings=[_finding(note="浅煎りの焙煎度の範囲が無い")],
                             disposition="fixed"),
                     ["--revised-draft", path])
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    fixed_id = _json(fixed)["review_id"]

    # 3. 再検査は別の記録。**前の記録を指す。**
    recheck = _record(_review(vocabulary_id, recheck_of=fixed_id,
                               findings=[_finding(result="no_problem",
                                                   note="条件が入った")]),
                       ["--draft", path])
    assert recheck.returncode == 0, recheck.stdout + recheck.stderr

    # 4. 読む。**新しい原稿に当たるのは再検査だけ。**
    listed = _json(_thth(["topics", "review", isolated_account["name"],
                           "--draft", path]))
    assert listed["count"] == 1
    assert listed["reviews"][0]["recheck_of"] == fixed_id
    assert listed["reviews"][0]["draft_sha256"] == after

    everything = _json(_thth(["topics", "review", isolated_account["name"]]))
    assert everything["count"] == 3
    assert everything["unsupported"] == []


def test_A03_原稿が変われば確認済みを引き継がない(isolated_account, thth_root):
    """受け入れ A03。**旧結果は履歴。**"""
    vocabulary_id = _register_vocabulary()
    path = str(write_queue_file(isolated_account["queue_dir"], "a3.md", body=BODY,
                                 fm_overrides={"status": "draft"}))
    review_id = _json(_record(_review(vocabulary_id), ["--draft", path]))["review_id"]

    same = _json(_thth(["topics", "review", review_id, "--draft", path]))
    assert same["freshness"]["applies_to_draft"] is True

    with open(path, "a", encoding="utf-8") as f:
        f.write("追記。\n")
    changed = _json(_thth(["topics", "review", review_id, "--draft", path]))
    assert changed["freshness"]["applies_to_draft"] is False
    assert changed["review"]["review_id"] == review_id   # 記録自体は変わらない


def test_A02_機械検査と意味評価を別の欄に出す(isolated_account, thth_root):
    """受け入れ A02・Codex §7。**一つの緑表示にまとめない。**"""
    vocabulary_id = _register_vocabulary()
    review_id = _json(_record(_review(vocabulary_id, draft_sha256="a" * 64, findings=[
        _finding("unsupported_claim", check_method="machine_check",
                 result="no_problem", note="引用は本文にある"),
        _finding("unsupported_claim", check_method="llm_eval",
                 result="suspected", note="引用より主張が強い"),
        _finding("missing_condition", check_method="human", result="problem",
                 note="条件が無い")])))["review_id"]

    out = _json(_thth(["topics", "review", review_id]))
    assert [f["result"] for f in out["machine_checks"]] == ["no_problem"]
    assert [f["result"] for f in out["semantic_evaluations"]] == ["suspected"]
    assert [f["result"] for f in out["human_confirmations"]] == ["problem"]
    # **THTH が見ていないものを名前で言う。**
    assert "公開してよいかどうか" in out["out_of_scope"]
    assert out["next_actions"], "未処置の指摘があるのに次の操作が無い"


def test_otherが増えたら道具が自分で言う(isolated_account, thth_root):
    """引継ぎ 設計条件 2。**語彙が足りないことを、道具が自分で言う。**"""
    vocabulary_id = _register_vocabulary()
    for i, note in enumerate(["媒体の仕様が変わった", "記事の前提が古い"]):
        out = _json(_record(_review(vocabulary_id, draft_sha256=str(i) * 64,
                                     findings=[_finding("other", note=note)])))
        assert any("other" in w for w in out["warnings"]), out

    listed = _json(_thth(["topics", "review", isolated_account["name"]]))
    assert listed["reasons"]["other"]["count"] == 2
    # 内訳は**説明をそのまま**返す（数えるだけにしない）。並び順は問わない。
    assert {n["note"] for n in listed["reasons"]["other"]["notes"]} == {
        "媒体の仕様が変わった", "記事の前提が古い"}
    assert any("分けていない" in w for w in listed["warnings"])


def test_A05_語彙が手元に無ければ保存しない_対応外と言う(isolated_account, thth_root):
    """受け入れ A05。**既知の分類にも 0 件にも変換しない。**

    **棚に別の語彙がある状態で試す。** 空の棚で試すと「手近な版で代用する」
    実装を通してしまう（この形の実装を入れて確かめた・2026-09-11）。
    """
    _register_vocabulary()
    proc = _record(_review("sha256:" + "f" * 64, draft_sha256="a" * 64))
    assert proc.returncode == 2
    out = _json(proc)
    assert out["error"]["code"] == "unknown_vocabulary"
    assert "読み替えません" in out["error"]["message"]

    missing = _record(_review("", draft_sha256="a" * 64))
    assert _json(missing)["error"]["code"] == "missing_vocabulary"


def test_語彙に無い理由は正しい形を添えて断る(isolated_account, thth_root):
    vocabulary_id = _register_vocabulary()
    proc = _record(_review(vocabulary_id, draft_sha256="a" * 64,
                            findings=[_finding("tone_too_casual")]))
    assert proc.returncode == 2
    out = _json(proc)
    assert out["error"]["code"] == "schema_error"
    assert "other" in out["error"]["message"]
    # **断るときこそ、正しい形を渡す**（規約 14）。
    assert "ReviewRecord" in out["expected_schema"]


def test_原稿の中身と名乗りが食い違えば黙って直さない(isolated_account, thth_root):
    vocabulary_id = _register_vocabulary()
    path = str(write_queue_file(isolated_account["queue_dir"], "m.md", body=BODY,
                                 fm_overrides={"status": "draft"}))
    proc = _record(_review(vocabulary_id, draft_sha256="b" * 64), ["--draft", path])
    assert proc.returncode == 2
    out = _json(proc)
    assert out["error"]["code"] == "draft_mismatch"
    assert "決められません" in out["error"]["message"]


def test_実在しない根拠idを断る(isolated_account, thth_root):
    vocabulary_id = _register_vocabulary()
    proc = _record(_review(vocabulary_id, draft_sha256="a" * 64, findings=[
        _finding(evidence_refs=["sha256:" + "9" * 64])]))
    assert proc.returncode == 2
    assert "実在しません" in _json(proc)["error"]["message"]


def test_acceptedの語彙は登録できない(isolated_account, thth_root):
    """**再検収 F4**（2026-09-11 Codex）。注意文だけでは、保存された
    `accepted` という主張は変わらない。

    > 実在しない review ID を付けた正例 2 件・反例 1 件で、書き手が
    > `state:accepted` を保存できました。…注意文を出すだけでは、保存状態の
    > accepted という主張は変わりません。

    **できていない状態を開けておかない。** 正式な採用（独立確認の照合と採用
    判断の記録）は第 3 段階で実装する。
    """
    payload = dict(VOCABULARY, entries=[
        _entry("other", definition="既存分類で説明できない"),
        _entry("missing_condition", definition="必要な条件が不足", state="accepted",
               includes=["比較の条件が無い"], excludes=["反応が読めないこと"])])
    proc = _thth(["topics", "record-vocabulary", "--json-stdin", "--by", "テスト"],
                  payload)
    assert proc.returncode == 2
    out = _json(proc)
    assert out["error"]["code"] == "promotion_not_implemented"
    assert "missing_condition" in out["error"]["message"]

    # proposed / shadow は通る。**書きながら育てる側は閉じない。**
    ok = _thth(["topics", "record-vocabulary", "--json-stdin", "--by", "テスト"],
                dict(VOCABULARY, entries=[
                    _entry("other"), _entry("missing_condition", state="shadow")]))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert _json(ok)["states"] == {"proposed": 1, "shadow": 1}
    assert any("masaru" in w for w in _json(ok)["warnings"])


def test_語彙は版で並ぶ_前の版を指す(isolated_account, thth_root):
    first = _register_vocabulary()
    second = _register_vocabulary(dict(
        VOCABULARY, supersedes=first,
        entries=VOCABULARY["entries"] + [_entry("redundant_segment",
                                                 definition="役割が増えない段")]))
    listed = _json(_thth(["topics", "vocabulary"]))
    assert listed["count"] == 2
    assert {v["vocabulary_id"] for v in listed["vocabularies"]} == {first, second}
    one = _json(_thth(["topics", "vocabulary", second]))
    assert one["vocabulary"]["supersedes"] == first


# --- 境界（Codex §11） -------------------------------------------------------

def _repo_state(repo_dir: str) -> tuple:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir,
                           capture_output=True, text=True).stdout
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir,
                            capture_output=True, text=True).stdout
    return head, status


def test_検収は原稿もGitもinflightも変えない(isolated_account, thth_root):
    """Codex §11。**編集診断は公開経路に触らない。**"""
    vocabulary_id = _register_vocabulary()
    path = str(write_queue_file(isolated_account["queue_dir"], "b.md", body=BODY,
                                 fm_overrides={"status": "draft"}))
    before_bytes = open(path, "rb").read()
    before_repo = _repo_state(isolated_account["repo_dir"])

    assert _record(_review(vocabulary_id), ["--draft", path]).returncode == 0

    assert open(path, "rb").read() == before_bytes, "原稿を書き換えた"
    assert _repo_state(isolated_account["repo_dir"]) == before_repo, "Git を動かした"
    for name in ("inflight", "sent"):
        assert not os.path.exists(os.path.join(thth_root, "state", name)), name


def test_stdoutはJSONだけ(isolated_account, thth_root):
    vocabulary_id = _register_vocabulary()
    proc = _record(_review(vocabulary_id, draft_sha256="a" * 64))
    json.loads(proc.stdout)          # 落ちなければ JSON だけ
    proc = _record(_review(vocabulary_id))   # draft_sha256 も --draft も無い
    assert proc.returncode == 2
    json.loads(proc.stdout)


@pytest.mark.parametrize("argv,new_style", [
    (["topics", "record-review"], True),
    (["topics", "review", "sha256:x"], True),
    (["topics", "record-vocabulary"], True),
    (["topics", "vocabulary"], True),
    (["topics", "nigamilab"], False),
    (["topics", "--note", "コーヒー", "--verdict", "alive"], False),
])
def test_既存の呼び方を壊さない(argv, new_style):
    assert topic_cli.is_new_style(argv) is new_style


# --- 出口条件の残り: 他 account と破損（Codex §12 第 1 段階） ----------------

def test_他accountの検収を混ぜない(isolated_account_factory, thth_root):
    """**アカウントを越えて判断を継承しない**（Codex §3）。数にも入れない。"""
    mine = isolated_account_factory("nigamilab-threads")
    theirs = isolated_account_factory("kopicha-threads")
    vocabulary_id = _register_vocabulary()

    _record(_review(vocabulary_id, account=mine["name"], draft_sha256="a" * 64,
                     findings=[_finding(note="こちらの指摘")]))
    _record(_review(vocabulary_id, account=theirs["name"], draft_sha256="b" * 64,
                     findings=[_finding("other", note="あちらの指摘")]))

    out = _json(_thth(["topics", "review", mine["name"]]))
    assert out["count"] == 1
    assert out["reviews"][0]["draft_sha256"] == "a" * 64
    # **他 account の `other` をこちらの語彙の不足として数えない。**
    assert out["reasons"]["other"]["count"] == 0
    assert out["warnings"] == []

    other = _json(_thth(["topics", "review", theirs["name"]]))
    assert other["count"] == 1 and other["reasons"]["other"]["count"] == 1


def test_壊れた語彙を無いことにしない_残りは読める(isolated_account, thth_root):
    """設計 §8。**破損と不存在は別の状態。** 1 件読めないことを、
    一覧全体が読めないことにもしない（束で落ちると**残りの検収まで消える**）。
    """
    vocabulary_id = _register_vocabulary()
    good = _json(_record(_review(vocabulary_id, draft_sha256="a" * 64)))["review_id"]
    path = os.path.join(thth_root, "state", "topic_advice", "vocabularies",
                        vocabulary_id.replace("sha256:", "") + ".json")
    row = json.load(open(path, encoding="utf-8"))
    row["entries"][0]["definition"] = "手で書き換えた定義"
    json.dump(row, open(path, "w", encoding="utf-8"), ensure_ascii=False)

    listed = _json(_thth(["topics", "review", isolated_account["name"]]))
    assert listed["ok"] is True, f"1 件壊れただけで一覧が落ちた: {listed}"
    assert listed["count"] == 1                      # 検収そのものは読める
    assert len(listed["unsupported"]) == 1
    reason = listed["unsupported"][0]["reason"]
    assert "読めません" in reason and "保存されていません" not in reason

    one = _json(_thth(["topics", "review", good]))
    assert one["support"]["status"] == "unsupported"
    assert "読めません" in one["support"]["reason"]
    # **0 件・問題なしに変換しない。** 指摘そのものは元のラベルのまま出る。
    assert [f["reason_id"] for f in one["human_confirmations"]] == \
        ["missing_condition"]


# --- 後から決まった処置を前の指摘に結び付ける（kopicha 9/21 の 1 往復で出た） --

def test_後から決めた処置は前の記録を指す_上書きしない(isolated_account, thth_root):
    """**記録は上書きしない**ので、処置を後から決めたときに線が要る。

    運用セッションの問い（2026-09-11）がそのまま反例だった——
    「dismissed ではなく**未着手のまま持ち越し**として扱えますか。見送りだと
    『見たうえで要らないと判断した』に読めますが、実際は『確かめていない』です」。
    処置の語彙には `deferred` が最初からあった。**足りなかったのは、その処置を
    前の指摘に結び付ける線のほう。**
    """
    vocabulary_id = _register_vocabulary()
    first = _json(_record(_review(vocabulary_id, draft_sha256="a" * 64,
                                   findings=[_finding("other", note="頁を見ていない")])))
    later = _json(_record(_review(
        vocabulary_id, draft_sha256="a" * 64,
        findings=[_finding("other", note="頁を見ていない")],
        supersedes=first["review_id"], disposition="deferred",
        disposition_reason="本文の修正では消えない。一覧頁の確認が要る")))

    out = _json(_thth(["topics", "review", isolated_account["name"]]))
    assert out["count"] == 2, "前の記録が消えている"
    rows = {r["review_id"]: r for r in out["reviews"]}
    # **古いほうも残る。** いまの処置がどれかは印で読む。
    assert rows[first["review_id"]]["superseded_by"] == [later["review_id"]]
    assert rows[first["review_id"]]["disposition"] == "unresolved"
    assert rows[later["review_id"]]["disposition"] == "deferred"
    assert rows[later["review_id"]]["superseded_by"] == []


def test_再検査と処置の書き足しを同じ印にしない(isolated_account, thth_root):
    """`recheck_of` は**もう一度見た**とき、`supersedes` は**処置を書き足した**とき。

    1 つにすると「処置を書いただけ」と「検査し直した」が区別できなくなる
    （§7 の「未評価と問題なしは別」と同じ筋）。
    """
    vocabulary_id = _register_vocabulary()
    fixed = _json(_record(_review(vocabulary_id, draft_sha256="a" * 64,
                                   disposition="fixed",
                                   revised_draft_sha256="b" * 64)))
    recheck = _json(_record(_review(vocabulary_id, draft_sha256="b" * 64,
                                     recheck_of=fixed["review_id"],
                                     findings=[_finding(result="no_problem")])))
    rows = {r["review_id"]: r for r in
            _json(_thth(["topics", "review", isolated_account["name"]]))["reviews"]}
    assert rows[recheck["review_id"]]["recheck_of"] == fixed["review_id"]
    assert rows[recheck["review_id"]]["supersedes"] is None
    assert rows[fixed["review_id"]]["superseded_by"] == []   # 再検査は処置ではない


def test_実在しない記録は指せない(isolated_account, thth_root):
    vocabulary_id = _register_vocabulary()
    for key in ("recheck_of", "supersedes"):
        proc = _record(_review(vocabulary_id, draft_sha256="a" * 64,
                                **{key: "sha256:" + "9" * 64}))
        assert proc.returncode == 2
        assert _json(proc)["error"]["code"] == "not_found"
    proc = _record(_review(vocabulary_id, draft_sha256="a" * 64,
                            supersedes="前のやつ"))
    assert "review_id" in _json(proc)["error"]["message"]


def test_直す前と後が同じ指紋なら断る(isolated_account, thth_root):
    """**往復の常態を罠にしない**（運用セッションが踏みかけた・2026-09-11）。

    指摘は「直す前の原稿」に対するものなのに、`--draft` は現物（＝もう直って
    いる）から計算する。両方渡すと**判定した対象と記録される対象がずれる。**
    """
    vocabulary_id = _register_vocabulary()
    path = str(write_queue_file(isolated_account["queue_dir"], "same.md", body=BODY,
                                 fm_overrides={"status": "draft"}))
    proc = _record(_review(vocabulary_id, disposition="fixed"),
                    ["--draft", path, "--revised-draft", path])
    assert proc.returncode == 2
    message = _json(proc)["error"]["message"]
    assert "直す前の原稿" in message and "--revised-draft" in message


def test_記録の応答に鎖が出る(isolated_account, thth_root):
    """**読み返さずに確かめられる**（改訂先が応答に無いと `review` を引き直す）。"""
    vocabulary_id = _register_vocabulary()
    path = str(write_queue_file(isolated_account["queue_dir"], "chain.md", body=BODY,
                                 fm_overrides={"status": "draft"}))
    fixed = _json(_record(_review(vocabulary_id, draft_sha256="a" * 64,
                                   disposition="fixed"),
                           ["--revised-draft", path]))
    assert fixed["revised_draft_sha256"] == _sha256(path)
    assert fixed["recheck_of"] is None and fixed["supersedes"] is None

    recheck = _json(_record(_review(vocabulary_id, recheck_of=fixed["review_id"],
                                     findings=[_finding(result="no_problem")]),
                             ["--draft", path]))
    assert recheck["recheck_of"] == fixed["review_id"]
    assert recheck["draft_sha256"] == fixed["revised_draft_sha256"]
