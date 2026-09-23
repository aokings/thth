"""施策の広場 段 9——open は二段確認（masaru 裁定 09-23・approve と同じ線）。

見るのは:
  - `--open` の post・`--visibility open` の update の 1 回目は、道具が他人の情報を落とした
    後の「他の持ち主に見える本文と観測」と digest を返すだけで open にしない（置き場にも
    変更ログにも書かない）。
  - `--confirm <digest>` の 2 回目で初めて open になる。digest が違う・中身が変わったら
    `open_digest_mismatch`。
  - project の範囲に置く・project に戻すのは一段でよい。
"""
from __future__ import annotations

import json

import pytest

from thth import admin_log, cli, plaza, plaza_redact
from tests.test_v340_plaza_open import ledger, join, OTHER_NAME, OTHER_TEXT  # noqa: F401
from tests.test_v340_plaza_store import owners, viewer  # noqa: F401  (fixture)

BODY = f"{OTHER_NAME} さんの返信「{OTHER_TEXT}」を見て、朝の問いを続けた"


def first_step(**kwargs):
    base = dict(kind="finding", title="朝の問い", body=BODY, scope_note="Threads の朝", by="s",
                visibility="open")
    base.update(kwargs)
    return plaza.post("kopicha-threads", **base), base


def test_postの一段目は見える中身とdigestだけで何も置かない(owners, ledger):
    join("kopicha", "other")
    preview, base = first_step()
    assert preview["report_type"] == "plaza_open_preview" and preview["opened"] is False
    assert preview["plaza_id"] is None and len(preview["digest"]) == 20
    visible = preview["visible_to_other_owners"]
    dumped = json.dumps(visible, ensure_ascii=False)
    # 見せるのは落とした後の写し（他の持ち主が読むものと同じ）。
    assert OTHER_NAME not in dumped and OTHER_TEXT not in dumped
    assert plaza_redact.OTHER_TEXT in visible["body"] and visible["owner"] == "kopicha"
    assert "kopicha-threads" not in dumped
    assert plaza.admin_list()["n"] == 0
    assert admin_log.read(event="plaza_posted")[0] == []
    # 二段目で初めて置かれ、他の持ち主の読みは一段目で見せたものと同じ。
    opened = plaza.post("kopicha-threads", confirm=preview["digest"], **base)
    assert opened["scope"] == "open"
    theirs = plaza.show(opened["plaza_id"], viewer("other-threads"))
    assert theirs["body"] == visible["body"] and theirs["title"] == visible["title"]


def test_digestが違えば置かない(owners, ledger):
    join("kopicha")
    preview, base = first_step()
    with pytest.raises(plaza.PlazaError, match="^open_digest_mismatch$"):
        plaza.post("kopicha-threads", confirm="0" * 20, **base)
    # 一段目のあとで本文を変えたら、同じ digest でも置かない。
    changed = dict(base, body=BODY + "（追記）")
    with pytest.raises(plaza.PlazaError, match="^open_digest_mismatch$"):
        plaza.post("kopicha-threads", confirm=preview["digest"], **changed)
    assert plaza.admin_list()["n"] == 0


def test_updateでopenにするのも二段(owners, ledger):
    join("kopicha", "other")
    inside = plaza.post("kopicha-threads", kind="finding", title="内輪", body=BODY,
                        scope_note="朝", by="s")
    preview = plaza.update(inside["plaza_id"], account="kopicha-threads", by="s",
                           visibility="open", viewer=viewer("kopicha-threads"))
    assert preview["report_type"] == "plaza_open_preview" and preview["plaza_id"] == inside["plaza_id"]
    assert OTHER_TEXT not in json.dumps(preview, ensure_ascii=False)
    stored = plaza.STORE.get(inside["plaza_id"])
    assert stored["scope"] == "project" and stored["open_copy"] is None
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.show(inside["plaza_id"], viewer("other-threads"))
    with pytest.raises(plaza.PlazaError, match="^open_digest_mismatch$"):
        plaza.update(inside["plaza_id"], account="kopicha-threads", by="s", visibility="open",
                     viewer=viewer("kopicha-threads"), confirm="f" * 20)
    assert plaza.STORE.get(inside["plaza_id"])["scope"] == "project"
    done = plaza.update(inside["plaza_id"], account="kopicha-threads", by="s", visibility="open",
                        viewer=viewer("kopicha-threads"), confirm=preview["digest"])
    assert done["scope"] == "open"
    assert plaza.show(inside["plaza_id"], viewer("other-threads"))["view"] == "open"
    # project に戻すのは一段。
    back = plaza.update(inside["plaza_id"], account="kopicha-threads", by="s",
                        visibility="project", viewer=viewer("kopicha-threads"))
    assert back["scope"] == "project"


def test_CLIの一段目は見える中身を人に見せて終わる(owners, ledger, tmp_path, capsys):
    join("kopicha")
    body = tmp_path / "b.txt"
    body.write_text(BODY, encoding="utf-8")
    argv = ["plaza", "post", "kopicha-threads", "--kind", "finding", "--title", "朝の問い",
            "--body-file", str(body), "--scope", "Threads の朝", "--open", "--by", "s"]
    assert cli.main(argv) == 1
    out = capsys.readouterr().out
    assert out.startswith("一段目: まだ open にしていません。他の持ち主には次のとおり見えます")
    assert OTHER_TEXT not in out and plaza_redact.OTHER_TEXT in out
    digest = out.split("digest: ")[1].split()[0]
    assert f"--confirm {digest}" in out
    assert plaza.admin_list()["n"] == 0
    assert cli.main(argv + ["--confirm", digest, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["scope"] == "open"
    inside = plaza.post("kopicha-bsky", kind="question", title="内輪の問い", body="b",
                        scope_note="Bluesky", by="b")
    update = ["plaza", "update", inside["plaza_id"], "--as", "kopicha-bsky", "--visibility", "open",
              "--by", "b", "--json"]
    assert cli.main(update) == 1
    step = json.loads(capsys.readouterr().out)
    assert step["opened"] is False
    assert cli.main(update + ["--confirm", "x" * 20]) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["open_digest_mismatch"]
    assert cli.main(update + ["--confirm", step["digest"]]) == 0


def test_projectの範囲は一段で置ける(owners):
    result = plaza.post("kopicha-threads", kind="finding", title="t", body="b", scope_note="s", by="s")
    assert result["report_type"] == "plaza_posted" and result["scope"] == "project"
