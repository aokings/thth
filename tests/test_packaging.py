"""`pip install thth` が本当に通るか（設計 v2 §3・v2-2b）。

**なぜ乾式試験では足りないか。** `tests/test_fresh_install.py` は repo を clone して
`python -m thth` を叩く——**repo がそこに在ることが前提**。`pip install thth` で入る人の
手元には repo が無い。`bin/thth` も `mcp/server.py` も `skills/` も、**wheel に入って
いなければ存在しない**。「同梱したつもり」は wheel を開くまで分からない。

だからここは**本当に `python -m build` して、本当に別の venv に入れて、本当に叩く**。

守ること:
  - **`build` と `venv` が無ければ skip ではなく fail。** skip は緑に見える。
    **配布物を作れないまま「テストは通った」と言うのがいちばん悪い**ので、
    理由と入れ方を出して落とす。
  - **実物の `~/.config/thth/` を読まない・書かない**（`tests/test_fresh_install.py`
    と同じ作法）。`HOME`・`THTH_ROOT`・`THTH_APP_DIR`・`THTH_APP_ENV_PATH` を全部
    tmp へ向ける。
  - **PyPI へは触らない。** `python -m build` はローカル、`pip install` は
    `--no-index --no-deps`（依存 0 なので index が要らないことの確認にもなる）。
    **登録と upload は masaru の手**（設計 v2 §7）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import zipfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACCOUNT = "demo-threads"


def _require(module: str, how: str) -> None:
    """無ければ **skip ではなく fail**（理由と入れ方つき）。"""
    if importlib.util.find_spec(module) is None:
        pytest.fail(
            f"`{module}` が無いので配布物を作れません（skip にしません——"
            f"配布物を作れないまま緑になるのがいちばん危ない）。\n"
            f"  入れ方: {how}")


def _run(argv: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=600, **kw)


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    """`python -m build` で sdist と wheel を作る（ローカル・PyPI に触らない）。"""
    _require("build", "python3 -m pip install build")
    out = tmp_path_factory.mktemp("thth-dist")
    r = _run([sys.executable, "-m", "build", "--outdir", str(out), REPO_ROOT])
    assert r.returncode == 0, f"python -m build が失敗しました:\n{r.stdout}\n{r.stderr}"
    names = sorted(os.listdir(out))
    wheels = [n for n in names if n.endswith(".whl")]
    sdists = [n for n in names if n.endswith(".tar.gz")]
    assert len(wheels) == 1, f"wheel が 1 つではありません: {names}"
    assert len(sdists) == 1, f"sdist が 1 つではありません: {names}"
    return {"dir": str(out), "wheel": os.path.join(str(out), wheels[0]),
            "sdist": os.path.join(str(out), sdists[0]),
            "wheel_name": wheels[0], "sdist_name": sdists[0]}


@pytest.fixture(scope="session")
def venv_thth(tmp_path_factory, built):
    """**別の venv** に wheel を入れる。repo は PYTHONPATH に入れない。"""
    _require("venv", "python3 に標準で入っています（Debian 系なら apt install python3-venv）")
    home = tmp_path_factory.mktemp("thth-venv")
    env_dir = os.path.join(str(home), "venv")
    r = _run([sys.executable, "-m", "venv", env_dir])
    assert r.returncode == 0, f"venv を作れませんでした:\n{r.stdout}\n{r.stderr}"
    py = os.path.join(env_dir, "bin", "python")
    assert os.path.exists(py), f"venv の python が見つかりません: {py}"
    # **`--no-index` で入る＝依存 0** の確認でもある（1 つでも依存があればここで落ちる）。
    r = _run([py, "-m", "pip", "install", "--no-index", "--no-deps", "--quiet",
              built["wheel"]])
    assert r.returncode == 0, (
        f"wheel を入れられませんでした（--no-index で落ちたなら依存が 0 ではありません）:\n"
        f"{r.stdout}\n{r.stderr}")
    return {"python": py, "bin": os.path.join(env_dir, "bin"),
            "thth": os.path.join(env_dir, "bin", "thth"),
            "thth_mcp": os.path.join(env_dir, "bin", "thth-mcp")}


def _isolated_env(venv_thth, tmp_path) -> dict:
    """実物の `~/.config/thth/` にも repo にも触らない環境変数一式。"""
    home = tmp_path / "home"
    root = tmp_path / "root"
    app_dir = tmp_path / "appdir"
    (app_dir / "accounts").mkdir(parents=True, exist_ok=True)
    home.mkdir(exist_ok=True)
    root.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = venv_thth["bin"] + os.pathsep + env.get("PATH", "")
    env["THTH_ROOT"] = str(root)
    env["THTH_APP_DIR"] = str(app_dir)
    env["THTH_APP_ENV_PATH"] = str(home / ".config" / "thth" / "app.env")
    # **repo を見せない。** 見せると wheel に何が入っていなくても動いてしまう。
    env.pop("PYTHONPATH", None)
    env.pop("THTH_DRY_RUN", None)
    env.pop("THTH_BIN", None)
    return env


def _write_demo_ledger(tmp_path) -> None:
    ledger = {
        "account": ACCOUNT, "project": "demo", "media": "threads", "handle": "demo",
        "user_id": "", "repo_dir": str(tmp_path / "repos" / "demo"),
        "queue_dir": "docs/sns/queue", "replies_dir": "data/sns/replies",
        "quiet_hours": ["22:00", "07:00"], "min_interval_hours": 6,
        "collect_days": 14, "hashtags": False, "stale_days": 7,
        "env": str(tmp_path / "home" / ".config" / "thth" / f"{ACCOUNT}.env"),
        "token": str(tmp_path / "home" / ".config" / "thth" / f"{ACCOUNT}.token"),
        "ping": "wrapper", "timeout": 300, "dry_run_env": "THTH_DRY_RUN",
        "production": False,
    }
    path = tmp_path / "appdir" / "accounts" / f"{ACCOUNT}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# 配布物の中身
# --------------------------------------------------------------------------

def test_wheel_に_mcp_と_skill_と_VERSION_が入っている(built):
    """設計 v2 §3「`mcp/` を pip に同梱・`skills/thth/SKILL.md`」。

    **repo の置き場は変えていない**（`mcp/server.py`・`skills/thth/SKILL.md` のまま）。
    wheel の中の場所だけが `thth/mcp_server.py`・`thth/skills/thth/SKILL.md`。
    """
    with zipfile.ZipFile(built["wheel"]) as z:
        names = set(z.namelist())
        assert "thth/VERSION" in names, (
            f"VERSION が wheel に無い（`thth --version` が 0.0.0 になる）: {sorted(names)[:20]}")
        assert "thth/mcp_server.py" in names, "MCP が wheel に入っていません"
        assert "thth/skills/thth/SKILL.md" in names, "skill が wheel に入っていません"
        assert "thth/cli.py" in names and "thth/adapters/threads.py" in names
        skill = z.read("thth/skills/thth/SKILL.md").decode("utf-8")
        entry = z.read(f"thth-{_version()}.dist-info/entry_points.txt").decode("utf-8")
    assert skill.startswith("---\n"), "skill に frontmatter がありません"
    assert "\nname: thth\n" in skill and "\ndescription: " in skill
    assert "thth = thth.cli:main" in entry, entry
    assert "thth-mcp = thth.mcp_server:main" in entry, entry


def test_依存は0本_devだけがpytestを要る(built):
    """**依存 0**（設計 v2 §3・pyproject の先頭）。metadata で確かめる。"""
    with zipfile.ZipFile(built["wheel"]) as z:
        meta = z.read(f"thth-{_version()}.dist-info/METADATA").decode("utf-8")
    runtime = [ln for ln in meta.splitlines()
               if ln.startswith("Requires-Dist:") and "extra ==" not in ln]
    assert runtime == [], f"実行時の依存が増えています: {runtime}"
    dev = [ln for ln in meta.splitlines() if ln.startswith("Requires-Dist:")]
    assert any("pytest-xdist" in ln for ln in dev), f"dev に pytest-xdist が無い: {dev}"


def test_sdist_に台帳と運用の日誌が入っていない(built):
    """`accounts/`（masaru の 4 本）と `docs/記録/`（運用の日誌）は配らない
    （設計 v2 §3 の台帳・§7-2）。"""
    r = _run(["tar", "tzf", built["sdist"]])
    assert r.returncode == 0, r.stderr
    entries = [ln.split("/", 1)[1] for ln in r.stdout.splitlines() if "/" in ln]
    assert not [e for e in entries if e.startswith("accounts/")], "台帳が sdist に入っています"
    assert not [e for e in entries if e.startswith("docs/")], "docs が sdist に入っています"
    assert any(e.startswith("skills/") for e in entries)
    assert any(e.startswith("bin/") for e in entries)


def _version() -> str:
    with open(os.path.join(REPO_ROOT, "thth", "VERSION"), encoding="utf-8") as f:
        return f.read().strip()


# --------------------------------------------------------------------------
# 別の venv に入れて叩く（repo を見せない）
# --------------------------------------------------------------------------

def test_別のvenvでthth_versionが版を言う(venv_thth, tmp_path):
    env = _isolated_env(venv_thth, tmp_path)
    r = _run([venv_thth["thth"], "--version"], env=env)
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    out = (r.stdout + r.stderr).strip()
    assert out.startswith(f"thth {_version()}"), out
    assert "0.0.0" not in out, f"VERSION を読めていません: {out}"


def test_別のvenvでthth_helpが全サブコマンドを出す(venv_thth, tmp_path):
    env = _isolated_env(venv_thth, tmp_path)
    r = _run([venv_thth["thth"], "--help"], env=env)
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    for name in ("lint", "preview", "approve", "queue", "throw", "board",
                 "doctor", "topics", "threads"):
        assert name in r.stdout, f"`{name}` が --help に出ていません:\n{r.stdout}"


def test_別のvenvでpython_m_thth_doctorが期待どおり止まる(venv_thth, tmp_path):
    """`python -m thth doctor <account>` は**トークンを入れる前は rc=2 で止まる**
    （`tests/test_fresh_install.py` と同じ「期待どおりの停止」）。**落ちるのではなく
    止まる**ことが、入口が通っている証拠。"""
    env = _isolated_env(venv_thth, tmp_path)
    _write_demo_ledger(tmp_path)
    r = _run([venv_thth["python"], "-m", "thth", "doctor", ACCOUNT], env=env)
    both = r.stdout + r.stderr
    assert "cannot be directly executed" not in both, both
    assert "Traceback" not in both, both
    assert r.returncode == 2, f"rc={r.returncode}\n{both}"
    assert "トークンが無い" in both, both


def test_別のvenvでMCPサーバが起動してtoolsを返す(venv_thth, tmp_path):
    """同梱の入口（`thth-mcp`）が **wheel だけで**立ち上がり、`tools/list` を返す。

    repo が無いので `bin/thth` は存在しない——`mcp/server.py` の `THTH_BIN` の
    落とし先（`python -m thth`）がここで初めて効く。
    """
    env = _isolated_env(venv_thth, tmp_path)
    req = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    stdin = "".join(json.dumps(o, ensure_ascii=False) + "\n" for o in req)
    r = subprocess.run([venv_thth["thth_mcp"]], input=stdin, capture_output=True,
                        text=True, timeout=120, env=env)
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    lines = [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2, r.stdout
    assert lines[0]["result"]["serverInfo"]["name"] == "thth"
    names = {t["name"] for t in lines[1]["result"]["tools"]}
    assert "thth_board" in names and "thth_lint" in names, names
    # **秘密を書く道具は MCP に出さない**（`tests/test_app_env_cli.py` と対）。
    assert not [n for n in names if "app" in n or "token" in n], names


def test_別のvenvのMCPが本物のCLIを呼べる(venv_thth, tmp_path):
    """`thth_board` を実際に呼ぶ。**`bin/thth` が無い状態で CLI に届く**ことの確認。"""
    env = _isolated_env(venv_thth, tmp_path)
    _write_demo_ledger(tmp_path)
    stdin = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "thth_board", "arguments": {}}}) + "\n"
    r = subprocess.run([venv_thth["thth_mcp"]], input=stdin, capture_output=True,
                        text=True, timeout=120, env=env)
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    resp = json.loads(r.stdout.strip())
    text = resp["result"]["content"][0]["text"]
    assert resp["result"]["isError"] is False, text
    payload = json.loads(text)
    assert "accounts" in payload, payload
