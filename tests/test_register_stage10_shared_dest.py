"""台帳の器 段 10 の向き替えの形の試験（`docs/受け入れ_台帳の器_段10_2026-11-14.md` の「10-5 の前に分かった穴 2 つ」）。

こぴちゃの向き替えは、repo の中に台帳のフォルダ（正本）を置き、repo の根を書き出した写しにする。そのとき:

1. 読者の出し口（site）と内の出し口（tools）が、同じ書き出し先（repo の根）を持つ。内の出し口が `dest_shared_with` で
   読者の出し口を名指しし、種類も置き場の型も重ならないときだけ許す（§7.1 の断りは、名指しの無いときそのまま）。
   互いの材料は、相手の旧い材料に数えない。
2. 台帳が書き出し先の中にあるとき、旧い材料の見張りは、出し口の置き場の型が載るフォルダだけを見る（repo の道具や
   文書を ignore に並べずに済む）。置き場のフォルダに残った旧い材料は、これまでどおり拾う。

見本は、こぴちゃの形を小さくした物（CSV と JSON だけ。PyYAML の無い箱でも走る）。
"""
from __future__ import annotations

import json
import pathlib

import pytest

from thth.register import adopt as A
from thth.register import route as R
from thth.register import seal as S
from thth.register import vessel as V
from thth.register.errors import RegisterRefused

AT = "2027-03-20T10:00:00+09:00"
NOW = "2027-03-21"

KINDS = {
    "claim": {"schema": "thth.kind/1", "kind": "claim", "place": "data/knowledge/claims.csv", "format": "csv",
              "id": {"field": "id"}},
    "page": {"schema": "thth.kind/1", "kind": "page", "place": "src/content/pages/*.json", "format": "json",
             "records": "file", "id": {"name": True}},
    "hyp": {"schema": "thth.kind/1", "kind": "hyp", "place": "data/knowledge/hyps.csv", "format": "csv",
            "id": {"field": "id"}},
}
CLAIMS = "id,text\nc1,水浸出物17.1%\nc2,テアニン18.6%\n"
HYPS = "id,idea\nh1,ミルクで渋みが減る\n"
PAGE = '{\n  "title": "CTC"\n}\n'
# repo のほかの物（器の材料でない）
OTHERS = {"README.md": "# repo\n", "scripts/build.mjs": "// build\n", "collectors/run.mjs": "// run\n",
          "data/knowledge/staging/a1/claims.csv": "id,text\nx,作業場\n", "data/raw/shop.json": "{}\n",
          ".github/workflows/observe.yml": "name: observe\n"}


def outlets(**tools_over) -> dict:
    tools = {"audience": "internal", "kinds": ["hyp"], "dest": ".", "dest_shared_with": ["site"]}
    tools.update(tools_over)
    tools = {k: v for k, v in tools.items() if v is not None}
    return {"schema": "thth.outlets/1", "outlets": {
        "site": {"audience": "reader", "kinds": ["claim", "page"], "fact": [{"question": "may_publish@site"}],
                 "dest": "."},
        "tools": tools}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key, sub in (("THTH_ROOT", "root"), ("HOME", "home"), ("XDG_CONFIG_HOME", "config")):
        (tmp_path / sub).mkdir()
        monkeypatch.setenv(key, str(tmp_path / sub))
    monkeypatch.delenv(S.ENV_PUBLIC, raising=False)
    try:
        S.openssl()
    except RegisterRefused:
        pytest.skip("OpenSSL 3 か 4 が決まった場所に無い")
    S.init_key()
    return tmp_path


def put(base: pathlib.Path, rel: str, text: str) -> None:
    (base / rel).parent.mkdir(parents=True, exist_ok=True)
    (base / rel).write_text(text, encoding="utf-8", newline="")


def repo_with_ledger(env, decl=None, kinds=None) -> tuple:
    """repo の根（写し）と、その中の台帳のフォルダ `register/`（正本）。写しは正本と同じバイトで始める。"""
    repo = env / "repo"
    led = repo / "register"
    (led / ".thth/kinds").mkdir(parents=True)
    for name, body in (kinds or KINDS).items():
        (led / f".thth/kinds/{name}.json").write_text(json.dumps(body, ensure_ascii=False))
    (led / ".thth/outlets.json").write_text(json.dumps(decl or outlets(), ensure_ascii=False, indent=1))
    for base in (led, repo):
        put(base, "data/knowledge/claims.csv", CLAIMS)
        put(base, "data/knowledge/hyps.csv", HYPS)
        put(base, "src/content/pages/ctc.json", PAGE)
    for rel, text in OTHERS.items():
        put(repo, rel, text)
    return repo, led


def accept(led, outlet, to):
    plan = R.accept_plan(str(led), outlet, str(to), AT)
    return R.accept(str(led), outlet, str(to), AT, confirm=plan["digest"], at=AT), plan


def baseline_and_accept(repo, led):
    R.baseline(str(led), "site", by="masaru", at="2027-03-01T00:00:00+09:00")
    accept(led, "site", repo)
    accept(led, "tools", repo)


def words(v) -> set:
    return {p["word"] for p in v["problems"]}


def check(led, outlet, to) -> dict:
    return R.export(str(led), outlet, str(to), AT, check=True)


# ------------------------------------------------------------------ 1. 宣言

def test_名指しの無い同じ書き出し先は今までどおり断る(env):
    with pytest.raises(RegisterRefused) as exc:
        repo, led = repo_with_ledger(env, outlets(dest_shared_with=None))
        R.load_outlets(str(led), V.open_vessel(str(led)))
    assert exc.value.code == "register_outlet_unknown" and "dest_shared:site" in str(exc.value)


def test_名指しがあり種類も置き場も重ならなければ読める(env):
    repo, led = repo_with_ledger(env)
    got = R.load_outlets(str(led), V.open_vessel(str(led)))
    assert got["tools"].shares_dest == ("site",)
    assert [o.name for o in R.shared_partners(str(led), V.open_vessel(str(led)), got["site"])] == ["tools"]


@pytest.mark.parametrize("over,word", [
    ({"dest_shared_with": ["nosuch"]}, "dest_shared_with:nosuch"),
    ({"dest": "tools"}, "dest_shared_with:site"),                      # 名指ししたのに書き出し先が違う
    ({"kinds": ["hyp", "claim"]}, "overlap"),                          # 同じ種類
    ({"dest_shared_with": "site"}, "dest_shared_with"),                # 並びでない
    ({"dest_shared_with": ["site", "site"]}, "dest_shared_with"),
    ({"dest": "sub"}, "dest_shared_with:site"),                        # 入れ子の書き出し先に名指し
])
def test_名指しの形が違えば断る(env, over, word):
    repo, led = repo_with_ledger(env, outlets(**over))
    with pytest.raises(RegisterRefused) as exc:
        R.load_outlets(str(led), V.open_vessel(str(led)))
    assert exc.value.code == "register_outlet_unknown" and word in str(exc.value)


def test_読者の出し口の側から名指しはできない(env):
    decl = outlets(dest_shared_with=None)
    decl["outlets"]["site"]["dest_shared_with"] = ["tools"]
    repo, led = repo_with_ledger(env, decl)
    with pytest.raises(RegisterRefused) as exc:
        R.load_outlets(str(led), V.open_vessel(str(led)))
    assert "outlets:site:dest_shared_with" in str(exc.value)


def test_置き場の型が重なれば断る(env):
    kinds = dict(KINDS, hyp=dict(KINDS["hyp"], place="data/knowledge/*.csv"))   # claims.csv にも当たる
    repo, led = repo_with_ledger(env, kinds=kinds)
    with pytest.raises(RegisterRefused) as exc:
        R.load_outlets(str(led), V.open_vessel(str(led)))
    assert "overlap" in str(exc.value)


@pytest.mark.parametrize("x,y,overlap", [
    ("data/a*.csv", "data/*b.csv", True),
    ("data/*.csv", "data/knowledge/x.csv", True),
    ("src/content/pages/*.json", "data/knowledge/claims.csv", False),
    ("data/knowledge/claims.csv", "data/knowledge/hyps.csv", False),
    ("data/knowledge/claims.csv", "data/knowledge/claims.csv", True),
    ("src/content/pages/*.json", "src/content/pages/*.md", True),     # 型でまとめて取るフォルダの中（穴 3）
    ("src/content/pages/*.json", "src/content/pages/hyps.csv", True),
    ("src/content/pages/*.json", "src/content/pages/sub/x.csv", True),
    ("src/*/x.json", "src/content/*.json", True),
    ("src/content/articles/*.md", "data/knowledge/hypotheses.csv", False),  # こぴちゃの形
])
def test_置き場の型の重なりの読み(x, y, overlap):
    assert R._patterns_overlap([x], [y]) is overlap


# ------------------------------------------------------------------ 2. repo の根へ 2 つの出し口を書き出す

def test_repoの根へsiteとtoolsを書き出しても元と同じバイトで両方とも緑(env):
    repo, led = repo_with_ledger(env)
    before = {p: (repo / p).read_bytes() for p in ("data/knowledge/claims.csv", "data/knowledge/hyps.csv",
                                                     "src/content/pages/ctc.json", *OTHERS)}
    baseline_and_accept(repo, led)
    assert {p: (repo / p).read_bytes() for p in before} == before
    for o in ("site", "tools"):
        assert R.verify(str(led), o, str(repo), NOW, no_previous=True)["ok"], o
        got = check(led, o, repo)
        assert got["stale"] == [] and got["differs"] == [] and got["same_as_seal"], o


def test_相手の材料は旧い材料に数えないが自分の置き場の旧い材料は拾う(env):
    repo, led = repo_with_ledger(env)
    baseline_and_accept(repo, led)
    put(repo, "src/content/pages/zz-old.json", '{"title": "old"}\n')     # 台帳に無い頁
    put(repo, "data/knowledge/old.csv", "id\nx\n")                       # どちらの型にも当たらない置き場の物
    assert check(led, "site", repo)["stale"] == ["data/knowledge/old.csv", "src/content/pages/zz-old.json"]
    assert check(led, "tools", repo)["stale"] == ["data/knowledge/old.csv"]
    assert "stale_material" in words(R.verify(str(led), "site", str(repo), NOW, no_previous=True))


def test_置き場のフォルダの外のrepoの物は見張らない(env):
    repo, led = repo_with_ledger(env)
    baseline_and_accept(repo, led)
    put(repo, "scripts/new-tool.mjs", "// new\n")
    put(repo, "data/knowledge/staging/b2/claims.csv", "id,text\ny,作業場\n")
    put(repo, "notes.txt", "メモ\n")
    for o in ("site", "tools"):
        assert check(led, o, repo)["stale"] == [], o
        assert R.verify(str(led), o, str(repo), NOW, no_previous=True)["ok"], o


def test_台帳が書き出し先の外なら今までどおり書き出し先の全部を見る(env):
    repo, led = repo_with_ledger(env)
    R.baseline(str(led), "site", by="masaru", at="2027-03-01T00:00:00+09:00")
    out = env / "out"
    accept(led, "site", out)
    put(out, "scripts/new-tool.mjs", "// new\n")
    assert check(led, "site", out)["stale"] == ["scripts/new-tool.mjs"]


def test_片方の写しを壊すとその出し口だけ赤(env):
    repo, led = repo_with_ledger(env)
    baseline_and_accept(repo, led)
    put(repo, "data/knowledge/hyps.csv", "id,idea\nh1,手で書き換えた\n")
    assert not R.verify(str(led), "tools", str(repo), NOW, no_previous=True)["ok"]
    assert R.verify(str(led), "site", str(repo), NOW, no_previous=True)["ok"]
    put(repo, "data/knowledge/hyps.csv", HYPS)
    put(repo, "data/knowledge/claims.csv", CLAIMS.replace("17.1", "17.9"))
    assert "material_mismatch" in words(R.verify(str(led), "site", str(repo), NOW, no_previous=True))
    assert R.verify(str(led), "tools", str(repo), NOW, no_previous=True)["ok"]


def test_正本を直すと受け入れるまでsiteは赤で受け入れれば写しに届く(env):
    repo, led = repo_with_ledger(env)
    baseline_and_accept(repo, led)
    A.row_set(str(led), "claim:c1", {"text": "水浸出物17.2%"}, by="corrector", at="2027-03-02T00:00:00+09:00")
    assert not R.verify(str(led), "site", str(repo), NOW, no_previous=True)["ok"]
    R.judge(str(led), "claim:c1", "may_publish@site", by="checker", at="2027-03-02T01:00:00+09:00", conclusion="good")
    accept(led, "site", repo)
    assert "17.2%" in (repo / "data/knowledge/claims.csv").read_text(encoding="utf-8")
    assert R.verify(str(led), "site", str(repo), NOW, no_previous=True)["ok"]
    assert R.verify(str(led), "tools", str(repo), NOW, no_previous=True)["ok"]


# ------------------------------------------------------------------ 3. 検収（Opus 11/14）の重い穴 3 つと足りない試験

def test_穴1_型の下のフォルダに残った旧い材料も拾う(env):
    repo, led = repo_with_ledger(env)
    baseline_and_accept(repo, led)
    put(repo, "src/content/pages/sub/old.json", '{"title": "old"}\n')   # fnmatch の * は / を越えるので書き出されうる
    assert check(led, "site", repo)["stale"] == ["src/content/pages/sub/old.json"]
    assert "stale_material" in words(R.verify(str(led), "site", str(repo), NOW, no_previous=True))


def test_穴2_出し口の種類を減らすと前の封の材料が旧い材料に挙がり受け入れが止まる(env):
    repo, led = repo_with_ledger(env)
    baseline_and_accept(repo, led)
    decl = outlets()
    decl["outlets"]["site"]["kinds"] = ["claim"]                          # page を外す
    (led / ".thth/outlets.json").write_text(__import__("json").dumps(decl, ensure_ascii=False))
    plan = R.accept_plan(str(led), "site", str(repo), AT)
    assert plan["stale"] == ["src/content/pages/ctc.json"]
    with pytest.raises(RegisterRefused) as exc:                          # 残したまま封をしない
        R.accept(str(led), "site", str(repo), AT, confirm=plan["digest"], at=AT)
    assert exc.value.code == "register_material_stale"


def test_穴3_内の材料を読者が型でまとめて取るフォルダに置く宣言は断る(env):
    kinds = dict(KINDS, hyp=dict(KINDS["hyp"], place="src/content/pages/hyps.csv"))
    repo, led = repo_with_ledger(env, kinds=kinds)
    with pytest.raises(RegisterRefused) as exc:
        R.load_outlets(str(led), V.open_vessel(str(led)))
    assert "overlap" in str(exc.value)


def test_相手の置き場に当たる出力は書く前に断る(env):
    repo, led = repo_with_ledger(env)
    vessel = V.open_vessel(str(led))
    site = R.outlet_of(str(led), vessel, "site")
    assert R.shared_clash(str(led), vessel, site, ["data/knowledge/hyps.csv", "data/knowledge/claims.csv"]) == \
        ["data/knowledge/hyps.csv"]
    assert R.shared_clash(str(led), vessel, site, ["data/knowledge/claims.csv"]) == []


def test_置き場のフォルダのsymlinkは数える(env):
    repo, led = repo_with_ledger(env)
    baseline_and_accept(repo, led)
    (repo / "src/content/pages/hidden").symlink_to(env / "elsewhere", target_is_directory=True)
    assert check(led, "site", repo)["stale"] == ["src/content/pages/hidden"]
