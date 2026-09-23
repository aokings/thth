"""3.5.1 件 3: 広場の公開（open）の二段確認の穴（3.4.0 検収で残した穴）。

二段確認が掛かっていたのは project → open の切り替えだけだった:
  (a) 既に open の 1 件の更新（判定の理由・観測の取り直し）で、他の持ち主に見える写しが
      二段確認なしで作り直された。
  (b) open の 1 件に付ける返信（他の持ち主にも写しで見える）も二段確認なしだった。

見るのは:
  - (a) 写しが変わる更新は 1 回目で変わらない（見える姿と digest だけ）・digest 違いは断る・
    同じ digest の 2 回目で変わる。写しが変わらない更新（判定のコードだけ・同じ数字の取り直し・
    project に戻す）は一段。MCP から写しを変える更新は `open_requires_cli`。
  - (b) open の 1 件への返信は 1 回目で足さない・digest 違いは断る・2 回目で足す。project 範囲の
    返信は一段。MCP からの open への返信は `open_requires_cli`（読めない 1 件は従前どおり
    `plaza_not_found` が先）。
"""
from __future__ import annotations

import datetime
import json

import pytest

from thth import cli, plaza
from tests.test_analytics_comparison import NOW, seed
from tests.test_v340_plaza_mcp import _tenant, _text
from tests.test_v340_plaza_observe import DECIDED, measured, post_measure  # noqa: F401  (fixture)
from tests.test_v340_plaza_open import join
from tests.test_v340_plaza_store import owners, post, viewer  # noqa: F401  (fixture)

LATER = NOW + datetime.timedelta(hours=1)
LATER2 = NOW + datetime.timedelta(hours=2)


def _update(plaza_id, **kwargs):
    base = dict(account="kopicha-threads", by="s", viewer=viewer("kopicha-threads"))
    base.update(kwargs)
    return plaza.update(plaza_id, **base)


def _open_measure(measured):
    join("kopicha", "other")
    return post_measure(measured, visibility="open")


# ------------------------------------------------------------ (a) open の更新

def test_a_判定の理由は写しが変わるので二段(measured, owners):
    opened = _open_measure(measured)
    before = json.dumps(plaza.STORE.get(opened["plaza_id"]), sort_keys=True)
    preview = _update(opened["plaza_id"], verdict="adopted", reason="朝の問いは効いた")
    assert preview["report_type"] == "plaza_open_preview" and preview["already_open"] is True
    assert preview["visible_to_other_owners"]["verdict_reason"] == "朝の問いは効いた"
    # 1 回目は置き場を変えない。
    assert json.dumps(plaza.STORE.get(opened["plaza_id"]), sort_keys=True) == before
    assert plaza.show(opened["plaza_id"], viewer("other-threads"))["verdict"] is None
    # digest 違いは断る（何も変えない）。
    with pytest.raises(plaza.PlazaError, match="^open_digest_mismatch$"):
        _update(opened["plaza_id"], verdict="adopted", reason="朝の問いは効いた", confirm="0" * 20)
    with pytest.raises(plaza.PlazaError, match="^open_digest_mismatch$"):
        _update(opened["plaza_id"], verdict="adopted", reason="別の理由", confirm=preview["digest"])
    assert json.dumps(plaza.STORE.get(opened["plaza_id"]), sort_keys=True) == before
    # 同じ digest の 2 回目で変わる。
    done = _update(opened["plaza_id"], verdict="adopted", reason="朝の問いは効いた",
                   confirm=preview["digest"])
    assert done["report_type"] == "plaza_updated" and done["verdict"] == "adopted"
    theirs = plaza.show(opened["plaza_id"], viewer("other-threads"))
    assert theirs["verdict"]["verdict"] == "adopted"


def test_a_観測の取り直しで数字が変われば二段(measured, owners):
    opened = _open_measure(measured)
    seed(owners["kopicha-threads"], "th-a0", DECIDED + datetime.timedelta(days=1), value=40)
    preview = _update(opened["plaza_id"], refresh=True, now=LATER)
    assert preview["report_type"] == "plaza_open_preview" and preview["already_open"] is True
    assert len(plaza.STORE.get(opened["plaza_id"])["observations"]) == 1
    with pytest.raises(plaza.PlazaError, match="^open_digest_mismatch$"):
        _update(opened["plaza_id"], refresh=True, now=LATER2, confirm="f" * 20)
    assert len(plaza.STORE.get(opened["plaza_id"])["observations"]) == 1
    # 2 回目は別の時刻に打っても、見える姿が同じなら通る（観測の時刻は写しに入らない）。
    done = _update(opened["plaza_id"], refresh=True, now=LATER2, confirm=preview["digest"])
    assert done["refreshed"] and done["n_observations"] == 2
    column = plaza.show(opened["plaza_id"], viewer("other-threads"))["observation"]["latest"]["columns"][0]
    assert column["metrics"]["views"]["observational_difference"] == 21


def test_a_写しが変わらない更新は一段(measured, owners):
    opened = _open_measure(measured)
    # 同じ数字の取り直し（写しは同じ）。
    same = _update(opened["plaza_id"], refresh=True, now=LATER)
    assert same["report_type"] == "plaza_updated" and same["n_observations"] == 2
    # 判定を入れる（理由が写しに入るので二段）。
    preview = _update(opened["plaza_id"], verdict="adopted", reason="効いた")
    _update(opened["plaza_id"], verdict="adopted", reason="効いた", confirm=preview["digest"])
    # 判定のコードだけを変える（理由の写しは同じ）なら一段。
    code_only = _update(opened["plaza_id"], verdict="inconclusive", reason="効いた")
    assert code_only["report_type"] == "plaza_updated" and code_only["verdict"] == "inconclusive"
    # project に戻す（他の持ち主から見えなくなる）のも一段。
    back = _update(opened["plaza_id"], visibility="project")
    assert back["report_type"] == "plaza_updated" and back["scope"] == "project"


def test_a_project範囲の更新は従前どおり一段(measured, owners):
    inside = post_measure(measured)
    done = _update(inside["plaza_id"], verdict="dropped", reason="効かなかった")
    assert done["report_type"] == "plaza_updated" and done["verdict"] == "dropped"


def test_a_写しが変わる更新はMCPから受けない(measured, owners):
    opened = _open_measure(measured)
    with pytest.raises(plaza.PlazaError, match="^open_requires_cli$"):
        _update(opened["plaza_id"], verdict="adopted", reason="効いた", via="mcp")
    assert plaza.STORE.get(opened["plaza_id"])["verdict"] is None
    # 写しが変わらない取り直しは MCP からでも一段で通る。
    assert _update(opened["plaza_id"], refresh=True, now=LATER, via="mcp")["n_observations"] == 2


def test_a_CLIの一段目は反映前の見える姿を出す(measured, owners, capsys):
    opened = _open_measure(measured)
    argv = ["plaza", "update", opened["plaza_id"], "--as", "kopicha-threads", "--verdict", "adopted",
            "--reason", "朝の問いは効いた", "--by", "s"]
    assert cli.main(argv) == 1
    out = capsys.readouterr().out
    assert out.startswith("一段目: まだ反映していません。更新すると、他の持ち主には次のとおり見えます")
    assert "判定の理由: 朝の問いは効いた" in out and "判定: " in out
    digest = next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("digest: "))
    assert f"--confirm {digest}" in out
    assert plaza.STORE.get(opened["plaza_id"])["verdict"] is None
    assert cli.main(argv + ["--confirm", digest, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "adopted"


# ------------------------------------------------------------ (b) open への返信

def _reply(plaza_id, account="other-threads", **kwargs):
    base = dict(account=account, kind="comment", text="うちでは夜のほうが伸びた", by="o",
                viewer=viewer(account))
    base.update(kwargs)
    return plaza.reply(plaza_id, **base)


def test_b_openへの返信は二段(owners):
    join("kopicha", "other")
    opened = post(visibility="open", title="朝の問い")
    preview = _reply(opened["plaza_id"])
    assert preview["report_type"] == "plaza_reply_open_preview" and preview["replied"] is False
    seen = preview["visible_to_other_owners"]
    assert seen["owner"] == "other" and seen["text"] == "うちでは夜のほうが伸びた"
    assert "by" not in seen and "account" not in seen
    assert plaza.STORE.get(opened["plaza_id"])["replies"] == []
    with pytest.raises(plaza.PlazaError, match="^open_digest_mismatch$"):
        _reply(opened["plaza_id"], confirm="0" * 20)
    with pytest.raises(plaza.PlazaError, match="^open_digest_mismatch$"):
        _reply(opened["plaza_id"], text="別の文", confirm=preview["digest"])
    assert plaza.STORE.get(opened["plaza_id"])["replies"] == []
    done = _reply(opened["plaza_id"], confirm=preview["digest"])
    assert done["report_type"] == "plaza_replied" and done["n_replies"] == 1
    mine = plaza.show(opened["plaza_id"], viewer("kopicha-threads"))
    assert mine["replies"][0]["text"] == "うちでは夜のほうが伸びた"


def test_b_同じ持ち主の返信もopenなら二段_project範囲は一段(owners):
    join("kopicha", "other")
    opened = post(visibility="open", title="朝の問い")
    first = _reply(opened["plaza_id"], account="kopicha-bsky", kind="agree", text=None)
    assert first["report_type"] == "plaza_reply_open_preview"
    inside = post(title="内輪の話")
    done = _reply(inside["plaza_id"], account="kopicha-bsky", kind="agree", text=None)
    assert done["report_type"] == "plaza_replied" and done["n_replies"] == 1


def test_b_読めない1件はopenと明かさずplaza_not_found(owners):
    join("kopicha")  # other は不参加——kopicha の open は other から読めない
    opened = post(visibility="open", title="朝の問い")
    for via in ("cli", "mcp"):
        with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
            _reply(opened["plaza_id"], via=via)


def test_b_CLIの一段目と二段目(owners, capsys, tmp_path):
    join("kopicha", "other")
    opened = post(visibility="open", title="朝の問い")
    text = tmp_path / "r.txt"
    text.write_text("うちでは夜のほうが伸びた", encoding="utf-8")
    argv = ["plaza", "reply", opened["plaza_id"], "--as", "other-threads", "--kind", "comment",
            "--text-file", str(text), "--by", "o"]
    assert cli.main(argv) == 1
    out = capsys.readouterr().out
    assert out.startswith("一段目: まだ返信していません。この返信は open の 1 件に付くので")
    assert "名義: other（threads）" in out and "うちでは夜のほうが伸びた" in out
    digest = next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("digest: "))
    assert plaza.STORE.get(opened["plaza_id"])["replies"] == []
    assert cli.main(argv + ["--confirm", "x" * 20, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["open_digest_mismatch"]
    assert cli.main(argv + ["--confirm", digest, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["n_replies"] == 1


def test_b_MCPからopenへの返信は受けない(tmp_path, monkeypatch):
    root, server = _tenant(tmp_path, monkeypatch)
    plaza.set_membership("kopicha", joined=True, by="operator")
    plaza.set_membership("other", joined=True, by="operator")
    preview = plaza.post("second", kind="finding", title="other の公開", body="夜が伸びる",
                         scope_note="other の夜", by="other-person", visibility="open")
    theirs = plaza.post("second", kind="finding", title="other の公開", body="夜が伸びる",
                        scope_note="other の夜", by="other-person", visibility="open",
                        confirm=preview["digest"])
    denied = server.call_tool("thth_plaza_reply", {"plaza_id": theirs["plaza_id"], "account": "first",
                                                   "kind": "comment", "text": "同感です"})
    assert denied["isError"] and _text(denied) == "open_requires_cli"
    assert plaza.STORE.get(theirs["plaza_id"])["replies"] == []
    # project 範囲の自分の 1 件には MCP から一段で返せる。
    mine = json.loads(_text(server.call_tool("thth_plaza_post", {
        "account": "first", "kind": "finding", "title": "朝は伸びる", "body": "朝の問いかけ",
        "scope": "Threads の朝の投稿"})))
    answered = json.loads(_text(server.call_tool("thth_plaza_reply", {
        "plaza_id": mine["plaza_id"], "account": "third", "kind": "agree"})))
    assert answered["n_replies"] == 1
