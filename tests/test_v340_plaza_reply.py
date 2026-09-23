"""施策の広場 第 4 段——返信・結果の更新・判定・追試（設計 3.4.0 §2・§9-2）。

見るのは:
  - 返信の種類: comment（本文必須）・tried（自分の measure へのリンク必須）・agree・
    disagree（理由必須）・trial（追試・結果が必須）。
  - **追試は再現した・再現しなかった・試していないを同じ重さで数える**（分母つき・
    一覧・1 件・比較の表のすべて）。リンクした自分の measure の観測も比較の表に並ぶ。
  - 判定（adopted・dropped・inconclusive）は理由必須・measure だけ・置いた持ち主だけ。
  - project 範囲の 1 件に他の持ち主は返せない（無い id と同じ断り）。
  - 返信も 1 日 30 件に数える・変更ログは presence-only。
"""
from __future__ import annotations

import datetime
import json

import pytest

from thth import admin_log, cli, jst, plaza
from tests.test_analytics_comparison import NOW
from tests.test_v340_plaza_observe import measured, post_measure, declaration  # noqa: F401
from tests.test_v340_plaza_store import owners, post, viewer  # noqa: F401  (fixture)


def reply(plaza_id, account="kopicha-bsky", *, kind="comment", text="うちでは差が無かった",
          by="bsky-session", **kwargs):
    return plaza.reply(plaza_id, account=account, kind=kind, text=text, by=by,
                       viewer=viewer(account), **kwargs)


def test_同じ持ち主の別の媒体から返信できる(owners):
    first = post(title="朝は伸びる")
    result = reply(first["plaza_id"], text="Bluesky でも朝が良い REPLY-BODY-MARK")
    assert result["n_replies"] == 1 and result["kind"] == "comment"
    shown = plaza.show(first["plaza_id"], viewer("kopicha-threads"))
    row = shown["replies"][0]
    assert row["own"] is True and row["text"] == "Bluesky でも朝が良い REPLY-BODY-MARK"
    assert row["account"] == "kopicha-bsky" and row["medium"] == "bluesky"
    entries, _ = admin_log.read(account="kopicha-bsky", event="plaza_replied")
    assert len(entries) == 1 and "REPLY-BODY-MARK" not in json.dumps(entries, ensure_ascii=False)


def test_project範囲の1件に他の持ち主は返せない(owners):
    first = post(title="kopicha だけの話")
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        reply(first["plaza_id"], "other-threads", by="other")
    assert plaza.show(first["plaza_id"], viewer("kopicha-threads"))["n_replies"] == 0


def test_返信の種類ごとの必須(owners):
    first = post(title="問いの冒頭")
    measure = post(kind="measure", title="うちの施策", account="kopicha-bsky")
    with pytest.raises(plaza.PlazaError, match="^reason_required$"):
        reply(first["plaza_id"], kind="disagree", text="  ")
    with pytest.raises(plaza.PlazaError, match="^invalid_post$"):
        reply(first["plaza_id"], kind="comment", text=None)
    with pytest.raises(plaza.PlazaError, match="^measure_link_required$"):
        reply(first["plaza_id"], kind="tried", text=None)
    with pytest.raises(plaza.PlazaError, match="^measure_link_invalid$"):
        reply(first["plaza_id"], kind="comment", measure_id=measure["plaza_id"])
    with pytest.raises(plaza.PlazaError, match="^measure_link_invalid$"):
        reply(first["plaza_id"], kind="tried", text=None, measure_id=first["plaza_id"])
    with pytest.raises(plaza.PlazaError, match="^invalid_reply_kind$"):
        reply(first["plaza_id"], kind="like")
    with pytest.raises(plaza.PlazaError, match="^invalid_trial_result$"):
        reply(first["plaza_id"], kind="trial", text=None)
    with pytest.raises(plaza.PlazaError, match="^invalid_trial_result$"):
        reply(first["plaza_id"], kind="comment", result="reproduced")
    assert reply(first["plaza_id"], kind="agree", text=None)["n_replies"] == 1
    assert reply(first["plaza_id"], kind="tried", text=None,
                 measure_id=measure["plaza_id"])["n_replies"] == 2
    with pytest.raises(plaza.PlazaError, match="^secret_detected$"):
        reply(first["plaza_id"], text="ghp_" + "d" * 36)


def test_他の持ち主の施策にはリンクできない(owners):
    first = post(title="問いの冒頭", account="other-threads", by="other")
    theirs = post(kind="measure", title="other の施策", account="other-threads", by="other")
    mine_view = plaza.Viewer({"other-threads": "other"})
    with pytest.raises(plaza.PlazaError, match="^measure_link_invalid$"):
        plaza.reply(first["plaza_id"], account="other-threads", kind="tried", text=None, by="o",
                    viewer=mine_view, measure_id=post(kind="measure", title="kopicha の施策")["plaza_id"])
    assert plaza.reply(first["plaza_id"], account="other-threads", kind="tried", text=None, by="o",
                       viewer=mine_view, measure_id=theirs["plaza_id"])["n_replies"] == 1


def test_追試は再現しなかった報告も同じ重さで数える(measured, owners):
    base = post_measure(measured)
    plaza_id = base["plaza_id"]
    mine = post(kind="measure", account="kopicha-mstdn", title="Mastodon で追試", by="mstdn",
                declarations=[declaration("kopicha-threads", baseline=measured["kopicha-threads"][0],
                                          changed=measured["kopicha-threads"][1],
                                          decl_id="trial-threads")], min_n=1, now=NOW)
    for account, result, extra in (("kopicha-mstdn", "not_reproduced", {"measure_id": mine["plaza_id"]}),
                                   ("kopicha-bsky", "reproduced", {}),
                                   ("kopicha-bsky", "not_reproduced", {}),
                                   ("kopicha-threads", "not_tried", {})):
        answer = plaza.reply(plaza_id, account=account, kind="trial", text=None, result=result,
                             by="s", viewer=viewer(account), now=NOW, **extra)
    assert answer["trials"] == {"reproduced": 1, "not_reproduced": 2, "not_tried": 1, "denominator": 4}
    shown = plaza.show(plaza_id, viewer("kopicha-threads"))
    assert shown["trials"] == answer["trials"]
    assert [row["result"] for row in shown["replies"]] == [
        "not_reproduced", "reproduced", "not_reproduced", "not_tried"]
    listed = next(row for row in plaza.list_posts(viewer("kopicha-bsky"))["posts"]
                  if row["plaza_id"] == plaza_id)
    assert listed["trials"] == answer["trials"]
    table = shown["comparison"]
    assert table["trials"] == answer["trials"]
    # リンクした自分の measure の観測も列に並ぶ（source: trial）。
    assert [(c["source"], c["plaza_id"]) for c in table["columns"]][-1] == ("trial", mine["plaza_id"])


def test_追試の人向けの表示(measured, owners, capsys):
    base = post_measure(measured)
    for result in ("reproduced", "not_reproduced"):
        plaza.reply(base["plaza_id"], account="kopicha-bsky", kind="trial", text="1 週間",
                    result=result, by="s", viewer=viewer("kopicha-bsky"), now=NOW)
    assert cli.main(["plaza", "show", base["plaza_id"], "--as", "kopicha"]) == 0
    out = capsys.readouterr().out
    assert "[追試 2 件] 再現した 1・再現しなかった 1・試していない 0" in out
    assert "追試  s（bluesky）  再現しなかった" in out
    assert cli.main(["plaza", "list", "kopicha"]) == 0
    assert "追試 2 件（再現した 1・再現しなかった 1・試していない 0）" in capsys.readouterr().out


def test_判定は理由必須_measureだけ_置いた持ち主だけ(measured, owners):
    base = post_measure(measured)
    finding = post(title="気づき")
    with pytest.raises(plaza.PlazaError, match="^reason_required$"):
        plaza.update(base["plaza_id"], account="kopicha-threads", by="s", verdict="adopted",
                     viewer=viewer("kopicha-threads"))
    with pytest.raises(plaza.PlazaError, match="^invalid_verdict$"):
        plaza.update(base["plaza_id"], account="kopicha-threads", by="s", verdict="great",
                     reason="r", viewer=viewer("kopicha-threads"))
    with pytest.raises(plaza.PlazaError, match="^not_a_measure$"):
        plaza.update(finding["plaza_id"], account="kopicha-threads", by="s", verdict="adopted",
                     reason="r", viewer=viewer("kopicha-threads"))
    with pytest.raises(plaza.PlazaError, match="^nothing_to_update$"):
        plaza.update(base["plaza_id"], account="kopicha-threads", by="s",
                     viewer=viewer("kopicha-threads"))
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.update(base["plaza_id"], account="other-threads", by="o", verdict="dropped",
                     reason="r", viewer=viewer("other-threads"))
    # 同じ持ち主の別の媒体からは判定できる。
    result = plaza.update(base["plaza_id"], account="kopicha-bsky", by="s", verdict="adopted",
                          reason="Threads で +11。Bluesky は差なし", viewer=viewer("kopicha-bsky"))
    assert result["verdict"] == "adopted"
    shown = plaza.show(base["plaza_id"], viewer("kopicha-mstdn"))
    assert shown["verdict"]["verdict"] == "adopted"
    assert shown["verdict"]["reason"] == "Threads で +11。Bluesky は差なし"
    entries, _ = admin_log.read(account="kopicha-bsky", event="plaza_updated")
    assert entries[-1]["diff"]["verdict"] == [None, "adopted"]
    assert "Threads で +11" not in json.dumps(entries, ensure_ascii=False)


def test_返信も1日の件数に数える(owners, monkeypatch):
    monkeypatch.setattr(plaza, "DAILY_LIMIT", 3)
    first = post(title="朝", account="kopicha-bsky")
    reply(first["plaza_id"], text="一つ目")
    reply(first["plaza_id"], text="二つ目")
    # 置いた 1 件＋返信 2 件で 3 件。
    with pytest.raises(plaza.PlazaError, match="^plaza_rate_limited$"):
        reply(first["plaza_id"], text="三つ目")


def test_CLIの返信と判定(measured, owners, tmp_path, capsys):
    base = post_measure(measured)
    note = tmp_path / "note.txt"
    note.write_text("Mastodon では変わらなかった", encoding="utf-8")
    assert cli.main(["plaza", "reply", base["plaza_id"], "--as", "kopicha-mstdn", "--kind", "trial",
                     "--result", "not_reproduced", "--text-file", str(note), "--by", "m",
                     "--json"]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["trials"]["not_reproduced"] == 1 and answer["trials"]["denominator"] == 1
    assert cli.main(["plaza", "update", base["plaza_id"], "--as", "kopicha-threads",
                     "--verdict", "inconclusive", "--reason", "媒体で結果が割れた", "--by", "t"]) == 0
    assert "判定 判断保留" in capsys.readouterr().out
    assert cli.main(["plaza", "reply", base["plaza_id"], "--as", "other-threads", "--kind",
                     "agree", "--by", "o", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["plaza_not_found"]
