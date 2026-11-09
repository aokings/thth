"""release の関門の柵（設計「監査と審査の機械化と前向きな断り」§6・§7 の段 D）。

完成の条件: 偽の版（リリースノートが無い・危ない領域に触るのに監査の書が無い・危ないが 1 以上）で止まり、
本物の形で通る。作り物の git の repo を tmp に作って `tools/release_gate.py` の `check` にかける
（CI の結果と互換の柵は差し替える）。
"""
from __future__ import annotations

import fnmatch
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from thth import refusal_catalog as rc

ROOT = Path(__file__).resolve().parent.parent


def _gate():
    spec = importlib.util.spec_from_file_location("release_gate", ROOT / "tools" / "release_gate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_gate"] = module  # dataclass が自分のモジュールを引く
    spec.loader.exec_module(module)
    return module


gate = _gate()

GREEN = lambda root, sha: ("green", "test.yml run 1（completed・success）")  # noqa: E731
COMPAT_OK = lambda root: (True, "")  # noqa: E731


class Repo:
    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.path), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
             "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
            check=True, capture_output=True, text=True).stdout.strip()

    def write(self, rel: str, text: str) -> None:
        path = self.path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message: str, **files: str) -> str:
        for rel, text in files.items():
            self.write(rel.replace("__", "/"), text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def version(self, version: str, notes: bool = True, server: str | None = None) -> None:
        self.write("thth/VERSION", version + "\n")
        sv = server or version
        self.write("server.json", json.dumps({"version": sv, "packages": [{"version": sv}]}))
        if notes:
            self.write(f"docs/リリースノート_{version}_2026-11-04.md", f"# {version}\n")


def audit_text(range_: str, dangers: int = 0, fixed: str = "なし", waived: str = "", fence: str | None = "なし（試験）",
               auditor: str = "Fable") -> str:
    head = f"---\nrange: {range_}\ndangers: {dangers}\nwaived: {waived}\nfixed: {fixed}\nauditor: {auditor}\n---\n"
    body = "# 監査\n\n## 出す前に直す\n\n- なし\n"
    if fence is not None:
        body += f"\n## 柵にしたもの\n\n{fence}\n"
    return head + body


@pytest.fixture
def repo(tmp_path):
    r = Repo(tmp_path / "repo")
    r.version("1.0.0")
    r.commit("1.0.0", **{"thth__guard.py": "x = 1\n", "thth__other.py": "y = 1\n",
                         "callback__src__index.js": "export default {}\n"})
    r.git("tag", "v1.0.0")
    return r


def codes(repo, ref="HEAD", **kw):
    kw.setdefault("ci", GREEN)
    kw.setdefault("compat", COMPAT_OK)
    return [b.code for b in gate.check(repo.path, ref, **kw)]


# ---- 本物の形で通る ----

def test_危ない領域に触らない版は監査の書なしで通る(repo):
    repo.write("thth/other.py", "y = 2\n")
    repo.version("1.1.0")
    repo.commit("1.1.0")
    assert codes(repo) == []


def test_危ない領域に触る版は覆う監査の書があり危ないが0なら通る(repo):
    repo.commit("guard を直す", **{"thth__guard.py": "x = 2\n"})
    audited = repo.git("rev-parse", "--short", "HEAD")
    repo.version("1.1.0")
    repo.write("docs/監査_1.1.0_2026-11-04.md", audit_text(f"v1.0.0..{audited}"))
    repo.commit("1.1.0")
    assert codes(repo) == []


def test_監査のあとの直しはfixedに並べれば覆う(repo):
    first = repo.commit("guard", **{"thth__guard.py": "x = 2\n"})
    fix = repo.commit("監査の直し", **{"thth__guard.py": "x = 3\n"})
    repo.version("1.1.0")
    repo.write("docs/監査_1.1.0_2026-11-04.md", audit_text(f"v1.0.0..{first[:7]}", fixed=fix[:7]))
    repo.commit("1.1.0")
    assert codes(repo) == []


def test_危ないが残っても却下の理由があれば通る(repo):
    audited = repo.commit("guard", **{"thth__guard.py": "x = 2\n"})
    repo.version("1.1.0")
    repo.write("docs/監査_1.1.0_2026-11-04.md",
               audit_text(f"v1.0.0..{audited[:7]}", dangers=1, waived="masaru 11/04 次の版で直す"))
    repo.commit("1.1.0")
    assert codes(repo) == []


def test_tagを打ったcommitそのものは通る(repo):
    repo.write("thth/other.py", "y = 2\n")
    repo.version("1.1.0")
    repo.commit("1.1.0")
    repo.git("tag", "v1.1.0")
    assert codes(repo) == []


# ---- 偽の版で止まる ----

def test_リリースノートが無い版は止まる(repo):
    repo.write("thth/other.py", "y = 2\n")
    repo.version("1.1.0", notes=False)
    repo.commit("1.1.0")
    assert codes(repo) == ["release_notes_missing"]


def test_危ない領域に触るのに監査の書が無い版は止まる(repo):
    repo.commit("Worker を直す", **{"callback__src__index.js": "export default {fetch(){}}\n"})
    repo.version("1.1.0")
    repo.commit("1.1.0")
    blocks = gate.check(repo.path, "HEAD", ci=GREEN, compat=COMPAT_OK)
    assert [b.code for b in blocks] == ["release_audit_missing"]
    assert "callback" in blocks[0].seen


def test_危ないが1以上の監査の書は止まる(repo):
    audited = repo.commit("guard", **{"thth__guard.py": "x = 2\n"})
    repo.version("1.1.0")
    repo.write("docs/監査_1.1.0_2026-11-04.md", audit_text(f"v1.0.0..{audited[:7]}", dangers=2))
    repo.commit("1.1.0")
    assert codes(repo) == ["release_audit_dangers_open"]


def test_監査のあとに危ない領域を変えてfixedに無ければ止まる(repo):
    audited = repo.commit("guard", **{"thth__guard.py": "x = 2\n"})
    repo.commit("監査のあとの変更", **{"thth__guard.py": "x = 9\n"})
    repo.version("1.1.0")
    repo.write("docs/監査_1.1.0_2026-11-04.md", audit_text(f"v1.0.0..{audited[:7]}"))
    repo.commit("1.1.0")
    assert codes(repo) == ["release_audit_missing"]


def test_前の版の監査の書は今の版の変更を覆わない(repo):
    repo.write("docs/監査_1.0.0_2026-11-01.md", audit_text("v0.9.0..v1.0.0"))
    repo.commit("古い監査の書")
    repo.commit("guard", **{"thth__guard.py": "x = 2\n"})
    repo.version("1.1.0")
    repo.commit("1.1.0")
    assert "release_audit_missing" in codes(repo)


@pytest.mark.parametrize("kw,problem", [
    ({"fence": None}, "柵にしたもの"),
    ({"fence": "<!-- 空 -->"}, "空"),
    ({"auditor": ""}, "auditor"),
])
def test_監査の書の形が崩れていたら止まる(repo, kw, problem):
    audited = repo.commit("guard", **{"thth__guard.py": "x = 2\n"})
    repo.version("1.1.0")
    repo.write("docs/監査_1.1.0_2026-11-04.md", audit_text(f"v1.0.0..{audited[:7]}", **kw))
    repo.commit("1.1.0")
    blocks = gate.check(repo.path, "HEAD", ci=GREEN, compat=COMPAT_OK)
    assert [b.code for b in blocks] == ["release_audit_malformed", "release_audit_missing"]
    assert problem in blocks[0].seen


def test_版を上げていなければ止まる(repo):
    repo.commit("直し", **{"thth__other.py": "y = 2\n"})
    assert codes(repo) == ["release_version_not_bumped"]


def test_VERSIONとserver_jsonが食い違えば止まる(repo):
    repo.version("1.1.0", server="1.0.9")
    repo.commit("1.1.0")
    assert codes(repo) == ["release_version_mismatch"]


@pytest.mark.parametrize("state,code", [("red", "release_ci_red"), ("waiting", "release_ci_waiting"),
                                        ("unreadable", "release_ci_unreadable")])
def test_CIが緑でなければ止まる(repo, state, code):
    repo.version("1.1.0")
    repo.commit("1.1.0")
    assert codes(repo, ci=lambda root, sha: (state, "見た")) == [code]
    assert codes(repo, ci=lambda root, sha: (state, "見た"), skip_ci=True) == []


def test_互換の柵が赤なら止まる(repo):
    repo.version("1.1.0")
    repo.commit("1.1.0")
    assert codes(repo, compat=lambda root: (False, "tests/test_server_json.py::x")) == ["release_compat_red"]


def test_作業木の未commitの変更は見ない(repo):
    repo.commit("直し", **{"thth__other.py": "y = 2\n"})
    repo.version("1.1.0")  # 書いただけで commit しない
    assert codes(repo) == ["release_version_not_bumped"]


@pytest.mark.parametrize("mark", ["Unpublished update", "未公開の更新案"])
def test_privacyの施行日が未設定のまま残っていれば止まる(repo, mark):
    repo.version("1.1.0")
    repo.commit("1.1.0", **{"callback__public__privacy__index.html": f"<p>{mark} — x</p>\n"})
    blocks = gate.check(repo.path, "HEAD", ci=GREEN, compat=COMPAT_OK, skip_ci=True)
    hit = [b for b in blocks if b.code == "release_privacy_date_unset"]
    assert hit and mark in hit[0].seen


def test_privacyに施行日が入っていれば止めない(repo):
    repo.version("1.1.0")
    repo.commit("1.1.0", **{"callback__public__privacy__index.html": "<p>Effective 2026-11-09</p>\n"})
    assert "release_privacy_date_unset" not in codes(repo, skip_ci=True)


def test_危ない領域にMetaの書く口と認可がある():
    for path, name in (("thth/meta_write.py", "meta_write"), ("thth/meta_events.py", "meta_write"),
                       ("thth/meta_brand.py", "meta_write"), ("thth/meta_shop.py", "meta_write"),
                       ("thth/authflow.py", "auth_keys"), ("thth/adapters/auth_facebook.py", "auth_keys")):
        assert name in gate.areas_of(path), path


# ---- 出力の形（前向きな断り） ----

def test_止めるときは1行目が符丁で3つが続く(repo):
    repo.version("1.1.0", notes=False)
    repo.commit("1.1.0")
    out = gate.render(gate.check(repo.path, "HEAD", ci=GREEN, compat=COMPAT_OK)).splitlines()
    assert out[0] == "release_blocked: release_notes_missing"
    assert out[1].startswith("なぜ: ") and out[2].startswith("通すには: ") and out[3].startswith("もっと良く: ")
    assert out[4].startswith("見たもの: ")


def test_関門の符丁はすべて目録にあり3つがそろう():
    import ast
    tree = ast.parse((ROOT / "tools" / "release_gate.py").read_text(encoding="utf-8"))
    found = {node.args[0].value for node in ast.walk(tree)
             if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_refuse"
             and node.args and isinstance(node.args[0], ast.Constant)}
    assert len(found) == 11
    for code in found:
        entry = rc.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"], code


# ---- 表と雛形の柵 ----

def test_危ない領域の表の型はどれも今のファイルに当たる():
    """名前を変えたファイルが表から黙って外れないように（表の型が 1 つも当たらなければ赤）。"""
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True,
                             check=True).stdout.splitlines()
    dead = [f"{name}: {pattern}" for name, patterns in gate.DANGER_AREAS.items() for pattern in patterns
            if not any(fnmatch.fnmatchcase(path, pattern) for path in tracked)]
    assert dead == []


def test_設計の表の名前がすべて危ない領域にある():
    for name in ("server_writes", "server_reads", "guard", "plaza_redact", "leave", "callback",
                 "privacy_terms", "approval"):
        assert name in gate.DANGER_AREAS


def test_雛形の頭は関門が読める():
    text = (ROOT / gate.AUDIT_TEMPLATE).read_text(encoding="utf-8")
    audit = gate.parse_audit(gate.AUDIT_TEMPLATE, text)
    assert audit.problem is None
    assert audit.dangers == 0 and audit.fixed == [] and audit.auditor


def test_publishは関門のあとで走る():
    text = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    assert "python tools/release_gate.py" in text
    assert "needs: gate" in text
    # main への push の試験（test.yml）は関門で遅くしない。
    assert "release_gate" not in (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
