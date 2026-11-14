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


@pytest.mark.parametrize("key", ["confirmed", "kind", "status", "source", "review_by", "public", "name"])
def test_柵_秘密らしい値を頭のどの欄に書いてもshowに出ない(capsys, env, key):
    """監査 5.19.0 直したほうがよい 4: 形の崩れた行の confirmed と kind の値を生で出していた。"""
    path = add(capsys, env)
    secret = "sk-ant-abcdefghijklmnop1234"
    write(path / "思い" / "鍵.md", **dict(GOOD, id="voice-secret", **{key: secret}))
    for argv in (("show", "media-sample"), ("show", "media-sample", "--json")):
        _rc, out, err = run(capsys, "register", *argv)
        assert secret not in out + err and "sk-ant" not in out + err, argv
    rc_, value, _ = jrun(capsys, "register", "show", "media-sample")
    [row] = [r for r in value["rows"] if r["id"] == "voice-secret"]
    assert row["usable"] is False
    if key in ("confirmed", "kind"):
        assert row[key] is None


def test_showは行ごとに使えるかと符丁を言う(capsys, env):
    add(capsys, env)
    rc_, value, _ = jrun(capsys, "register", "show", "media-sample")
    assert rc_ == 0
    codes = {row["id"]: row["code"] for row in value["rows"]}
    assert codes == {"fact-term-start": "register_row_guess", "voice-editorial": "register_row_guess",
                     "voice-not-covered": "register_row_guess"}
    # 人の読む形も、使えない行には符丁を出し「使える」と言わない。
    rc_, out, _ = run(capsys, "register", "show", "media-sample")
    assert rc_ == 0 and "使える" not in out and out.count("register_row_guess") == 3
    assert run(capsys, "register", "confirm", "media-sample", "voice-editorial", "--by", "m")[0] == 0
    line = next(x for x in run(capsys, "register", "show", "media-sample")[1].splitlines() if x.startswith("voice-editorial"))
    assert "\t使える\t" in line


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


@pytest.mark.parametrize("where", ["register_state/media-sample", "register_state", "registers/x"])
def test_柵_THTHの置き場の中の台帳のフォルダは控えない(capsys, env, where):
    """監査 5.19.0 の気づき 8: register_state/<名前>/ の中に置くと、register remove と leave が台帳ごと消していた。"""
    inside = env["root"] / where / "台帳"
    shutil.copytree(sample(env, "media-sample"), inside)
    rc_, value, _ = jrun(capsys, "register", "add", "media-sample", "--path", str(inside), "--project", "sample",
                         "--kind", "media", "--by", "m")
    assert rc_ == 2 and value["code"] == "register_path_unusable" and value["detail"] == "inside_thth_state"
    rc_, _out, err = run(capsys, "register", "add", "media-sample", "--path", str(inside), "--project", "sample",
                         "--kind", "media", "--by", "m")
    assert rc_ == 2 and "THTH の置き場の外" in err
    assert not pathlib.Path(ledger.path_for("media-sample")).exists()


def test_前に控えた台帳のフォルダが認めた記録の置き場の中でもremoveは台帳を消さない(capsys, env):
    inside = pathlib.Path(ledger.state_dir_for("media-sample")) / "台帳"
    shutil.copytree(sample(env, "media-sample"), inside)
    # 5.19.0 の add の断りより前に控えた物（控えを直に書く）。
    ledger.write_new("media-sample", ledger.build("media-sample", project="sample", path=str(inside), kind="media"))
    assert run(capsys, "register", "confirm", "media-sample", "voice-editorial", "--by", "m")[0] == 0
    rc_, value, _ = jrun(capsys, "register", "remove", "media-sample", "--by", "m")
    assert rc_ == 0 and value["deleted"] == ["state_files", "ledger"]
    assert (inside / "思い" / "編集の考え.md").exists()
    assert not pathlib.Path(ledger.confirmed_path("media-sample")).exists()


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


# 台帳の機械が import してよい物の表（監査 5.19.0 直したほうがよい 6）。表に無い物は赤にする（網の部品の名前を
# 並べて拾う形だと、`from ..shop import square`・`from .. import shop`・`from ..adapters import threads` を拾えなかった）。
# privacy の「この部分の THTH は網に出ません」「この下書きには、店の頁もレジも使いません」はこの表と結ぶ
# （tests/test_privacy_matches_code.py）。足すときは、その部品が網に出ず店もレジも読まないことを確かめてから足す。
REGISTER_ALLOWED_THTH = frozenset((
    "thth.accounts", "thth.jst", "thth.admin_log", "thth.redact", "thth.refusal", "thth.refusals", "thth.secrets_fs",
    "thth.queuefile", "thth.lint", "thth.draft_origin",
))
# 標準の部品のうち、網に出る・外の命令を動かす・名前で部品を読み込む物（表の外）。`subprocess` は draft.py の
# `git rev-parse` と、seal.py の決まった場所の openssl（封の署名と確かめ・設計_台帳の器 §5.6。網に出ない）だけ許す。
STDLIB_DENIED = frozenset((
    "urllib", "http", "socket", "ssl", "socketserver", "ftplib", "smtplib", "poplib", "imaplib", "nntplib",
    "telnetlib", "xmlrpc", "asyncio", "webbrowser", "multiprocessing", "concurrent", "ctypes", "importlib",
    "subprocess", "pty", "requests", "httpx",
))
# trail.py: 段 5 の跡（git の commit どうしの差・判断の記録の続きの比べ）。決まった場所の git の読むだけの命令
# （rev-parse・ls-tree・cat-file）だけを、PATH もシェルも使わずに呼ぶ（seal.py の openssl と同じ決め方）。
SUBPROCESS_ALLOWED = frozenset(("draft.py", "seal.py", "trail.py"))
# 標準の外の部品で、許すのは YAML を字として読む PyYAML だけ（台帳の器 段 1・設計 §5.2「追加の部品を入れた機械だけ」）。
# 許すのは yamlread.py の 1 枚だけ。PyYAML は網にも外の命令にも触れない。入れていない機械では import が失敗し、
# YAML の記録は「中は見ていない」になる（依存 0 は変えない・`pip install 'thth[yaml]'`）。
OPTIONAL_ALLOWED = {"yamlread.py": frozenset(("yaml",))}
# 台帳の機械が使う、台帳の外の THTH の部品のうち、台帳の機械と同じ柵で縛る物（表の 1 つ・下書きの出所の控え）。
REGISTER_EXTRA_FILES = (pathlib.Path(__file__).resolve().parent.parent / "thth" / "draft_origin.py",)


def _absolute_imports(path: pathlib.Path, package: str) -> set:
    """`path` の import を絶対の名前にする（`from .. import shop` は `thth.shop`）。`__import__` の呼び出しも拾う。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parts = package.split(".")
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = parts[:len(parts) - node.level + 1]
                base = ".".join(base_parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if node.module == "__future__":
                continue
            if node.module:
                found.add(base)
            # `from X import y` の y は部品か名前か分からない。`from .. import y` は部品、`from thth.X import y` は
            # X.y が部品のときだけ部品として数える（X が表の外なら X で拾える）。
            for alias in node.names:
                full = f"{base}.{alias.name}"
                if not node.module or (base.startswith("thth") and _is_module(full)):
                    found.add(full)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__":
            found.add("__import__")
    return found


def _is_module(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def import_problems(files, package: str) -> list:
    """許した表の外の import（`<ファイル>: <名前>` の並び）。"""
    import sys
    wrong = []
    for path in files:
        for name in sorted(_absolute_imports(path, package)):
            top = name.split(".")[0]
            if name == "__import__":
                wrong.append(f"{path.name}: {name}")
            elif top == "thth":
                if name == "thth" or name.startswith(package + ".") or name == package:
                    continue
                if name not in REGISTER_ALLOWED_THTH:
                    wrong.append(f"{path.name}: {name}")
            elif top in STDLIB_DENIED:
                if not (top == "subprocess" and path.name in SUBPROCESS_ALLOWED):
                    wrong.append(f"{path.name}: {name}")
            elif top not in sys.stdlib_module_names:
                if top not in OPTIONAL_ALLOWED.get(path.name, ()):
                    wrong.append(f"{path.name}: {name}")
    return wrong


def net_imports(directory: pathlib.Path) -> list:
    """台帳の機械（`directory` の *.py と、同じ柵で縛る台帳の外の部品）の、許した表の外の import。"""
    files = sorted(directory.glob("*.py"))
    if directory.resolve() == REGISTER_DIR.resolve():
        return import_problems(files, "thth.register") + import_problems(REGISTER_EXTRA_FILES, "thth")
    return import_problems(files, "thth.register")


def test_柵_台帳の機械は網の部品をimportしない():
    assert net_imports(REGISTER_DIR) == []


def test_壊れた例_網の柵はurllibのimportを拾う(tmp_path):
    (tmp_path / "bad.py").write_text("import urllib.request\nfrom .. import httpsafe\n", encoding="utf-8")
    assert len(net_imports(tmp_path)) == 2


@pytest.mark.parametrize("line", [
    "from ..shop import square", "from .. import shop", "from ..shop.kinds import client", "from ..adapters import threads",
    "import subprocess", "from ..shop import wire", "import thth.shop.pos", "from thth import httpsafe",
    "import importlib", "x = __import__('urllib.request')", "import requests", "from .. import meta_write",
    "from ..shop.wire import StoreRefused",
])
def test_柵_台帳の機械のimportは許した表の外なら赤(tmp_path, line):
    """監査 5.19.0 直したほうがよい 6: レジを読む口・投稿の口・外の命令を import したら拾う（1 行ずつ）。"""
    (tmp_path / "bad.py").write_text(line + "\n", encoding="utf-8")
    assert net_imports(tmp_path), line


def test_柵_yamlのimportはyamlreadの1枚だけ(tmp_path):
    (tmp_path / "other.py").write_text("import yaml\n", encoding="utf-8")
    (tmp_path / "yamlread.py").write_text("import yaml\nimport requests\n", encoding="utf-8")
    assert sorted(net_imports(tmp_path)) == ["other.py: yaml", "yamlread.py: requests"]


def test_台帳の機械のimportの表は標準の部品と許した部品だけ(tmp_path):
    (tmp_path / "ok.py").write_text("from __future__ import annotations\nimport json\nfrom .. import jst, redact\n"
                                    "from . import form\nfrom .errors import RegisterRefused\n", encoding="utf-8")
    assert net_imports(tmp_path) == []
    assert not any(name.startswith("thth.shop") or name.startswith("thth.adapters") for name in REGISTER_ALLOWED_THTH)
