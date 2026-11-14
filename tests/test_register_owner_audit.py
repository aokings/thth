"""段 8 の検収（2026-11-14・別の者）の反例を写した試験。外す範囲の重い穴 3 つと、中から直した 3 つ。

答え `thth-work/audit-s8/答え_段8の検収_2026-11-14.md` の §3（重い穴）と §4 の 1・2・6。道具立ては
`tests/test_register_owner.py` の `world`（THTH_ROOT・HOME・XDG_CONFIG_HOME を一時の置き場へ・relay は偽物）。
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from types import SimpleNamespace

import pytest

from tests.test_register_owner import _folder, _json, _main, _tree, world  # noqa: F401  (fixture)
from tests.test_v212_server_writes import env  # noqa: F401  (fixture)
from thth import accounts, leave
from thth.register import ledger, owner


def _guard_env(world):
    """外す命令を打つ前に、向け先が一時の置き場であることを確かめる。"""
    tmp = str(world["tmp"])
    assert os.environ["THTH_ROOT"].startswith(tmp) and os.environ["HOME"].startswith(tmp)
    assert os.environ["XDG_CONFIG_HOME"].startswith(tmp) and ledger.registers_dir().startswith(tmp)


def _kept(before, after):
    return all(after.get(k) == v for k, v in before.items())


# ------------------------------------------------------------------ 穴 1: 読めない控えのフォルダが消える

@pytest.mark.parametrize("where", ["state", "thth_root_other", "config"])
def test_穴1_addはTHTHの置き場の中の台帳のフォルダを断る(world, capsys, where):
    root = world["root"]
    folder = {"state": root / "state" / "alpha" / "ledger", "thth_root_other": root / "anything" / "ledger",
              "config": world["home"] / ".config" / "thth" / "ledger"}[where]
    _folder(folder)
    rc, value, _ = _json(capsys, "register", "add", "inner", "--path", str(folder), "--project", "alpha",
                         "--kind", "media", "--by", "operator")
    assert rc == 2 and value["code"] == "register_path_unusable" and value["detail"] == "inside_thth_state"
    assert "inner" not in ledger.list_names()


@pytest.mark.parametrize("where", ["slash", "home", "tmp_parent"])
def test_穴1_addはTHTHの置き場を含む親のフォルダを断る(world, capsys, where):
    folder = {"slash": pathlib.Path("/"), "home": world["home"], "tmp_parent": world["tmp"]}[where]
    rc, value, _ = _json(capsys, "register", "add", "wide", "--path", str(folder), "--project", "alpha",
                         "--kind", "media", "--by", "operator")
    assert rc == 2 and value["code"] == "register_path_unusable" and value["detail"] == "contains_thth_state"
    assert "wide" not in ledger.list_names()


@pytest.mark.parametrize("broken", ["{broken", '{"register": "inner", "extra": 1}'])
def test_穴1_読めない控えでも口座のstateの中の台帳のフォルダは消さない(world, capsys, broken):
    """検収の再現: 前に控えた（今の add なら断る）控えを読めなくして、project leave を 2 段とも打つ。"""
    _guard_env(world)
    root = world["root"]
    inner = _folder(root / "state" / "alpha" / "ledger")
    ledger.write_new("inner", ledger.build("inner", project="alpha", path=str(inner), kind="media"))
    pathlib.Path(ledger.path_for("inner")).write_text(broken)
    before = _tree(world["tmp"])
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    assert rc == 2 and value["code"] == "register_leave_overlap" and value["detail"] == "account_tree"
    assert _kept(before, _tree(world["tmp"])) and (inner / "行.md").exists()
    # 口座の leave も同じ（何も止めずに断る）。
    rc = leave.command(SimpleNamespace(name="alpha", by="operator", json=False, plaza_open=None))
    assert rc == 2 and "owned_tree_contains_register_folder" in capsys.readouterr().err
    assert _kept(before, _tree(world["tmp"])) and leave.read("alpha") is None


def test_穴1_控えが無くても構造で見て消す木の中の台帳らしい物を消さない(world, capsys):
    """控えのファイルごと無い（生の道も拾えない）。.thth/ を持つフォルダが state の中にあれば、それだけで断る。"""
    _guard_env(world)
    root = world["root"]
    inner = _folder(root / "state" / "alpha2" / "deep" / "ledger")
    before = _tree(world["tmp"])
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    assert rc == 2 and value["code"] == "register_leave_overlap"
    assert _kept(before, _tree(world["tmp"])) and (inner / ".thth" / "judgments.jsonl").exists()


def test_穴1_記録の置き場の中に台帳らしい物があれば何も消さない(world, capsys):
    _guard_env(world)
    root = world["root"]
    hidden = _folder(root / "register_state" / "alpha-media" / "sub")
    before = _tree(world["tmp"])
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    assert rc == 2 and value["code"] == "register_leave_overlap" and value["detail"] == "register_state"
    assert _kept(before, _tree(world["tmp"])) and (hidden / "行.md").exists()


# ------------------------------------------------------------------ 穴 2: 打ち直しで所属を見直さない

def _stop_at_alpha(monkeypatch):
    real = leave.run
    seen = []

    def flaky(account, **kwargs):
        seen.append(account)
        if account == "alpha" and seen.count("alpha") == 1:
            raise ValueError("synthetic stop")
        return real(account, **kwargs)
    monkeypatch.setattr(leave, "run", flaky)
    return seen


def test_穴2_止まっている間に口座と控えを付け替えたら打ち直しはどれも消さずに止まる(world, capsys, monkeypatch):
    _guard_env(world)
    root = world["root"]
    _stop_at_alpha(monkeypatch)
    _, plan, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", plan["digest"])
    assert rc == 2 and value["code"] == "register_leave_incomplete"
    # 止まっている間に: alpha2 を beta へ・alpha-media の控えを外して、同じ名前で beta の台帳として控え直す。
    cfg = json.loads((root / "accounts/alpha2.json").read_text())
    cfg["project"] = "beta"
    (root / "accounts/alpha2.json").write_text(json.dumps(cfg))
    assert _json(capsys, "register", "remove", "alpha-media", "--by", "operator")[0] == 0
    assert _json(capsys, "register", "add", "alpha-media", "--path", str(world["folders"]["beta-media"]),
                 "--project", "beta", "--kind", "media", "--by", "operator")[0] == 0
    before = _tree(world["tmp"])
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", plan["digest"])
    assert rc == 2 and value["code"] == "register_leave_changed"
    assert value["detail"] == "account" and value["account"] == "alpha2"
    assert value["was"] == "alpha" and value["now"] == "beta"
    after = _tree(world["tmp"])
    assert all(after.get(k) == v for k, v in before.items() if not k.startswith("root/register_leave/")
               and not k.startswith("root/state/_admin/"))
    assert (root / "accounts/alpha2.json").exists() and pathlib.Path(ledger.path_for("alpha-media")).exists()
    # 記録は「やめた」として日時つきで残り、次の見積りは今の所属で出る。
    assert owner.read_journal("project", "alpha") is None
    rc, fresh, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    assert rc == 0 and fresh["accounts"] == ["alpha"] and fresh["registers"] == []


@pytest.mark.parametrize("change", ["path", "project", "role"])
def test_穴2_控えの道かプロジェクトか役が変われば控えを消さずに止まる(world, capsys, monkeypatch, change):
    _guard_env(world)
    real = owner.remove_registers
    calls = []

    def once(journal, by, guard):
        calls.append(1)
        if len(calls) == 1:
            raise owner.RegisterRefused("register_log_unavailable")   # 控えの段の前で止まった形
        return real(journal, by, guard)
    monkeypatch.setattr(owner, "remove_registers", once)
    _, plan, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    rc, _value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", plan["digest"])
    assert rc == 2 and owner.read_journal("project", "alpha")["phase"] == "registers"
    control = pathlib.Path(ledger.path_for("alpha-media"))
    data = json.loads(control.read_text())
    if change == "path":
        data["path"] = str(world["folders"]["beta-media"])
    elif change == "project":
        data["project"] = "beta"
    else:
        data["role"] = "common"
    control.write_text(json.dumps(data))
    raw = control.read_bytes()
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", plan["digest"])
    assert rc == 2 and value["code"] == "register_leave_changed" and value["detail"] == "register"
    assert control.read_bytes() == raw and pathlib.Path(ledger.state_dir_for("alpha-media")).exists()


# ------------------------------------------------------------------ 穴 3: 控えの道が / で止まらない

def test_穴3_控えの道がスラッシュでも見積りと健康が止まる(world, capsys):
    _guard_env(world)
    control = pathlib.Path(ledger.path_for("beta-media"))
    data = json.loads(control.read_text())
    data["path"] = "/"
    control.write_text(json.dumps(data))
    before = _tree(world["tmp"])
    start = time.monotonic()
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    assert rc == 2 and value["code"] == "register_folder_too_wide" and value["detail"] == "root"
    rc, value, _ = _json(capsys, "owner", "leave", "--by", "operator")
    assert rc == 2 and value["code"] == "register_folder_too_wide"
    rc, out, _ = _main(capsys, "register", "health", "--all", "--json")
    lines = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert rc == 0 and {"register": "beta-media", "error": "register_folder_too_wide"} in lines
    assert any(line.get("register") == "alpha-media" and "error" not in line for line in lines)
    rc, value, _ = _json(capsys, "register", "health", "alpha-media", "--across")
    assert rc == 0 and {"register": "beta-media", "why": "register_folder_too_wide"} in value["across"]["unreadable"]
    rc, value, _ = _json(capsys, "register", "health", "beta-media")
    assert rc == 2 and value["code"] == "register_folder_too_wide"
    assert time.monotonic() - start < 30
    assert _kept(before, _tree(world["tmp"]))


def test_穴3_歩くファイルが上限を超えたら断る(world, capsys, monkeypatch):
    _guard_env(world)
    monkeypatch.setattr(ledger, "WALK_MAX", 10)
    for i in range(12):
        (world["folders"]["beta-media"] / f"f{i}.txt").write_text("x")
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    assert rc == 2 and value["code"] == "register_folder_too_wide" and value["detail"] == "files"


# ------------------------------------------------------------------ 中から直した物

def test_中1_名前の末尾の改行を断る(world, capsys):
    _guard_env(world)
    before = _tree(world["tmp"])
    rc, value, _ = _json(capsys, "project", "leave", "alpha\n", "--by", "operator")
    assert rc == 2 and value["code"] == "register_args_invalid"
    with pytest.raises(ValueError):
        leave.run("alpha\n", by="operator")
    rc, value, _ = _json(capsys, "register", "add", "x\n", "--path", str(world["folders"]["shared"]), "--project",
                         "alpha", "--kind", "media", "--by", "operator")
    assert rc == 2 and value["code"] == "register_args_invalid"
    assert not ledger.name_ok("alpha\n") and ledger.name_ok("alpha")
    after = _tree(world["tmp"])
    assert _kept(before, after) and not any("\n" in k for k in after)


def test_中2_外し終わったあと同じ名前で入れ直しても外し直せる(world, capsys):
    _guard_env(world)
    root = world["root"]
    template = json.loads((root / "accounts" / "alpha.json").read_text())
    _, plan, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    rc, first, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", plan["digest"])
    assert rc == 0 and first["phase"] == "completed"
    # 打ち直しは前の完了を返す（二度消さない）。
    rc, again, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", plan["digest"])
    assert rc == 0 and again["operation_id"] == first["operation_id"]
    # 退出した口座の名前は使い直せない（`account add` の柵）ので、口座は新しい名前で・台帳の控えは同じ名前で入れ直す。
    token = root / "secrets" / "alpha3.json"
    token.write_text(json.dumps({"access_token": "synthetic-not-a-key"}))
    token.chmod(0o600)
    template.update(account="alpha3", token=str(token), repo_dir=str(root / "repos/_server/alpha3"))
    (root / "accounts" / "alpha3.json").write_text(json.dumps(template))
    ledger.write_new("alpha-media", ledger.build("alpha-media", project="alpha",
                                                 path=str(world["folders"]["alpha-media"]), kind="media"))
    _, plan2, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    assert plan2["digest"] != plan["digest"]
    rc, second, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", plan2["digest"])
    assert rc == 0 and second["operation_id"] != first["operation_id"]
    assert second["registers"] == ["alpha-media"] and second["deleted"]["register_controls"] == 1
    assert not (root / "accounts/alpha3.json").exists() and "alpha-media" not in ledger.list_names()
    done = sorted((root / "register_leave" / "done").iterdir())
    assert len(done) == 2   # 済んだ記録は名前に日時を付けて 2 つとも残る


def test_中6_読めない控えがある間は共通の台帳を足さない(env, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("THTH_REGISTERS_DIR", raising=False)
    first = _folder(tmp_path / "c1")
    assert _json(capsys, "register", "add", "c1", "--path", str(first), "--common", "--by", "operator")[0] == 0
    pathlib.Path(ledger.path_for("c1")).write_text("{broken")
    second = _folder(tmp_path / "c2")
    rc, value, _ = _json(capsys, "register", "add", "c2", "--path", str(second), "--common", "--by", "operator")
    assert rc == 2 and value["code"] == "register_ledger_invalid" and value["detail"] == "unreadable:c1"
    assert "c2" not in ledger.list_names() and not (second / ".thth" / "kinds").exists()
    assert accounts.thth_root()
