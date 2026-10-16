"""4.0 S1 §3.2・§3.3・§14: 主体の台帳と、はてなの口座の突き合わせ（T-1a・T-1b・T-1c）。

見るのは:
  - 主体の台帳の必須欄・blogs[] の重複・lanes 空・名前とファイル名の不一致が符丁つきで断られる（T-1a）。
  - 主体の置き場（`principal_state/`）が口座の置き場（`state/`）と別の木にある（§14）。
  - はてなの口座の loader の 5 つの符丁（T-1b）。
  - SNS の口座は 1 バイトも変わらない・`principal` を書いても読まない（T-1c）。
  - `thth principal add/show/set/resume/auth` と `thth account add --media hatenablog`・doctor の dummy。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.conftest import run_thth
from tests.hatena_s1_helpers import account_data, principal_data, world  # noqa: F401
from thth import accounts, principals


def code_of(excinfo):
    return getattr(excinfo.value, "code", None)


# --------------------------------------------------------------------------
# T-1a 主体の台帳
# --------------------------------------------------------------------------

def test_T1a_正しい主体の台帳は読める(world):
    p = principals.load_principal("hatena-main")
    assert p["hatena_id"] == "lane-owner"
    assert p["token"] == str(world.main_token)
    assert principals.limits(p) == {"daily_max_posts": 6, "daily_max_retracts": 3,
                                    "daily_max_bookmarks": 3, "burst": {"count": 3, "minutes": 30},
                                    # 4.0 S4 §5.3
                                    "daily_max_self_bookmarks": 1, "bookmark_same_host_daily": 1}


@pytest.mark.parametrize("field", ["principal", "kind", "hatena_id", "token", "blogs"])
def test_T1a_必須欄が無ければ_principal_field_missing(world, field):
    data = world.principal("hatena-main")
    data.pop(field)
    (world.principals_dir / "hatena-main.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(accounts.AccountError) as e:
        principals.load_principal("hatena-main")
    assert code_of(e) == "principal_field_missing"


def test_T1a_blogsの重複は_blog_duplicate(world):
    world.edit_principal("hatena-main", blogs=[{"blog_id": "a.hatenablog.com", "lanes": ["lane-a"]},
                                               {"blog_id": "a.hatenablog.com", "lanes": ["lane-b"]}])
    with pytest.raises(accounts.AccountError) as e:
        principals.load_principal("hatena-main")
    assert code_of(e) == "blog_duplicate"


@pytest.mark.parametrize("lanes", [[], None, [""], "lane-a"])
def test_T1a_lanesが空なら_lanes_empty(world, lanes):
    """柵: `lanes: []` を通すように loader を緩めると赤。"""
    row = {"blog_id": "a.hatenablog.com"}
    if lanes is not None:
        row["lanes"] = lanes
    world.edit_principal("hatena-main", blogs=[row])
    with pytest.raises(accounts.AccountError) as e:
        principals.load_principal("hatena-main")
    assert code_of(e) == "lanes_empty"


def test_T1a_名前とファイル名が違えば_principal_name_mismatch(world):
    data = world.principal("hatena-main")
    data["principal"] = "hatena-renamed"
    (world.principals_dir / "hatena-main.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(accounts.AccountError) as e:
        principals.load_principal("hatena-main")
    assert code_of(e) == "principal_name_mismatch"


@pytest.mark.parametrize("change,code", [
    ({"kind": "twitter"}, "principal_kind_unknown"),
    ({"hatena_id": "a/b"}, "hatena_id_invalid"),
    ({"blogs": [{"blog_id": "A.Hatenablog.com", "lanes": ["lane-a"]}]}, "blog_id_invalid"),
    ({"daily_max_bookmarks": -1}, "invalid_guard_limits"),
    ({"burst": {"count": 0, "minutes": 30}}, "invalid_guard_limits"),
    ({"auth": "basic"}, "principal_invalid"),
])
def test_T1a_形が違えば符丁で断る(world, change, code):
    world.edit_principal("hatena-main", **change)
    with pytest.raises(accounts.AccountError) as e:
        principals.load_principal("hatena-main")
    assert code_of(e) == code


def test_T1a_無い主体は_principal_missing_と名前の検査(world):
    with pytest.raises(accounts.AccountError) as e:
        principals.load_principal("hatena-none")
    assert code_of(e) == "principal_missing"
    with pytest.raises(accounts.AccountError) as e:
        principals.load_principal("../etc/passwd")
    assert code_of(e) == "invalid_principal_name"


def test_T1a_主体の置き場は口座の置き場と別の木にある(world):
    """§14: `state/_principals/` ではなく `$THTH_ROOT/principal_state/<p>/`。"""
    state = principals.state_dir_for("hatena-main")
    assert state == str(world.root / "principal_state" / "hatena-main")
    assert not state.startswith(str(world.root / "state") + os.sep)
    # 「_principals」という口座が作れても、主体の状態とはぶつからない。
    assert accounts.name_is_safe("_principals")
    assert accounts.state_dir_for("_principals") != os.path.dirname(state)
    assert principals.lock_path_for("hatena-main").startswith(state + os.sep)
    assert principals.token_lock_path_for("hatena-main").startswith(state + os.sep)
    # 台帳は口座の台帳の隣（$THTH_ACCOUNTS_DIR の親の principals/）。
    assert principals.principals_dir() == str(world.principals_dir)


def test_T1a_THTH_PRINCIPALS_DIRで差し替えられる(world, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("THTH_PRINCIPALS_DIR", str(elsewhere))
    assert principals.principals_dir() == str(elsewhere)
    with pytest.raises(accounts.AccountError) as e:
        principals.load_principal("hatena-main")
    assert code_of(e) == "principal_missing"


# --------------------------------------------------------------------------
# T-1b はてなの口座の突き合わせ（§3.3 の 1〜5）
# --------------------------------------------------------------------------

def test_T1b_正しい口座は読める(world):
    cfg = accounts.load_account("lane-a-hatenablog")
    assert cfg["media"] == "hatenablog"
    assert cfg["principal"] == "hatena-main"
    assert cfg["blog_id"] == "a.hatenablog.com"


def _account_edit(world, name, **values):
    data = world.account(name)
    for key, value in values.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    world.write_account(data)


@pytest.mark.parametrize("edit,code", [
    ({"principal": "hatena-none"}, "principal_missing"),
    ({"principal": None}, "principal_missing"),
    ({"blog_id": "z.hatenablog.com"}, "blog_unknown"),
    ({"blog_id": None}, "blog_unknown"),
    ({"blog_id": "b.hatenablog.com"}, "blog_not_allowed_for_lane"),
    ({"token": "/somewhere/else.token"}, "principal_token_mismatch"),
    ({"handle": "another-id"}, "principal_handle_mismatch"),
])
def test_T1b_5つの符丁がそれぞれ落ちる(world, edit, code):
    """柵: 5 つのうち 1 つの検査を消すと赤。"""
    _account_edit(world, "lane-a-hatenablog", **edit)
    with pytest.raises(accounts.AccountError) as e:
        accounts.load_account("lane-a-hatenablog")
    assert code_of(e) == code
    assert code in str(e.value)


def test_T1b_主体がはてなでなければ_principal_missing(world, monkeypatch):
    p = principals.load_principal("hatena-main")
    cfg = world.account("lane-a-hatenablog")
    with pytest.raises(accounts.AccountError) as e:
        principals.check_account(cfg, "lane-a-hatenablog", {**p, "kind": "other"})
    assert code_of(e) == "principal_missing"


def test_T1b_主体の台帳からレーンを外すと口座が読めなくなる(world):
    world.edit_principal("hatena-main", blogs=[
        {"blog_id": "a.hatenablog.com", "lanes": ["lane-b"]},
        {"blog_id": "b.hatenablog.com", "lanes": ["lane-b"]}])
    with pytest.raises(accounts.AccountError) as e:
        accounts.load_account("lane-a-hatenablog")
    assert code_of(e) == "blog_not_allowed_for_lane"


def test_T1b_tokenは同じパスなら書き方が違っても通る(world, monkeypatch):
    home = str(world.main_token.parent.parent)
    monkeypatch.setenv("HOME", home)
    _account_edit(world, "lane-a-hatenablog", token="~/secrets/hatena-main.token")
    assert accounts.load_account("lane-a-hatenablog")["token"] == str(world.main_token)


# --------------------------------------------------------------------------
# T-1c SNS の口座は変わらない
# --------------------------------------------------------------------------

def test_T1c_SNSの口座はprincipalを書いても読まない(world):
    base = account_data("lane-a-threads", project="lane-a", principal="hatena-none",
                        blog_id="z.hatenablog.com", token=world.tmp / "x.token", handle="whoever")
    base["media"] = "threads"
    world.write_account(base)
    cfg = accounts.load_account("lane-a-threads")
    # 主体の台帳は無い（hatena-none）が、threads の口座は何も確かめない。
    assert cfg["principal"] == "hatena-none"
    assert dict(cfg) == {**base, "repo_dir": accounts._expand(base["repo_dir"]),
                         "env": base["env"], "token": base["token"]}
    assert not principals.uses_principal(cfg)


def test_T1c_SNSの口座の読み込みは主体の台帳を開かない(world, monkeypatch):
    base = account_data("lane-a-x", project="lane-a", principal="hatena-main",
                        blog_id="a.hatenablog.com", token=world.tmp / "x.token", handle="whoever")
    base["media"] = "x"
    world.write_account(base)
    monkeypatch.setattr(principals, "load_principal",
                        lambda *a, **k: pytest.fail("SNS の口座が主体の台帳を読んだ"))
    accounts.load_account("lane-a-x")


# --------------------------------------------------------------------------
# 雛形・dummy・CLI
# --------------------------------------------------------------------------

def test_雛形はダミーでproduction_false():
    from tests.hatena_s1_helpers import EXAMPLES
    for name in ("hatenablog.json", "principal-hatena.json"):
        text = (EXAMPLES / name).read_text(encoding="utf-8")
        for 本物 in ("nigamilab", "kopicha", "asmon-kanto", "aoking", "masaru"):
            assert 本物 not in text
    acct = json.loads((EXAMPLES / "hatenablog.json").read_text(encoding="utf-8"))
    assert acct["production"] is False and acct["scheduled"] is False
    for key in accounts.REQUIRED_FIELDS:
        assert key in acct
    p = json.loads((EXAMPLES / "principal-hatena.json").read_text(encoding="utf-8"))
    principals.validate(p["principal"], p)
    assert acct["token"] == p["token"]
    assert acct["handle"] == p["hatena_id"]


def test_dummy_fieldsはblog_idとhandleを名指しする():
    from tests.hatena_s1_helpers import EXAMPLES
    acct = json.loads((EXAMPLES / "hatenablog.json").read_text(encoding="utf-8"))
    fields = {d["field"] for d in accounts.dummy_fields(acct)}
    assert {"blog_id", "handle"} <= fields
    p = json.loads((EXAMPLES / "principal-hatena.json").read_text(encoding="utf-8"))
    fields = {d["field"] for d in accounts.dummy_fields(p)}
    assert {"blog_id", "hatena_id"} <= fields
    # SNS の口座の判定は変わらない（threads の demo は handle だけ）。
    threads = json.loads((EXAMPLES / "threads.json").read_text(encoding="utf-8"))
    assert {d["field"] for d in accounts.dummy_fields(threads)} == {"redirect_uri", "handle"}


def _cli_env(world):
    return {"THTH_ROOT": str(world.root), "THTH_ACCOUNTS_DIR": str(world.accounts_dir)}


def test_principal_add_show_set_resume_auth(world, tmp_path):
    env = _cli_env(world)
    r = run_thth(["principal", "add", "hatena-new", "--hatena-id", "new-id",
                  "--blog", "new.hatenablog.com=lane-n", "--by", "tester"], env=env)
    assert r.returncode == 0, r.stderr
    path = world.principals_dir / "hatena-new.json"
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["blogs"] == [{"blog_id": "new.hatenablog.com", "lanes": ["lane-n"], "syntax": "markdown"}]
    assert data["token"].endswith("/hatena-new.token")
    # 二度目は上書きしない
    r = run_thth(["principal", "add", "hatena-new", "--hatena-id", "new-id", "--by", "tester"], env=env)
    assert r.returncode == 2
    # show（秘密は「ある／ない」だけ）
    r = run_thth(["principal", "show", "hatena-main", "--json"], env=env)
    assert r.returncode == 0, r.stderr
    shown = json.loads(r.stdout)
    assert shown["token"] == "absent"
    assert shown["accounts"] == ["lane-a-hatenablog", "lane-a-shared", "lane-b-hatenablog", "lane-b-shared"]
    assert shown["stopped"] is None
    # set
    r = run_thth(["principal", "set", "hatena-main", "daily_max_bookmarks", "5", "--by", "tester"], env=env)
    assert r.returncode == 0, r.stderr
    assert world.principal("hatena-main")["daily_max_bookmarks"] == 5
    r = run_thth(["principal", "set", "hatena-main", "burst", "4", "20", "--by", "tester"], env=env)
    assert r.returncode == 0, r.stderr
    assert world.principal("hatena-main")["burst"] == {"count": 4, "minutes": 20}
    r = run_thth(["principal", "set", "hatena-main", "hold_minutes", "5", "--by", "tester"], env=env)
    assert r.returncode == 2 and "invalid_setting" in r.stderr
    # resume（止めてから戻す）
    principals.stop("hatena-main", "owner", actor="tester")
    r = run_thth(["principal", "resume", "hatena-main", "--by", "tester"], env=env)
    assert r.returncode == 0, r.stderr
    assert principals.stopped("hatena-main") is None
    # auth（S2 §2.4）: 端末でなければ読まない（subprocess の標準入力は端末ではない）
    r = run_thth(["principal", "auth", "hatena-main", "--by", "tester"], env=env)
    assert r.returncode == 2
    assert "not_a_tty" in r.stderr


def test_account_add_hatenablog(world):
    env = _cli_env(world)
    r = run_thth(["account", "add", "lane-a-blog2", "--media", "hatenablog", "--project", "lane-a",
                  "--principal", "hatena-main", "--blog", "shared.hatenablog.com", "--by", "tester"], env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    data = world.account("lane-a-blog2")
    assert data["media"] == "hatenablog"
    assert data["principal"] == "hatena-main" and data["blog_id"] == "shared.hatenablog.com"
    assert data["token"] == str(world.main_token) and data["handle"] == "lane-owner"
    assert data["production"] is False and data["scheduled"] is False
    accounts.load_account("lane-a-blog2")


@pytest.mark.parametrize("args,needle", [
    (["--principal", "hatena-main"], "--blog"),
    (["--blog", "a.hatenablog.com"], "--principal"),
    (["--principal", "hatena-main", "--blog", "b.hatenablog.com"], "blog_not_allowed_for_lane"),
    (["--principal", "hatena-none", "--blog", "a.hatenablog.com"], "principal_missing"),
])
def test_account_add_hatenablogは主体と突き合わせて断る(world, args, needle):
    r = run_thth(["account", "add", "lane-a-bad", "--media", "hatenablog", "--project", "lane-a",
                  *args, "--by", "tester"], env=_cli_env(world))
    assert r.returncode == 2
    assert needle in r.stderr
    assert not (world.accounts_dir / "lane-a-bad.json").exists()


def test_account_add_のprincipalはhatenablogだけ(world):
    r = run_thth(["account", "add", "lane-a-threads", "--media", "threads", "--project", "lane-a",
                  "--principal", "hatena-main", "--by", "tester"], env=_cli_env(world))
    assert r.returncode == 2
    assert not (world.accounts_dir / "lane-a-threads.json").exists()


def test_doctorは雛形のままのblog_idと主体のhatena_idを名指しする(world):
    from tests.hatena_s1_helpers import EXAMPLES
    p = json.loads((EXAMPLES / "principal-hatena.json").read_text(encoding="utf-8"))
    p["token"] = str(world.main_token)
    world.write_principal(p)
    acct = json.loads((EXAMPLES / "hatenablog.json").read_text(encoding="utf-8"))
    acct["token"] = str(world.main_token)
    world.write_account(acct)
    r = run_thth(["doctor", acct["account"], "--json"], env=_cli_env(world))
    report = json.loads(r.stdout)
    fields = {d["field"] for d in report["dummy_fields"]}
    assert {"blog_id", "handle", "principal.hatena_id", "principal.blog_id"} <= fields
    assert r.returncode != 0
