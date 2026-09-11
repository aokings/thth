"""仮説の棚の CLI（masaru 指示 2026-09-11・外部調査の §9 を型にしたもの）。

**実プロセスで回す**（規約 11）。`record-hypothesis` / `hypotheses` を
`bin/thth` 経由で叩き、dispatch・parser・棚まで通す。

本題は最後の 1 本——外部調査の報告から作った
`docs/仮説_露出と反応率_v1.json` の H01〜H10 が、実際に登録できること。
"""
from __future__ import annotations

import json
import os

from tests.test_review_cli import _json, _thth
from thth import topic_models as models

NOW = "2026-09-12T00:00:00+09:00"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_HYPOTHESES = os.path.join(
    REPO_ROOT, "docs", "仮説_露出と反応率_v1.json")


def _source(**over):
    row = {"tier": "L1", "ref": "O1", "date": "2025-01-01",
           "note": "テスト用の出所"}
    row.update(over)
    return row


def _prediction(**over):
    row = {"statement": "テスト用の予測", "metrics": ["views"],
           "window": "24h", "scope": "同一 account 内"}
    row.update(over)
    return row


def _hypothesis(**over):
    row = {
        "code": "T01",
        "claim": "テスト用の主張",
        "kind": "structural",
        "sources": [_source()],
        "predictions": [_prediction()],
        "refutation": "テスト用の反証条件",
        "sample_design": "M",
        "counter_hypothesis": "テスト用の対抗仮説",
        "scope": "同一 account 内",
        "state": "proposed",
        "proposed_by": "テスト（上書きされるはず）",
        "verifier": "テスト検証者",
        "created_at": NOW,
        "supersedes": None,
    }
    row.update(over)
    return row


def _register(payload, by="テスト"):
    return _thth(["topics", "record-hypothesis", "--json-stdin", "--by", by],
                  payload)


# --- 1. 登録して読める -------------------------------------------------------

def test_仮説を登録して読めること(thth_root):
    proc = _register(_hypothesis())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = _json(proc)
    assert out["ok"] is True
    assert out["code"] == "T01"
    hypothesis_id = out["hypothesis_id"]

    single = _json(_thth(["topics", "hypotheses", hypothesis_id]))
    assert single["hypothesis"]["claim"] == "テスト用の主張"
    # **--by で proposed_by を上書きする**（record-vocabulary の created_by と同じ）。
    assert single["hypothesis"]["proposed_by"] == "テスト"

    listed = _json(_thth(["topics", "hypotheses"]))
    assert listed["count"] == 1
    assert listed["hypotheses"][0]["hypothesis_id"] == hypothesis_id


# --- 2. accepted は登録できない ---------------------------------------------

def test_acceptedでは登録できないこと(thth_root):
    proc = _register(_hypothesis(state="accepted"))
    assert proc.returncode == 2
    out = _json(proc)
    assert out["error"]["code"] == "promotion_not_implemented"
    # **保存されていない**ことも確かめる。
    assert _json(_thth(["topics", "hypotheses"]))["count"] == 0


# --- 3. 出所に日付が無ければ断る --------------------------------------------

def test_出所に日付が無ければ断ること(thth_root):
    bad_source = {"tier": "L1", "ref": "O1", "note": "日付を忘れた"}
    proc = _register(_hypothesis(sources=[bad_source]))
    assert proc.returncode == 2
    out = _json(proc)
    assert out["error"]["code"] == "schema_error"
    assert "date" in out["error"]["message"] or "足りません" in out["error"]["message"]


# --- 4. 台帳に無い指標を予測に書くと断る ------------------------------------

def test_台帳に無い指標を予測に書くと断ること(thth_root):
    proc = _register(_hypothesis(
        predictions=[_prediction(metrics=["dwell_time"])]))
    assert proc.returncode == 2
    out = _json(proc)
    assert out["error"]["code"] == "schema_error"
    assert "dwell_time" in out["error"]["message"]
    assert "views" in out["error"]["message"]  # 使える指標の一覧が出る


# --- 5. U は shadow にできないが proposed としては登録できる ----------------

def test_Uの仮説はshadowにできないがproposedとしては登録できること(thth_root):
    u_row = _hypothesis(sample_design=models.UNIDENTIFIABLE, predictions=[],
                          state="shadow")
    proc = _register(u_row)
    assert proc.returncode == 2
    assert _json(proc)["error"]["code"] == "schema_error"

    proc2 = _register(dict(u_row, state="proposed"))
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    out2 = _json(proc2)
    assert out2["sample_design"] == "U"
    assert out2["state"] == "proposed"


# --- 6. 一覧で U が目立つ ----------------------------------------------------

def test_一覧でUが目立つこと(thth_root):
    _register(_hypothesis(code="T01", sample_design=models.UNIDENTIFIABLE,
                            predictions=[], state="proposed"))
    _register(_hypothesis(code="T02", sample_design="M", state="proposed"))

    listed = _json(_thth(["topics", "hypotheses"]))
    by_code = {r["code"]: r for r in listed["hypotheses"]}
    assert "［識別不能］" in by_code["T01"]["claim"]
    assert "［識別不能］" not in by_code["T02"]["claim"]

    # --state で絞れる。
    proposed_only = _json(_thth(["topics", "hypotheses", "--state", "proposed"]))
    assert proposed_only["count"] == 2


# --- 7. 本題：報告から作った 10 件が実際に登録できる ------------------------

def test_露出と反応率の仮説10件が登録できること(thth_root):
    with open(DOCS_HYPOTHESES, encoding="utf-8") as f:
        rows = json.load(f)
    assert len(rows) == 10

    registered_ids = set()
    for row in rows:
        proc = _register(row, by="codex（外部調査 2026-09-11 依頼分）")
        assert proc.returncode == 0, (
            f"{row.get('code')} が登録できません: {proc.stdout}{proc.stderr}")
        out = _json(proc)
        assert out["ok"] is True
        registered_ids.add(out["hypothesis_id"])

    # 10 件とも別の記録として保存されている（中身が縮退して同一 ID にならない）。
    assert len(registered_ids) == 10

    listed = _json(_thth(["topics", "hypotheses"]))
    assert listed["count"] == 10
    codes = sorted(r["code"] for r in listed["hypotheses"])
    assert codes == [f"H{i:02d}" for i in range(1, 11)]

    # **U の仮説は一覧で目立つ**（H04-H10 は U のはず）。
    u_codes = {r["code"] for r in listed["hypotheses"]
               if r["sample_design"] == models.UNIDENTIFIABLE}
    for r in listed["hypotheses"]:
        if r["code"] in u_codes:
            assert "［識別不能］" in r["claim"]
        else:
            assert "［識別不能］" not in r["claim"]
