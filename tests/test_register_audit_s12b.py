"""5.23.0 積み増しの検収（s12b）の器の直し: 認めた指紋を checkout ごとに持つ・控えの無い下書きの頭の道の鍵。

見本の店を一時の置き場に写して使う。網には出ない。"""
from __future__ import annotations

import json
import os
import pathlib
import shutil

import pytest

from tests.test_register_base import add, env, jrun  # noqa: F401
from tests.test_register_store import clock, confirm, edit  # noqa: F401
from thth import queuefile
from thth.register import form, ledger, recheck

ROW = "cafe-special-2026-12-12"
ROW_FILE = ("臨時", "2026-12-12_設備の点検.md")


def test_同じcheckoutで訂正して認め直すと訂正の前の中身に戻した行は使えない(capsys, env, clock):
    main = add(capsys, env, "cafe-sample", kind="store")
    confirm(capsys, "cafe-sample", ROW)                                  # 18:00 を認めた
    edit(main.joinpath(*ROW_FILE), "close: 18:00", "close: 19:00")
    confirm(capsys, "cafe-sample", ROW)                                  # 19:00 に直して認め直した
    rec = ledger.read_confirmed("cafe-sample")[ROW]
    assert list(rec["paths"].values()) == [rec["digest"]]
    edit(main.joinpath(*ROW_FILE), "close: 19:00", "close: 18:00")      # 古い枝が戻した、など
    rc, v, err = jrun(capsys, "register", "use", "cafe-sample", ROW)
    assert rc == 2 and v["code"] == "register_row_unconfirmed", (v, err)


def test_pathsの無い前の行は控えのフォルダで認めた物と読み同じフォルダの認め直しで捨てる(capsys, env, clock):
    main = add(capsys, env, "cafe-sample", kind="store")
    confirm(capsys, "cafe-sample", ROW)
    rows = ledger.read_confirmed("cafe-sample")
    rows[ROW] = {k: rows[ROW][k] for k in ("digest", "at", "by")}       # 5.22.0 までの形
    ledger.write_confirmed("cafe-sample", rows)
    rc, v, err = jrun(capsys, "register", "use", "cafe-sample", ROW)
    assert rc == 0, err
    edit(main.joinpath(*ROW_FILE), "close: 18:00", "close: 19:00")
    confirm(capsys, "cafe-sample", ROW)
    assert len(ledger.read_confirmed("cafe-sample")[ROW]["paths"]) == 1
    edit(main.joinpath(*ROW_FILE), "close: 19:00", "close: 18:00")
    rc, v, err = jrun(capsys, "register", "use", "cafe-sample", ROW)
    assert rc == 2 and v["code"] == "register_row_unconfirmed", (v, err)


def test_控えの無い下書きの頭に書いた道の鍵は使わない(capsys, env, clock, tmp_path):
    main = add(capsys, env, "cafe-sample", kind="store")
    (main / ".thth").mkdir(exist_ok=True)
    (main / ".thth/register.json").write_text(json.dumps({"schema": "thth.register_name/1",
                                                          "register": "cafe-sample"}) + "\n")
    confirm(capsys, "cafe-sample", ROW)
    evil = tmp_path / "evil" / "cafe-sample"
    shutil.copytree(main, evil)                                          # 前に認めた古い中身のまま
    edit(main.joinpath(*ROW_FILE), "close: 18:00", "close: 19:00")
    confirm(capsys, "cafe-sample", ROW)
    digest = form.fingerprint(form.find(form.scan(str(evil))["rows"], ROW).text)
    q = tmp_path / "q"
    q.mkdir()
    injected = q / "2026-12-01-register-cafe-sample-b.md"
    injected.write_text("---\nfrom_register: cafe-sample/" + ROW + "\nfrom_register_digest: " + digest +
                        "\nnot_after: 2099-01-01T00:00:00+09:00\n\x00register_path: " + str(evil) +
                        "\n---\n本文\n", encoding="utf-8")
    line = recheck.check(str(injected), queuefile.parse(str(injected)).front_matter)
    assert line is not None and "register_row_changed" in line, line


def _named(main):
    (main / ".thth").mkdir(exist_ok=True)
    (main / ".thth/register.json").write_text(json.dumps({"schema": "thth.register_name/1",
                                                          "register": "cafe-sample"}) + "\n")


def _confirm_at(capsys, path):
    rc, v, err = jrun(capsys, "register", "confirm", "cafe-sample", ROW, "--path", str(path), "--by", "m")
    if rc == 1:
        rc, v, err = jrun(capsys, "register", "confirm", "cafe-sample", ROW, "--path", str(path), "--by", "m",
                          "--confirm", v["digest"])
    return rc, v, err


def test_同じフォルダを大文字小文字の違う綴りで渡しても前の指紋を捨てる(capsys, env, clock):
    main = add(capsys, env, "cafe-sample", kind="store")
    _named(main)
    other = pathlib.Path(str(main.parent) + "/" + main.name.upper())
    if not other.exists():
        pytest.skip("大文字小文字を区別する置き場")
    confirm(capsys, "cafe-sample", ROW)                                  # 18:00（控えの道の綴り）
    edit(main.joinpath(*ROW_FILE), "close: 18:00", "close: 19:00")
    rc, v, err = _confirm_at(capsys, other)                              # 同じフォルダを別の綴りで認め直した
    assert rc == 0, err
    assert len(ledger.read_confirmed("cafe-sample")[ROW]["paths"]) == 1
    edit(main.joinpath(*ROW_FILE), "close: 19:00", "close: 18:00")
    rc, v, err = jrun(capsys, "register", "use", "cafe-sample", ROW)
    assert rc == 2 and v["code"] == "register_row_unconfirmed", (v, err)


def test_道の形の鍵の前の行は同じフォルダの鍵に直して読む(capsys, env, clock):
    main = add(capsys, env, "cafe-sample", kind="store")
    confirm(capsys, "cafe-sample", ROW)
    rows = ledger.read_confirmed("cafe-sample")
    old = rows[ROW]["digest"]
    rows[ROW] = {"digest": old, "at": rows[ROW]["at"], "by": "m", "paths": {os.path.realpath(main): old}}
    ledger.write_confirmed("cafe-sample", rows)                          # c209305 の形
    edit(main.joinpath(*ROW_FILE), "close: 18:00", "close: 19:00")
    confirm(capsys, "cafe-sample", ROW)
    rec = ledger.read_confirmed("cafe-sample")[ROW]
    assert list(rec["paths"]) == [ledger.folder_key(str(main))]
    assert rec["folders"] == {ledger.folder_key(str(main)): os.path.realpath(main)}
    edit(main.joinpath(*ROW_FILE), "close: 19:00", "close: 18:00")
    rc, v, err = jrun(capsys, "register", "use", "cafe-sample", ROW)
    assert rc == 2 and v["code"] == "register_row_unconfirmed", (v, err)


def test_9フォルダめで認めると最も古いフォルダの指紋が落ち使えない向きに倒れる(capsys, env, clock):
    main = add(capsys, env, "cafe-sample", kind="store")
    _named(main)
    confirm(capsys, "cafe-sample", ROW)                                  # 本体は 18:00
    for n in range(ledger.PATHS_MAX):
        wt = env["tmp"] / f"wt{n}" / "cafe-sample"
        shutil.copytree(main, wt)
        edit(wt.joinpath(*ROW_FILE), "close: 18:00", f"close: {18 + n // 5}:{n % 5 + 1}0")
        rc, v, err = _confirm_at(capsys, wt)
        assert rc == 0, (n, v, err)
    assert len(ledger.read_confirmed("cafe-sample")[ROW]["paths"]) == ledger.PATHS_MAX
    rc, v, err = jrun(capsys, "register", "use", "cafe-sample", ROW)
    assert rc == 2 and v["code"] == "register_row_unconfirmed", (v, err)
