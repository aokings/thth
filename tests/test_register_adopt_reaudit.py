"""台帳の器 段 2 の柵（その 2）: Codex の再検収（2026-11-14・`~/Documents/THTH/codex/答え_段2の再検収_2026-11-14.md`）の
6 つと、同じ型の穴。反例は再検収の再現の台本（`register-stage2-reaudit/controller/`）と同じ形にした。
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib

import pytest

from thth import cli
from thth.register import adopt as A, judgment as J, keep as K, vessel as V
from thth.register.errors import RegisterRefused

ROOT = pathlib.Path(__file__).resolve().parents[1]
AT = "2026-11-14T12:00:00+09:00"


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
    return {e["path"]: K.view_bytes(str(root), e) for e in K.view(str(root))}


# ================================================================== 1: 遅れて終わった再開が次の段取りを消す

def test_再1_遅れて終わった再開は次の入れ替えの段取りを書き換えず消さない(env, monkeypatch):
    root = env / "register"
    for f in "abc":
        write(root / f"{f}.txt", f"old-{f}")
    plan = K.prepare(str(root), {"a.txt": b"new-a"}, what="merge", by="first", at=AT)
    K.begin(str(root), plan)
    stale_copy = K.pending(str(root))        # 1 人めが読んだ段取り（このあと止まる）
    assert K.apply(str(root), by="second", at=AT)["applied"] == 1   # 2 人めが済ませる
    third = K.prepare(str(root), {"b.txt": b"new-b", "c.txt": b"new-c"}, what="merge", by="third", at=AT)
    K.begin(str(root), third)
    K._step(str(root), K.pending(str(root)), 0, *K._from_to(K.pending(str(root))["entries"][0], back=False))
    # 1 人めが、読んであった古い段取りで後始末まで進む。
    monkeypatch.setattr(K, "pending", lambda r: stale_copy)
    try:
        K.apply(str(root), by="first-late", at=AT)
    except RegisterRefused:
        pass
    monkeypatch.undo()
    now = K.pending(str(root))
    assert now is not None and now["id"] == third["id"] and "done" not in now
    assert visible(root) == {"a.txt": b"new-a", "b.txt": b"old-b", "c.txt": b"old-c"}
    result = K.apply(str(root), by="recovery", at=AT)
    assert result["applied"] == 1 and K.pending(str(root)) is None
    assert (root / "c.txt").read_bytes() == b"new-c"


# ================================================================== 2: 断りながらほかの者の書き込みを上書きする

def test_再2_退かした枚と新しい道の両方にほかの者が書けば断り何も書き換えない(env, monkeypatch):
    root = env / "register"
    write(root / "a.txt", "old")
    plan = K.prepare(str(root), {"a.txt": b"new"}, what="merge", by="w", at=AT)
    K.begin(str(root), plan)
    fd = os.open(root / "a.txt", os.O_RDWR)
    real = K._replace

    def after_publication(tmp, path):
        real(tmp, path)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, b"first-edit")          # 置き換えの前から開いていた枚（退かした枚）へ
        os.ftruncate(fd, len(b"first-edit"))
        pathlib.Path(path).write_bytes(b"second-edit")   # 新しい道へ

    monkeypatch.setattr(K, "_replace", after_publication)
    try:
        with pytest.raises(RegisterRefused) as info:
            K.apply(str(root), by="r", at=AT)
    finally:
        os.close(fd)
    assert info.value.code == "register_batch_conflict"
    assert (root / "a.txt").read_bytes() == b"second-edit"
    aside = info.value.extra.get("aside")
    assert aside and (root / aside).read_bytes() == b"first-edit"   # どちらの書き込みも残る
    assert K.pending(str(root)) is not None


# ================================================================== 3: 内側の明示の番号が消えても赤にならない

def test_再3_内側の行の明示の番号が消えたら差は赤(env, capsys):
    source, register, decl = env / "source", env / "register", env / "decl"
    kind = {"schema": "thth.kind/1", "kind": "record", "place": "*.json", "format": "json", "records": "file",
            "id": {"name": True}, "units": {"groups": [], "items": ["rows", "*"], "id": "id"}}
    write(decl / "kinds/record.json", json.dumps(kind))
    data = {"rows": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}]}
    write(source / "one.json", json.dumps(data))
    A.adopt(str(source), str(register), plan=plan_of("**"), by="m", at=AT, decl=str(decl))
    data["rows"].pop()
    write(register / "one.json", json.dumps(data))
    value = A.diff(str(register), "last..now")
    assert value["red"] and value["units"]["vanished"] == [{"record": "record:one", "unit": "i:b"}]
    rc_ = cli.main(["register", "diff", str(register), "last..now", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc_ != 0 and out["code"] == "register_record_vanished"
    # 番号の無い（鍵の）単位が中身で動くだけなら、赤にしない。
    data["rows"][0]["text"] = "A2"
    write(register / "one.json", json.dumps(data))
    K.save_snapshot(str(register), K.view(str(register)), what="keep", by="m", at=AT)
    data["rows"][0]["text"] = "A3"
    write(register / "one.json", json.dumps(data))
    assert not A.diff(str(register), "last..now")["red"]


# ================================================================== 4: 除く型で範囲に届く symlink を黙って落とす

def test_再4_除く型があっても先の実在の枚が範囲に入るsymlinkは断る(env):
    src, outside = env / "source", env / "outside"
    write(outside / "private.txt", "OUTSIDE\r\n")
    write(outside / "z.txt", "Z\n")
    src.mkdir()
    (src / "link").symlink_to(outside, target_is_directory=True)
    plan = plan_of("link/**/*.txt", exclude=["link/**/[!z]*"])
    assert A.selector(plan)("link/z.txt")
    with pytest.raises(RegisterRefused) as info:
        A.adopt(str(src), str(env / "reg"), plan=plan, by="a", at=AT)
    assert info.value.detail == "symlink" and info.value.extra["links"] == ["link"]


def test_再4_先の実在の枚がどれも除かれるsymlinkは断らない(env):
    src, outside = env / "source", env / "outside"
    write(outside / "pkg/index.txt", "x")
    write(src / "keep/a.txt", "a")
    (src / "keep/node_modules").symlink_to(outside, target_is_directory=True)
    result = A.adopt(str(src), str(env / "reg"), plan=plan_of("keep/**/*.txt", exclude=["keep/node_modules/**"]),
                     by="a", at=AT)
    assert result["files"] == 1 and result["same"]


# ================================================================== 5・6: 穴 10 の再演

def _realcheck():
    spec = importlib.util.spec_from_file_location("realcheck", ROOT / "tools/register_adopt_realcheck.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _replay_inputs(env, module):
    """2 つの commit の、親の木（範囲の中の全部）と、その commit の後の 3 枚。"""
    replay = env / "replay"
    claims = "id,statement_ja,object_text_ja,claim_status\nc-1,一,x,supported\nc-2,二,y,supported\n"
    evidence = "id,claim_id,finding_ja\ne-1,c-1,あ\ne-2,c-2,い\n"
    ac = "file,claim_id,statement_hash,status,added_at\na.md,c-1,h1,supported,2026-10-01\na.md,c-2,h2,supported,2026-10-01\n"
    entities = "id,entity_type,canonical_name\norigin-a,origin,あ\n"
    parent1 = replay / "8ead0fc/parent"
    for rel, text in [("data/knowledge/claims.csv", claims), ("data/knowledge/evidence.csv", evidence),
                      ("data/knowledge/article-claims.csv", ac), ("data/knowledge/entities.csv", entities),
                      ("src/content/origins/assam.yaml", "title: アッサム\n"),
                      ("src/content/origins/sikkim.yaml", "title: シッキム\ndraft: true\n")]:
        write(parent1 / rel, text)
    after1 = {"data/knowledge/claims.csv": claims.replace("c-1,一,", "c-1,一（直し）,"),
              "data/knowledge/evidence.csv": evidence.replace("e-1,c-1,あ", "e-1,c-1,あ（直し）"),
              "data/knowledge/article-claims.csv": ac.replace("a.md,c-1,h1,supported,2026-10-01",
                                                              "a.md,c-1,h9,supported,2026-10-02")}
    for rel, text in after1.items():
        write(replay / "8ead0fc/after" / rel, text)
    # 2 つめの親は、1 つめの後の木（親の木は commit ごとに独立に渡す）。
    parent2 = replay / "3398d6d28/parent"
    for p in parent1.rglob("*"):
        if p.is_file():
            write(parent2 / p.relative_to(parent1), p.read_bytes())
    for rel, text in after1.items():
        write(parent2 / rel, text)
    write(replay / "3398d6d28/after/src/content/origins/sikkim.yaml", "title: シッキム\n")
    write(replay / "3398d6d28/after/data/knowledge/entities.csv", entities + "origin-sikkim,origin,シッキム\n")
    write(replay / "3398d6d28/after/data/knowledge/article-claims.csv",
          after1["data/knowledge/article-claims.csv"] + "sikkim.md,c-2,h2,supported,2026-10-03\n")
    return replay


@pytest.mark.skipif(not V.yamlread.available(), reason="PyYAML が無い機械（pip install 'thth[yaml]'）")
def test_再5_再演は親の木の全部を取り込みその3枚だけを当てる(env):
    module = _realcheck()
    replay = _replay_inputs(env, module)
    out = module.kopicha_replay(env / "shadow", replay)
    for commit in module.REPLAY:
        got = out[commit]
        assert got["basis_is_parent"], commit          # 当てる前の台帳 ＝ 親の木（範囲の中の全部）
        assert got["files_changed"] == sorted(module.REPLAY[commit])
        assert got["others_same_as_parent"]
    assert out["8ead0fc"]["records_moved"] == sorted(out["8ead0fc"]["records_moved"])


@pytest.mark.skipif(not V.yamlread.available(), reason="PyYAML が無い機械（pip install 'thth[yaml]'）")
def test_再6_外れた判断の集合は依る所の動いた記録の集合と等しい(env, monkeypatch):
    module = _realcheck()
    replay = _replay_inputs(env, module)
    out = module.kopicha_replay(env / "shadow", replay)
    first = out["8ead0fc"]
    assert len(first["records_moved"]) == 3
    assert first["stale"] == first["expected_stale"] == first["records_moved"]
    assert first["stale_as_expected"]
    # 根拠と控えの判断だけを外さない誤りを入れると、赤になる。
    real = J.evaluate

    def wrong(vessel, judgment, *a, **k):
        state = real(vessel, judgment, *a, **k)
        kind = judgment["target"]["record"].split(":")[0]
        return "effective" if kind in ("evidence", "article_claim") else state

    monkeypatch.setattr(J, "evaluate", wrong)
    broken = module.kopicha_replay(env / "shadow2", replay)["8ead0fc"]
    assert not broken["stale_as_expected"]
