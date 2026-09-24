"""3.8.1 visibility の既定は project の持ち主の組次第（設計 3.8.1）。

見るのは:
  - project が持ち主の組に入っていれば、CLI の `plaza post`（--open・--owner 無し）と
    MCP の `thth_plaza_post`（visibility 省略）の既定は owner に変わる。
  - 組に入っていない project は従前どおり既定が project（回帰固定）。
  - open は明示（`--open`／二段確認）が無ければ決して既定にならない（回帰固定・
    組があっても・open に参加していても）。
  - 明示の `--owner`／MCP の `visibility` があれば、組があってもそれに従う。
  - 既定で owner にしたときだけ応答に「範囲: owner（組 <名前> の既定）」と `visibility_default`。
"""
from __future__ import annotations

import json

from thth import cli, plaza
from tests.test_v340_plaza_store import owners  # noqa: F401  (fixture)
from tests.test_v340_plaza_mcp import _post, _tenant, _text
from tests.test_v380_plaza_owner import grouped  # noqa: F401  (fixture)


def _body(tmp_path, text="本文です。朝の投稿で試した", name="body.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _cli_post(tmp_path, capsys, account, title, *, extra=()):
    rc = cli.main(["plaza", "post", account, "--kind", "finding", "--title", title,
                   "--body-file", _body(tmp_path, name=title + ".txt"),
                   "--scope", "Threads の朝の投稿", "--by", "k", *extra, "--json"])
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else None)


def test_CLI_組が無いうちは既定がproject_組に入れば既定がowner(grouped, capsys, tmp_path):
    # 組が無い間は従前どおり既定が project（回帰固定）。
    rc, posted = _cli_post(tmp_path, capsys, "kopicha-threads", "組なし")
    assert rc == 0 and posted["scope"] == "project" and posted["visibility_default"] is None

    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    rc, posted = _cli_post(tmp_path, capsys, "kopicha-threads", "組あり")
    assert rc == 0 and posted["scope"] == "owner" and posted["visibility_default"] == "masaru"

    # 組に入っていない project（third）は既定が project のまま（回帰固定）。
    rc, posted = _cli_post(tmp_path, capsys, "third-threads", "組の外")
    assert rc == 0 and posted["scope"] == "project" and posted["visibility_default"] is None

    # 明示の --owner があれば、組が無くても（既に組があっても）それに従う。
    rc, posted = _cli_post(tmp_path, capsys, "kopicha-threads", "明示のowner",
                           extra=("--owner",))
    assert rc == 0 and posted["scope"] == "owner" and posted["visibility_default"] is None


def test_CLIの人向け応答に範囲の既定を明記する(grouped, capsys, tmp_path):
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    rc = cli.main(["plaza", "post", "kopicha-threads", "--kind", "finding", "--title", "人向け",
                   "--body-file", _body(tmp_path), "--scope", "Threads の朝の投稿", "--by", "k"])
    out = capsys.readouterr().out
    assert rc == 0 and "範囲: owner（組 masaru の既定）" in out
    # 明示の --owner のときは既定の案内を出さない（すでに自分で選んでいる）。
    rc = cli.main(["plaza", "post", "kopicha-threads", "--kind", "finding", "--title", "明示",
                   "--body-file", _body(tmp_path, name="明示.txt"), "--scope", "Threads の朝の投稿",
                   "--by", "k", "--owner"])
    out = capsys.readouterr().out
    assert rc == 0 and "範囲: owner（組" not in out


def test_openは組があっても参加していても既定にならない(grouped, capsys, tmp_path):
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    plaza.set_membership("kopicha", joined=True, by="operator")
    rc, posted = _cli_post(tmp_path, capsys, "kopicha-threads", "参加中でも既定はowner")
    assert rc == 0 and posted["scope"] == "owner"
    # --open は今までどおり明示 + 二段確認が要る（既定にはならない）。
    rc = cli.main(["plaza", "post", "kopicha-threads", "--kind", "finding", "--title", "openは明示",
                   "--body-file", _body(tmp_path, name="open.txt"), "--scope", "Threads の朝の投稿",
                   "--by", "k", "--open", "--json"])
    out = json.loads(capsys.readouterr().out)
    # 一段目は何も書かずに終わる（approve の一段目と同じ rc 1・`_emit_preview`）。
    assert rc == 1 and out["report_type"] == "plaza_open_preview" and out["opened"] is False


def test_MCPのvisibility省略は組次第_明示すれば従う(tmp_path, monkeypatch):
    _root, server = _tenant(tmp_path, monkeypatch)
    # 組が無ければ従前どおり project（回帰固定）。
    posted = json.loads(_text(_post(server)))
    assert posted["scope"] == "project" and posted["visibility_default"] is None

    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    # 省略すれば owner が既定。
    defaulted = json.loads(_text(_post(server, title="省略で owner")))
    assert defaulted["scope"] == "owner" and defaulted["visibility_default"] == "masaru"

    # 明示すればそれに従う（組があっても project のまま）。
    explicit = json.loads(_text(_post(server, title="明示は project", visibility="project")))
    assert explicit["scope"] == "project" and explicit["visibility_default"] is None

    # open は MCP の口に無い（既定にもならない・従前どおり）。
    denied = _post(server, title="open は無い", visibility="open")
    assert denied["isError"] and _text(denied) == "open_requires_cli"
