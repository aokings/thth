"""読む側の台帳名の検査（セキュリティ監査 2026-09-14・P2-2）。

**見つかり方**: `thth account add` は前から名前を検査していた（監査 1・P2-1）が、
**読む側（`accounts.load_account()`）には検査が無かった**。`thth queue ../x` は
置き場の外の `.json` を開きに行き、**返る文言が「そこに何があるか」で変わる**
——無ければ「台帳が無い」、JSON でなければ「台帳が壊れている」。読めるだけで
ファイルの**存在と種類**を探れた。

**直し方**: 名前そのものを先に検査して、外れたら**在る／無いに触れない同じ
1 つの文言**で断る（`thth/accounts.py` に規則を 1 つ置き、`account_cli` は
それを使う）。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.conftest import run_thth
from thth import account_cli as account_cli_mod
from thth import accounts as accounts_mod

危ない名前 = ["../x", "../../etc/passwd", "a/b", ".", "..", "", "x\x00y", "ふつうの日本語"]


def test_規則は1か所にある():
    """`account_cli` は `accounts` のものを使う（2 か所に写しを持たない）。"""
    assert account_cli_mod.name_is_safe is accounts_mod.name_is_safe
    assert account_cli_mod.NAME_RE is accounts_mod.NAME_RE


@pytest.mark.parametrize("name", 危ない名前)
def test_load_accountは置き場の外を指せる名前を読む前に断る(isolated_account, name):
    with pytest.raises(accounts_mod.AccountError) as e:
        accounts_mod.load_account(name)
    assert "使えない字" in str(e.value)
    # **在るとも無いとも言っていない。**
    assert "台帳が無い" not in str(e.value)
    assert "壊れている" not in str(e.value)


def test_存在の探りができない(isolated_account, tmp_path):
    """**在るファイル**を指しても、無いものを指しても、返る言葉が同じ。"""
    置き場 = isolated_account["accounts_dir"]
    外 = os.path.join(os.path.dirname(置き場), "そとの台帳.json")
    with open(外, "w", encoding="utf-8") as f:
        json.dump({"media": "threads"}, f)

    実在 = os.path.relpath(外, 置き場)[: -len(".json")]      # `../そとの台帳`
    不在 = "../" + "そんなものは無い"

    文言 = []
    for name in (実在, 不在):
        with pytest.raises(accounts_mod.AccountError) as e:
            accounts_mod.load_account(name)
        文言.append(str(e.value).replace(name, "<名前>"))
    assert 文言[0] == 文言[1], 文言


def test_普通の名前はこれまでどおり読める(isolated_account):
    cfg = accounts_mod.load_account(isolated_account["name"])
    assert cfg["media"]


def test_queueとdoctorが同じ言葉で断る(isolated_account):
    q = run_thth(["queue", "../x", "--json"])
    assert "使えない字" in q.stdout + q.stderr, q.stdout + q.stderr

    d = run_thth(["doctor", "../x"])
    assert d.returncode == 2, d.stdout + d.stderr
    assert "使えない字" in d.stdout + d.stderr


def test_名前の長さは64まで(isolated_account):
    """第 5・6 段 P3: 名前はそのままファイル名になるので上から切る。"""
    assert accounts_mod.NAME_MAX == 64
    assert accounts_mod.name_is_safe("a" * 64) is True
    assert accounts_mod.name_is_safe("a" * 65) is False
    with pytest.raises(accounts_mod.AccountError) as e:
        accounts_mod.load_account("a" * 65)
    assert "使えない字" in str(e.value)
    # 既存の呼び手（ふつうの長さ）は変わらない。
    assert accounts_mod.name_is_safe(isolated_account["name"]) is True
