"""まっさらな導入の乾式試験（設計 v1.0.0 §2 の C2）。

**この repo を一時ディレクトリに clone し、偽の `app.env` と偽の台帳だけを置いて、
`docs/導入_自分のMetaアプリで動かす.md` §7 の順番（doctor → lint → board → dry-run）
をそのまま通す。**

守ること:
  - **実物の `~/.config/thth/` を読まない・書かない。** `HOME`・`THTH_APP_DIR`・
    `THTH_APP_ENV_PATH`・`THTH_ROOT` を全部 `tmp_path` へ向ける。`THTH_ROOT` を
    向け損ねると、`thth board` が打った人の実物の `$THTH_ROOT/accounts/` を読み、
    その `token` 欄が指す実物の `.token` を開く。**clone 側に台帳は無い**
    （2026-09-14 に repo の 6 本を `git rm`・設計 v2 §3）。
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
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ACCOUNT = "demo-threads"

# **かつて clone に入って来ていた masaru の 6 本**（2026-09-14 に `git rm`・設計 v2 §3）。
#
# repo にはもう 1 本も無い。ここに名前を残しておくのは「**もう付いて来ない**」を
# 固定するため——board に 1 つでも出たら、台帳が repo へ戻ったか、隔離が破れて
# 実物の `$THTH_ROOT/accounts/` を読んでいる。
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
    # **配り物に台帳は入っていない**（2026-09-14・設計 v2 §3）。この module の
    # 試験はほぼ全部「clone に台帳が 0 本」を前提にしているので、前提そのものを
    # ここで 1 度だけ固定する（雛形 `accounts.example/` は入っている）。
    assert not os.path.exists(os.path.join(app, "accounts")), (
        "clone に accounts/ が入っている（台帳が repo に戻った）: "
        f"{sorted(os.listdir(os.path.join(app, 'accounts')))}")
    assert os.path.exists(os.path.join(app, "accounts.example", "threads.json")), (
        "clone に accounts.example/ が無い（`thth account add` の雛形が配られていない）")
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
            # **雛形の `demo` にしない**（上の `redirect_uri` と同じ理由・C10）。
            # ここは「導入者が自分の値を入れ終えた台帳」の想定で、§7 の順番を
            # 通す場。雛形のままの台帳は下の `test_C10_...` が別に見る。
            "handle": "demo-user",
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
            # **雛形のダミー（`https://example.invalid/`）にしない**（監査 2・C10・
            # 2026-09-13）。`thth auth` は**ダミーの値では認可 URL を出さずに rc=2**
            # で断るようになった。ここは §3・§5 の往復（app.env を読んで URL を
            # 組む）を通す場なので、実在しないことは同じで**ダミーではない**綴りに
            # する（`.test` は RFC 6761 の予約 TLD・本物の口には届かない）。
            # ダミーで断ることそのものは `test_C10_addした台帳のダミーをdoctorが言う`。
            "redirect_uri": "https://demo.example.test/",
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

    `--paste` で新しい認可を開始し、その state と空 code を返す。HTTP には届かない。app.env → redirect_uri
    → 認可 URL の表示まで進み、code が空なので rc=2 で止まる。
    """
    # Start a fresh flow, then return its state with an empty code. --code now
    # resumes an existing session and cannot exercise this first-flow boundary.
    import select
    import urllib.parse
    with subprocess.Popen([sys.executable, "-m", "thth", "auth", ACCOUNT, "--paste", "--by", "test-operator"],
            cwd=fresh.app, env={**fresh.env(), "PYTHONUNBUFFERED":"1"}, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        ready, _, _ = select.select([process.stdout], [], [], 15)
        if not ready:
            process.kill()
            pytest.fail("authorization URL was not produced")
        first = process.stdout.readline()
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(first.strip()).query)['state'][0]
        tail, err = process.communicate('https://demo.example.test/?' + urllib.parse.urlencode({'state':state,'code':''}) + "\n", timeout=30)
        r = subprocess.CompletedProcess(process.args, process.returncode, first+tail, err)
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
    r = fresh.run("auth", ACCOUNT, "--code", "", "--by", "test-operator")
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

def test_step_3b_THTH_APP_DIRを設定しない導入者のboard(fresh):
    """**乾式試験が 1 本も通していなかった経路**（独立監査 1・P3-12）。

    ここまでの試験はすべて `THTH_APP_DIR` を偽の場所へ向けている。だが
    **clone した人はそれを設定しない**。その経路をそのまま通す。

    **2026-09-14 に前提が変わった**（設計 v2 §3）。前は「`accounts/` が clone に
    入って来るので、6 本を消して自分のを 1 本置く」経路だった。いまは repo に
    台帳が 1 本も無いので、**`thth account add` を 1 本打つだけ**——`--force` は
    要らない（互換 (c) に落ちる台帳がそもそも無い）。

    専用の clone も要らなくなった（`accounts/` を消して回る試験ではなくなった
    ので、session fixture の clone を共有できる）。
    """
    # **前提**: clone に台帳は 1 本も無い（`clone_app()` が確かめている）。
    # 外もまだ無いので、置き場は (b) の綴りで台帳 0 本。
    assert not os.path.isdir(os.path.join(fresh.thth_root, "accounts")), \
        "この試験の前提が崩れている（外が最初からある）"

    add = 導入者として打つ(fresh, "account", "add", ACCOUNT, "--by", "test-operator",
                          "--media", "threads", "--project", "demo")
    assert add.returncode == 0, f"rc={add.returncode}\nout={add.stdout}\nerr={add.stderr}"

    r = 導入者として打つ(fresh, "board")
    assert r.returncode == 0, f"rc={r.returncode}\nout={r.stdout}\nerr={r.stderr}"
    assert ACCOUNT in r.stdout, r.stdout
    for other in OTHER_LEDGERS:
        assert other not in r.stdout, (
            f"{other} が board に出た（repo に台帳が戻ったか、実物を読んでいる）:\n{r.stdout}")


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


def test_v2_2a_clone_は台帳0本_addは断られずに通る(fresh):
    """**まっさらな clone に `THTH_ROOT` だけ設定して `thth account add` を 1 本。**

    **2026-09-14 に前提が変わった。** 前は repo に masaru の 6 本が同梱されていて、
    互換 (c) でそれが board に並び、`add` は「その 6 本が以後読まれなくなる」と
    言って 1 度断っていた（だから導入文書は `--force` を案内していた）。6 本を
    `git rm` した今は——**互換 (c) に落ちる台帳がそもそも無い**ので、`add` は
    断らない。

    ここが固定するのは 3 つ: `add` 前の board が台帳 0 本で**互換の警告も出ない**・
    `add`（`--force` 無し）が rc=0 で通る・書かれたのは**外**で clone の中には
    `accounts/` すら出来ない。
    """
    外 = os.path.join(fresh.thth_root, "accounts")
    clone_accounts = os.path.join(fresh.app, "accounts")

    # --- 前提: add する前は台帳 0 本。互換にも落ちていない -----------------
    前 = 導入者として打つ(fresh, "board")
    assert 前.returncode == 0, f"rc={前.returncode}\nout={前.stdout}\nerr={前.stderr}"
    for other in OTHER_LEDGERS:
        assert other not in 前.stdout, (
            f"{other} が board に出た（repo に台帳が戻っている）:\n{前.stdout}")
    # **互換 (c) に落ちていない**——repo に `accounts/` が無いので、置き場は
    # 最初から外の綴り。警告も stderr に出ない。
    assert "互換" not in 前.stdout, 前.stdout
    assert f"台帳の置き場: {外}" in 前.stdout, 前.stdout
    assert "台帳が repo の中にあります" not in 前.stderr, 前.stderr
    assert not os.path.isdir(外), "この試験の前提が崩れている（外が最初からある）"

    # --- `thth account add` を 1 本（**`--force` は要らない**）--------------
    add = 導入者として打つ(fresh, "account", "add", ACCOUNT, "--by", "test-operator",
                          "--media", "threads", "--project", "demo")
    assert add.returncode == 0, f"rc={add.returncode}\nout={add.stdout}\nerr={add.stderr}"
    # 断りの文言が出ていないこと（**ここが `--force` を要らなくした変更の的**）。
    assert "以後読まれません" not in add.stderr, add.stderr
    assert "--force" not in add.stderr, add.stderr

    # 書かれたのは **外**。clone の中には `accounts/` が出来てもいない。
    assert os.path.exists(os.path.join(外, f"{ACCOUNT}.json")), add.stdout
    assert not os.path.exists(clone_accounts), (
        f"`account add` が clone の中に書いた: {os.listdir(clone_accounts)}")

    # --- board に demo だけが出る ----------------------------------------
    後 = 導入者として打つ(fresh, "board")
    assert 後.returncode == 0, f"rc={後.returncode}\nout={後.stdout}\nerr={後.stderr}"
    assert ACCOUNT in 後.stdout, 後.stdout
    for other in OTHER_LEDGERS:
        assert other not in 後.stdout, (
            f"{other} が board に出た:\n{後.stdout}")
    assert "互換" not in 後.stdout, 後.stdout
    assert "台帳が repo の中にあります" not in 後.stderr, 後.stderr
    assert f"台帳の置き場: {外}" in 後.stdout, 後.stdout


def test_v2_2a_migrateは写すものが無いと言って止まる(fresh):
    """**`thth account migrate` はまだ在る**（互換 (c) はコードに 1 版だけ残す）
    **が、まっさらな clone には写す元が無い。**

    前はここが「repo の 6 本を外へ写して、repo は触らない」試験だった。6 本を
    `git rm` した今、新しく clone した人にとっての正しい振る舞いは「**写すものが
    無い**」——`--dry-run` も本番も rc=0 で、**外のディレクトリを作らない**
    （作ると (b) が成立してしまうが、それはここでは害が無い。とはいえ
    「何もしない」と言った道具が置き場を作るのは筋が通らない）。

    移行そのもの（repo の中→外へ copy）は `tests/test_accounts_dir.py` の
    `test_migrateはrepoの中を外へ写す_repoは触らない` が tmp の app dir で見る。
    """
    assert not os.path.exists(os.path.join(fresh.app, "accounts")), \
        "この試験の前提が崩れている（clone に台帳がある）"

    for argv in (["account", "migrate", "--dry-run"], ["account", "migrate"]):
        r = 導入者として打つ(fresh, *argv)
        assert r.returncode == 0, f"{argv}: rc={r.returncode}\n{r.stdout}{r.stderr}"
        assert "することはありません" in r.stdout, r.stdout
        assert not os.path.isdir(os.path.join(fresh.thth_root, "accounts")), (
            f"{argv} が外のディレクトリを作った")


# --------------------------------------------------------------------------
# C10（監査 2・masaru 裁定 2026-09-13）: `add` した台帳で `auth` まで無言で進ませない
# --------------------------------------------------------------------------

def test_C10_addした台帳のダミーをdoctorが言う(fresh):
    """**`thth account add` → `thth doctor` を、まっさらな clone の上でそのまま。**

    `add` が写す雛形の `redirect_uri` はダミー（`https://example.invalid/`）で、
    handle の既定は `--project` の値。前はその 1 本で `thth doctor` を打っても
    **何も言われず**、`thth auth` まで進んでブラウザで初めて詰まった——
    **`add` と `auth` の間に、どこにも書かれていない手作業が 2 つ**あった。

    ここで見るのは 3 つ: `add` が書いたその場で言う・`doctor` が名指しで言う・
    `auth` が**認可 URL を出す前に** rc=2 で断る。
    """
    # **`--force` は付けない**（2026-09-14・repo に台帳が無いので互換 (c) に
    # 落ちず、`add` は断らない）。ここで `--force` を付けたままにすると、
    # 「断りが出る形」に戻っても試験が気づかない。
    add = 導入者として打つ(fresh, "account", "add", ACCOUNT, "--by", "test-operator",
                          "--media", "threads", "--project", "demo")
    assert add.returncode == 0, f"rc={add.returncode}\nout={add.stdout}\nerr={add.stderr}"
    # (1) 書いたその場で 1 行（`thth auth` の前に直すこと・直し方 2 通り）。
    行 = [l for l in add.stdout.splitlines() if "redirect_uri" in l and "ダミー" in l]
    assert len(行) == 1, add.stdout
    assert "thth auth" in 行[0] and "--redirect-uri" in 行[0], 行[0]

    # (2) `doctor` が名指しする（トークンが無いので rc は 2 のまま）。
    doctor = 導入者として打つ(fresh, "doctor", ACCOUNT)
    assert doctor.returncode == 2, doctor.stdout + doctor.stderr
    assert "ダミーのまま" in doctor.stdout, doctor.stdout
    assert "https://example.invalid/" in doctor.stdout, doctor.stdout
    assert "§4" in doctor.stdout, doctor.stdout
    # **HTTP には届いていない**（読み取りの道具が、直す前に外へ出ていない）。
    assert "HTTP" not in doctor.stdout, doctor.stdout

    # (3) `auth` は認可 URL を出す前に断る（偽の app.env・実 Meta には行かない）。
    auth = 導入者として打つ(fresh, "auth", ACCOUNT, "--code", "", "--by", "test-operator")
    assert auth.returncode == 2, auth.stdout + auth.stderr
    assert "/oauth/authorize?" not in auth.stdout, auth.stdout
    assert "ダミー" in auth.stdout, auth.stdout
