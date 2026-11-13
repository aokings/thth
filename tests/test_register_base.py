"""台帳の機械の土台（計画 `docs/計画_実店舗_2026-11-13.md` 段 1・`thth/register/`）。

柵:
  - 形の点検: 頭の 7 つの欄・印の語・日付・秘密らしい値。崩れた行は 1 行ずつ直し方を添えて断る。
  - 印の柵: 推測の行・違うと言われた行・退役した行・見直し日を過ぎた行・出してよい印の無い行は材料にしない。
  - 頭を手で「本人が認めた」に書き換えても、THTH の認めた記録（中身の指紋）が無ければ材料にしない。
  - 認めたあとに中身を変えたら、認めたが外れる。
  - 認める操作が台帳に書くのは頭の決まった欄（confirmed・confirmed_at）だけ。
  - 断りの符丁はどれも目録に なぜ・通すには・もっと良く がそろう。
  - 新しいファイルは網の部品を import しない。
  - 見本の台帳（`registers.example/media-sample`・架空の自前のメディア）がそのまま通る。
網には出ない（台帳は手元のフォルダ）。
"""
from __future__ import annotations

import ast
import json
import pathlib
import shutil

import pytest

from thth import cli
from thth import refusal_catalog as rc
from thth.register import errors, form, ledger

ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTER_DIR = ROOT / "thth" / "register"
EXAMPLES = ROOT / "registers.example"


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home)]:
        monkeypatch.setenv(key, str(value))
    return {"tmp": tmp_path, "root": root}


def run(capsys, *argv):
    rc_ = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc_, out, err


def jrun(capsys, *argv):
    rc_, out, err = run(capsys, *argv, "--json")
    return rc_, json.loads(out), err


def sample(env, name="media-sample") -> pathlib.Path:
    target = env["tmp"] / name
    shutil.copytree(EXAMPLES / name, target)
    return target


def add(capsys, env, name="media-sample", kind="media", project="sample") -> pathlib.Path:
    path = sample(env, name)
    rc_, out, err = run(capsys, "register", "add", name, "--path", str(path), "--project", project,
                        "--kind", kind, "--by", "m")
    assert rc_ == 0, err
    return path


def write(path: pathlib.Path, **head) -> pathlib.Path:
    body = head.pop("_body", "本文")
    lines = ["---"] + [f"{key}: {value}" for key, value in head.items()] + ["---", body, ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


GOOD = {"id": "voice-x", "kind": "voice", "status": "本採用", "source": "owner", "confirmed": "推測",
        "review_by": "2099-01-01", "public": "true"}


# ------------------------------------------------------------------ 見本の台帳と形の点検

def test_見本のメディアの台帳は形の点検を通る(capsys, env):
    add(capsys, env)
    rc_, value, err = jrun(capsys, "register", "check", "media-sample")
    assert rc_ == 0, err
    assert value["rows"] == 3 and value["problems"] == []


def test_柵_頭の欄が欠けた行は1行ずつ直し方を添えて断る(capsys, env):
    path = add(capsys, env)
    bad = dict(GOOD)
    del bad["review_by"]
    bad["status"] = "たぶん"
    write(path / "思い" / "崩れ.md", **bad)
    rc_, out, err = run(capsys, "register", "check", "media-sample")
    assert rc_ == 2
    assert "register_row_invalid" in err
    assert "missing:review_by" in err and "status" in err
    assert "通すには: " in err and "もっと良く: " in err
    rc_, value, _ = jrun(capsys, "register", "check", "media-sample")
    assert value["code"] == "register_row_invalid"
    [problem] = value["problems"]
    assert set(problem["words"]) == {"missing:review_by", "status"}


def test_柵_秘密らしい値のある行は断る(capsys, env):
    path = add(capsys, env)
    write(path / "思い" / "鍵.md", **dict(GOOD, id="voice-secret", _body="token: sk-ant-abcdefghijklmnop1234"))
    rc_, value, _ = jrun(capsys, "register", "check", "media-sample")
    assert rc_ == 2 and value["problems"][0]["words"] == ["secret"]
    rc_, value, _ = jrun(capsys, "register", "use", "media-sample", "voice-secret")
    assert rc_ == 2 and value["code"] == "register_row_secret"
    assert "sk-ant" not in json.dumps(value)


def test_同じidの行は断る(capsys, env):
    path = add(capsys, env)
    write(path / "思い" / "二枚目.md", **dict(GOOD, id="voice-editorial"))
    rc_, value, _ = jrun(capsys, "register", "check", "media-sample")
    assert rc_ == 2 and ["id_duplicate"] in [p["words"] for p in value["problems"]]


def test_頭の無いmdは行でなく読み物として数える(capsys, env):
    path = add(capsys, env)
    (path / "README.md").write_text("# 読み物\n", encoding="utf-8")
    rc_, value, _ = jrun(capsys, "register", "check", "media-sample")
    assert rc_ == 0 and value["skipped"] == 1


# ------------------------------------------------------------------ 印の柵

def test_柵_推測の行は材料に使えない(capsys, env):
    add(capsys, env)
    rc_, value, err = jrun(capsys, "register", "use", "media-sample", "fact-term-start")
    assert rc_ == 2 and value["code"] == "register_row_guess"
    assert value["why"] and value["fix"] and value["better"]


def test_認めた行は材料に使え外に出してよい欄だけが出る(capsys, env):
    add(capsys, env)
    assert run(capsys, "register", "confirm", "media-sample", "voice-editorial", "--by", "m")[0] == 0
    rc_, value, err = jrun(capsys, "register", "use", "media-sample", "voice-editorial")
    assert rc_ == 0, err
    assert value["fields"] == {} and "次の一歩" in value["body"]
    assert "confirmed" not in value["fields"] and "review_by" not in value["fields"]


def test_柵_認めたあとに中身を変えたら認めたが外れる(capsys, env):
    path = add(capsys, env)
    assert run(capsys, "register", "confirm", "media-sample", "voice-editorial", "--by", "m")[0] == 0
    target = path / "思い" / "編集の考え.md"
    target.write_text(target.read_text(encoding="utf-8").replace("結論を急がせない", "結論を先に書く"), encoding="utf-8")
    rc_, value, _ = jrun(capsys, "register", "use", "media-sample", "voice-editorial")
    assert rc_ == 2 and value["code"] == "register_row_unconfirmed"


def test_柵_頭を手で認めたに書き換えても記録が無ければ使えない(capsys, env):
    path = add(capsys, env)
    target = path / "思い" / "編集の考え.md"
    target.write_text(target.read_text(encoding="utf-8").replace("confirmed: 推測", "confirmed: 本人が認めた"),
                      encoding="utf-8")
    rc_, value, _ = jrun(capsys, "register", "use", "media-sample", "voice-editorial")
    assert rc_ == 2 and value["code"] == "register_row_unconfirmed"


@pytest.mark.parametrize("change, code", [
    ({"review_by": "2020-01-01"}, "register_row_review_passed"),
    ({"public": "false"}, "register_row_not_public"),
    ({"status": "退役"}, "register_row_retired"),
])
def test_柵_見直し日と出してよい印と退役(capsys, env, change, code):
    path = add(capsys, env)
    write(path / "思い" / "足した.md", **dict(GOOD, **change))
    assert run(capsys, "register", "confirm", "media-sample", "voice-x", "--by", "m")[0] == 0
    rc_, value, _ = jrun(capsys, "register", "use", "media-sample", "voice-x")
    assert rc_ == 2 and value["code"] == code


def test_違うと言った行は消さずに残し材料にしない(capsys, env):
    path = add(capsys, env)
    assert run(capsys, "register", "deny", "media-sample", "fact-term-start", "--by", "m")[0] == 0
    text = (path / "事実" / "開講の時期.md").read_text(encoding="utf-8")
    assert "confirmed: 本人が違うと言った" in text and "春の講座" in text
    rc_, value, _ = jrun(capsys, "register", "use", "media-sample", "fact-term-start")
    assert rc_ == 2 and value["code"] == "register_row_denied"


def test_柵_認める操作が書くのは頭の決まった欄だけ(capsys, env):
    path = add(capsys, env)
    target = path / "思い" / "編集の考え.md"
    before = target.read_text(encoding="utf-8").split("\n")
    assert run(capsys, "register", "confirm", "media-sample", "voice-editorial", "--by", "m")[0] == 0
    after = target.read_text(encoding="utf-8").split("\n")
    changed = [line for line in after if line not in before] + [line for line in before if line not in after]
    assert {line.partition(":")[0] for line in changed} <= set(form.MANAGED)
    assert len(after) == len(before) + 1          # confirmed_at が 1 行増えただけ
    # 記録は THTH の側（台帳のフォルダの外）にある。
    assert not any(p.name == "confirmed.json" for p in path.rglob("*"))
    assert pathlib.Path(ledger.confirmed_path("media-sample")).exists()


def test_showは行ごとに使えるかと符丁を言う(capsys, env):
    add(capsys, env)
    rc_, value, _ = jrun(capsys, "register", "show", "media-sample")
    assert rc_ == 0
    codes = {row["id"]: row["code"] for row in value["rows"]}
    assert codes == {"fact-term-start": "register_row_guess", "voice-editorial": "register_row_guess",
                     "voice-not-covered": "register_row_guess"}


def test_無い行と形の違う番号は断る(capsys, env):
    add(capsys, env)
    assert jrun(capsys, "register", "use", "media-sample", "nothing")[1]["code"] == "register_row_missing"
    assert jrun(capsys, "register", "use", "media-sample", "../x")[1]["code"] == "register_args_invalid"


def test_symlinkの台帳と行は辿らない(capsys, env):
    path = sample(env)
    link = env["tmp"] / "link"
    link.symlink_to(path)
    rc_, value, _ = jrun(capsys, "register", "add", "x", "--path", str(link), "--project", "p", "--kind", "media",
                         "--by", "m")
    assert rc_ == 2 and value["code"] == "register_path_unusable"
    outside = env["tmp"] / "outside.md"
    write(outside, **dict(GOOD, id="voice-outside"))
    (path / "思い" / "外.md").symlink_to(outside)
    assert run(capsys, "register", "add", "y", "--path", str(path), "--project", "p", "--kind", "media",
               "--by", "m")[0] == 0
    rc_, value, _ = jrun(capsys, "register", "check", "y")
    assert ["symlink"] in [p["words"] for p in value["problems"]]


def test_removeは控えと記録を消し台帳のフォルダは残す(capsys, env):
    path = add(capsys, env)
    assert run(capsys, "register", "confirm", "media-sample", "voice-editorial", "--by", "m")[0] == 0
    rc_, value, _ = jrun(capsys, "register", "remove", "media-sample", "--by", "m")
    assert rc_ == 0 and value["deleted"] == ["state", "ledger"]
    assert path.exists() and (path / "思い" / "編集の考え.md").exists()
    assert jrun(capsys, "register", "show", "media-sample")[1]["code"] == "register_missing"


# ------------------------------------------------------------------ 断りの目録と網

def test_断りの符丁はどれも目録に3つがそろう():
    for code in errors.REASONS:
        entry = rc.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"], code
        assert entry["doors"] == ("cli",), code


NET = ("urllib", "http", "socket", "ssl", "requests", "httpx", "thth.httpsafe", "thth.shop.wire")


def _imports(path: pathlib.Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def net_imports(directory: pathlib.Path) -> list:
    wrong = []
    for path in sorted(directory.glob("*.py")):
        for name in _imports(path):
            bare = name.lstrip(".")
            if any(bare == net or bare.startswith(net + ".") for net in NET) or bare in ("httpsafe", "shop.wire") \
                    or bare.endswith(".httpsafe") or bare.endswith("shop.wire"):
                wrong.append(f"{path.name}: {name}")
    return wrong


def test_柵_台帳の機械は網の部品をimportしない():
    assert net_imports(REGISTER_DIR) == []


def test_壊れた例_網の柵はurllibのimportを拾う(tmp_path):
    (tmp_path / "bad.py").write_text("import urllib.request\nfrom .. import httpsafe\n", encoding="utf-8")
    assert len(net_imports(tmp_path)) == 2
