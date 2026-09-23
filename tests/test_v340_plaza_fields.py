"""施策の広場 段 2 補——利用者側の設計材料（設計 §9・報告 r20260923-7c297144）。

見るのは:
  - finding の細目 `kind_detail`（pattern・rule・pitfall・tool_tip）。finding 以外には付けない。
  - 媒体・企画の範囲（`--scope`・自由文 120 字）は**全部の書き込みで必須**。
  - `how`（数字を出し直せる thth の命令）は measure で必須。形（`thth ` で始まる 1 行）と
    秘密だけを検査し、**実行はしない**。
  - 見立てと事実の印 `evidence_level`: 人や LLM が名乗れるのは stated と hypothesis だけ。
    **observed を名乗ると断る**（observed は道具が付けた観測があるときだけ）。
"""
from __future__ import annotations

import json
import subprocess

import pytest

from thth import cli, plaza
from tests.test_v340_plaza_store import owners, post, viewer  # noqa: F401  (fixture)


def test_findingの細目(owners):
    result = post(kind_detail="pitfall")
    shown = plaza.show(result["plaza_id"], viewer("kopicha-bsky"))
    assert shown["kind_detail"] == "pitfall"
    listed = plaza.list_posts(viewer("kopicha-threads"))["posts"][0]
    assert listed["kind_detail"] == "pitfall"
    for kind, detail in (("finding", "anecdote"), ("question", "rule"), ("measure", "pattern")):
        with pytest.raises(plaza.PlazaError, match="^invalid_kind_detail$"):
            post(kind=kind, kind_detail=detail, title=f"{kind} {detail}")
    # 細目は任意（無くても finding は置ける）。
    assert plaza.show(post(title="細目なし")["plaza_id"], viewer("kopicha-threads"))["kind_detail"] is None


def test_範囲は全部の書き込みで必須(owners):
    for kind in ("finding", "question", "measure"):
        for value in (None, "", "   "):
            with pytest.raises(plaza.PlazaError, match="^scope_required$"):
                post(kind=kind, scope_note=value)
    with pytest.raises(plaza.PlazaError, match="^post_too_long$"):
        post(scope_note="範" * (plaza.SCOPE_NOTE_MAX + 1))
    with pytest.raises(plaza.PlazaError, match="^invalid_post$"):
        post(scope_note="一行目\n二行目")
    shown = plaza.show(post(scope_note="Mastodon の夜の投稿")["plaza_id"], viewer("kopicha-threads"))
    assert shown["scope_note"] == "Mastodon の夜の投稿"


def test_howはmeasureで必須_形だけ見て実行しない(owners, monkeypatch):
    with pytest.raises(plaza.PlazaError, match="^how_required$"):
        post(kind="measure", how=None)
    for bad in ("ls -la", "thth", "rm -rf / thth measured x", "thth measured a\nthth run b",
                "thth " + "x" * plaza.HOW_MAX):
        with pytest.raises(plaza.PlazaError, match="^invalid_how$"):
            post(kind="measure", how=bad, title=f"how {len(bad)}")
    with pytest.raises(plaza.PlazaError, match="^secret_detected$"):
        post(kind="measure", how="thth measured kopicha-threads --token ghp_" + "c" * 36)
    # 実行しない（subprocess を呼ばない）。
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("how を実行した"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("how を実行した"))
    result = post(kind="measure", how="thth measured kopicha-threads --since 2026-09-20")
    shown = plaza.show(result["plaza_id"], viewer("kopicha-threads"))
    assert shown["how"] == "thth measured kopicha-threads --since 2026-09-20"
    # finding の how は任意。
    assert post(title="how 付きの気づき", how="thth posts kopicha-threads")["plaza_id"]


def test_observedは名乗れない(owners):
    with pytest.raises(plaza.PlazaError, match="^observed_is_tool_only$"):
        post(evidence_level="observed")
    with pytest.raises(plaza.PlazaError, match="^observed_is_tool_only$"):
        post(kind="measure", evidence_level="observed", title="名乗る施策")
    with pytest.raises(plaza.PlazaError, match="^invalid_evidence_level$"):
        post(evidence_level="certain")


def test_名乗った印と道具が付ける印(owners):
    stated = post(title="本文だけ")
    hypothesis = post(title="見立て", evidence_level="hypothesis")
    assert stated["evidence_level"] == "stated" and hypothesis["evidence_level"] == "hypothesis"
    # 宣言の無い measure は観測が無いので observed にならない（名乗った印のまま）。
    measure = post(kind="measure", title="宣言なしの施策", evidence_level="hypothesis")
    assert measure["evidence_level"] == "hypothesis"
    rows = {row["title"]: row["evidence_level"]
            for row in plaza.list_posts(viewer("kopicha-threads"))["posts"]}
    assert rows == {"本文だけ": "stated", "見立て": "hypothesis", "宣言なしの施策": "hypothesis"}


def test_CLIの旗(owners, tmp_path, capsys):
    body = tmp_path / "b.txt"
    body.write_text("朝は伸びる気がする", encoding="utf-8")
    base = ["plaza", "post", "kopicha-mstdn", "--kind", "finding", "--title", "朝",
            "--body-file", str(body), "--by", "s", "--json"]
    assert cli.main(base) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["scope_required"]
    assert cli.main(base + ["--scope", "Mastodon の朝", "--evidence-level", "observed"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["cannot_say"] == ["observed_is_tool_only"]
    assert captured.err.startswith("observed_is_tool_only: observed（観測あり）は道具だけ")
    assert cli.main(base + ["--scope", "Mastodon の朝", "--kind-detail", "tool_tip",
                            "--evidence-level", "hypothesis", "--how", "thth posts kopicha-mstdn"]) == 0
    plaza_id = json.loads(capsys.readouterr().out)["plaza_id"]
    assert cli.main(["plaza", "show", plaza_id, "--as", "kopicha"]) == 0
    out = capsys.readouterr().out
    assert "印: 見立て・道具のコツ  範囲: Mastodon の朝" in out
    assert "出し直し: thth posts kopicha-mstdn（道具は実行していません）" in out
    measure = ["plaza", "post", "kopicha-mstdn", "--kind", "measure", "--title", "施策",
               "--body-file", str(body), "--scope", "Mastodon の朝", "--by", "s", "--json"]
    assert cli.main(measure) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["how_required"]
