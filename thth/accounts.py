"""accounts/<account>.json の読みと形式検査（設計 §4.2）。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

# この thth アプリ repo 自身の場所（thth/ パッケージの 1 つ上）。
# VM では $THTH_ROOT/app がここに一致する。
#
# **v2-2a まで `accounts/` は常にここ基準だった**（台帳は app repo に commit する・
# 設計 v1 §4.2）。設計 v2 §3「台帳を repo の外へ」（masaru 裁定 2026-09-13「出す」）で
# それを変えた——正は `$THTH_ROOT/accounts/`、ここ基準の `accounts/` は
# **互換の最後の手段**（`accounts_dir_info()` を読むこと）。
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 台帳の置き場を明示で差し替える環境変数（いちばん強い）。
ACCOUNTS_DIR_ENV = "THTH_ACCOUNTS_DIR"

# `accounts_dir_info()["source"]` が返す値。
SOURCE_ENV = "env"            # $THTH_ACCOUNTS_DIR
SOURCE_ROOT = "root"          # $THTH_ROOT/accounts（**正**）
SOURCE_APP_REPO = "app_repo"  # app repo の accounts/（**互換・1 版だけ**）

LEGACY_WARNING = ("台帳が repo の中にあります。"
                  "`thth account migrate` で外へ出してください")

REQUIRED_FIELDS = [
    "account", "project", "media", "handle", "repo_dir", "queue_dir",
    "replies_dir", "quiet_hours", "min_interval_hours", "collect_days",
    "hashtags", "stale_days", "env", "token", "ping", "timeout",
    "dry_run_env", "production",
]


class AccountError(Exception):
    """台帳が無い・壊れている・必須項目が足りない。"""


# --------------------------------------------------------------------------
# 雛形のダミー値（監査 2・C10「あるべきもの 9」・masaru 裁定 2026-09-13
# 「手がかかっても最善を」）
# --------------------------------------------------------------------------
#
# `thth account add` は `accounts.example/<media>.json` の雛形を写して 1 本書く。
# 雛形には**そのままでは絶対に通らない値**が入っている（`redirect_uri` は
# `https://example.invalid/`、Mastodon の `instance` は `https://mastodon.example`、
# handle は `demo`）。**その 1 本で `thth auth` を打つ人は、どこにも書かれていない
# 手作業を 2 つ挟む必要があった**——しかも `thth doctor` は何も言わなかった。
# 設計 v2 §3 の狙いは「見つかった瞬間に 1 回で成功する」なので、**道具のほうが
# 名指しで言う**。ここは「どの値が雛形のままか」の 1 か所の知識で、
# `thth doctor`（言う）と `thth auth`（進ませない）の両方が読む。
DUMMY_REDIRECT_URI_HOST = "example.invalid"
DUMMY_INSTANCE_HOST = "mastodon.example"
# 媒体ごとの雛形の handle。**「`demo` を含む」では見ない**——`demo-band` のような
# 実在の綴りを巻き込む。雛形の値と丸ごと一致したときだけダミーと呼ぶ。
DUMMY_HANDLES = {"threads": ("demo",), "mastodon": ("demo",),
                 "bluesky": ("demo.bsky.social",)}

_DUMMY_NEXT = {
    "redirect_uri": ("Meta アプリに登録した URL を "
                     "`thth account add <name> --redirect-uri <url>` か、"
                     "台帳の `redirect_uri` に入れてください（導入文書 §4）。"),
    "handle": ("そのアカウントの本物の handle を台帳の `handle` に入れてください"
               "（Bluesky は `name.bsky.social`・Mastodon は `@` を除いた利用者名・"
               "導入文書 §4）。"),
    "instance": ("自分のインスタンスの URL を "
                 "`thth account add <name> --instance https://<instance>` か、"
                 "台帳の `instance` に入れてください（導入文書 §4）。"),
}


def redirect_uri_is_dummy(value) -> bool:
    """`redirect_uri` が雛形のダミー（`https://example.invalid/`）か。

    **ホスト名で見る**（末尾の `/` の有無・`?` 付きの綴りで擦り抜けないように）。
    """
    return bool(value) and DUMMY_REDIRECT_URI_HOST in str(value)


def dummy_fields(cfg: dict) -> list[dict]:
    """台帳のうち**雛形のダミーのまま**の欄を並べる（`thth doctor` が名指しする）。

    返すのは `{"field", "value", "next"}` の並び。空なら「ダミーは残っていない」。
    **判定するだけで、何も直さない**（値を直すのは人の手・設計 §4.2）。
    """
    out: list[dict] = []
    media = cfg.get("media")

    def 足す(field: str, value) -> None:
        out.append({"field": field, "value": str(value), "next": _DUMMY_NEXT[field]})

    if redirect_uri_is_dummy(cfg.get("redirect_uri")):
        足す("redirect_uri", cfg.get("redirect_uri"))
    handle = (cfg.get("handle") or "").strip().lstrip("@")
    if handle and handle in DUMMY_HANDLES.get(media, ()):
        足す("handle", cfg.get("handle"))
    instance = cfg.get("instance")
    if instance and DUMMY_INSTANCE_HOST in str(instance):
        足す("instance", instance)
    return out


def thth_root() -> str:
    """state・logs の置き場の基準。設計 §3.1 の $THTH_ROOT。

    優先順位:
      1. 環境変数 `THTH_ROOT`（systemd unit が渡す）
      2. **app repo の basename が "app" なら、その親**（VM の `$THTH_ROOT/app` 配置）
      3. **repo から走っているとき**（`APP_DIR` に `.git` か `bin/thth` がある）は
         app repo 自身（Mac 手元・pytest。.gitignore の state/・logs/ と対応）
      4. それ以外＝`pip install` で入った道具は `$XDG_DATA_HOME/thth`、無ければ `~/.thth`

    2 を足した理由（2026-09-09・最初の本番投稿で踏んだ）: VM で masaru が手で
    `thth send` を打つと `THTH_ROOT` が無いので state が `/srv/thth/app/state/` に、
    timer から走ると unit が渡すので `/srv/thth/state/` に出来ていた。**置き場が
    2 つに割れると、ロックも inflight も別物になる**。§3.7 で「ロックを core の
    入口に置く」と直したのに、パスが割れていては同じ穴が開く（timer が走っている
    最中の手打ちがロックを踏まない）。**同じ機械の上では、呼び方が違っても同じ
    場所を指す**ことをコードで保証する。

    4 を足した理由（監査 1・P1-2・2026-09-13）: `pip install thth` で入れた人が
    `THTH_ROOT` を設定せずに打つと、`APP_DIR` は **`site-packages/`** になる。
    台帳も state も share の outbox も塩も仮名も、そこに落ちていた——
    **`pip install --upgrade thth` が黙って全部消す**（wheel の入れ替えで
    site-packages の中身が作り直される）。しかも消えたことは誰にも言われない。
    **道具の入れ替えで利用者のデータが消える置き場は、置き場ではない。**

    「repo から走っているか」は `APP_DIR` に `.git`（worktree では**ファイル**）か
    `bin/thth` があるかで見る。site-packages にはどちらも無い。
    """
    env = os.environ.get("THTH_ROOT")
    if env:
        return env
    if os.path.basename(APP_DIR) == "app":
        return os.path.dirname(APP_DIR)
    if running_from_repo():
        return APP_DIR
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return os.path.join(xdg, "thth")
    return os.path.join(os.path.expanduser("~"), ".thth")


def running_from_repo() -> bool:
    """`APP_DIR` が clone（か worktree）か。**`site-packages/` では False。**

    `.git` は clone ならディレクトリ、worktree ならファイルなので `exists` で見る。
    `bin/thth` を併せて見るのは、`.git` を持たない export（tarball を展開しただけの
    置き方）でも従来どおり動かすため。
    """
    return (os.path.exists(os.path.join(APP_DIR, ".git"))
            or os.path.exists(os.path.join(APP_DIR, "bin", "thth")))


def app_dir() -> str:
    """`accounts/` を探す基準。`THTH_APP_DIR` 環境変数で上書きできる（テストが
    subprocess 越しに隔離した accounts/ ディレクトリを指すのに使う。単体テストは
    `accounts.APP_DIR` を直接 monkeypatch してもよい。両方に対応するため、ここは
    毎回 `APP_DIR` を読み直す＝モジュール属性の書き換えを拾える）。"""
    return os.environ.get("THTH_APP_DIR") or APP_DIR


def env_accounts_dir() -> str | None:
    """`$THTH_ACCOUNTS_DIR`。**その場で絶対パスにする**（監査 1・P3）。

    相対パスのまま持ち回ると、**cwd が変わった瞬間に別の場所を指す**——
    timer（`WorkingDirectory` 次第）と手打ちで置き場が割れるし、`thth board` が
    出す 1 行は読んだ人がそのまま `ls` できる綴りでなくなる。`~` も展開する。
    """
    value = os.environ.get(ACCOUNTS_DIR_ENV)
    if not value:
        return None
    return os.path.abspath(os.path.expanduser(value))


def root_accounts_dir() -> str:
    """**正**の置き場（設計 v2 §3「台帳を repo の外へ」）。無くてもこの綴り。"""
    return os.path.join(thth_root(), "accounts")


def legacy_accounts_dir() -> str:
    """app repo の中の `accounts/`（設計 v1 §4.2 の置き場・互換）。"""
    return os.path.join(app_dir(), "accounts")


def accounts_dir_info() -> dict:
    """台帳の置き場を決める（設計 v2 §3・裁定 §7-1「出す」）。

    順番:
      (a) 環境変数 `THTH_ACCOUNTS_DIR`（明示。テストと特殊な配置のため）
      (b) `$THTH_ROOT/accounts/` —— **正**。あればこれ
      (c) それが無ければ **app repo の `accounts/`** —— **互換。1 版だけ**

    **(c) はこの版でも残す。** repo の `accounts/`（6 本）は **2026-09-14 に
    `git rm` した**——VM の `thth account migrate` が済み、board の互換警告が
    消え、次の `thth run` が通ってから（設計 v2 §8 の順番）。それでも (c) を
    残すのは、**(b) を失った機械を止めないための安全網**。VM の `/srv/thth/app`
    は `merge --ff-only` の clone なので、この削除が release に届けば作業ツリー
    からも 6 本が消えるが、(b) を先に見るので稼働には影響しない。
    **(c) を消すのは次の版。**

    **(b) は「ディレクトリがあるか」だけで見る**（中に台帳があるかは見ない）。
    空でもあれば正——`thth account add` を 1 本打った時点で外が正になり、repo の
    台帳は二度と読まれない。「外に足したのに repo の分も混ざって並ぶ」を作らない
    ため（監査 1・P3-12 が見つけたのがまさにそれ）。

    返り値は `{"path": …, "source": …}`。`source` は上の 3 つの定数のどれか。
    どちらも無いときは (b) の綴りを `root` として返す（＝台帳 0 本）。
    """
    env = env_accounts_dir()
    if env:
        return {"path": env, "source": SOURCE_ENV}
    outside = root_accounts_dir()
    if os.path.isdir(outside):
        return {"path": outside, "source": SOURCE_ROOT}
    legacy = legacy_accounts_dir()
    if os.path.isdir(legacy):
        return {"path": legacy, "source": SOURCE_APP_REPO}
    return {"path": outside, "source": SOURCE_ROOT}


# 互換の警告は**1 プロセスに 1 行**。`accounts_dir()` は 1 回の実行で何十回も
# 呼ばれるので、毎回出すと画面が警告で埋まって読まれなくなる。
_legacy_warned = False


def reset_legacy_warning() -> None:
    """テスト用。1 プロセスの中で警告をもう一度出させる。"""
    global _legacy_warned
    _legacy_warned = False


def _warn_legacy_once(path: str) -> None:
    global _legacy_warned
    if _legacy_warned:
        return
    _legacy_warned = True
    # **stderr へ出す。** `--json` は stdout に JSON 1 個だけ、という出力契約
    # （設計 §6）を警告で壊さないため。
    print(f"⚠ {LEGACY_WARNING}（{path}）", file=sys.stderr)


def accounts_dir() -> str:
    info = accounts_dir_info()
    if info["source"] == SOURCE_APP_REPO:
        _warn_legacy_once(info["path"])
    return info["path"]


def _expand(value):
    if not isinstance(value, str):
        return value
    value = value.replace("$THTH_ROOT", thth_root())
    if value.startswith("~"):
        value = os.path.expanduser(value)
    return value


def dir_is_unreadable(d: str) -> bool:
    """**ディレクトリはあるのに読めない**（権限）か。

    `os.path.exists()` は権限が無いときも False を返す。それをそのまま
    「無い」と言うと、`thth doctor` が **嘘をつく**——台帳はそこに在るのに
    「台帳が無い」と言われた人は、作り直しに行ってしまう（監査 1・P2-2）。
    """
    return os.path.isdir(d) and not os.access(d, os.R_OK | os.X_OK)


# **アカウント名はそのままファイル名になる**（`<accounts_dir>/<name>.json`）。
# 区切りや `..` を混ぜると置き場の外を指せる。`thth account add` の側は前から
# 断っていたが（監査 1・P2-1）、**読む側に検査が無かった**（セキュリティ監査
# 2026-09-14・P2-2）——`thth queue ../../etc/passwd` は `json.JSONDecodeError` を
# 出し、存在しない綴りは `FileNotFoundError` になるので、**返る文言の違いで
# 「そこに何かあるか」を探れた**。書く側と読む側で 1 か所の知識にする。
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def name_is_safe(name) -> bool:
    """置き場の中の 1 ファイルに必ず収まる名前か。`.`・`..` は名前ではない。"""
    return (isinstance(name, str) and bool(name)
            and bool(NAME_RE.match(name)) and name not in (".", ".."))


def load_account(name: str) -> dict:
    """`accounts/<name>.json` を読んで検査する。$THTH_ROOT・~ を展開したコピーを返す。

    **「無い」と「読めない」を言い分ける**（監査 1・P2-2）。

    **名前そのものを先に検査する**（セキュリティ監査 2026-09-14・P2-2）。
    置き場の外を指せる綴りは、読む前に同じ 1 つの文言で断る——**存在の探りを
    させない**（在る／無い／壊れているで文言が変わらない）。
    """
    if not name_is_safe(name):
        raise AccountError(
            f"アカウント名に使えない字が入っています: {name!r}"
            f"（使えるのは英数字と `_`・`.`・`-` だけ。名前はそのまま"
            f"ファイル名になるので、`/` や `..` は置き場の外を指せます）")
    d = accounts_dir()
    path = os.path.join(d, f"{name}.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        if dir_is_unreadable(d):
            raise AccountError(
                f"台帳の置き場が読めません（権限）: {d}"
                f"——**無いのではありません。** 権限を直してください") from e
        raise AccountError(f"台帳が無い: {name}（{path}）") from e
    except PermissionError as e:
        raise AccountError(
            f"台帳が読めません（権限）: {path}"
            f"——**無いのではありません。** 権限を直してください") from e
    except OSError as e:
        raise AccountError(f"台帳が読めません: {path}（{e.strerror}）") from e
    except json.JSONDecodeError as e:
        raise AccountError(f"台帳が壊れている: {name}（{e}）") from e
    missing = [k for k in REQUIRED_FIELDS if k not in data]
    if missing:
        raise AccountError(f"{name}: 台帳に項目が足りません: {missing}")
    out = dict(data)
    for key in ("repo_dir", "env", "token"):
        out[key] = _expand(out[key])
    return out


def state_dir_for(account_name: str) -> str:
    return os.path.join(thth_root(), "state", account_name)


def logs_dir_for(account_name: str) -> str:
    return os.path.join(thth_root(), "logs", account_name)


# **「はじめから repo を持たない」印**（`$THTH_ROOT/repos/_none`）。送信専用の
# アカウント（`thth send` だけで使う）の台帳が指す捨て場で、**作られることは無い**。
REPO_NONE_BASENAME = "_none"

# `repo_state()` の 3 値。
REPO_NONE = "none"        # はじめから repo を持たない（空・`_none`・null）
REPO_OK = "ok"            # 実在する git repo
REPO_BROKEN = "broken"    # 指定があるのに使えない（無い・`.git` が無い・読めない）


def resolved_repo_dir(account_cfg: dict) -> str:
    """台帳の `repo_dir` を**絶対パス**にして返す（無指定なら空文字列）。

    相対で書かれていたら `$THTH_ROOT` 基準で畳む（`_expand()` が `$THTH_ROOT`・
    `~` を展開するのと同じ基準に合わせる）。**cwd 基準にしない**——timer から
    走るときの cwd は `/` で、手で打つときの cwd は人それぞれなので、
    **同じ台帳が実行のたびに別の場所を指す。**
    """
    raw = (account_cfg or {}).get("repo_dir")
    raw = raw.strip() if isinstance(raw, str) else ""
    if not raw:
        return ""
    if not os.path.isabs(raw):
        raw = os.path.join(thth_root(), raw)
    return os.path.normpath(raw)


def repo_state(account_cfg: dict) -> str:
    """置き場の判定（セキュリティ監査 2 回目・P2-1）。`REPO_NONE`／`OK`／`BROKEN`。

    **「repo を持たない」と「repo が使えない」は別物**。v2.0.1 で
    `is_repo_backed()` を「実在する git repo か」の 1 つの真偽値にしたとき、
    **この 2 つが同じ False に潰れた**——`repo_dir` が一時的に見えない
    （mount が落ちた・clone を移した・`.git` を退避した・権限が変わった）だけで
    採取が**黙って `state/` に転び**、書いたものは版管理にも `thth board` の
    「未送信」にも出なくなる。v2.0.0 はここで loud に断っていた（断られれば
    人は直しに行ける。黙って別の場所に書かれると、気づくのは数日後）。

    - `REPO_NONE`: `repo_dir` が空・`null`・`repos/_none`。**はじめから持たない。**
      置き場は `$THTH_ROOT/state/<account>/data/sns/…`（`data_dirs()`）。
    - `REPO_OK`: 実在して `.git` がある。従来どおり repo の中。
    - `REPO_BROKEN`: 指定があるのに使えない。**採取しない・取り直さない**
      （`collect.run_collect()`・`collect.refresh_replies()` が断る）。
    """
    raw = (account_cfg or {}).get("repo_dir")
    raw = raw.strip() if isinstance(raw, str) else ""
    if not raw:
        return REPO_NONE
    if os.path.basename(raw.rstrip("/\\")) == REPO_NONE_BASENAME:
        return REPO_NONE
    path = resolved_repo_dir(account_cfg)
    if not os.path.isdir(path):
        return REPO_BROKEN
    if not os.path.exists(os.path.join(path, ".git")):
        return REPO_BROKEN
    return REPO_OK


def repo_problem(account_cfg: dict) -> str | None:
    """`REPO_BROKEN` の理由を 1 行で（そうでなければ None）。

    **何がどう駄目かを名指しする**——「repo がありません」だけでは、mount が
    落ちているのか台帳の綴りが違うのかが判らない。
    """
    if repo_state(account_cfg) != REPO_BROKEN:
        return None
    path = resolved_repo_dir(account_cfg)
    if dir_is_unreadable(path):
        理由 = "読めません（権限）——**無いのではありません**"
    elif not os.path.isdir(path):
        理由 = "そこにありません（mount・綴り・移動を確かめてください）"
    else:
        理由 = "`.git` がありません（git repo ではありません）"
    return (f"台帳の repo_dir が使えません: {path}（{理由}）。"
            f"**repo を持たないアカウント**（`thth send` だけで使う）なら、"
            f"台帳の repo_dir を `$THTH_ROOT/repos/{REPO_NONE_BASENAME}` に"
            f"してください——そう名乗れば採取は state に置きます")


def is_repo_backed(account_cfg: dict) -> bool:
    """`repo_dir` が**実在する git repo** か（設計 v2.0.1 §1）。

    **「ディレクトリがある」では足りない。** `.git` だけ失われた壊れた clone に
    採取を書き足すと、版管理に載らないまま `thth board` の「未送信」にも出ない
    （`writeback.sync_repo()` が同じ理由でここを同期失敗として扱う）。
    **git が無いなら repo ではない。**

    **これで「state に転ばせてよいか」を決めてはいけない**（監査 2 回目・P2-1）。
    転ばせてよいのは `repo_state() == REPO_NONE` のときだけで、`REPO_BROKEN` は
    断る側。ここは「いま git を呼べるか」を聞く口として残す。
    """
    return repo_state(account_cfg) == REPO_OK


def data_dirs(account_cfg: dict, account_name: str) -> dict:
    """採集が書き、読み手が読む**置き場を 1 か所で決める**（設計 v2.0.1 §1）。

    規則は 2 行。

    1. `repo_dir` が実在する git repo なら、従来どおり `repo_dir/data/sns/…`。
       `replies_dir` の指定もそのまま効く（**既存の経路は 1 バイトも変えない**）。
    2. **`repo_dir` を持たないとき**（空・`null`・`repos/_none`）だけ
       **`$THTH_ROOT/state/<account>/data/sns/…`**。

    **「指定があるのに使えない」は 2 ではない**（監査 2 回目・P2-1）。そこは
    従来どおり repo の中を指したまま——採取の側（`collect.run_collect()`）が
    loud に断るので、書かれることはない。**黙って別の場所に転ばせない。**

    2 が要る理由（運用 2026-09-14）。`thth send`（同席の様態）で出した投稿は
    queue を通らないので原稿 repo が無く、**書く先が無いという理由だけで実測も
    返信も 1 件も採れていなかった。** 出したものを測れないなら、出す意味が薄い。

    state 側で `replies_dir` を効かせないのは、**合わせる相手の repo が無い**から
    （あの指定は「利用者 repo のどこに置くか」の話）。置き場は道具が決める。

    戻り値の鍵は `insights_posts`・`insights_account`・`replies`・`inbox`・
    `engagements`（絡みの台帳・発注 T0-1）。
    **読み手も書き手もここを通る**——直書きが 1 か所でも残ると、そこだけ別の
    場所を見る（`thth/measured.py`・`thth/replies.py`・`thth/account_report.py`）。
    """
    account_cfg = account_cfg or {}
    if repo_state(account_cfg) == REPO_NONE:
        base = state_dir_for(account_name)
        replies = os.path.join(base, "data", "sns", "replies")
    else:
        base = resolved_repo_dir(account_cfg)
        replies = os.path.join(
            base, account_cfg.get("replies_dir") or "data/sns/replies")
    return {
        "insights_posts": os.path.join(base, "data", "sns", "insights", "posts"),
        "insights_account": os.path.join(base, "data", "sns", "insights", "account"),
        "replies": replies,
        "inbox": os.path.join(base, "data", "sns", "inbox"),
        # 絡みの台帳（設計「自分の泉」§4・発注 T0-1）。`insights_posts` 等と
        # 同じ流儀——repo が無い account は state 側、あれば repo の中。
        "engagements": os.path.join(base, "data", "sns", "engagements"),
    }


_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_.-]")


def repo_lock_path_for(repo_dir: str) -> str:
    """`repo_dir` を正規化した単位のロックパス（外部レビュー §2・受け入れ 7・8）。

    設計は「1 repo に clone は 1 つ、アカウントは複数ぶら下がってよい」なので、
    別アカウントの投稿でも同じ index・作業ツリー・rebase 状態を同時に触りうる。
    ロックの単位を account ではなく **repo_dir** にする。

    `os.path.realpath()` で正規化するので、同じ clone を指す別表記（相対パス・
    末尾スラッシュの有無・symlink 越し）でも同じロックファイルになる。realpath は
    パスが実在しなくても正規化できるので、repo を持たないアカウント
    （`masaru-threads` の `repos/_none` 等）でも壊れない——そのディレクトリ用の
    ロックが 1 本できるだけで、他とは衝突しない。
    """
    real = os.path.realpath(repo_dir)
    digest = hashlib.sha256(real.encode("utf-8")).hexdigest()[:12]
    base = os.path.basename(real.rstrip(os.sep)) or "root"
    safe_base = _SAFE_CHARS_RE.sub("_", base)
    return os.path.join(thth_root(), "state", "_repos", f"{safe_base}-{digest}.lock")


def account_lock_path_for(account_name: str) -> str:
    """1 アカウント分の実行ロック（`core._account_locks()` が握る）。

    **綴りを 1 か所にする**——`thth board` が「いま run が走っているか」を見る
    ために同じパスを組み立てる（別々に書くと、片方を直したときに board が
    黙って別のファイルを見る）。
    """
    return os.path.join(state_dir_for(account_name), "lock")


def app_lock_path() -> str:
    """自己更新のロック（`selfupdate._pull_locked()` が握る・アカウント別ではない）。"""
    return os.path.join(thth_root(), "state", "_app.lock")


def load_token(account_cfg: dict) -> dict | None:
    """`<account>.token` を読む。無ければ None（T1 はここに触れない・秘密を扱わない）。"""
    path = account_cfg.get("token")
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def token_exists(account_cfg: dict) -> bool:
    """`thth run` の事前確認（設計 §3.2・T3a 訂正 2026-09-09）。

    以前の名前は `env_and_token_exist()` で、env ファイルと token の**両方**が
    無ければ False にしていた。**設計が変わって env ファイルは要らなくなった**:
    アプリ ID・シークレットは Meta 管理画面の「ユーザートークン生成ツール」で
    tester ごとに直接発行する運用にしたので（設計 §4.2）、`~/.config/thth/<account>.env`
    を置かない。実際 VM に置いてあるのは `<account>.token` だけ（統括が 2026-09-09
    に確認・L1）。env を必須のままにすると `thth run` は毎回ここで exit 2 になり、
    timer を立てても何も投げない。

    **token だけを必須にする。env は任意**（あれば `load_env()` で読める。中身は
    `HEALTHCHECK_URL` 等・秘密ではない付随情報）。
    """
    token_path = account_cfg.get("token")
    return bool(token_path and os.path.exists(token_path))


def load_env(account_cfg: dict) -> dict:
    """`accounts/<account>.json` の `env`（任意・`HEALTHCHECK_URL` 等）を読む。

    無ければ空の dict（`thth run` を止める理由にはしない・`token_exists()` 参照）。
    """
    path = account_cfg.get("env")
    if not path or not os.path.exists(path):
        return {}
    data: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            data[key] = value
    return data


def list_account_names() -> list[str]:
    """置き場にある台帳の名前。**無い**なら空、**読めない**なら loud に落ちる。

    前は `os.listdir()` の `PermissionError` がそのまま traceback になっていた
    （`thth board`・`thth account`・監査 1・P2-2）。「台帳 0 本」と黙って返すのは
    もっと悪い——**本番 6 本が消えたように見える**。`AccountError` に言い換えて、
    呼んだ側が置き場を 1 行出してから非ゼロで終われるようにする。
    """
    d = accounts_dir()
    try:
        names = os.listdir(d)
    except (FileNotFoundError, NotADirectoryError):
        return []
    except PermissionError as e:
        raise AccountError(
            f"台帳の置き場が読めません（権限）: {d}"
            f"——**0 本なのではありません。** 権限を直してください") from e
    except OSError as e:
        raise AccountError(f"台帳の置き場が読めません: {d}（{e.strerror}）") from e
    return sorted(name[:-5] for name in names if name.endswith(".json"))
