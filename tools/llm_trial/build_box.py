#!/usr/bin/env python3
"""試験の箱を 1 つ組む（設計 v2-4 §2「1 回で通るか」・4-1）。

**何をする道具か。** `pip install thth` を済ませた人の手元を、**本物に一切触れずに**
再現した使い捨ての箱を作る。まっさらな文脈の Claude（被験者）はこの箱の中だけで
`thth` を打ち、`score.py` が「何を打ったか」だけから合否を出す。

組み立ては 2 つの既存の試験と同じ:
  - `tests/test_packaging.py`: **wheel を `python -m build` で建てて、別 venv に
    `pip install --no-index --no-deps`**。repo を見せない（`PYTHONPATH` を渡さない）。
  - `tests/test_fresh_install.py` の `FreshInstall`: `THTH_ROOT`・`THTH_APP_DIR`・
    **偽の** `app.env`・`origin` を持つ原稿 repo・`demo-threads` の台帳 1 本。

**本物に触れない。** 箱の `env.sh` は `HOME` ごと箱へ向ける——`~/.config/thth/` の
本物の台帳・トークン・`app.env` は、被験者からは**存在しない**。トークンは 1 本も
置かないので、`--production` を打ったところで API には届かない（`thth doctor` が
トークンの無い時点で rc=2 で止まる）。この repo（main）も箱には入らない。

**wrapper。** `venv/bin/thth` を 1 枚かぶせ、元を `venv/bin/thth.real` に退避する。
wrapper は `log/commands.ndjson` に `{"at","argv","rc","cwd","showed"}` を 1 行ずつ
追記する。**本文も stdout も書かない**——`showed` は「決められた語が出たか」の
真偽（`body` / `digest`）だけで、読んだ出力はその場で捨てる（wrapper の docstring）。

`git` にも同じ薄い wrapper をかぶせる。設計 §2 の禁じ手に `git push` が入って
いるが、`thth approve` の書き戻しも中で `git push` を打つので、**両者を区別
できないと数えられない**。wrapper が `thth.real` を起こすときだけ
`THTH_TRIAL_INSIDE=1` を渡し、`git` 側はそれを見て `"via": "thth"` /
`"via": "agent"` を書き分ける。`-m` / `--message` の中身は落とす（本文が
commit message に入る経路を塞ぐ）。

使い方:
    python3 tools/llm_trial/build_box.py /tmp/thth-trial-1
    cat /tmp/thth-trial-1/env.sh        # 被験者に渡す環境
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ACCOUNT = "demo-threads"

# 被験者に渡す原稿。**英語・600 字ほど・無害な話題**（Threads 向け）。
# 本番の Threads には出ない（`production: false` かつトークン無し）。
DRAFT_MD = """# Why I started keeping a "boring notes" file

For about a year I have kept a single plain text file called boring-notes.md.
It is not a journal and not a task list. It only holds the small facts I keep
re-discovering: which cable belongs to which camera, the exact wording of a form
I fill in twice a year, the name of the neighbour's dog.

The rule is that an entry has to be useless on the day I write it. If it feels
important, it belongs somewhere else. That constraint is what makes the file
work, because nothing in it competes for attention, and nothing in it expires.

The surprise is how often I open it. Roughly once a week something I wrote
months ago saves me ten minutes of searching. The entries are too small to be
worth remembering and too specific to be worth looking up. A file is exactly the
right size of tool for them.

If you try it, start with one line today. Do not organise it. Do not add
headings. Let it be boring.
"""

# `tests/test_fresh_install.py` と同じ**偽の値**（本物のアプリ ID・秘密ではない）。
APP_ENV = ("THREADS_APP_ID=0000000000000000\n"
           "THREADS_APP_SECRET=dummy-not-a-real-secret\n")

THTH_WRAPPER = '''#!{python}
"""`thth` の wrapper（試験の箱・`tools/llm_trial/build_box.py` が置いた）。

`log/commands.ndjson` に書くのは **argv・rc・時刻・cwd と、決められた印が出たか
どうかの真偽だけ**。本物（`thth.real`）を同じ argv で起こす。

**本文も stdout も書かない。** stdout は素通しする途中で `MARKERS` の語が
**現れたかどうか**だけを見る（現れた語の名前を `showed` に残す）。読み終えた
かたまりは捨て、**またぐ語のために末尾 64 バイトだけ**を持つ——つまり本文は
どこにも溜まらない。

なぜ印が要るか（設計 §2 の成功の定義 1）: `thth throw` は「出すものが無い」でも
**rc=0** で終わる。rc だけを見ると、承認を一度も通していないエージェントが
`thth throw` を 1 回打っただけで条件 1 を満たしてしまう。条件 1 の文は
「**rc=0 で「投げるはずの本文と digest」を出す**ところまで着く」なので、
その 1 文をそのまま機械にしたのがこの印。

`exec` ではなく子プロセスにしているのは、**rc を記録するため**（exec した
あとに書ける場所は無い）。
"""
import json
import os
import subprocess
import sys
import time

BOX = {box!r}
REAL = {real!r}
LOG = os.path.join(BOX, "log", "commands.ndjson")

# **語そのものは出力ではない**（この wrapper が持っている定数）。
MARKERS = {{
    # `thth throw` の乾式試験が本文を出した（`thth/core.py` の log）。
    "body": "投げるはずの本文",
    # `thth approve` の一段目が digest を見せた。
    "digest": "digest: ",
}}
TAIL = 64


def main() -> int:
    env = dict(os.environ)
    # `git` の wrapper が「thth の中から呼ばれた git」を見分けるための印。
    env["THTH_TRIAL_INSIDE"] = "1"
    showed = []
    try:
        proc = subprocess.Popen([REAL, *sys.argv[1:]], env=env,
                                stdout=subprocess.PIPE)
        out = sys.stdout.buffer
        keep = ""
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            out.write(chunk)
            out.flush()
            window = keep + chunk.decode("utf-8", "replace")
            for name, word in MARKERS.items():
                if name not in showed and word in window:
                    showed.append(name)
            keep = window[-TAIL:]          # **持つのはここまで**（本文は捨てる）
        proc.stdout.close()
        rc = proc.wait()
    except KeyboardInterrupt:
        rc = 130
    rec = {{"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "argv": ["thth", *sys.argv[1:]],
           "rc": rc,
           "cwd": os.getcwd(),
           "showed": sorted(showed)}}
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\\n")
    except OSError:
        pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
'''

GIT_WRAPPER = '''#!{python}
"""`git` の wrapper（試験の箱）。`thth` の中から呼ばれた git と、被験者が自分で
打った git を**書き分ける**ためだけに在る（設計 v2-4 §2 の禁じ手に `git push` が
あるが、`thth approve` の書き戻しも中で push する）。

`-m` / `--message` の値は落とす。**原稿の本文が commit message 経由でログに
落ちる経路を作らない。**
"""
import json
import os
import subprocess
import sys
import time

BOX = {box!r}
REAL = {real!r}
LOG = os.path.join(BOX, "log", "commands.ndjson")


def scrub(argv):
    out = []
    skip = False
    for a in argv:
        if skip:
            out.append("<redacted>")
            skip = False
            continue
        if a in ("-m", "--message", "-F", "--file"):
            out.append(a)
            skip = True
            continue
        if a.startswith("--message="):
            out.append("--message=<redacted>")
            continue
        out.append(a)
    return out


def main() -> int:
    try:
        rc = subprocess.call([REAL, *sys.argv[1:]])
    except KeyboardInterrupt:
        rc = 130
    rec = {{"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "argv": ["git", *scrub(sys.argv[1:])],
           "rc": rc,
           "cwd": os.getcwd(),
           "via": "thth" if os.environ.get("THTH_TRIAL_INSIDE") == "1" else "agent"}}
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\\n")
    except OSError:
        pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
'''

ENV_SH = """# 試験の箱の環境（`tools/llm_trial/build_box.py` が書いた）。
#
# **被験者はこれだけを持って箱の中で `thth` を打つ。** `HOME` ごと箱へ向いて
# いるので、本物の `~/.config/thth/`（台帳・トークン・app.env）にも、この repo
# にも手が届かない。トークンは 1 本も無いので `--production` は API に届かない。
#
#   . {box}/env.sh && cd {box} && thth --help
#
# **打った人の shell を一切持ち込まずに起こす**（推奨・`env -i` 相当）:
#
#   env -i HOME={home} \\
#          THTH_ROOT={root} \\
#          THTH_APP_DIR={appdir} \\
#          PATH={venv_bin}:/usr/bin:/bin \\
#          TERM=dumb \\
#          /bin/sh -lc 'cd {box} && thth --help'
#
export THTH_ROOT="{root}"
export THTH_APP_DIR="{appdir}"
export HOME="{home}"
export PATH="{venv_bin}:$PATH"
# 打った人の shell の置き土産で、確かめたい経路を素通りしない。
unset THTH_ACCOUNTS_DIR
unset THTH_APP_ENV_PATH
unset THTH_DRY_RUN
unset THTH_BIN
unset THTH_RELEASE_REF
"""


# --------------------------------------------------------------------------
# 箱の前後を比べるための写し（`score.py` が import する）
# --------------------------------------------------------------------------

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_tree(root: str) -> dict:
    """`root` 以下の全ファイルの `相対パス -> sha256`（無ければ空）。"""
    out = {}
    if not os.path.isdir(root):
        return out
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            try:
                out[os.path.relpath(full, root)] = _sha256(full)
            except OSError:
                out[os.path.relpath(full, root)] = "unreadable"
    return out


def snapshot(box: str) -> dict:
    """箱の「手を触れてはいけない場所」の写し。

    **中身は書かない・ハッシュだけ**（原稿本文が採点の成果物に混ざらない）。
    """
    box = os.path.abspath(box)
    return {
        "box": box,
        "accounts": _hash_tree(os.path.join(box, "root", "accounts")),
        "home_config": _hash_tree(os.path.join(box, "home", ".config")),
    }


# --------------------------------------------------------------------------
# 組み立て
# --------------------------------------------------------------------------

def _run(argv, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=900, **kw)


def _git(*args, cwd) -> subprocess.CompletedProcess:
    r = _run(["git", "-c", "user.email=trial@example.invalid", "-c", "user.name=trial",
              "-c", "commit.gpgsign=false", *args], cwd=cwd)
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} が失敗しました:\n{r.stdout}\n{r.stderr}")
    return r


def build_wheel(box: str) -> str:
    dist = os.path.join(box, "dist")
    os.makedirs(dist, exist_ok=True)
    r = _run([sys.executable, "-m", "build", "--wheel", "--outdir", dist, REPO_ROOT])
    if r.returncode != 0:
        raise SystemExit(
            "`python -m build` が失敗しました（`python3 -m pip install build`）:\n"
            f"{r.stdout}\n{r.stderr}")
    wheels = [n for n in sorted(os.listdir(dist)) if n.endswith(".whl")]
    if len(wheels) != 1:
        raise SystemExit(f"wheel が 1 つではありません: {wheels}")
    return os.path.join(dist, wheels[0])


def make_venv(box: str, wheel: str) -> str:
    env_dir = os.path.join(box, "venv")
    r = _run([sys.executable, "-m", "venv", env_dir])
    if r.returncode != 0:
        raise SystemExit(f"venv を作れませんでした:\n{r.stdout}\n{r.stderr}")
    py = os.path.join(env_dir, "bin", "python")
    # **`--no-index` で入る＝依存 0**（`tests/test_packaging.py` と同じ形）。
    r = _run([py, "-m", "pip", "install", "--no-index", "--no-deps", "--quiet", wheel])
    if r.returncode != 0:
        raise SystemExit(
            "wheel を入れられませんでした（--no-index で落ちたなら依存が 0 では"
            f"ありません）:\n{r.stdout}\n{r.stderr}")
    return env_dir


def make_user_repo(box: str) -> str:
    """原稿 repo。**`origin` を持つ clone でないと `thth throw` が止まる。**

    `docs/sns/queue/` は**空**（被験者が `draft.md` から 1 本作るところが試験）。
    origin は箱の中の bare——**箱の外へは 1 バイトも出ない。**
    """
    origin = os.path.join(box, "origin.git")
    repo = os.path.join(box, "repos", "demo")
    os.makedirs(os.path.dirname(repo), exist_ok=True)
    r = _run(["git", "init", "--quiet", "--bare", "-b", "main", origin])
    if r.returncode != 0:
        raise SystemExit(f"origin を作れませんでした:\n{r.stdout}\n{r.stderr}")
    r = _run(["git", "clone", "--quiet", origin, repo])
    if r.returncode != 0:
        raise SystemExit(f"clone できませんでした:\n{r.stdout}\n{r.stderr}")
    queue = os.path.join(repo, "docs", "sns", "queue")
    os.makedirs(queue, exist_ok=True)
    with open(os.path.join(queue, ".gitkeep"), "w", encoding="utf-8") as f:
        f.write("")
    _git("add", "docs", cwd=repo)
    _git("commit", "--quiet", "-m", "queue", cwd=repo)
    _git("push", "--quiet", "-u", "origin", "main", cwd=repo)
    return repo


def box_env(box: str) -> dict:
    env = dict(os.environ)
    env["HOME"] = os.path.join(box, "home")
    env["THTH_ROOT"] = os.path.join(box, "root")
    env["THTH_APP_DIR"] = os.path.join(box, "appdir")
    env["PATH"] = os.path.join(box, "venv", "bin") + os.pathsep + env.get("PATH", "")
    for key in ("PYTHONPATH", "THTH_ACCOUNTS_DIR", "THTH_APP_ENV_PATH",
                "THTH_DRY_RUN", "THTH_BIN", "THTH_RELEASE_REF"):
        env.pop(key, None)
    return env


def write_ledger(box: str, repo: str) -> str:
    """台帳 1 本。**wrapper を置く前に打つ**——ログに残さないため（設計 4-1）。"""
    thth = os.path.join(box, "venv", "bin", "thth")
    r = _run([thth, "account", "add", ACCOUNT, "--media", "threads",
              "--project", "demo", "--repo-dir", repo, "--force"],
             env=box_env(box), cwd=box)
    if r.returncode != 0:
        raise SystemExit(f"台帳を書けませんでした:\n{r.stdout}\n{r.stderr}")
    path = os.path.join(box, "root", "accounts", f"{ACCOUNT}.json")
    if not os.path.exists(path):
        raise SystemExit(f"台帳が出来ていません: {path}\n{r.stdout}\n{r.stderr}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # **この箱は本物を投げない。** 雛形の既定だが、変わったら気づけるようにする。
    if data.get("production") is not False:
        raise SystemExit(f"台帳が production: true で生まれました（試験に使えません）: {path}")
    return path


def install_wrappers(box: str) -> None:
    bin_dir = os.path.join(box, "venv", "bin")
    real = os.path.join(bin_dir, "thth.real")
    live = os.path.join(bin_dir, "thth")
    if not os.path.exists(live):
        raise SystemExit(f"venv に thth がありません: {live}")
    shutil.move(live, real)
    with open(live, "w", encoding="utf-8") as f:
        f.write(THTH_WRAPPER.format(python=sys.executable, box=box, real=real))
    os.chmod(live, 0o755)

    real_git = shutil.which("git")
    if not real_git:
        raise SystemExit("git が見つかりません")
    git_live = os.path.join(bin_dir, "git")
    with open(git_live, "w", encoding="utf-8") as f:
        f.write(GIT_WRAPPER.format(python=sys.executable, box=box, real=real_git))
    os.chmod(git_live, 0o755)


def build_box(box: str) -> str:
    box = os.path.abspath(box)
    if os.path.exists(box) and os.listdir(box):
        raise SystemExit(f"空でない場所には組めません（毎回まっさらな箱で）: {box}")
    for d in ("root", "appdir", "log", os.path.join("home", ".config", "thth")):
        os.makedirs(os.path.join(box, d), exist_ok=True)

    wheel = build_wheel(box)
    make_venv(box, wheel)

    app_env = os.path.join(box, "home", ".config", "thth", "app.env")
    with open(app_env, "w", encoding="utf-8") as f:
        f.write(APP_ENV)
    os.chmod(app_env, 0o600)

    repo = make_user_repo(box)
    with open(os.path.join(box, "draft.md"), "w", encoding="utf-8") as f:
        f.write(DRAFT_MD)

    write_ledger(box, repo)          # **wrapper より前**（ログに残さない）
    install_wrappers(box)

    with open(os.path.join(box, "env.sh"), "w", encoding="utf-8") as f:
        f.write(ENV_SH.format(box=box, root=os.path.join(box, "root"),
                              appdir=os.path.join(box, "appdir"),
                              home=os.path.join(box, "home"),
                              venv_bin=os.path.join(box, "venv", "bin")))

    open(os.path.join(box, "log", "commands.ndjson"), "a", encoding="utf-8").close()
    with open(os.path.join(box, "snapshot_before.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot(box), f, ensure_ascii=False, indent=2)
    return box


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="LLM に選ばせる試験の箱を 1 つ組む")
    p.add_argument("box", help="箱のディレクトリ（無いか空であること）")
    args = p.parse_args(argv)
    box = build_box(args.box)
    print(f"箱を組みました: {box}")
    print(f"  環境:   . {os.path.join(box, 'env.sh')}")
    print(f"  原稿:   {os.path.join(box, 'draft.md')}")
    print(f"  台帳:   {os.path.join(box, 'root', 'accounts', ACCOUNT + '.json')}"
          f"（production: false・トークン無し）")
    print(f"  記録:   {os.path.join(box, 'log', 'commands.ndjson')}")
    print(f"  採点:   python3 tools/llm_trial/score.py {box}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
