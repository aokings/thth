"""台帳の器 段 2 の柵（その 3）: Codex の 3 回めの検収（2026-11-14・`~/Documents/THTH/codex/答え_段2の三回め_2026-11-14.md`）の
6 つと、同じ型の穴。反例は 3 回めの再現の台本（`register-stage2-third-current/controller/`）と同じ形にした。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys

import pytest

from thth import cli
from thth.register import adopt as A, judgment as J, keep as K, vessel as V
from thth.register.errors import RegisterRefused

ROOT = pathlib.Path(__file__).resolve().parents[1]
AT = "2026-11-14T18:00:00+09:00"
OPTS = dict(by="audit", at=AT)
ROOTUSER = hasattr(os, "geteuid") and os.geteuid() == 0


def write(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def plan_of(*include, exclude=()):
    return A.parse_plan({"schema": A.PLAN_SCHEMA, "include": list(include), "exclude": list(exclude)})


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", tmp_path), ("XDG_CONFIG_HOME", tmp_path)]:
        monkeypatch.setenv(key, str(value))
    return tmp_path


def visible(root: pathlib.Path) -> dict:
    return {e["path"]: K.view_bytes(str(root), e).decode() for e in K.view(str(root))}


class Stop(Exception):
    pass


class StopAt:
    """`keep` の中の os の書き換え（rename・link・unlink・replace）を数え、`at` 回めの直後で止める（落ちた形）。"""
    NAMES = ("rename", "link", "unlink", "replace")

    def __init__(self, at: int):
        self.at, self.count = at, 0

    def __getattr__(self, name):
        real = getattr(os, name)
        if name not in self.NAMES:
            return real

        def call(*args, **kwargs):
            value = real(*args, **kwargs)
            self.count += 1
            if self.count == self.at:
                raise Stop(f"{name} {self.count}")
            return value
        return call


# ================================================================== 1: 古い再開が次の束の段取りを退かす

def _late_finish_case(base: pathlib.Path):
    root = base / "register"
    for f in "abc":
        write(root / f"{f}.txt", f"old-{f}")
    K.begin(str(root), K.prepare(str(root), {"a.txt": b"new-a"}, what="merge", by="first", at=AT))
    stale = K.pending(str(root))               # 1 人めが読み、後始末の直前まで進んで止まっていた段取り
    assert K.apply(str(root), by="second", at=AT)["applied"] == 1   # 2 人めが済ませる
    third = K.prepare(str(root), {"b.txt": b"new-b", "c.txt": b"new-c"}, what="merge", by="third", at=AT)
    K.begin(str(root), third)
    now = K.pending(str(root))
    K._step(str(root), now, 0, *K._from_to(now["entries"][0], back=False))   # 次の束は 1 枚めまで置いた
    return root, stale, third


@pytest.mark.parametrize("stop_at", range(1, 7))
def test_三1_古い再開の後始末がどの瞬間で止まっても次の束は前の姿で見え再開で進む(env, monkeypatch, stop_at):
    root, stale, third = _late_finish_case(env / f"case-{stop_at}")
    logged = len(K.history(str(root)))
    # 1 人めは「まだ自分の段取り」と確かめ終えたあとで（2 人めと 3 人めの前に）止まっていた。そこから続け、
    # keep の中の stop_at 回めの書き換えの直後で落ちる。
    monkeypatch.setattr(K, "pending", lambda r: stale)
    monkeypatch.setattr(K, "_mine", lambda r, p: True)
    monkeypatch.setattr(K, "os", StopAt(stop_at))
    try:
        K.apply(str(root), by="first-late", at=AT)
    except (Stop, RegisterRefused):
        pass
    monkeypatch.undo()
    now = K.pending(str(root))
    assert now is not None and now["id"] == third["id"]           # 次の束の段取りは消えない
    assert visible(root) == {"a.txt": "new-a", "b.txt": "old-b", "c.txt": "old-c"}   # 前の姿だけ（混ぜない）
    assert len(K.history(str(root))) == logged                      # 混ざった姿を控えに書かない
    result = K.apply(str(root), by="recovery", at=AT)
    assert result["applied"] == 1 and K.pending(str(root)) is None
    assert visible(root) == {"a.txt": "new-a", "b.txt": "new-b", "c.txt": "new-c"}


@pytest.mark.parametrize("stop_at", range(1, 13))
def test_三1_同じ型_入れ替えのどの瞬間で止まっても読み手は前か後だけで再開で済み控えに残る(env, monkeypatch, stop_at):
    root = env / "register"
    write(root / "a.txt", "old-a")
    write(root / "b.txt", "old-b")
    plan = K.prepare(str(root), {"a.txt": b"new-a", "b.txt": None, "c.txt": b"new-c"}, what="merge", by="w", at=AT)
    K.begin(str(root), plan)
    monkeypatch.setattr(K, "os", StopAt(stop_at))
    stopped = True
    try:
        K.apply(str(root), by="w", at=AT)
        stopped = False
    except Stop:
        pass
    monkeypatch.undo()
    before = {"a.txt": "old-a", "b.txt": "old-b"}
    after = {"a.txt": "new-a", "c.txt": "new-c"}
    assert visible(root) in (before, after)
    K.apply(str(root), by="recovery", at=AT)
    assert K.pending(str(root)) is None and visible(root) == after
    last = K.history(str(root))[-1]
    assert last["fp"] == K.catalog_fp(K.view(str(root)))          # 済んだ姿が控えの跡に残る（止まった所によらず）
    # 次の束も始められる（済んだ段取りが次を塞がない）
    assert K.run(str(root), {"a.txt": b"third"}, what="merge", by="w", at=AT)["applied"] == 1
    assert stopped or stop_at > 0


def test_三1_同じ型_済んだ同じ段取りを2人が同時に見て次を始めても段取りは1つだけ(env, monkeypatch):
    root = env / "register"
    write(root / "a.txt", "old")
    assert K.run(str(root), {"a.txt": b"one"}, what="merge", by="w", at=AT)["applied"] == 1
    first = K.prepare(str(root), {"a.txt": b"two"}, what="merge", by="x", at=AT)
    second = K.prepare(str(root), {"a.txt": b"three"}, what="merge", by="y", at=AT)
    seen = K._pending_file(str(root))           # 2 人とも、済んだ前の段取り（か無い）を読んだあと
    monkeypatch.setattr(K, "_pending_file", lambda r: seen)
    K.begin(str(root), first)
    with pytest.raises(RegisterRefused):
        K.begin(str(root), second)
    monkeypatch.undo()
    assert K.pending(str(root))["id"] == first["id"]


# ================================================================== 2: 読めないフォルダを「消えた」とする

@pytest.mark.skipif(ROOTUSER, reason="root では権限で読めなくならない")
def test_三2_元のフォルダが読めなければ取り込み直しは断り台帳から何も落とさない(env):
    source, reg = env / "source", env / "register"
    write(source / "blocked/wanted.txt", "must not disappear\n")
    write(source / "open.txt", "here\n")
    opts = dict(plan=A.parse_plan(None), **OPTS)
    assert A.adopt(str(source), str(reg), **opts)["files"] == 2
    logged = len(K.history(str(reg)))
    (source / "blocked").chmod(0)
    try:
        with pytest.raises(RegisterRefused) as e:
            A.adopt(str(source), str(reg), **opts)
    finally:
        (source / "blocked").chmod(0o755)
    assert e.value.code == "register_adopt_unusable" and e.value.detail == "unreadable"
    assert (reg / "blocked/wanted.txt").read_bytes() == b"must not disappear\n"
    assert K.pending(str(reg)) is None and len(K.history(str(reg))) == logged


@pytest.mark.skipif(ROOTUSER, reason="root では権限で読めなくならない")
def test_三2_同じ型_台帳の中のフォルダが読めなければ読み手と控えは断る(env, capsys):
    source, reg = env / "source", env / "register"
    write(source / "blocked/wanted.txt", "keep me\n")
    write(source / "open.txt", "here\n")
    A.adopt(str(source), str(reg), plan=A.parse_plan(None), **OPTS)
    (reg / "blocked").chmod(0)
    try:
        with pytest.raises(RegisterRefused) as e:
            K.view(str(reg))
        assert e.value.detail == "unreadable"
        with pytest.raises(RegisterRefused):
            K.dir_catalog(str(reg))
        with pytest.raises(RegisterRefused):
            A.restore(str(reg), str(env / "out"))
    finally:
        (reg / "blocked").chmod(0o755)


@pytest.mark.skipif(ROOTUSER, reason="root では権限で読めなくならない")
def test_三2_同じ型_範囲の中の読めないファイルも断る(env):
    source, reg = env / "source", env / "register"
    write(source / "secret.txt", "x\n")
    (source / "secret.txt").chmod(0)
    try:
        with pytest.raises(RegisterRefused) as e:
            A.adopt(str(source), str(reg), plan=A.parse_plan(None), **OPTS)
    finally:
        (source / "secret.txt").chmod(0o644)
    assert e.value.detail == "unreadable"
    assert not reg.exists() or not any(reg.iterdir())


# ================================================================== 3: 宣言だけを変えて内側の番号が消える

def _diff_json(reg):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["register", "diff", str(reg), "first..now", "--json"])
    return code, json.loads(out.getvalue())


def test_三3_宣言だけを変えて内側の番号が消えれば差は赤で番号を言う(env):
    source, reg, decl = env / "source", env / "reg", env / "decl"
    kind = {"schema": "thth.kind/1", "kind": "record", "place": "*.json", "format": "json", "records": "file",
            "id": {"name": True}, "units": {"groups": [], "items": ["rows", "*"], "id": "id"}}
    write(decl / "kinds/record.json", json.dumps(kind))
    write(source / "one.json", json.dumps({"rows": [{"id": "a", "new_id": "a"}, {"id": "b", "new_id": "c"}]}))
    A.adopt(str(source), str(reg), plan=A.parse_plan(None), decl=str(decl), **OPTS)
    before = V.open_vessel(str(reg))
    j = J.make(before, {"record": "record:one", "unit": "i:b"}, "reads_right", by="audit", at=AT, conclusion="good")
    write(reg / ".thth/kinds/record.json", json.dumps(dict(kind, units=dict(kind["units"], id="new_id"))))
    after = V.open_vessel(str(reg))
    assert before.get("record:one").version == after.get("record:one").version   # 記録の版は同じ
    assert J.evaluate(after, j) == "vanished"
    code, value = _diff_json(reg)
    assert code != 0 and value["code"] == "register_record_vanished"
    assert value["units_vanished"] == [{"record": "record:one", "unit": "i:b"}]


# ================================================================== 4: 範囲の外の枚の実行の印だけが変わった求め

def test_三4_止めたあとに段取りの外の枚の実行の印だけが変わればその求めは続きと見ない(env, monkeypatch):
    source, reg, out = env / "source", env / "register", env / "restored"
    for name in ("a.txt", "b.txt"):
        write(source / name, "old")
        (source / name).chmod(0o644)
    opts = dict(plan=A.parse_plan(None), **OPTS)
    A.adopt(str(source), str(reg), **opts)
    write(source / "a.txt", "new")
    monkeypatch.setattr(K, "_replace", lambda a, b: (_ for _ in ()).throw(Stop()))
    with pytest.raises(Stop):
        A.adopt(str(source), str(reg), **opts)
    monkeypatch.undo()
    assert [e["path"] for e in K.pending(str(reg))["entries"]] == ["a.txt"]
    (source / "b.txt").chmod(0o755)
    with pytest.raises(RegisterRefused) as e:
        A.adopt(str(source), str(reg), **opts)      # 前の求め（b は 644）とは別の求め
    assert e.value.code == "register_batch_pending"
    assert K.apply(str(reg), by="audit", at=AT)["applied"] == 1
    result = A.adopt(str(source), str(reg), **opts)
    assert result["same"]
    assert (reg / "b.txt").stat().st_mode & 0o777 == 0o755
    A.restore(str(reg), str(out))
    assert (out / "b.txt").stat().st_mode & 0o111


def test_三4_同じ型_段取りの外の枚が消えた求めも続きと見ない(env, monkeypatch):
    root = env / "register"
    write(root / "a.txt", "old-a")
    write(root / "b.txt", "old-b")
    K.begin(str(root), K.prepare(str(root), {"a.txt": b"new-a"}, what="merge", by="w", at=AT))
    assert K.matches_pending(str(root), K.pending(str(root)), {"a.txt": b"new-a", "b.txt": b"old-b"})
    assert not K.matches_pending(str(root), K.pending(str(root)), {"a.txt": b"new-a", "b.txt": b"old-b"},
                                 {"b.txt": True})
    assert not K.matches_pending(str(root), K.pending(str(root)), {"a.txt": b"new-a", "c.txt": None, "b.txt": None})


# ================================================================== 5: 宣言の無い 64 KB 超

def test_三5_種類の宣言が無い64KBを超える枚は取り込みで断り宣言があれば通る(env):
    src, reg = env / "source", env / "reg"
    write(src / "large.json", b'{"text":"' + b"x" * 70000 + b'"}')
    write(src / "small.json", b"{}")
    plan = plan_of("**")
    with pytest.raises(RegisterRefused) as e:
        A.adopt(str(src), str(reg), plan=plan, **OPTS)
    assert e.value.code == "register_adopt_unusable" and e.value.detail == "too_large"
    assert e.value.extra["files"] == [{"path": "large.json", "size": 70011, "kind": None,
                                       "max_bytes": V.FILE_MAX_DEFAULT}]
    assert not reg.exists() or not any(reg.iterdir())
    decl = env / "decl"
    write(decl / "kinds/big.json", json.dumps({"schema": "thth.kind/1", "kind": "big", "place": "large.json",
                                               "format": "json", "id": {"name": True}, "max_bytes": 100000}))
    result = A.adopt(str(src), str(reg), plan=plan, decl=str(decl), **OPTS)
    assert result["same"] and result["files"] == 2


def test_三5_同じ型_取り込み直しで元の枚が64KBを超えても宣言が無ければ断る(env):
    src, reg = env / "source", env / "reg"
    write(src / "grow.json", b"{}")
    A.adopt(str(src), str(reg), plan=plan_of("**"), **OPTS)
    write(src / "grow.json", b'{"t":"' + b"y" * (V.FILE_MAX_DEFAULT + 1) + b'"}')
    with pytest.raises(RegisterRefused) as e:
        A.adopt(str(src), str(reg), plan=plan_of("**"), **OPTS)
    assert e.value.detail == "too_large"
    assert (reg / "grow.json").read_bytes() == b"{}"


# ================================================================== 6: 範囲の欠けと混入を、選び方と別に見る

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def adopt_tests():
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        return _load("register_adopt_roundtrip_fixtures", ROOT / "tests/test_register_adopt.py")
    finally:
        sys.path.remove(str(ROOT / "tests"))


@pytest.mark.parametrize("operation,target", [("drop", "data/article-links.csv"), ("add", "data/knowledge/notes.txt")])
def test_三6_往復の試験は範囲の中の欠けと範囲の外の混入を見つける(capsys, env, monkeypatch, adopt_tests, operation,
                                               target):
    real = A.selector
    if operation == "add":
        # 範囲の頭のフォルダ（data/knowledge）の中にある、範囲の外の枚（選び方が誤って拾う）。
        build = adopt_tests.MEDIA["kopicha"]

        def with_extra(src):
            include, exclude, outside = build(src)
            write(src / target, "範囲の外\n")
            return include, exclude, outside + [target]
        monkeypatch.setitem(adopt_tests.MEDIA, "kopicha", with_extra)

    def wrong(plan):
        select = real(plan)
        if operation == "drop":
            return lambda rel: False if rel == target else select(rel)
        return lambda rel: rel == target or select(rel)
    monkeypatch.setattr(A, "selector", wrong)
    fx = {"tmp": env, "root": env / "root"}
    with pytest.raises(AssertionError):
        adopt_tests.test_試験2a_7つ全部で取り込み戻すと数と名前とsha256が全部同じで元には書かない(capsys, fx, "kopicha")


@pytest.fixture
def realcheck():
    return _load("register_adopt_realcheck_third", ROOT / "tools/register_adopt_realcheck.py")


@pytest.mark.parametrize("operation,target", [("drop", "data/article-links.csv"),
                                              ("add", "data/knowledge/outside.txt")])
def test_三6_実物の台本の比べも範囲の中の欠けと範囲の外の混入を見つける(env, monkeypatch, adopt_tests, realcheck,
                                                   operation, target):
    source = env / "source"
    adopt_tests._kopicha(source)
    if operation == "add":
        write(source / target, "outside the csv plan\n")
    _, include, exclude = realcheck.PLANS["kopicha"]
    plan = A.parse_plan({"schema": A.PLAN_SCHEMA, "include": include, "exclude": exclude})
    real = A.selector

    def wrong(plan):
        select = real(plan)
        if operation == "drop":
            return lambda rel: False if rel == target else select(rel)
        return lambda rel: rel == target or select(rel)
    monkeypatch.setattr(A, "selector", wrong)
    decl = realcheck.write_decl(env / "decl", realcheck.KOPICHA_KINDS)
    result = realcheck.roundtrip("kopicha", str(source), plan, env / "shadow", decl)
    scope = realcheck.scope_check("kopicha", str(source), str(env / "shadow/register"))
    assert not result["same"]
    assert not scope["all_adopted"]
    if operation == "drop":
        assert target in result["missing"] and target in scope["missing"]
    else:
        assert target in result["extra"] and target in scope["extra"]


def test_三6_実物の台本の比べは正しい選び方なら通る(env, adopt_tests, realcheck):
    source = env / "source"
    adopt_tests._kopicha(source)
    _, include, exclude = realcheck.PLANS["kopicha"]
    plan = A.parse_plan({"schema": A.PLAN_SCHEMA, "include": include, "exclude": exclude})
    decl = realcheck.write_decl(env / "decl", realcheck.KOPICHA_KINDS)
    result = realcheck.roundtrip("kopicha", str(source), plan, env / "shadow", decl)
    scope = realcheck.scope_check("kopicha", str(source), str(env / "shadow/register"))
    assert result["same"] and scope["all_adopted"] and not scope["missing"] and not scope["extra"]
