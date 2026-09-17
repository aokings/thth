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
import signal
import subprocess
import sys
import time
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNT = "demo-threads"

# --------------------------------------------------------------------------
# `--engage`（発注 T4-2）: 「この枝に絡んで」の試験——`demo-threads` の箱に
# **足す**（既存の箱を壊さない）、偽の Bluesky サーバへ向いた台帳 1 本。
# --------------------------------------------------------------------------
# **雛形のダミー（`accounts.DUMMY_HANDLES["bluesky"]` = `demo.bsky.social`）と
# 揃えない**（T6-4）。台帳の handle がそれと一致すると `thth doctor` が
# 「ダミーのままです」で rc=1 になり、採点に入らない箱の欠陥で被験者が
# 「壊れているのか」と迷っていた（試験の摩擦）。偽サーバの whoami が返す
# handle（`fake_bluesky_server.HANDLE_DEMO`）とここは**揃えたまま**、値だけ
# 雛形と違うものにする。
ENGAGE_ACCOUNT = "demo-bluesky"
ENGAGE_HANDLE = "trial-demo.bsky.social"
FAKE_BLUESKY_SERVER = os.path.join(TOOLS_DIR, "fake_bluesky_server.py")
# `.token` の形だけ本物に似せる（値は読まれない・`Adapter.has_token()` は
# 「在るか」しか見ない・`thth/adapters/bluesky.py`）。
FAKE_APP_PASSWORD = "fake-fake-fake-fake"

# 被験者に渡す原稿（既定）。**英語・380〜440 字・無害な話題**（Threads 向け・
# `len()` で数える・改行込み）。「1 回で通るか」（設計 §2）は媒体の上限（Threads
# 500 字未満）に収まっている原稿でないと測れない（第 2 回の記録 §6・H2）。
# 見出し（`#`）は付けない・URL は入れない。本番の Threads には出ない
# （`production: false` かつトークン無し）。
DRAFT_MD = """For about a year I have kept a plain text file called boring-notes.md,
holding only small facts I keep re-discovering: which cable fits which
camera, the wording of a form I fill in twice a year.

An entry has to be useless on the day I write it. That constraint keeps the
file working, since nothing in it competes for attention.

Once a week something I wrote months ago saves me ten minutes. Start with
one line, let it stay boring.
"""

# `--long` のときだけ使う、**上限超え**の原稿（942 字ほど）。「上限超えの原稿で
# 編集の往復を測る」別の試験用（設計セッション向けの逃げ道ではなく、意図した
# 第 2 の測定）。既定の箱には置かない——`draft.md` は常に上限内でなければ
# 「1 回で通るか」を測れない（H2）。
DRAFT_MD_LONG = """# Why I started keeping a "boring notes" file

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


def _selfcheck_default_draft() -> None:
    """既定の原稿（`--long` を付けないときの `draft.md`）が Threads の上限
    （500 字未満）に収まっているかの自己検査（H2）。

    ここが緩むと「1 回で通るか」の原稿が上限超えのまま配られ、`thth send` /
    `thth throw` が最初の 1 回で「長すぎます」に当たって止まる——測りたい
    条件（設計 §2 の成功の定義 1〜4）にすら着けない。`build_box.py` 自身が
    ここで fail する（テストの緑にも、箱を組めたという見かけにも紛れ込ませない）。
    """
    n = len(DRAFT_MD)
    if n >= 500:
        raise SystemExit(
            "1 回で通るかの原稿が上限を超えています"
            f"（既定の draft.md が {n} 字・Threads の上限は 500 字未満）。"
            "build_box.py の DRAFT_MD を短くしてください。"
            "上限超えの原稿で測りたいときは --long を使ってください。")


_selfcheck_default_draft()  # 読み込まれた時点（import 経由も含む）で検査する。

# `tests/test_fresh_install.py` と同じ**偽の値**（本物のアプリ ID・秘密ではない）。
APP_ENV = ("THREADS_APP_ID=0000000000000000\n"
           "THREADS_APP_SECRET=dummy-not-a-real-secret\n")

THTH_WRAPPER = '''#!{python}
"""`thth` の wrapper（試験の箱・`tools/llm_trial/build_box.py` が置いた）。

`log/commands.ndjson` に書くのは **argv・rc・時刻・cwd と、決められた印が出たか
どうかの真偽だけ**。本物（`thth.real`）を同じ argv で起こす。

**本文も stdout も書かない。** stdout は素通しする途中で `MARKERS` の語と、
JSON object の `digest` キーが**現れたかどうか**だけを見る（印の名前を `showed`
に残す）。読み終えたかたまりは捨て、**またぐ語のために末尾 64 文字だけ**と
JSON の構文状態だけを持つ——つまり本文はどこにも溜まらない。

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


class JSONDigestMarker:
    """JSON object の `digest` key と正規の 12 桁 hex 値を見つける有限状態機械。

    stdout 全体や JSON の値は保存しない。文字列内の `digest`、値としての
    `"digest"`、`\\"digest\\":` という引用は key ではないので数えない。
    `null`・空文字・12 桁でない値も「digest を見せた」にはしない。保持する候補は
    key の 6 文字と値の 12 文字までなので、stdout の長さによらず保持量は一定。
    """

    TARGET = "digest"

    def __init__(self):
        self.object_depth = 0
        self.in_string = False
        self.escaped = False
        self.candidate = None
        self.pending_key = False
        self.expect_value = False
        self.reading_value = False
        self.value_candidate = None
        self.value_escaped = False
        self.found = False

    def feed(self, text):
        for ch in text:
            if self.reading_value:
                if self.value_escaped:
                    self.value_escaped = False
                    self.value_candidate = None
                    continue
                if ch == "\\\\":
                    self.value_escaped = True
                    self.value_candidate = None
                    continue
                if ch == '"':
                    self.reading_value = False
                    self.found = (
                        self.value_candidate is not None
                        and len(self.value_candidate) == 12)
                    self.value_candidate = None
                    if self.found:
                        return True
                    continue
                if self.value_candidate is not None:
                    if ch in "0123456789abcdef" and len(self.value_candidate) < 12:
                        self.value_candidate += ch
                    else:
                        self.value_candidate = None
                continue

            if self.in_string:
                if self.escaped:
                    self.escaped = False
                    self.candidate = None
                    continue
                if ch == "\\\\":
                    self.escaped = True
                    continue
                if ch == '"':
                    self.in_string = False
                    self.pending_key = (
                        self.object_depth > 0 and self.candidate == self.TARGET)
                    self.candidate = None
                    continue
                if self.candidate is not None:
                    self.candidate += ch
                    if not self.TARGET.startswith(self.candidate):
                        self.candidate = None
                continue

            if self.pending_key:
                if ch.isspace():
                    continue
                if ch == ":":
                    self.pending_key = False
                    self.expect_value = True
                    continue
                self.pending_key = False

            if self.expect_value:
                if ch.isspace():
                    continue
                self.expect_value = False
                if ch == '"':
                    self.reading_value = True
                    self.value_candidate = ""
                    self.value_escaped = False
                continue

            if ch == '"':
                self.in_string = True
                self.escaped = False
                self.candidate = "" if self.object_depth > 0 else None
            elif ch == "{{":
                self.object_depth += 1
            elif ch == "}}":
                self.object_depth = max(0, self.object_depth - 1)
        return self.found


def main() -> int:
    env = dict(os.environ)
    # `git` の wrapper が「thth の中から呼ばれた git」を見分けるための印。
    env["THTH_TRIAL_INSIDE"] = "1"
    showed = []
    json_digest = JSONDigestMarker()
    json_mode = "--json" in sys.argv[1:]
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
            decoded = chunk.decode("utf-8", "replace")
            window = keep + decoded
            for name, word in MARKERS.items():
                # JSON の本文文字列に `digest: ...` と書かれていても印ではない。
                # `--json` のときは構文を見る scanner だけを使う。
                if json_mode and name == "digest":
                    continue
                if name not in showed and word in window:
                    showed.append(name)
            if json_mode and "digest" not in showed and json_digest.feed(decoded):
                showed.append("digest")
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


def _hash_data_dirs(box: str) -> dict:
    """各 repo の `data/`（絡みの台帳・返信の台帳）を `"<repo名>/<相対パス>" → sha256`
    でまとめて返す（T4-2・`score.py --engage` の条件 5「保存していない」用）。

    **平らな 1 つの dict にする**——`_changed()` は 1 段の辞書しか比べないので、
    `accounts`・`home_config` と同じ形に揃える。
    """
    out: dict = {}
    repos_dir = os.path.join(box, "repos")
    if not os.path.isdir(repos_dir):
        return out
    for name in sorted(os.listdir(repos_dir)):
        data_path = os.path.join(repos_dir, name, "data")
        for relpath, h in _hash_tree(data_path).items():
            out[f"{name}/{relpath}"] = h
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
        "data": _hash_data_dirs(box),
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
    """台帳 1 本。**wrapper を置く前に打つ**——ログに残さないため（設計 4-1）。

    **`scheduled: true` にする**（H1(a)・第 1 回の記録 §3）。`thth account add` は
    必ず `scheduled: false` で台帳を書く（`thth/account_cli.py:build_ledger()`——
    道具が作ったものがいきなり timer に載ってはいけない）。ところが
    `thth/account_report.py:622` の `scheduled = account_cfg.get("scheduled", True)`
    が偽なら、`thth account <name>` は **「同席専用（queue も timer も持たない）」**
    と述べ、repo も queue も出さない。第 1 回の被験者 3 体はこれを読んで
    `queue → lint → approve → throw` を**正しく諦め**、`send --text-file` に着いた。
    測りたかった経路が箱に無かったのだから、これは被験者ではなく**箱の欠陥**。

    `scheduled` は台帳の鍵**だけ**で決まる（`repo_dir` や queue dir の有無ではない
    ——それらは `scheduled` が真のときに初めて blocker として見られる）。だから
    ここで 1 鍵だけ立て直す。**`production` は false のまま**（この箱は本物を
    投げない）。原稿 repo の `docs/sns/queue/` は空のまま——雛形を作るところから
    が試験。
    """
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
    data["scheduled"] = True
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def start_fake_bluesky_server(box: str) -> dict:
    """`fake_bluesky_server.py` を箱に紐づく背景プロセスとして起こす（T4-2）。

    **本物の `bsky.social` には一切触れない**——このサーバは `127.0.0.1` の
    空きポートで待つだけで、台帳の `service`（`write_ledger_bluesky()`）を
    ここへ向けるので、被験者が `thth where`/`thth thread` を打っても
    行き先はこのローカルサーバだけになる。

    `start_new_session=True` で起こす——`build_box.py` 自身のプロセスが終わっても
    （箱を組んだあと、別の時点・別のプロセスで被験者が箱を使うので）サーバは
    生き続ける必要がある。起動直後の 1 行（`{"host","port"}`）だけを読み、
    それ以降の stdout は読まない（サーバ側もそれ以降は何も書かない・
    `fake_bluesky_server.py` の docstring）。

    `box/fake_bluesky.json` に `{"host","port","pid","url"}` を書く——
    `stop_fake_bluesky_server()` と `score.py --engage` がここから読む。
    """
    proc = subprocess.Popen(
        [sys.executable, FAKE_BLUESKY_SERVER, "--host", "127.0.0.1", "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        start_new_session=True)
    line = proc.stdout.readline()
    if not line:
        proc.kill()
        raise SystemExit("偽の Bluesky サーバを起動できませんでした（起動行が読めない）")
    info = json.loads(line)
    info["pid"] = proc.pid
    info["url"] = f"http://{info['host']}:{info['port']}"

    # **本当に応える状態か**を確かめてから返す（bind はできても accept ループに
    # 入るまでの一瞬を拾わない）。届かなければここで待たずに fail する——
    # 「箱は組めたが実は死んでいた」を見かけの緑にしない（作法 5）。
    deadline = time.monotonic() + 5.0
    last_err = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                    info["url"] + "/xrpc/app.bsky.feed.searchPosts?q=x", timeout=1) as r:
                r.read()
            break
        except Exception as e:  # noqa: BLE001 - 立ち上がりの一瞬の失敗は再試行するだけ
            last_err = e
            time.sleep(0.1)
    else:
        proc.kill()
        raise SystemExit(f"偽の Bluesky サーバが応えません（{info['url']}）: {last_err}")

    with open(os.path.join(box, "fake_bluesky.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    return info


def stop_fake_bluesky_server(box: str) -> None:
    """`start_fake_bluesky_server()` が起こしたプロセスを止める（試験の後始末）。

    無ければ・既に死んでいれば何もしない（loud reject は要らない場面——
    後始末の二重呼び出しはよくある）。
    """
    path = os.path.join(box, "fake_bluesky.json")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        info = json.load(f)
    try:
        os.kill(info["pid"], signal.SIGTERM)
    except (ProcessLookupError, PermissionError, KeyError):
        pass


def write_ledger_bluesky(box: str, repo: str, *, service: str) -> str:
    """`demo-bluesky` の台帳（媒体 bluesky・`service` を偽サーバへ・T4-2）。

    `write_ledger()`（`demo-threads`）と**同じ理由で** `scheduled: true` に
    立て直す（H1(a)）——`queue → lint → approve` の経路が箱に無いと、被験者は
    それを「無い」と正しく諦めてしまう。
    """
    thth = os.path.join(box, "venv", "bin", "thth")
    r = _run([thth, "account", "add", ENGAGE_ACCOUNT, "--media", "bluesky",
              "--project", "demo-bluesky", "--handle", ENGAGE_HANDLE,
              "--instance", service, "--repo-dir", repo, "--force"],
             env=box_env(box), cwd=box)
    if r.returncode != 0:
        raise SystemExit(f"demo-bluesky の台帳を書けませんでした:\n{r.stdout}\n{r.stderr}")
    path = os.path.join(box, "root", "accounts", f"{ENGAGE_ACCOUNT}.json")
    if not os.path.exists(path):
        raise SystemExit(f"台帳が出来ていません: {path}\n{r.stdout}\n{r.stderr}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("production") is not False:
        raise SystemExit(f"台帳が production: true で生まれました（試験に使えません）: {path}")
    if data.get("service") != service:
        raise SystemExit(f"台帳の service が偽サーバを向いていません: {data.get('service')!r}")
    data["scheduled"] = True
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def write_token_bluesky(box: str) -> str:
    """`demo-bluesky` の `.token`（偽の値・T4-2）。

    **`~/.config/thth/demo-bluesky.token` に書く**（`box_env()` の `HOME` が
    箱の `home/` を指すので、実物の `~/.config/thth/` には触れない）。値は
    `has_token()` が「在るか」しか見ないので中身は偽物のままでよい——この
    トークンが実際に運ぶ先は `write_ledger_bluesky()` が向けた偽サーバだけ
    （`127.0.0.1`）で、本物の Bluesky には最初から経路が無い。
    """
    config_dir = os.path.join(box, "home", ".config", "thth")
    os.makedirs(config_dir, exist_ok=True)
    path = os.path.join(config_dir, f"{ENGAGE_ACCOUNT}.token")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"identifier": ENGAGE_HANDLE, "app_password": FAKE_APP_PASSWORD}, f)
    os.chmod(path, 0o600)
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


def write_draft(box: str, *, long: bool = False) -> str:
    """`draft.md` を 1 本書く。既定は上限内（`DRAFT_MD`）、`long=True` のときだけ
    上限超え（`DRAFT_MD_LONG`）——`--long` は編集の往復を測る別の試験用。"""
    path = os.path.join(box, "draft.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(DRAFT_MD_LONG if long else DRAFT_MD)
    return path


def build_box(box: str, *, long: bool = False, engage: bool = False) -> str:
    """箱を 1 つ組む。`engage=True`（`--engage`・T4-2）なら、既定の
    `demo-threads`（Threads の「1 回で通るか」用）に**足して**、偽の Bluesky
    サーバへ向いた `demo-bluesky` の台帳も用意する（「この枝に絡んで」用）。
    """
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
    write_draft(box, long=long)

    write_ledger(box, repo)          # **wrapper より前**（ログに残さない）

    if engage:
        # **ここも wrapper より前**（demo-threads と同じ理由）。偽サーバは
        # 箱に紐づく背景プロセスとして起こす——`fake_bluesky.json` に pid・port
        # が残るので、使い終えたら `stop_fake_bluesky_server(box)` で止める。
        server_info = start_fake_bluesky_server(box)
        write_ledger_bluesky(box, repo, service=server_info["url"])
        write_token_bluesky(box)

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
    p.add_argument("--long", action="store_true",
                   help="draft.md を上限超え（942 字ほど）にする"
                        "（既定は 380〜440 字・上限内。編集の往復を測る別の試験用）")
    p.add_argument("--engage", action="store_true",
                   help="偽の Bluesky サーバ（fake_bluesky_server.py）と "
                        "demo-bluesky の台帳を足す（発注 T4-2「この枝に絡んで」の試験用）")
    args = p.parse_args(argv)
    box = build_box(args.box, long=args.long, engage=args.engage)
    print(f"箱を組みました: {box}")
    print(f"  環境:   . {os.path.join(box, 'env.sh')}")
    print(f"  原稿:   {os.path.join(box, 'draft.md')}"
          + ("（--long・上限超え）" if args.long else "（上限内）"))
    print(f"  台帳:   {os.path.join(box, 'root', 'accounts', ACCOUNT + '.json')}"
          f"（production: false・トークン無し）")
    if args.engage:
        print(f"  台帳:   {os.path.join(box, 'root', 'accounts', ENGAGE_ACCOUNT + '.json')}"
              f"（production: false・偽サーバへ向いたトークンあり）")
        print(f"  偽サーバ: {os.path.join(box, 'fake_bluesky.json')}"
              "（使い終えたら stop_fake_bluesky_server(box) で止める）")
        print(f"  固定文:  {os.path.join(TOOLS_DIR, 'prompts', 'engage_prompt.md')}")
    print(f"  記録:   {os.path.join(box, 'log', 'commands.ndjson')}")
    print(f"  採点:   python3 tools/llm_trial/score.py {box}"
          + (" --engage" if args.engage else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
