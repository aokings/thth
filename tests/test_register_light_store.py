"""5.23.0 の監査の軽い点（控えと別の checkout）: 認めた記録が行き来しない・記録にフォルダが残る・作業木の下書きの承認。

見本の店（`registers.example/cafe-sample`）を一時の置き場に写して使う。網には出ない。
"""
from __future__ import annotations

import json
import shutil

from tests.conftest import commit_and_push_path
from tests.test_register_base import add, env, jrun, run  # noqa: F401
from tests.test_register_store import approve_first, clock, confirm, edit, make_draft  # noqa: F401
from thth import admin_log
from thth.register import ACTION_PREFIX


ROW = "cafe-special-2026-12-12"
ROW_FILE = ("臨時", "2026-12-12_設備の点検.md")


def two_checkouts(capsys, env):
    """控えた本体（名乗りあり）と、中身の違う作業木。"""
    main = add(capsys, env, "cafe-sample", kind="store")
    (main / ".thth").mkdir(exist_ok=True)
    (main / ".thth/register.json").write_text(json.dumps({"schema": "thth.register_name/1",
                                                          "register": "cafe-sample"}) + "\n")
    wt = env["tmp"] / "wt" / "cafe-sample"
    shutil.copytree(main, wt)
    edit(wt.joinpath(*ROW_FILE), "close: 18:00", "close: 19:00")
    return main, wt


def test_中身の違うcheckoutで認めてももう片方の同じ行が認めていないに戻らない(capsys, env, clock):
    main, wt = two_checkouts(capsys, env)
    confirm(capsys, "cafe-sample", ROW)
    rc, v, err = jrun(capsys, "register", "use", "cafe-sample", ROW)
    assert rc == 0, err
    rc, v, err = jrun(capsys, "register", "confirm", "cafe-sample", ROW, "--path", str(wt), "--by", "m")
    if rc == 1:
        rc, v, err = jrun(capsys, "register", "confirm", "cafe-sample", ROW, "--path", str(wt), "--by", "m",
                          "--confirm", v["digest"])
    assert rc == 0, err
    # 両方の中身の認めが残る（本体の行も作業木の行も使える）
    rc, v, err = jrun(capsys, "register", "use", "cafe-sample", ROW)
    assert rc == 0, (v, err)
    rc, v, err = jrun(capsys, "register", "use", "cafe-sample", ROW, "--path", str(wt))
    assert rc == 0, (v, err)
    # 認めていない中身（本体をさらに変える）は今までどおり使えない
    edit(main.joinpath(*ROW_FILE), "close: 18:00", "close: 17:00")
    rc, v, err = jrun(capsys, "register", "use", "cafe-sample", ROW)
    assert rc == 2 and v["code"] == "register_row_unconfirmed", (v, err)


def test_pathで打った命令の記録にどのフォルダかが残る(capsys, env, clock):
    main, wt = two_checkouts(capsys, env)
    confirm(capsys, "cafe-sample", "cafe-event-2026-12-14")
    rc, v, err = jrun(capsys, "register", "deny", "cafe-sample", "cafe-event-2026-12-14", "--path", str(wt), "--by", "m")
    assert rc == 0, err
    events, _ = admin_log.read(account="cafe-sample", event=ACTION_PREFIX + "denied")
    assert events and events[-1]["diff"]["path"][1] == str(wt), events
    events, _ = admin_log.read(account="cafe-sample", event=ACTION_PREFIX + "confirmed")
    assert events and "path" not in events[-1]["diff"]       # 控えの道で打った物には足さない


def test_作業木で作った下書きは承認のとき作業木の台帳を読み直す(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    main, wt = two_checkouts(capsys, env)
    rc, v, err = jrun(capsys, "register", "confirm", "cafe-sample", ROW, "--path", str(wt), "--by", "m")
    if rc == 1:
        rc, v, err = jrun(capsys, "register", "confirm", "cafe-sample", ROW, "--path", str(wt), "--by", "m",
                          "--confirm", v["digest"])
    assert rc == 0, err
    rc, v, err = jrun(capsys, "register", "draft", "cafe-sample", "--to", account["name"], "--row", ROW, "--path", str(wt))
    assert rc == 0, err
    [one] = v["drafts"]
    path = one["path"]
    assert "19:00" in open(path, encoding="utf-8").read()
    commit_and_push_path(path, message="test: register draft")
    digest = approve_first(capsys, path)
    rc, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc == 0 and "承認しました" in out, err


def test_作業木の下書きでも作業木の行が変われば承認しない(capsys, env, clock, isolated_account_factory):
    account = isolated_account_factory()
    main, wt = two_checkouts(capsys, env)
    rc, v, err = jrun(capsys, "register", "confirm", "cafe-sample", ROW, "--path", str(wt), "--by", "m")
    if rc == 1:
        rc, v, err = jrun(capsys, "register", "confirm", "cafe-sample", ROW, "--path", str(wt), "--by", "m",
                          "--confirm", v["digest"])
    rc, v, err = jrun(capsys, "register", "draft", "cafe-sample", "--to", account["name"], "--row", ROW, "--path", str(wt))
    assert rc == 0, err
    path = v["drafts"][0]["path"]
    commit_and_push_path(path, message="test: register draft")
    digest = approve_first(capsys, path)
    edit(wt.joinpath(*ROW_FILE), "close: 19:00", "close: 20:00")
    rc, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc == 1 and "承認しました" not in out
    # 作業木の名乗りを別の台帳に書き換えても、そこを読まずに断る
    edit(wt.joinpath(*ROW_FILE), "close: 20:00", "close: 19:00")
    (wt / ".thth/register.json").write_text(json.dumps({"schema": "thth.register_name/1", "register": "other"}))
    rc, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc == 1 and "register_path_other" in err and "承認しました" not in out
