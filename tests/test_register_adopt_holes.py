"""台帳の器 段 2 の柵: Codex の検収（2026-11-14・`~/Documents/THTH/codex/答え_段2の検収_2026-11-14.md`）の穴 10 と、
同じ型の穴（ほかの書く口・読む口の競合と symlink）。

1 つの試験が 1 つの穴の反例を写す。反例は検収の再現の台本（`register-stage2-audit/`）と同じ形にした。
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import stat
import unicodedata

import pytest

from thth import cli
from thth.register import adopt as A, keep as K, vessel as V
from thth.register.errors import RegisterRefused

ROOT = pathlib.Path(__file__).resolve().parents[1]
AT = "2026-11-14T12:00:00+09:00"


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home)]:
        monkeypatch.setenv(key, str(value))
    return tmp_path


def write(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def tree(root: pathlib.Path) -> dict:
    """フォルダの全部のファイル（symlink は除く）の 道 → (中身, 権限)。"""
    return {str(p.relative_to(root)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
            for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()}


def plan_of(*include, exclude=()):
    return A.parse_plan({"schema": A.PLAN_SCHEMA, "include": list(include), "exclude": list(exclude)})


def refused(fn, *args, **kwargs) -> RegisterRefused:
    with pytest.raises(RegisterRefused) as info:
        fn(*args, **kwargs)
    return info.value


# ================================================================== 穴 1: symlink を通して元へ書く

def test_穴1_台帳のdataが元を指すsymlinkならmergeは書く前に断り元は1バイトも変わらない(env, capsys):
    source, reg, bundle = env / "source", env / "register", env / "bundle"
    write(source / "data/rows.csv", "id,text\na,ORIGINAL\n")
    reg.mkdir()
    (reg / "data").symlink_to(source / "data", target_is_directory=True)
    write(bundle / "data/rows.csv", "id,text\na,NEW\n")
    before = tree(source)
    rc_ = cli.main(["register", "merge", str(reg), str(bundle), "--by", "audit", "--json"])
    value = json.loads(capsys.readouterr().out)
    assert rc_ != 0 and value["detail"] == "symlink"
    assert tree(source) == before


def test_穴1_台帳のththが元を指すsymlinkなら取り込み直しは書く前に断り元の控えは変わらない(env):
    source, reg = env / "source", env / "register"
    write(source / "payload.txt", "PAYLOAD\n")
    write(source / "controls/adopt.json", json.dumps({
        "schema": A.ADOPT_SCHEMA, "source": str(source), "adopted": [],
        "plan": {"schema": A.PLAN_SCHEMA, "include": ["payload.txt"]}}))
    reg.mkdir()
    (reg / ".thth").symlink_to(source / "controls", target_is_directory=True)
    before = tree(source)
    exc = refused(A.adopt, str(source), str(reg), plan=plan_of("payload.txt"), by="audit", at=AT)
    assert exc.detail == "symlink"
    assert tree(source) == before


@pytest.mark.parametrize("inner", ["kept", "batch", "kept/objects", "kinds"])
def test_同じ型_台帳のththの中の置き場がsymlinkなら外には書かずに断る(env, inner):
    source, reg, outside = env / "source", env / "register", env / "outside"
    write(source / "a.txt", "a\n")
    A.adopt(str(source), str(reg), plan=plan_of("**"), by="m", at=AT)
    write(outside / "keep.txt", "outside\n")
    target = reg / ".thth" / inner
    if target.exists():
        target.rename(env / ("moved-" + inner.replace("/", "-")))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    before = tree(outside)
    write(source / "a.txt", "b\n")
    decl = env / "decl"
    write(decl / "kinds/t.json", json.dumps({"schema": "thth.kind/1", "kind": "t", "place": "*.txt",
                                             "format": "bytes", "id": {"name": True}}))
    with pytest.raises(RegisterRefused):
        A.adopt(str(source), str(reg), plan=plan_of("**"), by="m", at=AT, decl=str(decl))
    assert tree(outside) == before


def test_同じ型_表の1行を書く口も台帳の外を指すsymlinkの親には書かない(env):
    source, reg, outside = env / "source", env / "register", env / "outside"
    write(source / "t/rows.csv", "id,text\na,A\n")
    decl = env / "decl"
    write(decl / "kinds/row.json", json.dumps({"schema": "thth.kind/1", "kind": "row", "place": "t/*.csv",
                                               "format": "csv", "id": {"field": "id"}}))
    A.adopt(str(source), str(reg), plan=plan_of("**"), by="m", at=AT, decl=str(decl))
    write(outside / "rows.csv", "id,text\na,A\n")
    (reg / "t").rename(env / "moved-t")
    (reg / "t").symlink_to(outside, target_is_directory=True)
    before = tree(outside)
    with pytest.raises(RegisterRefused):
        A.merge(str(reg), str(_bundle(env, "t/rows.csv", "id,text\na,B\n")), by="m", at=AT)
    with pytest.raises(RegisterRefused):
        A.row_add(str(reg), "row", {"id": "b", "text": "B"}, by="m", at=AT, path="t/rows.csv")
    assert tree(outside) == before


def _bundle(env, rel, text):
    bundle = env / ("bundle-" + rel.replace("/", "-"))
    write(bundle / rel, text)
    return bundle


def test_同じ型_戻す先がsymlinkなら書かずに断る(env):
    source, reg, outside = env / "source", env / "register", env / "outside"
    write(source / "a.txt", "a\n")
    A.adopt(str(source), str(reg), plan=plan_of("**"), by="m", at=AT)
    outside.mkdir()
    link = env / "dest"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RegisterRefused):
        A.restore(str(reg), str(link))
    assert tree(outside) == {}


# ================================================================== 穴 2: 読み始めた直後に入れ替えが始まる

def test_穴2_読み手が段取りなしを読んだ直後に入れ替えが始まっても混ざった姿を返さない(env, monkeypatch):
    root = env / "register"
    write(root / "a.txt", "old-a")
    write(root / "b.txt", "old-b")
    before = K.catalog_fp(K.dir_catalog(str(root)))
    desired = {"a.txt": b"new-a", "b.txt": b"new-b"}
    real_walk, real_replace = K.walk, K._replace
    events = []

    def stop_after_one(a, b):
        if events:
            raise InterruptedError("1 枚で止まる")
        real_replace(a, b)
        events.append(b)

    def interleave(*args, **kwargs):
        K.walk = real_walk
        K._replace = stop_after_one
        try:
            K.run(str(root), desired, what="merge", by="w", at=AT)
        except InterruptedError:
            pass
        finally:
            K._replace = real_replace
        yield from real_walk(*args, **kwargs)

    monkeypatch.setattr(K, "walk", interleave)
    restored = A.restore(str(root), str(env / "restored"))
    contents = {p.name: p.read_text() for p in (env / "restored").iterdir()}
    assert len(events) == 1
    assert contents == {"a.txt": "old-a", "b.txt": "old-b"}
    assert restored["fp"] == before


def test_同じ型_差の今も読み始めた直後の入れ替えで混ざらない(env, monkeypatch):
    source, reg, decl = env / "source", env / "register", env / "decl"
    write(source / "a.txt", "old-a")
    write(source / "b.txt", "old-b")
    write(decl / "kinds/t.json", json.dumps({"schema": "thth.kind/1", "kind": "t", "place": "*.txt",
                                             "format": "bytes", "id": {"name": True}}))
    A.adopt(str(source), str(reg), plan=plan_of("**"), by="m", at=AT, decl=str(decl))
    real_open = V.open_vessel
    calls = []

    def interleave(root, *args, **kwargs):
        # 差が「今」の目録を読み終え、記録を読む直前に、別の者の入れ替えが走り切る。
        if not calls and str(root) == str(reg):
            calls.append(1)
            K.run(str(reg), {"a.txt": b"new-a", "b.txt": b"new-b"}, what="merge", by="w", at=AT)
        return real_open(root, *args, **kwargs)

    monkeypatch.setattr(V, "open_vessel", interleave)
    value = A.diff(str(reg), "first..now")
    assert calls
    changed_files = sorted(value["files"]["changed"])
    changed_records = sorted(item["path"] for item in value["records"]["changed"])
    assert changed_files == changed_records  # 目録と記録が同じ時点を見ている
    assert value["to"] == K.catalog_fp(K.view(str(reg)))


# ================================================================== 穴 3: 照合と置き換えの間のほかの者の書き込み

@pytest.mark.parametrize("rollback", [False, True])
def test_穴3_続けるか戻すかの照合のあとにほかの者が書いた枚は上書きせず断る(env, monkeypatch, rollback):
    root = env / "register"
    write(root / "a.txt", "old")
    plan = K.prepare(str(root), {"a.txt": b"new"}, what="merge", by="w", at=AT)
    K.begin(str(root), plan)
    if rollback:
        write(root / "a.txt", "new")  # 置き換えが 1 枚済んだところで止まった
    real_replace = K._replace
    events = []

    def other_writer(a, b):
        write(root / "a.txt", "other-actor")
        events.append(1)
        real_replace(a, b)

    monkeypatch.setattr(K, "_replace", other_writer)
    with pytest.raises(RegisterRefused) as info:
        (K.rollback if rollback else K.apply)(str(root), by="r", at=AT)
    assert info.value.code == "register_batch_conflict"
    assert events and (root / "a.txt").read_bytes() == b"other-actor"
    assert K.pending(str(root)) is not None


def test_穴3_置き換えの途中で落ちても打ち直しで続きになる(env, monkeypatch):
    root = env / "register"
    write(root / "a.txt", "old-a")
    write(root / "b.txt", "old-b")

    def stop(a, b):
        raise InterruptedError("置き換えの直前で止まる")

    monkeypatch.setattr(K, "_replace", stop)
    with pytest.raises(InterruptedError):
        K.run(str(root), {"a.txt": b"new-a", "b.txt": b"new-b"}, what="merge", by="w", at=AT)
    monkeypatch.undo()
    assert {e["path"]: K.view_bytes(str(root), e) for e in K.view(str(root))} == {"a.txt": b"old-a", "b.txt": b"old-b"}
    result = K.run(str(root), {"a.txt": b"new-a", "b.txt": b"new-b"}, what="merge", by="x", at=AT)
    assert result["continued"]
    assert (root / "a.txt").read_bytes() == b"new-a" and (root / "b.txt").read_bytes() == b"new-b"


def test_同じ型_同じ時に始めた2つの入れ替えはあとの段取りで先の段取りを上書きしない(env, monkeypatch):
    root = env / "register"
    write(root / "a.txt", "old")
    first = K.prepare(str(root), {"a.txt": b"one"}, what="merge", by="x", at=AT)
    second = K.prepare(str(root), {"a.txt": b"two"}, what="merge", by="y", at=AT)
    real_pending = K.pending
    monkeypatch.setattr(K, "pending", lambda r: None)  # 2 人とも「段取りなし」を読んだあと
    K.begin(str(root), first)
    with pytest.raises(RegisterRefused):
        K.begin(str(root), second)
    monkeypatch.setattr(K, "pending", real_pending)
    assert K.pending(str(root))["id"] == first["id"]


# ================================================================== 穴 4: 宣言を足した取り込み直しと控え

def test_穴4_宣言を足して取り込み直すと控えに宣言が入りあとで消えた番号を差が赤で見つける(env):
    src, root, decl = env / "source", env / "register", env / "decl"
    write(src / "rows.csv", "id,text\na,A\nb,B\n")
    A.adopt(str(src), str(root), plan=plan_of("**"), by="first", at=AT)
    write(decl / "kinds/row.json", json.dumps({"schema": "thth.kind/1", "kind": "row", "place": "rows.csv",
                                               "format": "csv", "id": {"field": "id"}}))
    A.adopt(str(src), str(root), plan=plan_of("**"), by="declarer", at=AT, decl=str(decl))
    assert sorted(V.open_vessel(str(root)).records) == ["row:a", "row:b"]
    assert K.load_snapshot(str(root), "last").get("decl")
    write(root / "rows.csv", "id,text\na,A\n")
    value = A.diff(str(root), "last..now")
    assert value["red"] and value["records"]["vanished"] == ["row:b"]


# ================================================================== 穴 5: 実物の範囲の検収の記録

def _realcheck():
    spec = importlib.util.spec_from_file_location("realcheck", ROOT / "tools/register_adopt_realcheck.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("media,rel", [
    ("kopicha", "data/knowledge/staging/2026-10-01-x/central.csv"),
    ("kopicha", "data/knowledge/staging/2026-10-01-x/verify_v.csv"),
    ("kopicha", "data/knowledge/staging/2026-10-01-x/verify_t.csv"),
    ("kopicha", "data/knowledge/staging/2026-10-01-x/verify_input.csv"),
    ("nigami", "data/staging/2026-10-01-x/VERIFY.md"),
    ("mmr", "staging/credits/judg/abc.json"),
    ("jiangshi", "audits/reader/2026-10-01.json"),
])
def test_穴5_実物の範囲に検収の記録が入る(media, rel):
    module = _realcheck()
    _folder, include, exclude = module.PLANS[media]
    select = A.selector(plan_of(*include, exclude=exclude))
    assert select(rel), (media, rel)


def test_穴5_実物の範囲の数は選び方と別に目録の数で確かめる():
    module = _realcheck()
    for media in module.PLANS:
        assert media in module.EXPECTED, media
        assert module.EXPECTED[media]["source"].startswith("docs/目録_台帳の器_")


# ================================================================== 穴 6: 固定の道の symlink・綴りの違い

def test_穴6_固定の道の親がsymlinkなら範囲の外を取り込まず断る(env):
    src, outside = env / "source", env / "outside"
    write(outside / "private.txt", "OUTSIDE\r\n")
    src.mkdir()
    (src / "link").symlink_to(outside, target_is_directory=True)
    exc = refused(A.adopt, str(src), str(env / "reg-fixed"), plan=plan_of("link/private.txt"), by="a", at=AT)
    assert exc.detail == "symlink"
    assert not (env / "reg-fixed").exists()


def test_穴6_型の頭がsymlinkなら黙って0にせず断る(env):
    src, outside = env / "source", env / "outside"
    write(outside / "private.txt", "OUTSIDE\r\n")
    src.mkdir()
    (src / "link").symlink_to(outside, target_is_directory=True)
    exc = refused(A.adopt, str(src), str(env / "reg-glob"), plan=plan_of("link/*.txt"), by="a", at=AT)
    assert exc.detail == "symlink"


def test_同じ型_範囲の外のsymlinkは除く型に当たれば断らない(env):
    src, outside = env / "source", env / "outside"
    write(outside / "x.txt", "x")
    write(src / "keep/a.txt", "a")
    (src / "node_modules").symlink_to(outside, target_is_directory=True)
    result = A.adopt(str(src), str(env / "reg"), plan=plan_of("**", exclude=["node_modules/**"]), by="a", at=AT)
    assert result["same"] and result["files"] == 1


def test_同じ型_範囲に当たらない枚のsymlinkは断らずフォルダのsymlinkは下に範囲が届けば断る(env):
    src, outside = env / "source", env / "outside"
    write(outside / "VERIFY.md", "x")
    write(src / "staging/b1/VERIFY.md", "ok")
    (src / "staging/b1/other.json").symlink_to(outside / "VERIFY.md")
    result = A.adopt(str(src), str(env / "reg"), plan=plan_of("staging/**/VERIFY.md"), by="a", at=AT)
    assert result["files"] == 1
    (src / "staging/b2").symlink_to(outside, target_is_directory=True)
    exc = refused(A.adopt, str(src), str(env / "reg2"), plan=plan_of("staging/**/VERIFY.md"), by="a", at=AT)
    assert exc.detail == "symlink" and exc.extra["links"] == ["staging/b2"]


@pytest.mark.parametrize("asked",["LOWER.txt", unicodedata.normalize("NFD", "é.txt")])
def test_穴6_綴りの違う固定の道は実在の綴りで扱い違う綴りでは保存しない(env, asked):
    src = env / "source"
    write(src / "lower.txt", "case")
    write(src / unicodedata.normalize("NFC", "é.txt"), "accent")
    real = {p.name for p in src.iterdir()}
    if asked in real or not (src / asked).exists():
        pytest.skip("この機械のファイルの仕組みは綴りを区別する")
    exc = refused(A.adopt, str(src), str(env / "reg"), plan=plan_of(asked), by="a", at=AT)
    assert exc.detail == "spelling"
    assert exc.extra["actual"] in real
    assert not (env / "reg").exists()


# ================================================================== 穴 7: 削除を含む取り込み直しが止まる

def test_穴7_削除を含む取り込み直しを途中で止めても同じadoptで続きになる(env, monkeypatch):
    src, root = env / "source", env / "register"
    write(src / "a-delete.txt", "old-delete")
    write(src / "b-update.txt", "old-update")
    A.adopt(str(src), str(root), plan=plan_of("**"), by="first", at=AT)
    (src / "a-delete.txt").unlink()
    write(src / "b-update.txt", "new-update")

    def stop(a, b):
        raise InterruptedError("削除のあと・置き換えの前で止まる")

    monkeypatch.setattr(K, "_replace", stop)
    with pytest.raises(InterruptedError):
        A.adopt(str(src), str(root), plan=plan_of("**"), by="writer", at=AT)
    monkeypatch.undo()
    assert K.pending(str(root)) is not None
    result = A.adopt(str(src), str(root), plan=plan_of("**"), by="retry", at=AT)
    assert result["continued"] and result["same"]
    assert K.pending(str(root)) is None
    assert sorted(p.name for p in root.iterdir() if p.name != ".thth") == ["b-update.txt"]


# ================================================================== 穴 8: 実行の印だけの変化

def test_穴8_実行の印だけが変わった取り込み直しも入れ替えて控え戻すと印も同じ(env):
    src, reg = env / "source", env / "reg"
    write(src / "run.sh", "run\n")
    os.chmod(src / "run.sh", 0o644)
    first = A.adopt(str(src), str(reg), plan=plan_of("**"), by="m", at=AT)
    os.chmod(src / "run.sh", 0o755)
    second = A.adopt(str(src), str(reg), plan=plan_of("**"), by="m", at=AT)
    assert second["same"] and second["changed"] == 1
    assert os.stat(reg / "run.sh").st_mode & stat.S_IXUSR
    assert K.load_snapshot(str(reg), "last")["id"] != K.load_snapshot(str(reg), "first")["id"]
    out = env / "out"
    A.restore(str(reg), str(out), at_snapshot="last")
    assert os.stat(out / "run.sh").st_mode & stat.S_IXUSR
    out2 = env / "out2"
    A.restore(str(reg), str(out2), at_snapshot="first")
    assert not os.stat(out2 / "run.sh").st_mode & stat.S_IXUSR
    assert first["fp"] == second["fp"]  # 数・名前・sha256 は同じ


# ================================================================== 穴 9: 64 KB を超える枚

@pytest.mark.parametrize("max_bytes,ok", [(None, False), (80000, True)])
def test_穴9_既定の上限を超える枚は宣言が無ければ取り込みの時点で断り宣言があれば通る(env, max_bytes, ok):
    src = env / "source"
    payload = json.dumps({"text": "x" * 70000}).encode()
    write(src / "large.json", payload)
    decl = env / "decl"
    kind = {"schema": "thth.kind/1", "kind": "large", "place": "*.json", "format": "json", "id": {"name": True}}
    if max_bytes:
        kind["max_bytes"] = max_bytes
    write(decl / "kinds/large.json", json.dumps(kind))
    reg = env / "reg"
    if ok:
        result = A.adopt(str(src), str(reg), plan=plan_of("*.json"), by="m", at=AT, decl=str(decl))
        assert result["same"]
        assert all(not r.problems for r in V.open_vessel(str(reg)).all_records)
    else:
        exc = refused(A.adopt, str(src), str(reg), plan=plan_of("*.json"), by="m", at=AT, decl=str(decl))
        assert exc.detail == "too_large" and exc.extra["files"][0]["path"] == "large.json"
        assert not reg.exists()


# ================================================================== 穴 10: 試験 2 (b) の実物の再演（台本の確かめ）

@pytest.mark.skipif(not V.yamlread.available(), reason="PyYAML が無い機械（pip install 'thth[yaml]'）")
def test_穴10_再演の台本は2つのcommitの3枚ずつを置き換えて変わった記録の判断だけを外す(env):
    module = _realcheck()
    src = env / "kopicha"
    write(src / "data/knowledge/claims.csv",
          "id,statement_ja,object_text_ja,claim_status\nc-1,一,x,supported\nc-2,二,y,supported\n")
    write(src / "data/knowledge/evidence.csv", "id,claim_id,finding_ja\ne-1,c-1,あ\ne-2,c-2,い\n")
    write(src / "data/knowledge/article-claims.csv", "file,claim_id,statement_hash,status,added_at\n"
          "a.md,c-1,h1,supported,2026-10-01\na.md,c-2,h2,supported,2026-10-01\n")
    write(src / "data/knowledge/entities.csv", "id,entity_type,canonical_name\norigin-a,origin,あ\n")
    write(src / "src/content/origins/assam.yaml", "title: アッサム\n")
    write(src / "src/content/origins/sikkim.yaml", "title: シッキム\ndraft: true\n")
    reg = env / "reg"
    decl = module.write_decl(env / "decl", module.KOPICHA_KINDS)
    A.adopt(str(src), str(reg), plan=plan_of("**"), by="m", at=AT, decl=decl)
    replay = env / "replay"
    for rel in module.REPLAY["8ead0fc"]:
        write(replay / "8ead0fc/before" / rel, (src / rel).read_bytes())
    after8 = {
        "data/knowledge/claims.csv":
            "id,statement_ja,object_text_ja,claim_status\nc-1,一（直し）,x,supported\nc-2,二,y,supported\n",
        "data/knowledge/evidence.csv": "id,claim_id,finding_ja\ne-1,c-1,あ（直し）\ne-2,c-2,い\n",
        "data/knowledge/article-claims.csv": "file,claim_id,statement_hash,status,added_at\n"
                                             "a.md,c-1,h9,supported,2026-10-02\na.md,c-2,h2,supported,2026-10-01\n"}
    for rel, text in after8.items():
        write(replay / "8ead0fc/after" / rel, text)
    write(replay / "3398d6d28/before/src/content/origins/sikkim.yaml", "title: シッキム\ndraft: true\n")
    write(replay / "3398d6d28/before/data/knowledge/entities.csv", (src / "data/knowledge/entities.csv").read_bytes())
    write(replay / "3398d6d28/before/data/knowledge/article-claims.csv", after8["data/knowledge/article-claims.csv"])
    write(replay / "3398d6d28/after/src/content/origins/sikkim.yaml", "title: シッキム\n")
    write(replay / "3398d6d28/after/data/knowledge/entities.csv",
          "id,entity_type,canonical_name\norigin-a,origin,あ\norigin-sikkim,origin,シッキム\n")
    write(replay / "3398d6d28/after/data/knowledge/article-claims.csv",
          after8["data/knowledge/article-claims.csv"] + "sikkim.md,c-2,h2,supported,2026-10-03\n")
    out = module.kopicha_replay(reg, replay)
    first = out["8ead0fc"]
    assert first["files_changed"] == sorted(module.REPLAY["8ead0fc"])
    assert {r.split(":")[0] for r in first["records_moved"]} == {"article_claim", "claim", "evidence"}
    assert len(first["records_moved"]) == 3 and "claim:c-1" in first["records_moved"]
    assert first["stale_only_moved"] and "claim:c-1" in first["stale"] and "claim:c-2" not in first["stale"]
    second = out["3398d6d28"]
    assert second["files_changed"] == sorted(module.REPLAY["3398d6d28"])
    assert second["records_moved"] == ["page:sikkim"] and second["stale"] == ["page:sikkim"]
    assert len(second["records_added"]) == 2  # 点 1・控え 1
