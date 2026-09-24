"""3.8.0 A 持ち主（owner）の範囲（設計 3.8.0 §A）。

見るのは:
  - 見える範囲に project と open の間の 1 段 owner（同じ持ち主の組の全 project）。
  - **組は管理者が登録する**（`thth admin plaza owner set <組> <project>… --by`・変更ログ・
    既定は組なし）。組に登録していない project には見せない（無い id と同じ断り）。
  - owner への書き込みは一段（二段確認の対象外）。open と二段確認の既存の処理は変えない。
  - 組を解けば、owner の範囲の書き込みは相手から見えない。
"""
from __future__ import annotations

import datetime
import json

import pytest

from thth import admin_log, cli, jst, morning, plaza
from tests.conftest import init_real_repo
from tests.test_v340_plaza_store import owners, post, reply  # noqa: F401  (fixture)

TABLE = {"kopicha-threads": "kopicha", "kopicha-bsky": "kopicha", "kopicha-mstdn": "kopicha",
         "other-threads": "other", "third-threads": "third"}


def viewer(*names):
    return plaza.Viewer({name: TABLE[name] for name in names})


@pytest.fixture
def grouped(owners, isolated_account_factory, tmp_path):
    """kopicha・other・third の 3 つの持ち主。組は既定で無い。"""
    third_repo = init_real_repo(tmp_path, "third")
    owners["third-threads"] = isolated_account_factory(
        "third-threads", project="third", repo_dir=third_repo, handle="third_shop")
    return owners


def test_既定は組なし_ownerには置けず他のprojectには見えない(grouped):
    assert plaza.owner_groups() == {}
    with pytest.raises(plaza.PlazaError, match="^plaza_owner_unregistered$"):
        post(visibility="owner", title="組が無い")
    inside = post(title="PROJECT-ONLY")
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.show(inside["plaza_id"], viewer("other-threads"))
    assert plaza.list_posts(viewer("other-threads"))["n"] == 0
    assert "plaza_owner_unregistered" in plaza.REASONS and "plaza_owner_unregistered" in plaza.NEXT


def test_組に登録したprojectだけにownerの書き込みが見える(grouped):
    result = plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    assert result["projects"] == ["kopicha", "other"] and result["changed"] is True
    posted = post(visibility="owner", title="OWNER-TITLE", body="OWNER-BODY 朝は伸びる")
    assert posted["scope"] == "owner" and posted["report_type"] == "plaza_posted"
    shown = plaza.show(posted["plaza_id"], viewer("other-threads"))
    assert shown["view"] == "owner" and shown["body"].startswith("OWNER-BODY")
    assert shown["account"] == "kopicha-threads"
    listed = plaza.list_posts(viewer("other-threads"))
    assert [row["plaza_id"] for row in listed["posts"]] == [posted["plaza_id"]]
    assert listed["posts"][0]["view"] == "owner" and listed["owner_projects"] == ["kopicha"]
    # 組に入っていない project には見せない（無い id と同じ断り・一覧にも出ない）。
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.show(posted["plaza_id"], viewer("third-threads"))
    assert plaza.list_posts(viewer("third-threads"))["n"] == 0
    third_text = json.dumps(plaza.list_posts(viewer("third-threads")), ensure_ascii=False)
    assert "OWNER-TITLE" not in third_text
    # project の範囲の書き込みは組の相手にも見せない（従前どおり）。
    inside = post(title="PROJECT-ONLY-2")
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.show(inside["plaza_id"], viewer("other-threads"))


def test_組を解けばownerの書き込みは相手から見えない(grouped):
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    posted = post(visibility="owner", title="あとで見えなくなる")
    assert plaza.show(posted["plaza_id"], viewer("other-threads"))["view"] == "owner"
    removed = plaza.unset_owner("masaru", by="operator")
    assert removed["projects"] == ["kopicha", "other"]
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.show(posted["plaza_id"], viewer("other-threads"))
    # 置いた project からは見える。
    assert plaza.show(posted["plaza_id"], viewer("kopicha-bsky"))["view"] == "own"
    with pytest.raises(plaza.PlazaError, match="^plaza_owner_not_found$"):
        plaza.unset_owner("masaru", by="operator")


def test_組から外したprojectにも見えない(grouped):
    plaza.set_owner("masaru", ["kopicha", "other", "third"], by="operator")
    posted = post(visibility="owner", title="3 つの組")
    assert plaza.show(posted["plaza_id"], viewer("third-threads"))["view"] == "owner"
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.show(posted["plaza_id"], viewer("third-threads"))
    assert plaza.show(posted["plaza_id"], viewer("other-threads"))["view"] == "owner"


def test_組の検査と1つのprojectは1つの組だけ(grouped):
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    with pytest.raises(plaza.PlazaError, match="^plaza_owner_conflict$"):
        plaza.set_owner("another", ["other", "third"], by="operator")
    with pytest.raises(plaza.PlazaError, match="^invalid_owner$"):
        plaza.set_owner("../x", ["third"], by="operator")
    with pytest.raises(plaza.PlazaError, match="^invalid_owner$"):
        plaza.set_owner("solo", [], by="operator")
    with pytest.raises(plaza.PlazaError, match="^invalid_account$"):
        plaza.set_owner("solo", ["no-such-project"], by="operator")
    with pytest.raises(plaza.PlazaError, match="^by_required$"):
        plaza.set_owner("solo", ["third"], by=None)
    # account 名ならその project。同じ組の置き直しは変化なし。
    again = plaza.set_owner("masaru", ["kopicha-bsky", "other"], by="operator")
    assert again["projects"] == ["kopicha", "other"] and again["changed"] is False


def test_変更ログはpresence_onlyで組の名前と数だけ(grouped):
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    plaza.unset_owner("masaru", by="operator")
    rows = [row for row in admin_log.read()[0] if row["event"].startswith("plaza_owner")]
    assert [row["event"] for row in rows] == ["plaza_owner_set", "plaza_owner_unset"]
    assert rows[0]["account"] == "masaru" and rows[0]["diff"]["n_projects"] == [0, 2]
    assert "kopicha" not in json.dumps(rows, ensure_ascii=False)


def test_ownerへの書き込みは一段_openの二段確認は変わらない(grouped):
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    # owner は一段（preview を返さずにそのまま置く）。
    result = plaza.post("kopicha-threads", kind="finding", title="一段", body="本文",
                        by="k", scope_note="Threads の朝", visibility="owner")
    assert result["report_type"] == "plaza_posted" and result["scope"] == "owner"
    # open は従前どおり参加が要り、二段確認。
    with pytest.raises(plaza.PlazaError, match="^plaza_not_joined$"):
        plaza.post("kopicha-threads", kind="finding", title="open", body="本文",
                   by="k", scope_note="Threads の朝", visibility="open")
    plaza.set_membership("kopicha", joined=True, by="operator")
    first = plaza.post("kopicha-threads", kind="finding", title="open", body="本文",
                       by="k", scope_note="Threads の朝", visibility="open")
    assert first["report_type"] == "plaza_open_preview"


def test_範囲の切り替え_projectからownerは一段_ownerからopenは二段(grouped):
    inside = post(title="切り替え")
    with pytest.raises(plaza.PlazaError, match="^plaza_owner_unregistered$"):
        plaza.update(inside["plaza_id"], account="kopicha-threads", by="k",
                     viewer=viewer("kopicha-threads"), visibility="owner")
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    done = plaza.update(inside["plaza_id"], account="kopicha-threads", by="k",
                        viewer=viewer("kopicha-threads"), visibility="owner")
    assert done["report_type"] == "plaza_updated" and done["scope"] == "owner"
    assert plaza.show(inside["plaza_id"], viewer("other-threads"))["view"] == "owner"
    for project in ("kopicha", "third"):
        plaza.set_membership(project, joined=True, by="operator")
    preview = plaza.update(inside["plaza_id"], account="kopicha-threads", by="k",
                           viewer=viewer("kopicha-threads"), visibility="open")
    assert preview["report_type"] == "plaza_open_preview"
    opened = plaza.update(inside["plaza_id"], account="kopicha-threads", by="k",
                          viewer=viewer("kopicha-threads"), visibility="open",
                          confirm=preview["digest"])
    assert opened["scope"] == "open"
    assert plaza.show(inside["plaza_id"], viewer("third-threads"))["view"] == "open"
    # open から owner へ戻すと写しを捨て、組の外からは見えない（一段）。
    back = plaza.update(inside["plaza_id"], account="kopicha-threads", by="k",
                        viewer=viewer("kopicha-threads"), visibility="owner")
    assert back["scope"] == "owner" and plaza.STORE.get(inside["plaza_id"])["open_copy"] is None
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.show(inside["plaza_id"], viewer("third-threads"))


def test_組の相手は返信できるが更新はできない(grouped):
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    posted = post(kind="measure", visibility="owner", title="組の施策")
    replied = reply(posted["plaza_id"], account="other-threads", kind="trial",
                    result="reproduced", text="うちでも再現", by="o", viewer=viewer("other-threads"))
    assert replied["report_type"] == "plaza_replied"
    shown = plaza.show(posted["plaza_id"], viewer("kopicha-threads"))
    assert shown["replies"][0]["text"] == "うちでも再現" and shown["replies"][0]["own"] is False
    with pytest.raises(plaza.PlazaError, match="^not_owner$"):
        plaza.update(posted["plaza_id"], account="other-threads", by="o",
                     viewer=viewer("other-threads"), verdict="adopted", reason="効いた")


def test_observeの0段に組の新着(grouped, monkeypatch):
    monkeypatch.setattr(morning, "_world", lambda *a, **k: morning.cell(cannot_say="no_watch_words"))
    now = jst.now_jst()
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    post(title="組の気づき", visibility="owner", now=now - datetime.timedelta(hours=1))
    post(title="PROJECT-INSIDE", now=now - datetime.timedelta(hours=1))
    cell = next(s for s in morning.build("other-threads", now=now, mark=False)["sections"]
                if s["section"] == "tool")["value"]["plaza"]
    assert cell["owner_new"] == 1 and cell["owner_denominator"] == 1
    assert cell["project_new"] == 0 and cell["owner_reason"] is None
    third = next(s for s in morning.build("third-threads", now=now, mark=False)["sections"]
                 if s["section"] == "tool")["value"]["plaza"]
    assert third["owner_new"] is None and third["owner_reason"] == "plaza_owner_unregistered"


def test_退出でownerの書き込みは消える(grouped):
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    posted = post(visibility="owner", title="退出で消える")
    counts = plaza.purge_account("kopicha-threads", by="leave")
    assert counts["removed"] == 1
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.show(posted["plaza_id"], viewer("other-threads"))


def test_CLIで組を登録してownerに置く(grouped, capsys, tmp_path):
    rc = cli.main(["admin", "plaza", "owner", "set", "masaru", "kopicha", "other",
                   "--by", "operator", "--json"])
    assert rc == 0 and json.loads(capsys.readouterr().out)["projects"] == ["kopicha", "other"]
    rc = cli.main(["admin", "plaza", "owner", "list"])
    assert rc == 0 and "masaru: kopicha・other" in capsys.readouterr().out
    body = tmp_path / "body.md"
    body.write_text("組の本文", encoding="utf-8")
    rc = cli.main(["plaza", "post", "kopicha-threads", "--kind", "finding", "--title", "CLI の組",
                   "--body-file", str(body), "--scope", "Threads の朝", "--owner", "--by", "k",
                   "--json"])
    posted = json.loads(capsys.readouterr().out)
    assert rc == 0 and posted["scope"] == "owner"
    rc = cli.main(["plaza", "post", "kopicha-threads", "--kind", "finding", "--title", "両方",
                   "--body-file", str(body), "--scope", "Threads の朝", "--owner", "--open",
                   "--by", "k"])
    assert rc == 2 and "invalid_visibility" in capsys.readouterr().err
    rc = cli.main(["admin", "plaza", "list", "--json"])
    assert rc == 0 and json.loads(capsys.readouterr().out)["owners"] == {"masaru": ["kopicha", "other"]}
    rc = cli.main(["admin", "plaza", "owner", "unset", "masaru", "--by", "operator"])
    assert rc == 0 and "見えなくなりました" in capsys.readouterr().out
