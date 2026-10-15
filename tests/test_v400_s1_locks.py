"""4.0 S1 §4.3・§4.4: ロックの 3 段（repo → principal → account）と token.lock（T-3a〜T-3c）。"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os

import pytest

from tests.hatena_s1_helpers import account_data, world  # noqa: F401
from thth import accounts, core, lock as lock_mod, principals, server_files


def cfg(name):
    return accounts.load_account(name)


class _Spy:
    def __init__(self, order):
        self.order = order

    def __call__(self, path):
        spy = self

        class Lock:
            def acquire(self):
                spy.order.append(("acquire", path))

            def release(self):
                spy.order.append(("release", path))
        return Lock()


# --------------------------------------------------------------------------
# T-3a 取る順
# --------------------------------------------------------------------------

def test_T3a_core_account_locksはrepo_principal_accountの順(world, monkeypatch):
    """柵: 順を入れ替えると赤。"""
    order = []
    monkeypatch.setattr(core.lock_mod, "AccountLock", _Spy(order))
    name = "lane-a-hatenablog"
    with core._account_locks(name, cfg(name), accounts.state_dir_for(name)):
        pass
    acquires = [p for op, p in order if op == "acquire"]
    releases = [p for op, p in order if op == "release"]
    assert acquires == [accounts.repo_lock_path_for(cfg(name)["repo_dir"]),
                        principals.lock_path_for("hatena-main"),
                        accounts.account_lock_path_for(name)]
    assert releases == list(reversed(acquires))


def test_T3a_server_files_account_locksも同じ順(world, monkeypatch):
    seen = []
    original_directory = server_files.directory
    original_lock_at = server_files.lock_at

    @contextlib.contextmanager
    def directory(path, **kwargs):
        with original_directory(path, **kwargs) as fd:
            seen.append(("dir", str(path)))
            yield fd

    @contextlib.contextmanager
    def lock_at(fd, name, **kwargs):
        seen.append(("lock", name))
        with original_lock_at(fd, name, **kwargs):
            yield

    monkeypatch.setattr(server_files, "directory", directory)
    monkeypatch.setattr(server_files, "lock_at", lock_at)
    name = "lane-a-hatenablog"
    with server_files.account_locks(name, cfg(name)):
        pass
    paths = [os.path.join(d, n) for (_, d), (_, n) in zip(seen[0::2], seen[1::2])]
    assert paths == [accounts.repo_lock_path_for(cfg(name)["repo_dir"]),
                     principals.lock_path_for("hatena-main"),
                     accounts.account_lock_path_for(name)]


def test_T3a_SNSの口座は今までどおり2段(world, monkeypatch, tmp_path):
    data = account_data("lane-a-threads", project="lane-a", principal="hatena-main",
                        blog_id="a.hatenablog.com", token=tmp_path / "t.token", handle="who",
                        repo_dir=str(tmp_path / "repo-a"))
    data["media"] = "threads"
    world.write_account(data)
    order = []
    monkeypatch.setattr(core.lock_mod, "AccountLock", _Spy(order))
    with core._account_locks("lane-a-threads", cfg("lane-a-threads"), accounts.state_dir_for("lane-a-threads")):
        pass
    acquires = [p for op, p in order if op == "acquire"]
    assert acquires == [accounts.repo_lock_path_for(str(tmp_path / "repo-a")),
                        accounts.account_lock_path_for("lane-a-threads")]


# --------------------------------------------------------------------------
# T-3b 2 レーンの同時
# --------------------------------------------------------------------------

def test_T3b_別レーンの同じ主体はprincipalで弾かれる(world, tmp_path):
    a, b = "lane-a-hatenablog", "lane-b-hatenablog"
    with core._account_locks(a, cfg(a), accounts.state_dir_for(a)):
        with pytest.raises(lock_mod.LockBusy):
            with core._account_locks(b, cfg(b), accounts.state_dir_for(b)):
                pytest.fail("principal を 2 つの process が同時に持った")
        # 遠くの道（待たない）も同じ
        with pytest.raises(lock_mod.LockBusy):
            with server_files.account_locks(b, cfg(b)):
                pytest.fail("principal を 2 つの process が同時に持った")
        # 別の主体の口座は通る
        c = "lane-c-hatenablog"
        with core._account_locks(c, cfg(c), accounts.state_dir_for(c)):
            pass
    # 放したあとは取れる
    with core._account_locks(b, cfg(b), accounts.state_dir_for(b)):
        pass


def test_T3b_SNSの口座は同じrepoで待つがprincipalは取らない(world, tmp_path):
    data = account_data("lane-a-threads", project="lane-a", principal="hatena-main",
                        blog_id="a.hatenablog.com", token=tmp_path / "t.token", handle="who",
                        repo_dir=str(tmp_path / "repo-a"))
    data["media"] = "threads"
    world.write_account(data)
    a = "lane-a-hatenablog"
    with core._account_locks(a, cfg(a), accounts.state_dir_for(a)):
        with pytest.raises(lock_mod.LockBusy):  # 同じ repo-a で弾かれる
            with core._account_locks("lane-a-threads", cfg("lane-a-threads"),
                                     accounts.state_dir_for("lane-a-threads")):
                pass
    # principal だけを持たれていても、SNS の口座は通る（principal を取らない）
    held = lock_mod.AccountLock(principals.lock_path_for("hatena-main"))
    held.acquire()
    try:
        with core._account_locks("lane-a-threads", cfg("lane-a-threads"),
                                 accounts.state_dir_for("lane-a-threads")):
            pass
        with server_files.account_locks("lane-a-threads", cfg("lane-a-threads")):
            pass
    finally:
        held.release()


def test_T3b_principalの待ちはrepoと同じ締め切りを共有する(world):
    held = lock_mod.AccountLock(principals.lock_path_for("hatena-main"))
    principals.ensure_state_dir("hatena-main")
    held.acquire()
    try:
        b = "lane-b-hatenablog"
        import time
        started = time.monotonic()
        with pytest.raises(lock_mod.LockBusy):
            with core._account_locks(b, cfg(b), accounts.state_dir_for(b), wait=0.3):
                pass
        assert 0.25 <= time.monotonic() - started < 5
    finally:
        held.release()


def test_T3b_主体の状態の置き場は0700(world):
    a = "lane-a-hatenablog"
    with core._account_locks(a, cfg(a), accounts.state_dir_for(a)):
        pass
    mode = os.stat(principals.state_dir_for("hatena-main")).st_mode & 0o777
    assert mode & 0o077 == 0


# --------------------------------------------------------------------------
# T-3c token の書き込み
# --------------------------------------------------------------------------

def test_T3c_tokenの書き込みはtoken_lockの中だけ(world, monkeypatch):
    from thth import secrets_fs
    observed = []
    original = secrets_fs.atomic_write_json

    def spy(path, data, **kwargs):
        # 書く瞬間に token.lock が他から取れない（＝握られている）こと
        fd = os.open(principals.token_lock_path_for("hatena-main"), os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        observed.append(path)
        return original(path, data, **kwargs)

    monkeypatch.setattr(secrets_fs, "atomic_write_json", spy)
    path = principals.write_token("hatena-main", {"access_token": "test-value"})
    assert observed == [str(world.main_token)] and path == str(world.main_token)
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == {"access_token": "test-value"}
    # 放したあとは取れる
    with principals.token_lock("hatena-main"):
        pass


def test_T3c_token_lockの二重は弾く(world):
    with principals.token_lock("hatena-main"):
        with pytest.raises(lock_mod.LockBusy):
            with principals.token_lock("hatena-main"):
                pass
        with pytest.raises(lock_mod.LockBusy):
            principals.write_token("hatena-main", {"access_token": "x"})
    assert not world.main_token.exists()


def test_T3c_runの経路はtokenを書かない(world, monkeypatch):
    world.main_token.parent.mkdir(parents=True, exist_ok=True)
    world.main_token.write_text(json.dumps({"access_token": "test-value"}), encoding="utf-8")
    os.chmod(world.main_token, 0o600)
    before = (world.main_token.read_bytes(), os.stat(world.main_token).st_mtime_ns)
    monkeypatch.setattr(principals, "write_token", lambda *a, **k: pytest.fail("run の経路が token を書いた"))
    from thth import secrets_fs
    monkeypatch.setattr(secrets_fs, "atomic_write_json", lambda *a, **k: pytest.fail("run の経路が token を書いた"))
    real_replace = os.replace

    def replace(src, dst, *a, **k):
        if os.path.abspath(str(dst)) == str(world.main_token):
            pytest.fail("run の経路が token を書いた")
        return real_replace(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", replace)
    for name in ("lane-a-hatenablog", "lane-b-hatenablog"):
        c = cfg(name)
        assert accounts.load_token(c) == {"access_token": "test-value"}
        assert accounts.token_exists(c)
        with core._account_locks(name, c, accounts.state_dir_for(name)):
            from thth import guard
            guard.check(name, c, "entry_create")
    assert (world.main_token.read_bytes(), os.stat(world.main_token).st_mtime_ns) == before
