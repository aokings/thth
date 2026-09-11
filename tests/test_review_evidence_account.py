"""根拠 ID を account で絞る／検収一覧に出自の内訳を出す（引継ぎ 2026-09-11）。

いまの `_known_ids()` は articles・observations・decisions・proposals・reviews
の**全 account の ID を 1 つの集合にプールしていた**。そのため
`thth topics record-review --by X` で、**別 account の判断（decision）を
自分の指摘の根拠 `evidence_refs` として保存できた。** THTH の設計原則は
「account を越えて判断を継承しない」なので、これを通さない（直し 1）。

`thth topics review <account>` にも出自（`provenance`）の内訳を足す（直し 2）
——試験のために書かれた原稿への指摘と、実運用の指摘を混ぜて数えないため。
"""
from __future__ import annotations

from tests.test_review_cli import (NOW, _finding, _json, _record,
                                     _register_vocabulary, _review, _thth)
from thth import topic_models as models
from thth import topic_store as store

FOREIGN_ACCOUNT = "kopicha-threads"       # 検収記録の既定 account は別
DRAFT = "a" * 64


def _fake_decision(account: str, **over) -> dict:
    """判断の記録を**最低限の形だけ**でっち上げる。

    `_known_ids()`／`_foreign_evidence()` は `context.account` しか見ないので、
    `record-decision` の本チャン（記事・候補比較・queue ファイル一式）を
    経由する必要はない。棚の内容アドレス照合（`verify()`）さえ満たせばよい。
    """
    row = {"schema_version": models.SCHEMA_VERSION,
           "context": {"account": account}, "note": "test-fixture"}
    row.update(over)
    row["decision_id"] = models.content_id(row, exclude=("decision_id",))
    saved, _wrote = store.put("decisions", row, id_key="decision_id")
    return saved


def _observe(topic="コーヒー"):
    """観測を 1 件残して observation_id を返す（account を持たない共有の事実）。"""
    payload = {"topic": topic, "search_mode": "manual_unknown", "provider": "test",
               "retrieved_at": NOW, "status": "empty", "samples": []}
    proc = _thth(["topics", "observe", "--json-stdin", "--by", "テスト"], payload)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return _json(proc)["observation_id"]


# --- 直し 1: 根拠 ID を account で絞る ---------------------------------------

def test_別accountの判断を根拠にした検収は断られる(isolated_account, thth_root):
    """**別 account の判断を根拠にできない**（THTH の設計原則）。"""
    vocabulary_id = _register_vocabulary()
    decision = _fake_decision(FOREIGN_ACCOUNT)

    proc = _record(_review(vocabulary_id, draft_sha256=DRAFT, findings=[
        _finding(evidence_refs=[decision["decision_id"]])]))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    out = _json(proc)
    assert out["error"]["code"] == "unrelated_reference"
    # **なぜ断ったかが分かる文言。** 「無い」（実在しません）とは別の文言。
    assert "アカウントを越えて" in out["error"]["message"]
    assert "実在しません" not in out["error"]["message"]


def test_同じaccountの判断は根拠にできる(isolated_account, thth_root):
    """**同じ account の判断は根拠として使える。**"""
    vocabulary_id = _register_vocabulary()
    # `_review()` の既定 account（test_review_cli.py）と揃える。
    own_account = _review(vocabulary_id, draft_sha256=DRAFT)["account"]
    decision = _fake_decision(own_account)

    proc = _record(_review(vocabulary_id, draft_sha256=DRAFT, findings=[
        _finding(evidence_refs=[decision["decision_id"]])]))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _json(proc)["stored"] is True


def test_別accountの検収も根拠にできない(isolated_account, thth_root):
    """`reviews` も `decisions` と同じ扱い（account に属する記録）。"""
    vocabulary_id = _register_vocabulary()
    foreign = _json(_record(_review(vocabulary_id, account=FOREIGN_ACCOUNT,
                                     draft_sha256=DRAFT)))

    proc = _record(_review(vocabulary_id, draft_sha256="b" * 64, findings=[
        _finding(evidence_refs=[foreign["review_id"]])]))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "アカウントを越えて" in _json(proc)["error"]["message"]


def test_候補比較はaccountを解決できないので根拠にできない(isolated_account, thth_root):
    """`proposal` は `context_id` しか持たず、**account を解決できない。**"""
    vocabulary_id = _register_vocabulary()
    proposal = {"schema_version": models.SCHEMA_VERSION,
                "context_id": "sha256:" + "c" * 64, "prompt_version": "t",
                "intended_reader": "だれか", "article_value": "何か",
                "post_angle": "何か", "candidates": []}
    proposal["proposal_id"] = models.content_id(proposal, exclude=("proposal_id",))
    saved, _wrote = store.put("proposals", proposal, id_key="proposal_id")

    proc = _record(_review(vocabulary_id, draft_sha256=DRAFT, findings=[
        _finding(evidence_refs=[saved["proposal_id"]])]))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    out = _json(proc)
    assert "解決できない" in out["error"]["message"]
    assert "アカウントを越えて" in out["error"]["message"]


def test_観測は別accountの検収からでも根拠にできる(isolated_account, thth_root):
    """**共有の事実**（誰がいたか）は account に依存しない。"""
    vocabulary_id = _register_vocabulary()
    observation_id = _observe()

    # 検収の account はデフォルト（nigamilab-threads）で、観測そのものは
    # account を持たない——「別 account の記録」ではないので通る。
    proc = _record(_review(vocabulary_id, draft_sha256=DRAFT, findings=[
        _finding(evidence_refs=[observation_id])]))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _json(proc)["stored"] is True


def test_実在しないIDは従来どおり実在しませんと断る(isolated_account, thth_root):
    """**「無い」と「他所のもの」は別の事実**（account 絞りで壊さない）。"""
    vocabulary_id = _register_vocabulary()
    proc = _record(_review(vocabulary_id, draft_sha256=DRAFT, findings=[
        _finding(evidence_refs=["sha256:" + "9" * 64])]))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "実在しません" in _json(proc)["error"]["message"]


# --- 直し 2: 検収一覧に出自の内訳 --------------------------------------------

def test_検収一覧にprovenanceの内訳が出る(isolated_account, thth_root):
    """**試験用の原稿への指摘と実運用の指摘を混ぜて数えない。**

    以前は `improvements` にしか出自の内訳が出ず、`review <account>` の
    一覧を見ただけでは production／trial／unknown を区別できなかった。
    """
    vocabulary_id = _register_vocabulary()
    for draft, provenance in (("a" * 64, "trial"), ("b" * 64, "production")):
        extra = ["--provenance", provenance] if provenance else []
        proc = _record(_review(vocabulary_id, draft_sha256=draft), extra)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    # 印の付く前の記録（provenance を渡さない）も 1 件混ぜる。
    proc = _record(_review(vocabulary_id, draft_sha256="c" * 64))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    out = _json(_thth(["topics", "review", isolated_account["name"]]))
    assert out["provenance"] == {"production": 1, "trial": 1, "unknown": 1}

    by_draft = {r["draft_sha256"]: r["provenance"] for r in out["reviews"]}
    assert by_draft["a" * 64] == "trial"
    assert by_draft["b" * 64] == "production"
    assert by_draft["c" * 64] == "unknown"
