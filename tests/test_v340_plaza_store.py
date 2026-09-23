"""施策の広場 第 1 段——置き場と記録（設計 3.4.0 §2・§6）。

見るのは:
  - 置き場は `state/_plaza/`（0600・0700）で、SNS の台帳には 1 バイトも書かない。
  - 骨は報告の口と共通（`thth/private_store.py`）——報告の口の試験はそのまま通る。
  - 秘密らしき値が混ざっていたら**置かずに** `secret_detected`。
  - 長さ（title 120・本文 8,000）・重複（24 時間・同じ account の既存 id だけ）・
    件数（1 account 1 日 30 件・置く＋返信）・by 必須・種類。
  - 変更ログ `plaza_posted` は presence-only（本文は入らない）。
  - 壊れた 1 件は数えて飛ばす（管理者にだけ件数）。
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import stat

import pytest

from thth import admin_log, jst, plaza, private_store, report_inbox
from tests.conftest import init_real_repo

BODY = "冒頭を問いにしたら返信が増えた気がする。\n朝の投稿で試した"


# ------------------------------------------------------------ 共通の舞台

@pytest.fixture
def owners(isolated_account_factory, tmp_path):
    """持ち主 2 組: kopicha（threads・bluesky・mastodon）と other（threads）。"""
    kopicha_repo = init_real_repo(tmp_path, "kopicha")
    other_repo = init_real_repo(tmp_path, "other")
    made = {
        "kopicha-threads": isolated_account_factory(
            "kopicha-threads", project="kopicha", repo_dir=kopicha_repo, handle="kopicha_tea"),
        "kopicha-bsky": isolated_account_factory(
            "kopicha-bsky", project="kopicha", media="bluesky", repo_dir=kopicha_repo,
            handle="kopicha.bsky.social"),
        "kopicha-mstdn": isolated_account_factory(
            "kopicha-mstdn", project="kopicha", media="mastodon", repo_dir=kopicha_repo,
            handle="kopicha", instance="https://mastodon.example"),
        "other-threads": isolated_account_factory(
            "other-threads", project="other", repo_dir=other_repo, handle="other_shop"),
    }
    return made


def viewer(*names, projects=None):
    """account 名 → project（台帳の fixture と同じ対応）。"""
    table = {"kopicha-threads": "kopicha", "kopicha-bsky": "kopicha", "kopicha-mstdn": "kopicha",
             "other-threads": "other"}
    return plaza.Viewer({name: table[name] for name in names})


SCOPE = "Threads の朝の投稿・茶の話題"


def post(account="kopicha-threads", *, kind="finding", title="冒頭を問いにする", body=BODY,
         by="kopicha-session", scope_note=SCOPE, **kwargs):
    if kind == "measure":
        kwargs.setdefault("how", f"thth measured {account}")
    if kwargs.get("visibility") == "open" and "confirm" not in kwargs:
        # open は二段確認（裁定 09-23）。試験の舞台づくりでは一段目の digest で二段目まで進める。
        preview = plaza.post(account, kind=kind, title=title, body=body, by=by,
                             scope_note=scope_note, **kwargs)
        assert preview["report_type"] == "plaza_open_preview" and preview["opened"] is False
        kwargs["confirm"] = preview["digest"]
    return plaza.post(account, kind=kind, title=title, body=body, by=by, scope_note=scope_note,
                      **kwargs)


def reply(plaza_id, **kwargs):
    """open の 1 件への返信は二段確認（3.5.1 件 3 (b)）。試験の舞台づくりでは一段目の digest で
    二段目まで進める（`post` と同じ）。project 範囲の 1 件なら一段目でそのまま足される。"""
    if "confirm" not in kwargs:
        first = plaza.reply(plaza_id, **kwargs)
        if first["report_type"] != "plaza_reply_open_preview":
            return first
        assert first["replied"] is False
        kwargs["confirm"] = first["digest"]
    return plaza.reply(plaza_id, **kwargs)


def plaza_dir():
    return Path(os.environ["THTH_ROOT"]) / "state" / "_plaza"


# ---------------------------------------------------------------- 置き場

def test_置き場は私有でSNSの台帳には書かない(owners):
    root = Path(os.environ["THTH_ROOT"])
    repo = Path(owners["kopicha-threads"]["repo_dir"])
    before_repo = sorted(str(p) for p in repo.rglob("*") if ".git" not in p.parts)
    result = post()
    assert result["report_type"] == "plaza_posted" and result["scope"] == "project"
    assert plaza.PLAZA_ID.match(result["plaza_id"]) and result["plaza_id"].startswith("p20260909-")
    path = plaza_dir() / (result["plaza_id"] + ".json")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(plaza_dir().stat().st_mode) == 0o700
    assert not (root / "state" / "kopicha-threads").exists()
    assert sorted(str(p) for p in repo.rglob("*") if ".git" not in p.parts) == before_repo
    record = json.loads(path.read_text())
    assert record["project"] == "kopicha" and record["medium"] == "threads"
    assert record["by"] == "kopicha-session" and record["tool_version"]


def test_骨は報告の口と共通(owners):
    # 同じ Store の型を使う（写しではない）。
    assert isinstance(plaza.STORE, private_store.Store)
    assert isinstance(report_inbox.STORE, private_store.Store)
    assert plaza.STORE.directory == "_plaza" and report_inbox.STORE.directory == "_reports"


def test_置き場がsymlinkなら読まない(owners, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    state = Path(os.environ["THTH_ROOT"]) / "state"
    state.mkdir(exist_ok=True)
    (state / "_plaza").symlink_to(elsewhere)
    with pytest.raises(plaza.PlazaError, match="^plaza_store_unavailable$"):
        post()
    assert list(elsewhere.iterdir()) == []


def test_変更ログはpresence_only(owners):
    post(body="本文の中身 SECRETLESS-BODY-MARK")
    entries, _ = admin_log.read(account="kopicha-threads", event="plaza_posted")
    assert len(entries) == 1
    text = json.dumps(entries, ensure_ascii=False)
    assert "SECRETLESS-BODY-MARK" not in text and "冒頭を問いにする" not in text
    assert entries[0]["diff"]["plaza"] == ["absent", "present"]


def test_ログに書けなければ置かない(owners, monkeypatch):
    def broken(*args, **kwargs):
        raise admin_log.AdminLogError("admin_log_write_failed")
    monkeypatch.setattr(admin_log, "append", broken)
    with pytest.raises(plaza.PlazaError, match="^plaza_log_unavailable$"):
        post()
    assert [p for p in plaza_dir().iterdir() if p.name.endswith(".json")] == []


# ------------------------------------------------------------------ 断り

def test_秘密らしき値は置かない(owners):
    for field in ("title", "body"):
        kwargs = {field: "token は ghp_" + "a" * 36 + " です"}
        with pytest.raises(plaza.PlazaError, match="^secret_detected$"):
            post(**kwargs)
    with pytest.raises(plaza.PlazaError, match="^secret_detected$"):
        post(kind="measure", hypothesis="Bearer abcdefghijklmnopqrstuvwxyz0123456789")
    assert not plaza_dir().exists() or [p for p in plaza_dir().iterdir()
                                         if p.name.endswith(".json")] == []


def test_長さの上限(owners):
    with pytest.raises(plaza.PlazaError, match="^post_too_long$"):
        post(title="題" * (plaza.TITLE_MAX + 1))
    with pytest.raises(plaza.PlazaError, match="^post_too_long$"):
        post(body="字" * (plaza.BODY_MAX + 1))
    assert post(title="題" * plaza.TITLE_MAX, body="字" * plaza.BODY_MAX)["plaza_id"]


def test_空と複数行の題は断る(owners):
    for kwargs in ({"title": ""}, {"body": "  "}, {"title": "一行目\n二行目"}):
        with pytest.raises(plaza.PlazaError, match="^invalid_post$"):
            post(**kwargs)


def test_byと種類は必須(owners):
    with pytest.raises(plaza.PlazaError, match="^by_required$"):
        post(by=None)
    with pytest.raises(plaza.PlazaError, match="^invalid_kind$"):
        post(kind="bug")


def test_施策でないものに宣言は付けられない(owners):
    with pytest.raises(plaza.PlazaError, match="^not_a_measure$"):
        post(kind="finding", declarations=[{"schema_version": 1}])


def test_重複は同じaccountの既存idだけを返す(owners):
    first = post()
    with pytest.raises(plaza.PlazaError, match="^duplicate_post$") as caught:
        post()
    assert caught.value.plaza_id == first["plaza_id"]
    # 他の持ち主が同じ題と本文を置いても、kopicha の id は返らない（置ける）。
    other = post("other-threads", by="other-session")
    assert other["plaza_id"] != first["plaza_id"]


def test_1日30件まで(owners):
    now = jst.now_jst()
    for i in range(plaza.DAILY_LIMIT):
        post(title=f"気づき {i}", now=now + datetime.timedelta(seconds=i))
    with pytest.raises(plaza.PlazaError, match="^plaza_rate_limited$"):
        post(title="31 件目", now=now + datetime.timedelta(minutes=5))
    # 24 時間たてば置ける。
    assert post(title="翌日", now=now + datetime.timedelta(hours=25))["plaza_id"]


def test_壊れた1件は数えて飛ばす_件数は管理者にだけ(owners):
    first = post()
    (plaza_dir() / "p20260909-deadbeef.json").write_text("{壊れている")
    mine = plaza.list_posts(viewer("kopicha-threads"))
    assert [row["plaza_id"] for row in mine["posts"]] == [first["plaza_id"]]
    assert mine["unreadable"] is None
    assert plaza.admin_list()["unreadable"] == 1


def test_本文の絶対パスは畳む(owners):
    root = os.environ["THTH_ROOT"]
    result = post(body=f"{root}/state/x を見た")
    record = plaza.show(result["plaza_id"], viewer("kopicha-threads"))
    assert root not in record["body"] and "$THTH_ROOT/state/x" in record["body"]
