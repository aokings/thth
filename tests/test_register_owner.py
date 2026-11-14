"""台帳の器 段 8: 共通の台帳と、外す範囲 3 つ（口座・メディア・持ち主）。

指示 `docs/指示_台帳の器_段8_健康と複数のメディア_2026-11-14.md` の §3 の頭 2 行・§4・§5、§1 の「済み」の線の 2〜4 と
重い穴（外す命令が、言った範囲より広く消す）。見本でメディア 2 つ（それぞれ口座 2 本と台帳 1 つ）と共通の台帳 1 つを
一時の置き場に作り、**残る物を試験が置き場を自分で見て確かめる**（台帳のフォルダの全ファイルの sha256 が前後で同じ）。

鍵は試験の値（本物ではない）。網には出ない（`env` の柵が外への接続を落とす・relay は偽物）。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import secrets
from types import SimpleNamespace

import pytest

from tests.test_v212_server_writes import env  # noqa: F401  (fixture)
from tests.test_v5181_leave_article import _principal
from thth import accounts, admin_log, cli, leave, relay
from thth.register import ACTION_PREFIX, ledger, owner, seal

BUILTIN = pathlib.Path(owner.__file__).resolve().parent / "builtin_kinds"


def _tree(path: pathlib.Path) -> dict:
    """置き場の全ファイル（symlink は追わずその物）の 相対の道 → sha256。"""
    out = {}
    if not path.exists():
        return out
    for here, dirs, files in os.walk(path, followlinks=False):
        for name in files + [d for d in dirs if os.path.islink(os.path.join(here, d))]:
            full = pathlib.Path(here) / name
            rel = str(full.relative_to(path))
            out[rel] = ("link:" + os.readlink(full)) if full.is_symlink() else hashlib.sha256(full.read_bytes()).hexdigest()
    return out


def _folder(path: pathlib.Path) -> pathlib.Path:
    """台帳のフォルダの見本（行・判断・封・器の控え）。"""
    (path / ".thth" / "kept").mkdir(parents=True)
    (path / "行.md").write_text("---\nid: a\nkind: voice\nstatus: 本採用\nsource: owner\nconfirmed: 推測\n"
                               "review_by: 2099-01-01\npublic: true\n---\n本文\n", encoding="utf-8")
    (path / ".thth" / "judgments.jsonl").write_text("", encoding="utf-8")   # 判断の記録（中身の形は段 3 の試験で見る）
    (path / ".thth" / "seal.web.json").write_text('{"seal":"synthetic"}\n', encoding="utf-8")
    (path / ".thth" / "kept" / "k1.json").write_text("{}\n", encoding="utf-8")
    return path


def _main(capsys, *argv):
    rc = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


def _json(capsys, *argv):
    rc, out, err = _main(capsys, *argv, "--json")
    return rc, json.loads(out) if out.strip() else None, err


@pytest.fixture
def world(env, tmp_path, monkeypatch, capsys):
    root = env["root"]
    home = pathlib.Path(os.environ["HOME"])
    # 封の鍵と主体の鍵は一時の置き場の中だけ（手元の本物の ~/.config/thth に触れない）。
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for key in ("THTH_PRINCIPALS_DIR", "THTH_REGISTERS_DIR"):
        monkeypatch.delenv(key, raising=False)
    calls = []

    def worker(kind, account, operation, body):
        assert (kind, operation, body) == ("account", "revoke", {})
        calls.append(account)
        return {"status": "revoked"}
    monkeypatch.setattr(relay, "signed_request", worker)
    for name, project in (("alpha2", "alpha"), ("beta2", "beta")):
        token = root / "secrets" / f"{name}.json"
        token.write_text(json.dumps({"access_token": secrets.token_urlsafe(32)}))
        token.chmod(0o600)
        cfg = json.loads((root / "accounts" / "alpha.json").read_text())
        cfg.update(account=name, project=project, token=str(token), repo_dir=str(root / "repos/_server" / name))
        (root / "accounts" / f"{name}.json").write_text(json.dumps(cfg))
    base = tmp_path / "owner"
    folders = {}
    for name, project in (("alpha-media", "alpha"), ("beta-media", "beta")):
        folders[name] = _folder(base / name)
        ledger.write_new(name, ledger.build(name, project=project, path=str(folders[name]), kind="media"))
        ledger.write_confirmed(name, {"a": {"digest": "x", "at": "t", "by": "m"}})
    folders["shared"] = _folder(base / "shared")
    # 共通の台帳の project はわざと alpha にする（`project leave alpha` が role で外すのを見る）。
    rc, value, _ = _json(capsys, "register", "add", "shared", "--path", str(folders["shared"]), "--common",
                         "--project", "alpha", "--by", "operator")
    assert rc == 0 and value["role"] == "common"
    ledger.write_confirmed("shared", {"a": {"digest": "y", "at": "t", "by": "m"}})
    keydir = home / ".config" / "thth"
    keydir.mkdir(parents=True, exist_ok=True)
    keydir.chmod(0o700)
    key = keydir / seal.KEY_NAME
    key.write_bytes(b"synthetic-not-a-key\n")
    key.chmod(0o600)
    return dict(root=root, folders=folders, calls=calls, key=key, home=home, base=base, tmp=tmp_path)


def _control(name) -> list:
    return [pathlib.Path(ledger.path_for(name)), pathlib.Path(ledger.state_dir_for(name))]


def _snap_controls(*names) -> dict:
    out = {}
    for name in names:
        control, state = _control(name)
        out[name] = (control.read_bytes() if control.exists() else None, _tree(state))
    return out


def _snap_account(root, name) -> dict:
    return {"ledger": (root / "accounts" / f"{name}.json").read_bytes(),
            "token": (root / "secrets" / f"{name}.json").read_bytes()}


# ------------------------------------------------------------------ 口座 1 つ

def test_口座を外すとその口座の物だけ消え最後の口座でも台帳の控えと判断とフォルダが残る(world, capsys):
    root, folders = world["root"], world["folders"]
    before = {n: _tree(p) for n, p in folders.items()}
    controls = _snap_controls("alpha-media", "beta-media", "shared")
    alpha2 = _snap_account(root, "alpha2")
    assert leave.run("alpha", by="operator")["phase"] == "completed"
    assert not (root / "accounts/alpha.json").exists() and not (root / "secrets/alpha.json").exists()
    assert _snap_account(root, "alpha2") == alpha2
    # その project の最後の口座（alpha2）。前は台帳の控えと記録も消していた（5.19.0）。
    rc = leave.command(SimpleNamespace(name="alpha2", by="operator", json=False, plaza_open=None))
    out = capsys.readouterr().out
    assert rc == 0 and "register_kept:" in out and "thth project leave alpha" in out
    assert not (root / "accounts/alpha2.json").exists()
    assert _snap_controls("alpha-media", "beta-media", "shared") == controls
    assert ledger.read_confirmed("alpha-media") == {"a": {"digest": "x", "at": "t", "by": "m"}}
    assert {n: _tree(p) for n, p in folders.items()} == before
    assert admin_log.read(account="alpha-media", event=ACTION_PREFIX + "removed")[0] == []


def test_口座のleaveは消す木と台帳のフォルダが重なれば何も止めずに断る(world, capsys):
    root = world["root"]
    inside = root / "state" / "alpha" / "台帳"
    _folder(inside)
    ledger.write_new("inner", ledger.build("inner", project="gamma", path=str(inside), kind="media"))
    before = _tree(inside)
    account = _snap_account(root, "alpha")
    rc = leave.command(SimpleNamespace(name="alpha", by="operator", json=False, plaza_open=None))
    err = capsys.readouterr().err
    assert rc == 2 and "owned_tree_contains_register_folder" in err
    assert _tree(inside) == before and _snap_account(root, "alpha") == account
    assert leave.read("alpha") is None   # 止めてもいない


# ------------------------------------------------------------------ メディア 1 つ

def test_メディアの見積りは何も消さず消える物と残る物の数を出す(world, capsys):
    before = _tree(world["tmp"])
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    assert rc == 0 and value["stage"] == "plan"
    assert value["accounts"] == ["alpha", "alpha2"] and value["registers"] == ["alpha-media"]
    # 見本の 4 ファイル × 3 と、共通の台帳に写した備え付けの種類の宣言 3 つ。
    assert value["remain"] == {"register_folders": 3, "register_files": 15, "common": ["shared"],
                               "other_media": ["beta"]}
    after = _tree(world["tmp"])
    assert all(after.get(k) == v for k, v in before.items())   # 1 つも消えず、1 つも変わらない
    rc, out, _ = _main(capsys, "project", "leave", "alpha", "--by", "operator")
    # digest は見積りの回ごとに変わる（その回だけの乱数を混ぜる・段 8 の検収 中 2）。
    assert rc == 0 and "まだ何も消していません" in out and "--confirm " in out and value["digest"] not in out
    assert world["calls"] == []


def test_メディアを外すとそのメディアの口座と控えだけが消え残る物は前後で同じ(world, capsys):
    root, folders = world["root"], world["folders"]
    before = {n: _tree(p) for n, p in folders.items()}
    kept_controls = _snap_controls("beta-media", "shared")
    others = {n: _snap_account(root, n) for n in ("beta", "beta2")}
    key = world["key"].read_bytes()
    _, plan, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", plan["digest"])
    assert rc == 0 and value["phase"] == "completed"
    assert value["deleted"]["accounts"] == 2 and value["deleted"]["register_controls"] == 1
    for name in ("alpha", "alpha2"):
        assert not (root / "accounts" / f"{name}.json").exists() and not (root / "secrets" / f"{name}.json").exists()
    assert not any(p.exists() for p in _control("alpha-media"))
    # 残る物を置き場で見る。
    assert {n: _tree(p) for n, p in folders.items()} == before
    assert _snap_controls("beta-media", "shared") == kept_controls
    assert {n: _snap_account(root, n) for n in ("beta", "beta2")} == others
    assert world["key"].read_bytes() == key
    events, _ = admin_log.read(account="alpha-media", event=ACTION_PREFIX + "removed")
    assert len(events) == 1 and events[0]["diff"]["via"] == [None, "project_leave"]
    # 打ち直しは同じ結果を返し、二度消さない。
    rc, again, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", plan["digest"])
    assert rc == 0 and again["deleted"] == value["deleted"]
    assert len(admin_log.read(account="alpha-media", event=ACTION_PREFIX + "removed")[0]) == 1


def test_見積りのあとに口座が増えれば合わずに断り何も消さない(world, capsys):
    root = world["root"]
    _, plan, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    cfg = json.loads((root / "accounts" / "alpha.json").read_text())
    token = root / "secrets" / "alpha3.json"
    token.write_text(json.dumps({"access_token": secrets.token_urlsafe(32)}))
    token.chmod(0o600)
    cfg.update(account="alpha3", token=str(token), repo_dir=str(root / "repos/_server/alpha3"))
    (root / "accounts" / "alpha3.json").write_text(json.dumps(cfg))
    before = _tree(world["tmp"])
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", plan["digest"])
    assert rc == 2 and value["code"] == "register_leave_confirm" and value["detail"] == "changed"
    after = _tree(world["tmp"])
    assert all(after.get(k) == v for k, v in before.items())


def test_途中で止めて打ち直すと続きから終わり二重に消さない(world, capsys, monkeypatch):
    root = world["root"]
    real = leave.run
    seen = []

    def flaky(account, **kwargs):
        seen.append(account)
        if account == "alpha2" and seen.count("alpha2") == 1:
            raise ValueError("synthetic stop")
        return real(account, **kwargs)
    monkeypatch.setattr(leave, "run", flaky)
    _, plan, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", plan["digest"])
    assert rc == 2 and value["code"] == "register_leave_incomplete" and value["account"] == "alpha2"
    journal = owner.read_journal("project", "alpha")
    assert journal["phase"] == "accounts" and journal["done"]["accounts"] == ["alpha"]
    assert all(p.exists() for p in _control("alpha-media"))   # 控えはまだ消していない
    # 見積りをやり直しても、途中の記録の digest でなければ続けない。
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", "0" * 12)
    assert rc == 2 and value["code"] == "register_leave_confirm" and value["detail"] == "in_progress"
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", plan["digest"])
    assert rc == 0 and value["phase"] == "completed"
    assert seen == ["alpha", "alpha2", "alpha2"]   # 済んだ alpha は二度呼ばない
    assert value["deleted"]["accounts"] == 2 and value["deleted"]["register_controls"] == 1
    assert not (root / "accounts/alpha2.json").exists() and not any(p.exists() for p in _control("alpha-media"))
    assert len(admin_log.read(account="alpha-media", event=ACTION_PREFIX + "removed")[0]) == 1
    assert len(admin_log.read(account="alpha2", event="account_removed")[0]) == 1


# ------------------------------------------------------------------ 重い穴: 控えを書き換えても広く消さない

def _tamper(name, path):
    control = pathlib.Path(ledger.path_for(name))
    data = json.loads(control.read_text())
    data["path"] = str(path)
    control.write_text(json.dumps(data))


def test_控えの道がsymlinkでほかの台帳を指してもその台帳のフォルダを消さない(world, capsys):
    folders = world["folders"]
    link = world["tmp"] / "link-to-beta"
    link.symlink_to(folders["beta-media"], target_is_directory=True)
    _tamper("alpha-media", link)
    before = {n: _tree(p) for n, p in folders.items()}
    beta = _snap_controls("beta-media")
    _, plan, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", plan["digest"])
    assert rc == 0 and value["phase"] == "completed"
    assert {n: _tree(p) for n, p in folders.items()} == before and link.is_symlink()
    assert _snap_controls("beta-media") == beta


@pytest.mark.parametrize("where", ["own_state_dotdot", "thth_root", "registers_dir", "other_state"])
def test_控えの道がTHTHの置き場を指すように書き換えてあれば何も消さずに断る(world, capsys, where):
    root = world["root"]
    target = {"own_state_dotdot": root / "register_state" / "alpha-media" / ".." / "alpha-media",
              "thth_root": root,
              "registers_dir": pathlib.Path(ledger.registers_dir()),
              "other_state": root / "register_state" / "beta-media"}[where]
    _tamper("alpha-media", target)
    before = _tree(world["tmp"])
    rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator")
    if where == "other_state":
        # ほかの台帳の記録の置き場を指す: そのメディアの物だけを消し、beta の記録は残る。
        rc, value, _ = _json(capsys, "project", "leave", "alpha", "--by", "operator", "--confirm", value["digest"])
        assert rc == 0
        assert _tree(root / "register_state" / "beta-media") == {
            k[len("root/register_state/beta-media/"):]: v for k, v in before.items()
            if k.startswith("root/register_state/beta-media/")}
        assert all(v == _tree(world["tmp"]).get(k) for k, v in before.items() if k.startswith("owner/"))
        return
    assert rc == 2 and value["code"] == "register_leave_overlap"
    after = _tree(world["tmp"])
    assert all(after.get(k) == v for k, v in before.items())
    assert world["calls"] == [] and not leave.names()


# ------------------------------------------------------------------ 持ち主の全部

def test_持ち主ごとやめると全部の台帳のフォルダが前後で同じで控え口座主体封の鍵が無い(world, capsys):
    root, folders = world["root"], world["folders"]
    token, _blog = _principal({"root": root}, "wordpress", "pr1", ["alpha"])
    before = {n: _tree(p) for n, p in folders.items()}
    everything = _tree(world["tmp"])
    rc, plan, _ = _json(capsys, "owner", "leave", "--by", "operator")
    assert rc == 0 and plan["stage"] == "plan" and plan["seal_key"] is True and "新しい封は作れなくなります" in plan["notice"]
    assert plan["accounts"] == ["alpha", "alpha2", "beta", "beta2"] and plan["principals"] == ["pr1"]
    assert plan["registers"] == ["alpha-media", "beta-media", "shared"]
    after = _tree(world["tmp"])
    assert all(after.get(k) == v for k, v in everything.items())   # 見積りは 1 つも消さない
    rc, out, _ = _main(capsys, "owner", "leave", "--by", "operator", "--confirm", plan["digest"])
    assert rc == 0
    assert out.index("知らせ: 封の秘密の鍵を消します") < out.index("持ち主の全部をやめました")
    assert {n: _tree(p) for n, p in folders.items()} == before
    assert ledger.list_names() == []
    assert not (root / "register_state").exists() or list((root / "register_state").iterdir()) == []
    assert accounts.list_account_names() == []
    assert not (root / "principals" / "pr1.json").exists() and not token.exists()
    assert not world["key"].exists()


def test_持ち主の鍵がTHTHの置き場の外なら消さずに残すと言う(world, capsys):
    root, tmp = world["root"], world["tmp"]
    token, _blog = _principal({"root": root}, "wordpress", "pr1", ["alpha"])
    outside = tmp / "elsewhere"
    outside.mkdir()
    moved = outside / "pr1.token"
    moved.write_bytes(token.read_bytes())
    data = json.loads((root / "principals" / "pr1.json").read_text())
    data["token"] = str(moved)
    (root / "principals" / "pr1.json").write_text(json.dumps(data))
    _, plan, _ = _json(capsys, "owner", "leave", "--by", "operator")
    rc, value, _ = _json(capsys, "owner", "leave", "--by", "operator", "--confirm", plan["digest"])
    assert rc == 0 and moved.exists()
    assert {"what": "principal_token_outside_thth", "name": "pr1"} in value["kept"]


# ------------------------------------------------------------------ 共通の台帳

def test_共通の台帳は2つめを断り控えを書かない(world, capsys):
    other = _folder(world["base"] / "other")
    rc, value, _ = _json(capsys, "register", "add", "other", "--path", str(other), "--common", "--by", "operator")
    assert rc == 2 and value["code"] == "register_common_exists" and value["detail"] == "shared"
    assert "other" not in ledger.list_names() and not (other / ".thth" / "kinds").exists()
    assert ledger.common_names() == ["shared"] and ledger.media_of("alpha") == ["alpha-media"]


def test_備え付けの種類の宣言が写りcheckが読める(world, capsys):
    shared = world["folders"]["shared"]
    for kind in ("correspondence", "operator", "account"):
        assert (shared / ".thth" / "kinds" / f"{kind}.json").read_bytes() == (BUILTIN / f"{kind}.json").read_bytes()
    assert json.loads(pathlib.Path(ledger.path_for("shared")).read_text())["role"] == "common"
    (shared / "correspondence").mkdir()
    (shared / "correspondence" / "kakishibu.json").write_text(json.dumps(
        {"media_a": "alpha", "id_a": "point:1", "media_b": "beta", "id_b": "point:9", "relation": "related"}))
    (shared / "operator").mkdir()
    (shared / "operator" / "me.json").write_text(json.dumps({"name": "見本の運営者"}))
    (shared / "account").mkdir()
    (shared / "account" / "x.json").write_text(json.dumps({"name": "alpha-x", "media": "x", "url": "https://x.com/a"}))
    rc, value, _ = _json(capsys, "register", "check", "shared")
    assert rc == 0, value
    # 関係の種類は必須。
    (shared / "correspondence" / "bad.json").write_text(json.dumps(
        {"media_a": "alpha", "id_a": "point:2", "media_b": "beta", "id_b": "point:3"}))
    rc, out, err = _main(capsys, "register", "check", "shared")
    assert "both_sides" in out + err


def test_備え付けと同じ名前の宣言があれば上書きせずに断る(env, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("THTH_REGISTERS_DIR", raising=False)
    folder = _folder(tmp_path / "c")
    (folder / ".thth" / "kinds").mkdir()
    (folder / ".thth" / "kinds" / "operator.json").write_text("{}\n")
    rc, value, _ = _json(capsys, "register", "add", "c", "--path", str(folder), "--common", "--by", "operator")
    assert rc == 2 and value["code"] == "register_builtin_kind_exists" and value["detail"] == "operator"
    assert (folder / ".thth" / "kinds" / "operator.json").read_text() == "{}\n"
    assert not (folder / ".thth" / "kinds" / "account.json").exists() and ledger.list_names() == []
