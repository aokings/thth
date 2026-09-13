"""まっさらな導入の乾式試験（設計 v1.0.0 §2 の C2）。

**この repo を一時ディレクトリに clone し、偽の `app.env` と偽の台帳だけを置いて、
`docs/導入_自分のMetaアプリで動かす.md` §7 の順番（doctor → lint → board → dry-run）
をそのまま通す。**

守ること:
  - **実物の `~/.config/thth/` を読まない・書かない。** `HOME`・`THTH_APP_DIR`・
    `THTH_APP_ENV_PATH`・`THTH_ROOT` を全部 `tmp_path` へ向ける。とくに
    `THTH_APP_DIR` は必須で、これが無いと `thth board` が clone に入っている
    masaru の 4 本の台帳を読み、その `token` 欄が指す実物の `.token` を開く。
  - **本物の API を叩かない。** 台帳は `production: false`（`mode: rehearsal`）で、
    トークンを 1 本も置かない。`doctor` はトークンが無い時点で HTTP に届く前に
    止まる。
  - **rc を必ず見る。** 存在しないサブコマンドは argparse が rc=2 で落とすので、
    文言だけを見ていると「止まった」と「通った」を取り違える——ここでは
    `トークンが無い` の rc=2 と argparse の rc=2 を区別するために、文言と rc の
    両方を assert する。

**期待どおりの停止**: トークンを入れる前の `thth doctor` は rc=2 で
`トークンが無い（thth token set を先に）` と言って止まる。これは失敗ではなく、
導入の途中経過が正しく見えていることの確認。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ACCOUNT = "demo-threads"

# clone に入って来る masaru の台帳（導入文書 §4 で消してもらうもの）。
#
# **6 本ある**（2026-09-13・v2-2a）。ここが 4 本だった間、`masaru-bluesky` と
# `masaru-mastodon` が board に出ていても、どの試験も気づかなかった
# （導入_Bluesky / 導入_Mastodon で 2 本増えたときに、ここを足していない）。
OTHER_LEDGERS = ("nigamilab-threads", "kopicha-threads", "masaru-threads",
                 "asmon-kanto-threads", "masaru-bluesky", "masaru-mastodon")

QUEUE_MD = """---
thth: 1
account: demo-threads
publish_at: 2026-09-12T08:00:00+09:00
status: draft
topic:
reply_to:
post_id:
posted_at:
---
# 乾式試験用の下書き

## threads

これは導入の確かめ用の下書きです。実際には出ません。
"""


def _git(*args, cwd):
    return subprocess.run(
        ["git", "-c", "user.email=fresh@example.invalid", "-c", "user.name=fresh",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, check=True)


def clone_app(dest: str) -> str:
    """**この worktree を `file://` で clone する。**

    `file://` にするのは hardlink の共有を避けて「他所から持ってきた clone」に
    近づけるため。clone は **HEAD の commit** を取るので、commit していない変更は
    入らない（＝配ったものを試している）。

    **clone は 1 回で使い回す**（この module の session fixture）。1 回 1.5 秒ほど
    かかり、試験の本数だけ繰り返すと全件テストが 2 倍以上に伸びる。使い回して
    よいのは、ここで叩く subcommand が **app repo を書き換えないから**——app 自身を
    `release` へ ff-only するのは `thth run` だけで（`thth/cli.py` `cmd_run()`）、
    この試験は `run` を呼ばない。**呼ぶようになったら clone を共有しないこと。**
    """
    app = os.path.join(dest, "app")
    subprocess.run(["git", "clone", "--quiet", "file://" + REPO_ROOT, app],
                   capture_output=True, text=True, check=True)
    assert os.path.exists(os.path.join(app, "thth", "cli.py"))
    assert os.path.exists(os.path.join(app, "thth", "__main__.py")), (
        "clone に thth/__main__.py が無い（`python -m thth` が使えない）")
    return app


class FreshInstall:
    """まっさらな clone と、そこへ被せる環境変数一式。"""

    def __init__(self, base, app, *, write_app_env=True):
        self.base = str(base)
        self.app = app
        self.home = os.path.join(self.base, "home")
        self.thth_root = os.path.join(self.base, "root")
        self.app_dir = os.path.join(self.base, "appdir")
        self.config = os.path.join(self.home, ".config", "thth")
        self.app_env = os.path.join(self.config, "app.env")
        self.repo = os.path.join(self.base, "repos", "demo")
        self.queue = os.path.join(self.repo, "docs", "sns", "queue")
        self.origin = os.path.join(self.base, "origin.git")
        self.write_app_env = write_app_env

    # ---- 組み立て -------------------------------------------------------
    def build(self):
        for d in (self.home, self.thth_root, os.path.join(self.app_dir, "accounts"),
                  self.config, os.path.dirname(self.repo)):
            os.makedirs(d, exist_ok=True)
        self._make_user_repo()
        if self.write_app_env:
            self._write_app_env()
        self._write_ledger()
        return self

    def _make_user_repo(self):
        """利用者 repo。**`origin` を持つ clone でないと `thth throw` が止まる。**"""
        subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", self.origin],
                       capture_output=True, text=True, check=True)
        subprocess.run(["git", "clone", "--quiet", self.origin, self.repo],
                       capture_output=True, text=True, check=True)
        os.makedirs(self.queue, exist_ok=True)
        with open(os.path.join(self.queue, "2026-09-12-hello.md"), "w",
                  encoding="utf-8") as f:
            f.write(QUEUE_MD)
        _git("add", "docs", cwd=self.repo)
        _git("commit", "--quiet", "-m", "queue", cwd=self.repo)
        _git("push", "--quiet", "-u", "origin", "main", cwd=self.repo)

    def _write_app_env(self):
        """**偽の値。** 本物のアプリ ID・シークレットではない。"""
        with open(self.app_env, "w", encoding="utf-8") as f:
            f.write("THREADS_APP_ID=0000000000000000\n")
            f.write("THREADS_APP_SECRET=dummy-not-a-real-secret\n")
        os.chmod(self.app_env, 0o600)

    def _write_ledger(self):
        ledger = {
            "account": ACCOUNT,
            "project": "demo",
            "media": "threads",
            "handle": "demo",
            "user_id": "",
            "repo_dir": self.repo,
            "queue_dir": "docs/sns/queue",
            "replies_dir": "data/sns/replies",
            "quiet_hours": ["22:00", "07:00"],
            "min_interval_hours": 6,
            "collect_days": 14,
            "hashtags": False,
            "stale_days": 7,
            "env": os.path.join(self.config, f"{ACCOUNT}.env"),
            "token": os.path.join(self.config, f"{ACCOUNT}.token"),
            "ping": "wrapper",
            "timeout": 300,
            "dry_run_env": "THTH_DRY_RUN",
            "production": False,
            "redirect_uri": "https://example.invalid/",
        }
        path = os.path.join(self.app_dir, "accounts", f"{ACCOUNT}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2)

    # ---- 実行 -----------------------------------------------------------
    def env(self):
        env = dict(os.environ)
        env["HOME"] = self.home
        env["THTH_ROOT"] = self.thth_root
        env["THTH_APP_DIR"] = self.app_dir
        env["THTH_APP_ENV_PATH"] = self.app_env
        env["PYTHONPATH"] = self.app
        env.pop("THTH_DRY_RUN", None)
        env.pop("THTH_RELEASE_REF", None)
        # **`THTH_ACCOUNTS_DIR` は必ず外す**（設計 v2 §3・v2-2a）。この試験は
        # 台帳の置き場の解決そのもの——(b) `$THTH_ROOT/accounts` と互換の (c)
        # app repo `accounts/`——を通す場なので、打った人の shell や別の fixture が
        # 置いた明示指定が紛れ込むと、**確かめたい経路を素通りして通ってしまう**。
        env.pop("THTH_ACCOUNTS_DIR", None)
        return env

    def run(self, *argv, stdin=""):
        """clone した thth を `python -m thth` で呼ぶ（PATH を通していない人が
        まず打つ形）。"""
        return subprocess.run([sys.executable, "-m", "thth", *argv],
                              cwd=self.app, env=self.env(), input=stdin,
                              capture_output=True, text=True, timeout=180)


@pytest.fixture(scope="session")
def cloned_app(tmp_path_factory):
    return clone_app(str(tmp_path_factory.mktemp("thth-fresh-clone")))


@pytest.fixture
def fresh(tmp_path, cloned_app):
    return FreshInstall(tmp_path, cloned_app).build()


# --------------------------------------------------------------------------
# 導入文書 §7 の順番: doctor → lint → board → dry-run
# --------------------------------------------------------------------------

def test_step_1_doctor_stops_because_there_is_no_token(fresh):
    """**期待どおりの停止。** トークンを入れる前の doctor は rc=2 で止まる。"""
    r = fresh.run("doctor", ACCOUNT)
    assert r.returncode == 2, f"rc={r.returncode}\nout={r.stdout}\nerr={r.stderr}"
    assert "トークンが無い" in r.stdout, r.stdout
    # argparse が知らない語で落ちたのではないこと（rc=2 は両方で起きる）。
    assert "usage:" not in r.stderr, r.stderr
    assert "invalid choice" not in r.stderr, r.stderr
    # 読み取りだけの道具が、トークンが無い段で API へ届いていないこと。
    assert "HTTP" not in r.stdout, r.stdout


def test_step_2_lint_passes_on_the_queue(fresh):
    r = fresh.run("lint", fresh.queue)
    assert r.returncode == 0, f"rc={r.returncode}\nout={r.stdout}\nerr={r.stderr}"
    assert "OK" in r.stdout, r.stdout


def test_step_2b_lint_refuses_an_empty_directory(fresh):
    """**「検査できるものが無い」を成功にしない**（loud reject・作法 5）。"""
    empty = os.path.join(fresh.base, "empty")
    os.makedirs(empty, exist_ok=True)
    r = fresh.run("lint", empty)
    assert r.returncode == 1, f"rc={r.returncode}\nout={r.stdout}\nerr={r.stderr}"
    assert "対象 0 件" in (r.stdout + r.stderr)


def test_step_3_board_shows_the_account_without_a_token(fresh):
    r = fresh.run("board")
    assert r.returncode == 0, f"rc={r.returncode}\nout={r.stdout}\nerr={r.stderr}"
    assert ACCOUNT in r.stdout, r.stdout
    assert "token=no_token" in r.stdout, r.stdout
    # **masaru の台帳を読んでいないこと**（THTH_APP_DIR の隔離が効いている）。
    for other in OTHER_LEDGERS:
        assert other not in r.stdout, f"{other} が board に出た（隔離が効いていない）"


def test_step_4_dry_run_is_rehearsal_and_posts_nothing(fresh):
    """`production: false` なので `mode: rehearsal`。draft は投げない。"""
    r = fresh.run("throw", ACCOUNT, "--now")
    assert r.returncode == 0, f"rc={r.returncode}\nout={r.stdout}\nerr={r.stderr}"
    assert r.stdout.splitlines()[0] == "mode: rehearsal", r.stdout
    assert "not_approved" in r.stdout, r.stdout


# --------------------------------------------------------------------------
# 導入文書 §3・§5: app.env を使うのは `thth auth` だけ（doctor は有無だけ見る・tests/test_doctor_next_step.py）
# --------------------------------------------------------------------------

def test_auth_reads_app_env_and_stops_at_the_code_prompt(fresh):
    """**偽の `app.env` が実際に読まれていること**を見る（変異の的）。

    `--code ""` で非対話にしてあるので HTTP には届かない。app.env → redirect_uri
    → 認可 URL の表示まで進み、code が空なので rc=2 で止まる。
    """
    r = fresh.run("auth", ACCOUNT, "--code", "")
    assert r.returncode == 2, f"rc={r.returncode}\nout={r.stdout}\nerr={r.stderr}"
    assert "app.env が無い" not in r.stdout, r.stdout
    assert "/oauth/authorize?" in r.stdout, r.stdout
    assert "code が読み取れませんでした" in r.stdout, r.stdout
    # 偽の app_id が認可 URL に載っている＝この app.env を読んだ、の証拠。
    assert "client_id=0000000000000000" in r.stdout, r.stdout


def test_auth_stops_loudly_when_app_env_is_missing(tmp_path, cloned_app):
    """**app.env を置かないと `thth auth` は止まる。**

    上のテストが本当に app.env を見ているかの裏取り（同じ組み立てで
    `write_app_env=False` にするだけ）。
    """
    fresh = FreshInstall(tmp_path, cloned_app, write_app_env=False).build()
    assert not os.path.exists(fresh.app_env)
    r = fresh.run("auth", ACCOUNT, "--code", "")
    assert r.returncode == 2, f"rc={r.returncode}\nout={r.stdout}\nerr={r.stderr}"
    assert "app.env が無い" in r.stdout, r.stdout
    assert "/oauth/authorize?" not in r.stdout, r.stdout


# --------------------------------------------------------------------------
# 導入文書 §6: timer は生成物を使う
# --------------------------------------------------------------------------

def test_systemd_unit_is_generated_from_the_ledger(fresh):
    r = fresh.run("systemd", ACCOUNT)
    assert r.returncode == 0, f"rc={r.returncode}\nout={r.stdout}\nerr={r.stderr}"
    assert f"Unit=thth@{ACCOUNT}.service" in r.stdout, r.stdout
    assert "OnCalendar=*:" in r.stdout and "/10" in r.stdout, r.stdout
    assert "Persistent=true" in r.stdout, r.stdout


# --------------------------------------------------------------------------
# 導入文書 §8: 配布の枝
# --------------------------------------------------------------------------

def test_board_does_not_claim_to_be_up_to_date_without_a_release_check(fresh):
    """clone した直後は `release` を一度も取りに行っていない。**「追いついて
    います」と言わない**（設計 §3.2.1）。"""
    r = fresh.run("board")
    assert r.returncode == 0, r.stderr
    assert "確認できません" in r.stdout, r.stdout
    assert "追いついています" not in r.stdout, r.stdout


# --------------------------------------------------------------------------
# 存在しないコマンドで「通った」ことにならない
# --------------------------------------------------------------------------

def test_a_command_that_does_not_exist_is_not_mistaken_for_a_stop(fresh):
    r = fresh.run("doctorr", ACCOUNT)
    assert r.returncode == 2
    assert "invalid choice" in r.stderr, r.stderr
    assert "トークンが無い" not in r.stdout


# --------------------------------------------------------------------------
# 導入文書 §4 の経路: **`THTH_APP_DIR` を設定しない**（clone した人が打つ形）
# --------------------------------------------------------------------------

def test_step_3b_THTH_APP_DIRを設定しない導入者のboard(tmp_path):
    """**乾式試験が 1 本も通していなかった経路**（独立監査 1・P3-12）。

    ここまでの試験はすべて `THTH_APP_DIR` を偽の場所へ向けている。だが
    **clone した人はそれを設定しない**——導入文書 §4 が言うのは
    「使わない台帳を消してください」で、`accounts/` は clone に入って来る。
    その経路（4 本を消して自分のを 1 本置く）を実際に通す。

    **この試験だけ専用の clone を作る**（`cloned_app` は session fixture で
    ほかの試験と共有しているので、そこから `accounts/` を消せない）。
    """
    app = clone_app(str(tmp_path))
    fresh = FreshInstall(tmp_path, app).build()

    accounts = os.path.join(app, "accounts")
    配られた台帳 = sorted(n for n in os.listdir(accounts) if n.endswith(".json"))
    # **前提を明示する。** clone は masaru の台帳を連れて来る——消さずに board を
    # 打つと 4 本とも出る（監査 1 の `freshreal.sh` が示したのがこれ）。ここが
    # 空なら、下の「出ないこと」は何も確かめていない。
    assert 配られた台帳, "clone に台帳が入っていない（この試験の前提が崩れている）"
    for other in OTHER_LEDGERS:
        assert f"{other}.json" in 配られた台帳, (
            f"{other} が clone に入っていない（この試験の前提が崩れている）")
    # 導入文書 §4: **使わない台帳を消して、自分のものだけを置く。**
    for name in 配られた台帳:
        os.remove(os.path.join(accounts, name))
    shutil.copy(os.path.join(fresh.app_dir, "accounts", f"{ACCOUNT}.json"),
                os.path.join(accounts, f"{ACCOUNT}.json"))

    env = fresh.env()
    env.pop("THTH_APP_DIR")            # **設定しない**（ここが実際の導入者）
    r = subprocess.run([sys.executable, "-m", "thth", "board"], cwd=app, env=env,
                       capture_output=True, text=True, timeout=180)

    assert r.returncode == 0, f"rc={r.returncode}\nout={r.stdout}\nerr={r.stderr}"
    assert ACCOUNT in r.stdout, r.stdout
    for other in OTHER_LEDGERS:
        assert other not in r.stdout, (
            f"{other} が board に出た（§4 で消したはずの台帳を読んでいる）:\n{r.stdout}")


# --------------------------------------------------------------------------
# 設計 v2 §3「台帳を repo の外へ」（v2-2a・masaru 裁定 2026-09-13「出す」）
# --------------------------------------------------------------------------

def 導入者として打つ(fresh, *argv):
    """**`THTH_APP_DIR` を設定しない**（clone した人が実際に打つ形）で thth を呼ぶ。

    `THTH_APP_DIR` を偽の場所へ向けると、確かめたい「clone の中の 6 本」が
    そもそも見えない。設定しないので `accounts.APP_DIR` は clone 自身になる。
    """
    env = fresh.env()
    env.pop("THTH_APP_DIR", None)
    return subprocess.run([sys.executable, "-m", "thth", *argv], cwd=fresh.app,
                          env=env, capture_output=True, text=True, timeout=180)


def test_v2_2a_台帳を消さなくてもaddした時点で外が正になる(fresh):
    """**まっさらな clone に `THTH_ROOT` だけ設定して `thth account add` を 1 本。**

    導入文書 §4 は今まで「使わない台帳を**消してください**」だった（`accounts/` は
    clone に入って来るので、消さないと masaru の 6 本が board に並ぶ）。設計 v2 §3 で
    それを変える——**外（`$THTH_ROOT/accounts/`）に 1 本置いた時点で、repo の中は
    もう読まれない。**

    ここが固定するのは「`add` した後、repo の 6 本が **1 本も** 出ないこと」。
    互換 (c) は「`$THTH_ROOT/accounts/` が**無いとき**」だけ効くので、`add` が
    ディレクトリを作った時点で外が正になる。
    """
    # --- 前提: add する前は、互換 (c) で clone の 6 本が見えている ---------
    前 = 導入者として打つ(fresh, "board")
    assert 前.returncode == 0, f"rc={前.returncode}\nout={前.stdout}\nerr={前.stderr}"
    for other in OTHER_LEDGERS:
        assert other in 前.stdout, (
            f"{other} が board に出ていない（互換 (c) が効いていない＝**VM が止まる形**）:"
            f"\n{前.stdout}")
    # **互換であることを画面が言っている。**
    assert "互換" in 前.stdout, 前.stdout
    assert "thth account migrate" in 前.stdout, 前.stdout
    assert not os.path.isdir(os.path.join(fresh.thth_root, "accounts")), (
        "この試験の前提が崩れている（外が最初からある）")

    # --- まず**断られる**（監査 1・P1-3）-----------------------------------
    # 互換 (c) のまま `add` を打つと、書く先が出来た瞬間に repo の 6 本が
    # 読まれなくなる。**それが望みなのか（clone に同梱された他人の台帳）**、
    # **事故なのか（自分の 6 本を移し忘れた VM）**は道具には区別が付かないので、
    # 何が起きるかを言って 1 度止まる。
    断られた = 導入者として打つ(fresh, "account", "add", ACCOUNT,
                              "--media", "threads", "--project", "demo")
    assert 断られた.returncode == 1, 断られた.stdout + 断られた.stderr
    assert "以後読まれません" in 断られた.stderr
    assert "--force" in 断られた.stderr
    assert not os.path.isdir(os.path.join(fresh.thth_root, "accounts")), \
        "断ったのに外のディレクトリが出来た"

    # --- `thth account add --force` を 1 本 --------------------------------
    # 導入者にとっては **repo の 6 本は他人のもの**なので、これが正しい進み方。
    add = 導入者として打つ(fresh, "account", "add", ACCOUNT,
                          "--media", "threads", "--project", "demo", "--force")
    assert add.returncode == 0, f"rc={add.returncode}\nout={add.stdout}\nerr={add.stderr}"

    # 書かれたのは **外**。clone の `accounts/` は 1 本も増えていない。
    外 = os.path.join(fresh.thth_root, "accounts")
    assert os.path.exists(os.path.join(外, f"{ACCOUNT}.json")), add.stdout
    clone側 = sorted(n for n in os.listdir(os.path.join(fresh.app, "accounts"))
                     if n.endswith(".json"))
    assert f"{ACCOUNT}.json" not in clone側, (
        f"`account add` が clone の中に書いた: {clone側}")
    assert len(clone側) == 6, f"clone の accounts/ が 6 本でない: {clone側}"

    # --- board に demo だけが出る ----------------------------------------
    後 = 導入者として打つ(fresh, "board")
    assert 後.returncode == 0, f"rc={後.returncode}\nout={後.stdout}\nerr={後.stderr}"
    assert ACCOUNT in 後.stdout, 後.stdout
    for other in OTHER_LEDGERS:
        assert other not in 後.stdout, (
            f"{other} が board に出た（**外に 1 本置いたのに repo の中も読んでいる**）:"
            f"\n{後.stdout}")
    # もう互換ではない——警告も出ない。
    assert "互換" not in 後.stdout, 後.stdout
    assert "台帳が repo の中にあります" not in 後.stderr, 後.stderr
    assert f"台帳の置き場: {外}" in 後.stdout, 後.stdout


def test_v2_2a_migrateはrepoの6本を外へ写す_repoは触らない(fresh):
    """**VM がやる手順**（導入文書 §4）を、clone の上でそのまま通す。

    `migrate --dry-run` → `migrate` → `board` で **6 本が変わらず見える**。
    そして **clone の `accounts/` は手つかず**（消すのは別の日・設計 v2 §8）。
    """
    clone_accounts = os.path.join(fresh.app, "accounts")
    元の6本 = sorted(n for n in os.listdir(clone_accounts) if n.endswith(".json"))
    assert len(元の6本) == 6, 元の6本

    dry = 導入者として打つ(fresh, "account", "migrate", "--dry-run")
    assert dry.returncode == 0, f"rc={dry.returncode}\nout={dry.stdout}\nerr={dry.stderr}"
    assert "--dry-run なので何も書いていません" in dry.stdout
    # **`--dry-run` は外のディレクトリを作らない。** 作ると (b) が成立して、
    # 次の `thth board` が「外（空）」を正と見なし、**6 本が消えて見える。**
    assert not os.path.isdir(os.path.join(fresh.thth_root, "accounts")), dry.stdout

    実行 = 導入者として打つ(fresh, "account", "migrate")
    assert 実行.returncode == 0, f"rc={実行.returncode}\nout={実行.stdout}\nerr={実行.stderr}"
    写された = sorted(n for n in os.listdir(os.path.join(fresh.thth_root, "accounts"))
                      if n.endswith(".json"))
    assert 写された == 元の6本, (写された, 元の6本)
    # **repo は触らない**（VM の `/srv/thth/app` は `merge --ff-only` の clone）。
    assert sorted(os.listdir(clone_accounts)) == sorted(os.listdir(clone_accounts))
    assert sorted(n for n in os.listdir(clone_accounts)
                  if n.endswith(".json")) == 元の6本

    # **6 本が変わらず見える**（移行の受け入れ条件そのもの）。
    board = 導入者として打つ(fresh, "board")
    assert board.returncode == 0, board.stdout + board.stderr
    for other in OTHER_LEDGERS:
        assert other in board.stdout, (
            f"{other} が migrate の後に board から消えた:\n{board.stdout}")
    assert "互換" not in board.stdout, board.stdout
