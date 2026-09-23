"""施策の広場 第 5 段——open・参加・非表示・他人の情報を落とす処理・退出（設計 3.4.0 §3・§6・§9-7）。

見るのは（**他の持ち主の情報を漏らさない**が最優先）:
  - 参加は持ち主ごと（既定は不参加）。不参加の持ち主は open を読めず、出せない。
    open の 1 件は、置いた持ち主と読む持ち主の両方が参加しているときだけ見える。
  - 他の持ち主が読むのは写しだけ: 他人の返信の本文・username・author_key・SNS の URL を
    落とし、account 名・by・宣言の投稿 ID・除外された他人の ID は出さない。自分の投稿は
    先頭 60 字と permalink まで。
  - open に出す文に `@名前`（自分の handle 以外）があれば `third_party_handle` で断る。
  - project に戻したら他の持ち主から見えない。非表示は置いた持ち主と管理者だけ。
  - 退出で project 範囲の書き込みは消え、open は選択で残す（既定は消す）。
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import pytest

from thth import admin_log, cli, jst, leave, plaza, plaza_redact, sent, accounts
from tests.test_analytics_comparison import NOW
from tests.test_v340_plaza_observe import (measured, post_measure, declaration,  # noqa: F401
                                           DECIDED)
from tests.test_v340_plaza_store import owners, post, reply, viewer  # noqa: F401  (fixture)

OTHER_NAME = "tanaka_tea_lover"
OTHER_TEXT = "田中の返信の本文そのもの、苦味がちょうどいいです"
AUTHOR_KEY = "0123456789abcdef"


@pytest.fixture
def ledger(owners):
    """kopicha の返信の台帳に他人の返信 1 件（username と本文）。"""
    repo = Path(owners["kopicha-threads"]["repo_dir"])
    path = repo / "data" / "sns" / "replies" / "th-a0.ndjson"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": "reply", "post_id": "th-a0", "reply_id": "r1",
                                "username": OTHER_NAME, "text": OTHER_TEXT,
                                "timestamp": "2026-09-12T08:00:00+09:00"},
                               ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def join(*projects):
    for project in projects:
        plaza.set_membership(project, joined=True, by="operator")


def leaky_body():
    return (f"@{OTHER_NAME} さんの返信「{OTHER_TEXT}」が嬉しかった。"
            f"仮名 {AUTHOR_KEY} の人。https://www.threads.net/@{OTHER_NAME}/post/abc")


# -------------------------------------------------------------- 参加

def test_参加は既定で不参加_管理者が入れる(owners):
    assert plaza.members() == frozenset()
    with pytest.raises(plaza.PlazaError, match="^plaza_not_joined$"):
        post(visibility="open")
    result = plaza.set_membership("kopicha-bsky", joined=True, by="operator")
    assert result["project"] == "kopicha" and result["changed"] is True
    assert plaza.set_membership("kopicha", joined=True, by="operator")["changed"] is False
    assert post(visibility="open")["scope"] == "open"
    entries, _ = admin_log.read(account="kopicha", event="plaza_joined")
    assert len(entries) == 1
    left = plaza.set_membership("kopicha", joined=False, by="operator")
    assert left["changed"] and plaza.members() == frozenset()
    assert len(admin_log.read(account="kopicha", event="plaza_left")[0]) == 1
    with pytest.raises(plaza.PlazaError, match="^invalid_account$"):
        plaza.set_membership("nosuchproject", joined=True, by="operator")
    with pytest.raises(plaza.PlazaError, match="^by_required$"):
        plaza.set_membership("kopicha", joined=True, by=None)


def test_不参加の持ち主はopenを読めない(owners):
    join("kopicha")
    opened = post(visibility="open", title="open の気づき")
    with pytest.raises(plaza.PlazaError, match="^plaza_not_joined$"):
        plaza.list_posts(viewer("other-threads"), open_only=True)
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.show(opened["plaza_id"], viewer("other-threads"))
    join("other")
    listed = plaza.list_posts(viewer("other-threads"), open_only=True)
    assert [row["plaza_id"] for row in listed["posts"]] == [opened["plaza_id"]]
    # 置いた持ち主が外れたら、他の持ち主からは見えない。
    plaza.set_membership("kopicha", joined=False, by="operator")
    assert plaza.list_posts(viewer("other-threads"), open_only=True)["n"] == 0
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.show(opened["plaza_id"], viewer("other-threads"))


# ------------------------------------------------- 他人の情報を落とす

def test_openの写しは他人の情報を落とす(measured, owners, ledger):
    join("kopicha", "other")
    ids = measured["kopicha-threads"]
    sent.write(accounts.state_dir_for("kopicha-threads"), post_id="th-a0",
               text=f"朝の一杯は問いから。{OTHER_NAME} さんありがとう" + "。" * 80,
               body_hash="0" * 8, sent_at=jst.iso(DECIDED + datetime.timedelta(days=1)))
    decl = declaration("kopicha-threads", baseline=ids[0], changed=ids[1] + ["someone-else-post"])
    opened = post(kind="measure", title="問いの冒頭", body=leaky_body().replace(f"@{OTHER_NAME}", OTHER_NAME),
                  declarations=[decl], min_n=1, now=NOW, visibility="open",
                  how="thth study-report question.json", by="kopicha-session")
    reply(opened["plaza_id"], account="kopicha-bsky", kind="comment",
          text=f"{OTHER_NAME} さんは Bluesky にもいる", by="bsky-session",
          viewer=viewer("kopicha-bsky"), now=NOW)
    theirs = plaza.show(opened["plaza_id"], viewer("other-threads"))
    dumped = json.dumps(theirs, ensure_ascii=False)
    for leak in (OTHER_NAME, OTHER_TEXT, AUTHOR_KEY, "someone-else-post", "th-a0", "th-b0",
                 "kopicha-threads", "kopicha-bsky", "kopicha-session", "bsky-session",
                 "https://www.threads.net"):
        assert leak not in dumped, leak
    assert theirs["view"] == "open" and theirs["owner"] == "kopicha"
    assert plaza_redact.OTHER_TEXT in theirs["body"] and plaza_redact.OTHER_KEY in theirs["body"]
    assert plaza_redact.OTHER_URL in theirs["body"]
    assert theirs["replies"][0]["owner"] == "kopicha" and "by" not in theirs["replies"][0]
    assert plaza_redact.OTHER_NAME in theirs["replies"][0]["text"]
    column = theirs["observation"]["latest"]["columns"][0]
    assert "account" not in column and column["excluded_counts"] == {"unknown_or_unowned_id": 1}
    previews = [row["preview"] for row in column["posts"] if row["preview"]]
    assert previews and all(len(p.rstrip("…")) <= plaza.PREVIEW_CHARS for p in previews)
    # 置いた持ち主は原本を読む（落とす前の本文・account 名）。
    mine = plaza.show(opened["plaza_id"], viewer("kopicha-mstdn"))
    assert OTHER_TEXT in mine["body"] and mine["account"] == "kopicha-threads"
    assert mine["open_copy"]["masked"] >= 4


def test_openに出す文の他人の名前の形は断る(owners, ledger):
    join("kopicha", "other")
    with pytest.raises(plaza.PlazaError, match="^third_party_handle$"):
        post(visibility="open", body="@someone_else さんの型を真似た")
    with pytest.raises(plaza.PlazaError, match="^third_party_handle$"):
        post(visibility="open", scope_note="@someone_else の読者層")
    # 自分の handle は許す（threads・bluesky・mastodon の形）。
    ok = post(visibility="open", title="自分の handle",
              body="@kopicha_tea と @kopicha.bsky.social と @kopicha@mastodon.example で試した")
    assert ok["scope"] == "open"
    # project の範囲なら置ける。open に切り替えるときに断る。
    inside = post(title="内輪の話", body="@someone_else さんの型を真似た")
    with pytest.raises(plaza.PlazaError, match="^third_party_handle$"):
        plaza.update(inside["plaza_id"], account="kopicha-threads", by="s", visibility="open",
                     viewer=viewer("kopicha-threads"))
    assert plaza.show(inside["plaza_id"], viewer("kopicha-threads"))["scope"] == "project"
    # open の 1 件への返信も同じ。メールアドレスの @ は名前の形ではない。
    with pytest.raises(plaza.PlazaError, match="^third_party_handle$"):
        reply(ok["plaza_id"], account="other-threads", kind="comment",
              text="@someone_else さんも言っていた", by="o", viewer=viewer("other-threads"))
    assert reply(ok["plaza_id"], account="other-threads", kind="comment",
                 text="連絡は info@example.com へ", by="o",
                 viewer=viewer("other-threads"))["n_replies"] == 1


def test_台帳が読めなければopenの写しを作らない(owners, ledger):
    join("kopicha")
    ledger.write_text("{壊れている\n", encoding="utf-8")
    with pytest.raises(plaza.PlazaError, match="^redaction_unavailable$"):
        post(visibility="open")
    # project の範囲なら置ける。
    assert post()["scope"] == "project"


# ------------------------------------------- 他の持ち主との往復

def test_他の持ち主の返信は写しだけが見える(owners, ledger):
    join("kopicha", "other")
    opened = post(visibility="open", title="朝の問い")
    reply(opened["plaza_id"], account="other-threads", kind="disagree",
          text="うちでは夜のほうが伸びた。other-private-mark", by="other-person",
          viewer=viewer("other-threads"))
    mine = plaza.show(opened["plaza_id"], viewer("kopicha-threads"))
    row = mine["replies"][0]
    assert row["own"] is False and row["owner"] == "other" and "by" not in row
    assert "account" not in row and "other-person" not in json.dumps(mine, ensure_ascii=False)
    theirs = plaza.show(opened["plaza_id"], viewer("other-threads"))
    assert theirs["replies"][0]["own"] is True and theirs["replies"][0]["by"] == "other-person"


def test_他の持ち主は更新と判定をできない(measured, owners, ledger):
    join("kopicha", "other")
    opened = post_measure(measured, visibility="open")
    with pytest.raises(plaza.PlazaError, match="^not_owner$"):
        plaza.update(opened["plaza_id"], account="other-threads", by="o", verdict="dropped",
                     reason="効かない", viewer=viewer("other-threads"))
    with pytest.raises(plaza.PlazaError, match="^not_owner$"):
        plaza.update(opened["plaza_id"], account="other-threads", by="o", visibility="project",
                     viewer=viewer("other-threads"))


def test_projectに戻したら他の持ち主から見えない(owners, ledger):
    join("kopicha", "other")
    opened = post(visibility="open", title="戻す")
    assert plaza.show(opened["plaza_id"], viewer("other-threads"))["view"] == "open"
    plaza.update(opened["plaza_id"], account="kopicha-threads", by="s", visibility="project",
                 viewer=viewer("kopicha-threads"))
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.show(opened["plaza_id"], viewer("other-threads"))
    assert plaza.STORE.get(opened["plaza_id"])["open_copy"] is None
    assert plaza.list_posts(viewer("other-threads"), open_only=True)["n"] == 0


def test_openの一覧は写しの題だけ(owners, ledger):
    join("kopicha", "other")
    post(visibility="open", title=f"{OTHER_NAME} の型", body="本文")
    listed = plaza.list_posts(viewer("other-threads"), open_only=True)
    dumped = json.dumps(listed, ensure_ascii=False)
    assert OTHER_NAME not in dumped and "kopicha-threads" not in dumped
    assert listed["posts"][0]["title"] == f"{plaza_redact.OTHER_NAME} の型"


# ---------------------------------------------------------------- 非表示

def test_管理者の非表示(owners, ledger, capsys):
    join("kopicha", "other")
    opened = post(visibility="open", title="消されるもの")
    assert cli.main(["admin", "plaza", "hide", opened["plaza_id"], "--reason", "個人名が入っていた",
                     "--by", "operator", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["hidden"]["reason"] == "個人名が入っていた"
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.show(opened["plaza_id"], viewer("other-threads"))
    mine = plaza.show(opened["plaza_id"], viewer("kopicha-threads"))
    assert mine["hidden"]["reason"] == "個人名が入っていた"
    with pytest.raises(plaza.PlazaError, match="^plaza_hidden$"):
        reply(opened["plaza_id"], account="kopicha-bsky", kind="agree", text=None, by="b",
              viewer=viewer("kopicha-bsky"))
    with pytest.raises(plaza.PlazaError, match="^already_hidden$"):
        plaza.hide(opened["plaza_id"], by="operator", reason="again")
    with pytest.raises(plaza.PlazaError, match="^reason_required$"):
        plaza.hide(opened["plaza_id"], by="operator", reason=" ")
    assert len(admin_log.read(account="kopicha-threads", event="plaza_hidden")[0]) == 1
    assert cli.main(["admin", "plaza", "list", "--all", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["posts"][0]["hidden"] is True and listed["joined_projects"] == ["kopicha", "other"]


def test_管理者のCLIで参加と退出(owners, capsys):
    assert cli.main(["admin", "plaza", "join", "kopicha", "--by", "operator"]) == 0
    assert "参加: kopicha" in capsys.readouterr().out
    assert cli.main(["admin", "plaza", "leave", "kopicha", "--by", "operator", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["joined"] is False
    assert cli.main(["admin", "plaza", "join", "kopicha", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["by_required"]


def test_CLIのopenの一覧(owners, ledger, capsys, tmp_path):
    join("kopicha")
    body = tmp_path / "b.txt"
    body.write_text("朝の問い", encoding="utf-8")
    argv = ["plaza", "post", "kopicha-threads", "--kind", "finding", "--title", "open",
            "--body-file", str(body), "--scope", "朝", "--open", "--by", "s", "--json"]
    # 一段目は見える中身と digest だけ（rc 1・何も置かない）。
    assert cli.main(argv) == 1
    preview = json.loads(capsys.readouterr().out)
    assert preview["opened"] is False and plaza.admin_list()["n"] == 0
    assert cli.main(argv + ["--confirm", preview["digest"]]) == 0
    capsys.readouterr()
    assert cli.main(["plaza", "list", "other", "--open", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["plaza_not_joined"]
    join("other")
    assert cli.main(["plaza", "list", "other", "--open", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["n"] == 1 and listed["posts"][0]["view"] == "open"


# ---------------------------------------------------------------- 退出

def _seed_for_leave(owners, ledger):
    join("kopicha", "other")
    project_post = post(title="内輪", account="kopicha-threads")
    open_post = post(title="公開", account="kopicha-threads", visibility="open",
                     body="朝の問いが効いた")
    others = post(title="bsky の公開", account="kopicha-bsky", visibility="open")
    reply(others["plaza_id"], account="kopicha-threads", kind="comment", text="同感",
          by="t", viewer=viewer("kopicha-threads"))
    reply(open_post["plaza_id"], account="other-threads", kind="agree", text=None, by="o",
          viewer=viewer("other-threads"))
    return project_post, open_post, others


def test_退出で書き込みは消える_既定はopenも消す(owners, ledger):
    project_post, open_post, others = _seed_for_leave(owners, ledger)
    counts = plaza.purge_account("kopicha-threads", by="operator")
    assert counts == {"removed": 2, "anonymized": 0, "replies_removed": 1, "replies_anonymized": 0}
    for gone in (project_post, open_post):
        with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
            plaza.show(gone["plaza_id"], plaza.Viewer(admin=True))
    assert plaza.show(others["plaza_id"], viewer("kopicha-bsky"))["n_replies"] == 0
    # 何度呼んでも同じ（退出の再試行）。
    assert plaza.purge_account("kopicha-threads", by="operator")["removed"] == 0


def test_退出でopenを残すと退出した持ち主の名義(owners, ledger):
    project_post, open_post, others = _seed_for_leave(owners, ledger)
    counts = plaza.purge_account("kopicha-threads", keep_open=True, by="operator")
    assert counts == {"removed": 1, "anonymized": 1, "replies_removed": 0, "replies_anonymized": 1}
    kept = plaza.show(open_post["plaza_id"], plaza.Viewer(admin=True))
    assert kept["account"] is None and kept["by"] == plaza.LEFT_LABEL
    assert "kopicha-threads" not in json.dumps(plaza.STORE.get(open_post["plaza_id"]),
                                               ensure_ascii=False)
    theirs = plaza.show(open_post["plaza_id"], viewer("other-threads"))
    assert theirs["body"] == "朝の問いが効いた"
    reply_row = plaza.show(others["plaza_id"], viewer("kopicha-bsky"))["replies"][0]
    assert reply_row["owner"] == "kopicha" and reply_row.get("by") != "t"


def test_account_leaveが広場の書き込みを片づける(owners, ledger, monkeypatch):
    _seed_for_leave(owners, ledger)
    from thth import deletion
    monkeypatch.setattr(leave, "_run_locked", lambda account, by: {"account": account})
    monkeypatch.setattr(deletion, "complete_for", lambda account, row: None)
    called = {}
    real = plaza.purge_account

    def spy(account, **kwargs):
        called.update(kwargs, account=account)
        return real(account, **kwargs)

    monkeypatch.setattr(plaza, "purge_account", spy)
    leave.run("kopicha-threads", by="operator", plaza_open="keep")
    assert called == {"account": "kopicha-threads", "keep_open": True, "by": "operator"}
    with pytest.raises(ValueError, match="^invalid_plaza_open$"):
        leave.run("kopicha-threads", by="operator", plaza_open="maybe")

    def broken(account, **kwargs):
        raise plaza.PlazaError("plaza_store_unavailable")

    monkeypatch.setattr(plaza, "purge_account", broken)
    with pytest.raises(ValueError, match="^plaza_cleanup_incomplete$"):
        leave.run("kopicha-threads", by="operator")


def test_account_leaveの旗(owners):
    parser = cli.build_parser()
    args = parser.parse_args(["account", "leave", "kopicha-threads", "--by", "op",
                              "--plaza-open", "keep"])
    assert args.plaza_open == "keep"
    assert parser.parse_args(["account", "leave", "x", "--by", "op"]).plaza_open == "delete"
