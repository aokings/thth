"""施策の広場 第 2 段——利用者の CLI（設計 3.4.0 §4）。

見るのは:
  - `thth plaza post → list → show --as` の往復（人向けと --json）。
  - **project 範囲の書き込みは他の project から読めない**（list・show の両方・leak probe）。
    無い id と読めない id は同じ `plaza_not_found`。
  - `show`・`reply`・`update` は `--as`（誰として読むか）が必須。
  - 断りは理由と次の一手（stderr）・--json は `cannot_say`。
"""
from __future__ import annotations

import json

from thth import cli, plaza
from tests.test_v340_plaza_store import owners, BODY  # noqa: F401  (fixture)

SECRET_TITLE = "KOPICHA-ONLY-TITLE"
SECRET_BODY = "KOPICHA-ONLY-BODY 朝の問いかけ"


def _body(tmp_path, text=BODY, name="body.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _post(tmp_path, capsys, account="kopicha-threads", *, title=SECRET_TITLE, body=SECRET_BODY,
          kind="finding", extra=()):
    rc = cli.main(["plaza", "post", account, "--kind", kind, "--title", title,
                   "--body-file", _body(tmp_path, body), "--scope", "Threads の朝の投稿",
                   "--by", "kopicha-session", *extra,
                   "--json"])
    captured = capsys.readouterr()
    return rc, json.loads(captured.out) if captured.out.strip() else None, captured.err


def test_post_list_show_の往復(owners, tmp_path, capsys):
    rc, posted, _ = _post(tmp_path, capsys)
    assert rc == 0 and posted["scope"] == "project" and posted["kind"] == "finding"
    plaza_id = posted["plaza_id"]

    assert cli.main(["plaza", "list", "kopicha", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [row["plaza_id"] for row in listed["posts"]] == [plaza_id]
    # 一覧に本文は出さない（show で読む）。
    assert SECRET_BODY not in json.dumps(listed, ensure_ascii=False)

    # 同じ持ち主の別の媒体（account）として読める。
    assert cli.main(["plaza", "show", plaza_id, "--as", "kopicha-bsky", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["body"] == SECRET_BODY and shown["view"] == "own"
    assert shown["observation"] is None and shown["observation_reason"] == "not_a_measure"


def test_人向けの出力(owners, tmp_path, capsys):
    assert cli.main(["plaza", "post", "kopicha-threads", "--kind", "question", "--title",
                     "Bluesky でタグは効いていますか", "--body-file", _body(tmp_path),
                     "--scope", "Bluesky の全投稿", "--by", "s"]) == 0
    out = capsys.readouterr().out
    assert "広場に置きました: p20260909-" in out and "問い" in out
    plaza_id = out.split("広場に置きました: ")[1].split("（")[0]
    assert cli.main(["plaza", "list", "kopicha-mstdn"]) == 0
    assert plaza_id in capsys.readouterr().out
    assert cli.main(["plaza", "show", plaza_id, "--as", "kopicha"]) == 0
    shown = capsys.readouterr().out
    assert "[本文（人や LLM が書いた。数字は道具が確かめていない）]" in shown
    assert "[観測（道具が付けた数字）] なし（施策ではないので道具は数字を付けていません）" in shown


def test_他のprojectからは一覧にも1件にも出ない(owners, tmp_path, capsys):
    rc, posted, _ = _post(tmp_path, capsys)
    assert rc == 0
    plaza_id = posted["plaza_id"]
    for target in ("other", "other-threads"):
        assert cli.main(["plaza", "list", target, "--json"]) == 0
        out = capsys.readouterr().out
        listed = json.loads(out)
        assert listed["n"] == 0 and listed["posts"] == []
        assert SECRET_TITLE not in out and SECRET_BODY not in out and plaza_id not in out
    # 読めない id と無い id は同じ断り（在ることを漏らさない）。
    assert cli.main(["plaza", "show", plaza_id, "--as", "other-threads", "--json"]) == 2
    captured = capsys.readouterr()
    denied = json.loads(captured.out)
    assert denied == {"cannot_say": ["plaza_not_found"]}
    assert SECRET_TITLE not in captured.err + captured.out
    assert cli.main(["plaza", "show", "p20260909-00000000", "--as", "other-threads", "--json"]) == 2
    assert json.loads(capsys.readouterr().out) == denied


def test_show_reply_update_は読む側の名指しが必須(owners, tmp_path, capsys):
    rc, posted, _ = _post(tmp_path, capsys)
    for argv in (["plaza", "show", posted["plaza_id"]],
                 ["plaza", "reply", posted["plaza_id"], "--kind", "agree", "--by", "s"],
                 ["plaza", "update", posted["plaza_id"], "--refresh", "--by", "s"]):
        try:
            cli.main(argv)
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("--as が無いのに通った")
        assert "--as" in capsys.readouterr().err


def test_断りは理由と次の一手(owners, tmp_path, capsys):
    rc = cli.main(["plaza", "post", "kopicha-threads", "--kind", "finding", "--title", "t",
                   "--body-file", _body(tmp_path), "--scope", "朝", "--json"])
    captured = capsys.readouterr()
    assert rc == 2 and json.loads(captured.out)["cannot_say"] == ["by_required"]
    assert captured.err.startswith("by_required: --by")
    rc, payload, err = _post(tmp_path, capsys, body="ghp_" + "b" * 36)
    assert rc == 2 and payload["cannot_say"] == ["secret_detected"]
    assert "ghp_" not in err


def test_重複の断りは既存のidを添える(owners, tmp_path, capsys):
    rc, first, _ = _post(tmp_path, capsys)
    rc, again, err = _post(tmp_path, capsys)
    assert rc == 2 and again == {"cannot_say": ["duplicate_post"], "plaza_id": first["plaza_id"]}


def test_知らないaccountは断る(owners, tmp_path, capsys):
    rc, payload, _ = _post(tmp_path, capsys, account="nobody-threads")
    assert rc == 2 and payload["cannot_say"] == ["account_unavailable"]
    assert cli.main(["plaza", "list", "nobody", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["invalid_account"]


def test_project範囲の一覧はprojectとaccountのどちらでも同じ(owners, tmp_path, capsys):
    _post(tmp_path, capsys)
    _post(tmp_path, capsys, account="kopicha-bsky", title="Bluesky の気づき")
    views = []
    for target in ("kopicha", "kopicha-threads", "kopicha-mstdn"):
        assert cli.main(["plaza", "list", target, "--json"]) == 0
        views.append([row["plaza_id"] for row in json.loads(capsys.readouterr().out)["posts"]])
    assert views[0] == views[1] == views[2] and len(views[0]) == 2
    assert plaza.list_posts(plaza.viewer_for_target("other"))["n"] == 0
