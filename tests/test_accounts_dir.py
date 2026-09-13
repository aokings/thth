"""台帳の置き場（設計 v2 §3「台帳を repo の外へ」・masaru 裁定 2026-09-13「出す」）。

見るのは 3 つ:

  - **解決の順番** (a) `$THTH_ACCOUNTS_DIR` → (b) `$THTH_ROOT/accounts` → (c) 互換の
    app repo `accounts/`。とくに **(c) を落とすと VM の稼働が止まる**（§8）ので、
    ここが「repo の中を読める」ことを固定する。
  - **`thth account migrate`** —— repo の中の台帳を外へ **copy** する。移動しない・
    repo を触らない・上書きしない・冪等。
  - **`thth account add`** —— 雛形から 1 本書く。書く先は必ず外。`production` は
    必ず false。既存を上書きしない。

**この file は `isolated_account_factory` を使わない。** あの fixture は
`$THTH_ACCOUNTS_DIR` を明示して解決の順番に依存させないようにしている（conftest の
但し書き）。ここは**その順番そのもの**が主題なので、環境変数を自分で組む。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from thth import account_cli as account_cli_mod
from thth import accounts as accounts_mod

from .conftest import run_thth

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def 置き場(tmp_path, monkeypatch):
    """`$THTH_ROOT` と「app repo」を tmp に向けた土台。**台帳はまだ 1 本も無い。**

    `THTH_ACCOUNTS_DIR` は明示的に消す（打った人の shell や別の fixture の
    置き土産で、確かめたい経路を素通りしないため）。
    """
    root = tmp_path / "root"
    app = tmp_path / "app"
    (root).mkdir()
    (app).mkdir()
    monkeypatch.delenv(accounts_mod.ACCOUNTS_DIR_ENV, raising=False)
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_APP_DIR", str(app))
    monkeypatch.setattr(accounts_mod, "APP_DIR", str(app))
    accounts_mod.reset_legacy_warning()
    return {"root": str(root), "app": str(app),
            "外": str(root / "accounts"), "repo の中": str(app / "accounts")}


def 台帳を置く(d: str, name: str, **overrides) -> str:
    """検査を通る最小の台帳を 1 本書く。"""
    data = {
        "account": name, "project": "demo", "media": "threads", "handle": "demo",
        "repo_dir": "$THTH_ROOT/repos/demo", "queue_dir": "docs/sns/queue",
        "replies_dir": "data/sns/replies", "quiet_hours": None,
        "min_interval_hours": 0, "collect_days": 14, "hashtags": False,
        "stale_days": 7, "env": "~/.config/thth/x.env",
        "token": "~/.config/thth/x.token", "ping": "wrapper", "timeout": 300,
        "dry_run_env": "THTH_DRY_RUN", "production": False,
    }
    data.update(overrides)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# --------------------------------------------------------------------------
# 解決の順番
# --------------------------------------------------------------------------

def test_順番a_環境変数がいちばん強い(置き場, tmp_path, monkeypatch):
    """(a) `$THTH_ACCOUNTS_DIR` があれば、外にも repo の中にも台帳があっても、そこ。"""
    明示 = tmp_path / "明示"
    台帳を置く(str(明示), "env-threads")
    台帳を置く(置き場["外"], "root-threads")
    台帳を置く(置き場["repo の中"], "repo-threads")
    monkeypatch.setenv(accounts_mod.ACCOUNTS_DIR_ENV, str(明示))

    info = accounts_mod.accounts_dir_info()
    assert info["source"] == accounts_mod.SOURCE_ENV
    assert info["path"] == str(明示)
    assert accounts_mod.list_account_names() == ["env-threads"]


def test_順番b_外があれば外が正(置き場):
    """(b) `$THTH_ROOT/accounts` があれば、repo の中は**もう読まない**。"""
    台帳を置く(置き場["外"], "root-threads")
    台帳を置く(置き場["repo の中"], "repo-threads")

    info = accounts_mod.accounts_dir_info()
    assert info["source"] == accounts_mod.SOURCE_ROOT
    assert info["path"] == 置き場["外"]
    # **repo の中の台帳が混ざらない**（監査 1・P3-12 が見つけた形）。
    assert accounts_mod.list_account_names() == ["root-threads"]


def test_順番b_外は空でも正(置き場):
    """**「ディレクトリがあるか」だけで見る。** 中身が 0 本でも外が正。

    `thth account add` を 1 本打った時点で外が正になり、repo の台帳は二度と
    読まれない——「外に足したのに repo の分も混ざって並ぶ」を作らないため。
    ここを「中に .json があるか」で見ると、**外の 1 本を消した瞬間に repo の
    6 本が黙って復活する。**
    """
    os.makedirs(置き場["外"], exist_ok=True)   # 空のまま
    台帳を置く(置き場["repo の中"], "repo-threads")

    info = accounts_mod.accounts_dir_info()
    assert info["source"] == accounts_mod.SOURCE_ROOT
    assert accounts_mod.list_account_names() == []


def test_順番c_外が無ければrepoの中を読む_VMが止まらない(置き場, capsys):
    """**(c) 互換。これを外すと VM の稼働が止まる**（設計 v2 §8 の止まる条件）。

    VM の `/srv/thth/app` は clone で、台帳はそこに commit されている。この経路が
    無いと、次の timer で本番のアカウントが全部「台帳が無い」になる。
    """
    台帳を置く(置き場["repo の中"], "repo-threads")
    assert not os.path.isdir(置き場["外"]), "この試験の前提が崩れている（外がある）"

    info = accounts_mod.accounts_dir_info()
    assert info["source"] == accounts_mod.SOURCE_APP_REPO
    assert info["path"] == 置き場["repo の中"]
    assert accounts_mod.list_account_names() == ["repo-threads"]

    # 台帳が実際に読める（＝VM が動き続ける）。
    cfg = accounts_mod.load_account("repo-threads")
    assert cfg["account"] == "repo-threads"


def test_互換のときだけ警告が出る_stderrへ1回(置き場, capsys):
    """警告は **stderr へ 1 プロセス 1 行**。

    stderr なのは `--json` の出力契約（stdout に JSON 1 個だけ・設計 §6）を
    壊さないため。1 行なのは `accounts_dir()` が 1 回の実行で何十回も呼ばれる
    ため（毎回出すと画面が埋まって読まれなくなる）。
    """
    台帳を置く(置き場["repo の中"], "repo-threads")
    for _ in range(5):
        accounts_mod.accounts_dir()
    err = capsys.readouterr().err
    assert err.count(accounts_mod.LEGACY_WARNING) == 1, err
    assert "thth account migrate" in err


def test_外が正のときは警告を出さない(置き場, capsys):
    台帳を置く(置き場["外"], "root-threads")
    accounts_mod.accounts_dir()
    err = capsys.readouterr().err
    assert accounts_mod.LEGACY_WARNING not in err, err


def test_どちらも無ければ外の綴りを返す_台帳0本(置き場):
    """外も repo の中も無いとき。**repo の中を作らない**——綴りは外のまま。"""
    info = accounts_mod.accounts_dir_info()
    assert info["source"] == accounts_mod.SOURCE_ROOT
    assert info["path"] == 置き場["外"]
    assert accounts_mod.list_account_names() == []


# --------------------------------------------------------------------------
# where_line（doctor・board が出す 1 行）
# --------------------------------------------------------------------------

def test_where_lineは互換のとき出し方を言う(置き場):
    台帳を置く(置き場["repo の中"], "repo-threads")
    line = account_cli_mod.where_line()
    assert 置き場["repo の中"] in line
    assert "互換" in line
    # **次の一手を同じ行に。** 「repo の中です」だけでは、読んだ人が動けない。
    assert "thth account migrate" in line
    assert 置き場["外"] in line


def test_where_lineは外のとき外を言う(置き場):
    os.makedirs(置き場["外"], exist_ok=True)
    line = account_cli_mod.where_line()
    assert 置き場["外"] in line
    assert "互換" not in line


def test_where_lineは環境変数のときそう言う(置き場, tmp_path, monkeypatch):
    明示 = tmp_path / "明示"
    明示.mkdir()
    monkeypatch.setenv(accounts_mod.ACCOUNTS_DIR_ENV, str(明示))
    line = account_cli_mod.where_line()
    assert str(明示) in line
    assert accounts_mod.ACCOUNTS_DIR_ENV in line


def test_書き込む先は互換でも必ず外(置き場):
    """**読むほうは 1 版だけ互換を残すが、書くほうは常に外。**

    そうしないと「外へ出す」作業のさなかに repo の中が増える。
    """
    台帳を置く(置き場["repo の中"], "repo-threads")
    assert accounts_mod.accounts_dir_info()["source"] == accounts_mod.SOURCE_APP_REPO
    assert account_cli_mod.target_accounts_dir() == 置き場["外"]


# --------------------------------------------------------------------------
# thth account migrate
# --------------------------------------------------------------------------

def test_migrateはrepoの中を外へ写す_repoは触らない(置き場):
    台帳を置く(置き場["repo の中"], "a-threads")
    台帳を置く(置き場["repo の中"], "b-threads")
    元の中身 = sorted(os.listdir(置き場["repo の中"]))

    r = run_thth(["account", "migrate"])
    assert r.returncode == 0, r.stdout + r.stderr

    assert sorted(os.listdir(置き場["外"])) == ["a-threads.json", "b-threads.json"]
    # **移動ではなく copy。** repo の作業ツリーを道具が動かすと、VM の
    # `merge --ff-only` が次の更新で止まる（設計 v2 §7-1）。
    assert sorted(os.listdir(置き場["repo の中"])) == 元の中身
    assert "repo の `accounts/` はそのまま残っています" in r.stdout


def test_migrateは冪等_2回目は写したと言わない(置き場):
    台帳を置く(置き場["repo の中"], "a-threads")
    一回目 = run_thth(["account", "migrate"])
    assert 一回目.returncode == 0
    一回目の更新時刻 = os.path.getmtime(os.path.join(置き場["外"], "a-threads.json"))

    二回目 = run_thth(["account", "migrate"])
    assert 二回目.returncode == 0, 二回目.stdout + 二回目.stderr
    assert "写すものはありませんでした" in 二回目.stdout
    assert "写した:" not in 二回目.stdout
    assert os.path.getmtime(os.path.join(置き場["外"], "a-threads.json")) == 一回目の更新時刻


def test_migrateは中身が違うものを上書きしない(置き場):
    """**外が正。** 運用が外で直したものを、消し忘れた repo の台帳が黙って
    巻き戻してはいけない。名指しで断り、rc は 0 にしない（loud reject・作法 5）。
    """
    台帳を置く(置き場["repo の中"], "a-threads", handle="repoのほう")
    台帳を置く(置き場["外"], "a-threads", handle="外のほう")

    r = run_thth(["account", "migrate"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "a-threads.json" in r.stdout
    assert "中身が違います" in r.stdout
    # **外は手つかず。**
    with open(os.path.join(置き場["外"], "a-threads.json"), encoding="utf-8") as f:
        assert json.load(f)["handle"] == "外のほう"


def test_migrateのdry_runは何も書かない(置き場):
    台帳を置く(置き場["repo の中"], "a-threads")

    r = run_thth(["account", "migrate", "--dry-run"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "写す（予定）: a-threads.json" in r.stdout
    # **ディレクトリすら作らない。** 作ってしまうと (b) が成立して、次の
    # `thth board` が「外（空）」を正と見なし、**台帳 0 本になる。**
    assert not os.path.exists(置き場["外"]), "--dry-run が外のディレクトリを作った"


def test_migrateはrepoに台帳が無ければ何もしない(置き場):
    r = run_thth(["account", "migrate"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "することはありません" in r.stdout


def test_migrateは名前を取らない(置き場):
    r = run_thth(["account", "migrate", "demo-threads"])
    assert r.returncode == 2, r.stdout + r.stderr
    assert "名前を取りません" in r.stderr


# --------------------------------------------------------------------------
# thth account add
# --------------------------------------------------------------------------

def 媒体ごとの必須(media: str) -> list:
    """媒体ごとに `add` が必須にしている欄（監査 2・C10・2026-09-13）。

    Threads は `--handle` の既定（`--project` の値）で当たるが、**Bluesky は
    `name.bsky.social`、Mastodon は利用者名＋instance** なので既定が当たらない。
    ここは「必須が効いていること」を見る場ではない（それは
    `test_addはblueskyのhandleを必須にする` 以下）ので、揃った呼び方を 1 か所で作る。
    """
    if media == "bluesky":
        return ["--handle", "demo2.bsky.social"]
    if media == "mastodon":
        return ["--handle", "demo2", "--instance", "https://demo.social"]
    return []


@pytest.mark.parametrize("media", ["threads", "bluesky", "mastodon"])
def test_addは雛形から外へ1本書く(置き場, media):
    r = run_thth(["account", "add", f"demo-{media}", "--media", media,
                  "--project", "demo", *媒体ごとの必須(media)])
    assert r.returncode == 0, r.stdout + r.stderr

    path = os.path.join(置き場["外"], f"demo-{media}.json")
    assert os.path.exists(path), r.stdout
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["account"] == f"demo-{media}"
    assert data["media"] == media
    assert data["project"] == "demo"
    # **書いたものが `load_account()` の検査を通る**（雛形が項目を欠いていない）。
    for key in accounts_mod.REQUIRED_FIELDS:
        assert key in data, f"{key} が雛形に無い（{media}）"


@pytest.mark.parametrize("media", ["threads", "bluesky", "mastodon"])
def test_addが作るものは必ずproduction_false(置き場, media):
    """**道具が作ったものが、いきなり本物を投げる形で生まれてはいけない**
    （設計 §4.2「`production: true` を自分で書かない限り dry-run」）。"""
    r = run_thth(["account", "add", f"demo-{media}", "--media", media,
                  "--project", "demo", *媒体ごとの必須(media)])
    assert r.returncode == 0, r.stdout + r.stderr
    with open(os.path.join(置き場["外"], f"demo-{media}.json"), encoding="utf-8") as f:
        data = json.load(f)
    assert data["production"] is False
    assert data["scheduled"] is False


def test_addが作った台帳は_productionを付けて打っても_rehearsalのまま(置き場):
    """`production: false` が**言葉だけでない**こと——実際に `mode: rehearsal`。

    **`--production` を付けて打つ。** 付けないと `core.throw_once()` の
    `production_flag` のほうで止まるので、**台帳が `true` でも `rehearsal` に
    見えてしまう**（2 つある関門のうち、台帳ではないほうを見てしまう）。
    ここで確かめたいのは `thth account add` が書いた**台帳の側**なので、
    もう一方の関門は開けた状態で見る。
    """
    assert run_thth(["account", "add", "demo-threads", "--media", "threads",
                     "--project", "demo"]).returncode == 0
    r = run_thth(["throw", "demo-threads", "--now", "--production"])
    assert r.stdout.splitlines()[0] == "mode: rehearsal", r.stdout + r.stderr


def test_addは既にあるものを上書きしない(置き場):
    台帳を置く(置き場["外"], "demo-threads", handle="先にあったほう")
    r = run_thth(["account", "add", "demo-threads", "--media", "threads",
                  "--project", "demo"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "上書きしません" in r.stderr
    with open(os.path.join(置き場["外"], "demo-threads.json"), encoding="utf-8") as f:
        assert json.load(f)["handle"] == "先にあったほう"


def test_addは互換のときは書く前にloudに断る_順番を間違えさせない(置き場):
    """**`migrate` より先に `add` を打たせない**（監査 1・P1-3）。

    読みが repo の中に落ちている機械（＝VM）で `add` を 1 本打つと、書く先の
    `$THTH_ROOT/accounts/` が**その瞬間に出来る**。解決順は「ディレクトリが
    あるか」だけで (b) を正とするので、**次の実行から repo の台帳は一切
    読まれない**（`thth run <account>` が「台帳が無い」の rc=2）。前はこれを
    何も言わずにやっていた。
    """
    台帳を置く(置き場["repo の中"], "repo-threads")
    台帳を置く(置き場["repo の中"], "repo2-threads")

    r = run_thth(["account", "add", "demo-threads", "--media", "threads",
                  "--project", "demo"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert "thth account migrate" in r.stderr
    assert "以後読まれません" in r.stderr
    assert "repo-threads.json" in r.stderr and "repo2-threads.json" in r.stderr
    assert "--force" in r.stderr
    # **書く前に断る。** 外のディレクトリが出来ていたら、断った意味が無い
    # （出来た瞬間に repo の台帳が読まれなくなる）。
    assert not os.path.exists(置き場["外"]), "断ったのに外のディレクトリが出来た"
    assert accounts_mod.list_account_names() == ["repo-threads", "repo2-threads"] or \
        accounts_mod.list_account_names() == ["repo2-threads", "repo-threads"]


def test_addは互換のときでもrepoの中に書かない(置き場):
    """**読みが互換 (c) に落ちていても、書く先は外**（`--force` で進んだとき）。"""
    台帳を置く(置き場["repo の中"], "repo-threads")
    r = run_thth(["account", "add", "demo-threads", "--media", "threads",
                  "--project", "demo", "--force"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert os.path.exists(os.path.join(置き場["外"], "demo-threads.json"))
    assert not os.path.exists(os.path.join(置き場["repo の中"], "demo-threads.json"))
    # そして **1 本足した時点で外が正**——repo の中の台帳はもう並ばない。
    assert accounts_mod.list_account_names() == ["demo-threads"]


def test_addのhandleの既定はprojectであってアカウント名ではない(置き場):
    """`nigamilab-threads` の handle は `nigamilab`。アカウント名をそのまま
    入れると `@nigamilab-threads` という**実在しない綴り**が board に並ぶ。

    **既定が残るのは Threads だけ**（監査 2・C10）——下の 2 本を見よ。
    """
    assert run_thth(["account", "add", "nigamilab-threads", "--media", "threads",
                     "--project", "nigamilab"]).returncode == 0
    with open(os.path.join(置き場["外"], "nigamilab-threads.json"), encoding="utf-8") as f:
        assert json.load(f)["handle"] == "nigamilab"


# --------------------------------------------------------------------------
# 無言の手作業を挟ませない（監査 2・C10・masaru 裁定 2026-09-13「手がかかっても最善を」）
# --------------------------------------------------------------------------

def test_addのredirect_uriが台帳に入る(置き場):
    """(a) `--redirect-uri` で渡した値がそのまま台帳に入り、**ダミーは残らない**。"""
    r = run_thth(["account", "add", "demo-threads", "--media", "threads",
                  "--project", "demo", "--redirect-uri", "https://thth.me/callback/"])
    assert r.returncode == 0, r.stdout + r.stderr
    with open(os.path.join(置き場["外"], "demo-threads.json"), encoding="utf-8") as f:
        data = json.load(f)
    assert data["redirect_uri"] == "https://thth.me/callback/"
    assert "example.invalid" not in json.dumps(data, ensure_ascii=False)
    # **本物を渡した欄に注意は出さない**（要らない注意は読まれなくなる）。
    # handle のほうは `--project demo` なので雛形と同じ綴りになり、そちらは出る。
    assert not [l for l in r.stdout.splitlines()
                if "redirect_uri" in l and "ダミー" in l], r.stdout


def test_addはredirect_uriを省いたら次の一手で1行言う(置き場):
    """(b) 省略時は雛形のダミーのまま書くが、**黙って書かない**。

    前はここが無言だった——`thth auth` を打った人が
    `https://example.invalid/` の認可 URL をブラウザで開いて初めて詰まった
    （**`add` と `auth` の間に、どこにも書かれていない手作業**）。
    """
    r = run_thth(["account", "add", "demo-threads", "--media", "threads",
                  "--project", "demo"])
    assert r.returncode == 0, r.stdout + r.stderr
    with open(os.path.join(置き場["外"], "demo-threads.json"), encoding="utf-8") as f:
        assert json.load(f)["redirect_uri"] == "https://example.invalid/"
    # 名指し・ダミーだと言う・`thth auth` の前に・直し方（2 通り）が 1 行に揃う。
    行 = [l for l in r.stdout.splitlines() if "redirect_uri" in l and "ダミー" in l]
    assert len(行) == 1, r.stdout
    assert "thth auth" in 行[0] and "--redirect-uri" in 行[0] and "台帳" in 行[0], 行[0]


def test_addのredirect_uriはthreads以外では黙って捨てない(置き場):
    """`redirect_uri` を読むのは Threads の `thth auth` だけ。**黙って捨てない**。"""
    r = run_thth(["account", "add", "demo-bluesky", "--media", "bluesky",
                  "--project", "demo", "--handle", "demo2.bsky.social",
                  "--redirect-uri", "https://thth.me/callback/"])
    assert r.returncode == 2, r.stdout + r.stderr
    assert "--redirect-uri は threads" in r.stderr, r.stderr
    assert not os.path.exists(os.path.join(置き場["外"], "demo-bluesky.json"))


def test_addはblueskyのhandleを必須にする(置き場):
    """(e) Bluesky の handle は `name.bsky.social`。**`--project` の値は当たらない。**

    外れたまま書くと board に実在しない綴りが並ぶだけでなく、`thth auth` の
    取り違え検査（台帳の handle と App Password の handle を突き合わせる）が
    **認可を保存しない**。黙って外れた値を書くより、ここで 1 回聞くほうが安い。
    """
    r = run_thth(["account", "add", "demo-bluesky", "--media", "bluesky",
                  "--project", "demo"])
    assert r.returncode == 2, r.stdout + r.stderr
    assert "--handle が要ります" in r.stderr, r.stderr
    assert "name.bsky.social" in r.stderr, r.stderr   # **例を添える**
    assert not os.path.exists(os.path.join(置き場["外"], "demo-bluesky.json")), \
        "断ったのに書いている"


def test_addはmastodonのhandleとinstanceを必須にする(置き場):
    """(e) Mastodon は利用者名＋インスタンス。**どちらも道具には推測できない。**"""
    handle無し = run_thth(["account", "add", "demo-mastodon", "--media", "mastodon",
                           "--project", "demo", "--instance", "https://mastodon.social"])
    assert handle無し.returncode == 2, handle無し.stdout + handle無し.stderr
    assert "--handle が要ります" in handle無し.stderr, handle無し.stderr
    assert "--instance https://mastodon.social" in handle無し.stderr, handle無し.stderr

    instance無し = run_thth(["account", "add", "demo-mastodon", "--media", "mastodon",
                             "--project", "demo", "--handle", "user"])
    assert instance無し.returncode == 2, instance無し.stdout + instance無し.stderr
    assert "--instance が要ります" in instance無し.stderr, instance無し.stderr
    assert "--handle user" in instance無し.stderr, instance無し.stderr

    assert not os.path.exists(os.path.join(置き場["外"], "demo-mastodon.json")), \
        "断ったのに書いている"


def test_addはthreadsのhandleを今までどおり既定で埋める(置き場):
    """(f) **Threads は従来どおり。** `--handle` を必須にしたのは他の 2 媒体だけ
    ——Threads の handle は利用者名そのもので、`--project` の値がだいたい当たる。"""
    r = run_thth(["account", "add", "nigamilab-threads", "--media", "threads",
                  "--project", "nigamilab"])
    assert r.returncode == 0, r.stdout + r.stderr
    with open(os.path.join(置き場["外"], "nigamilab-threads.json"), encoding="utf-8") as f:
        assert json.load(f)["handle"] == "nigamilab"


def test_addのinstanceは媒体で綴りが変わる(置き場):
    """Mastodon は `instance`、Bluesky は `service`（既存の台帳の綴り）。"""
    assert run_thth(["account", "add", "demo-mastodon", "--media", "mastodon",
                     "--project", "demo", "--handle", "demo2", "--instance",
                     "https://example.social"]).returncode == 0
    with open(os.path.join(置き場["外"], "demo-mastodon.json"), encoding="utf-8") as f:
        assert json.load(f)["instance"] == "https://example.social"

    assert run_thth(["account", "add", "demo-bluesky", "--media", "bluesky",
                     "--project", "demo", "--handle", "demo2.bsky.social",
                     "--instance", "https://pds.example"]).returncode == 0
    with open(os.path.join(置き場["外"], "demo-bluesky.json"), encoding="utf-8") as f:
        assert json.load(f)["service"] == "https://pds.example"


@pytest.mark.parametrize("name", [
    "../pwned", "../../pwned", "a/b", "sub/../../out",
    ".", "..", "demo threads", "demo\nthreads", "demo\\threads", "デモ-threads",
])
def test_addは置き場の外に書けない_名前を検査する(置き場, tmp_path, name):
    """**アカウント名はそのままファイル名になる**（監査 1・P2-1）。

    `thth account add ../pwned` が `accounts/../pwned.json` を書いて **rc=0** で
    終わっていた。しかも `list_account_names()` は置き場直下の `.json` しか
    見ないので、**board にも `account` 一覧にも出ない**——書かれたことに誰も
    気づけない。`thth posts` が post_id を検査するのと同じ守り方で断る。
    """
    外 = 置き場["外"]
    r = run_thth(["account", "add", name, "--media", "threads", "--project", "demo"])
    assert r.returncode == 2, r.stdout + r.stderr
    assert "使えない字" in r.stderr, r.stderr

    # **1 バイトも書いていない。** 置き場の外（親ディレクトリ）も見る。
    assert not os.path.exists(外) or os.listdir(外) == [], \
        f"断ったのに書いている: {os.listdir(外)}"
    親 = os.path.dirname(外.rstrip(os.sep))
    出来たもの = [n for n in os.listdir(親) if n.endswith(".json")]
    assert 出来たもの == [], f"置き場の外に書いている: {出来たもの}"


def test_addは普通の名前を断らない(置き場):
    """検査が厳しすぎて実在の綴りを弾いていないこと（`.`・`-`・`_` は通る）。"""
    for name in ("nigamilab-threads", "asmon_kanto-threads", "a.b-mastodon"):
        r = run_thth(["account", "add", name, "--media", "threads",
                      "--project", "demo"])
        assert r.returncode == 0, f"{name}: {r.stdout}{r.stderr}"
        assert os.path.exists(os.path.join(置き場["外"], f"{name}.json"))


def test_addは足りない指定を黙って埋めない(置き場):
    知らない媒体 = run_thth(["account", "add", "demo-x", "--media", "mixi",
                             "--project", "demo"])
    assert 知らない媒体.returncode == 2
    assert "invalid choice" in 知らない媒体.stderr

    project無し = run_thth(["account", "add", "demo-threads", "--media", "threads"])
    assert project無し.returncode == 2, project無し.stdout + project無し.stderr
    assert "--project" in project無し.stderr
    assert not os.path.exists(os.path.join(置き場["外"], "demo-threads.json"))

    media無し = run_thth(["account", "add", "demo-threads", "--project", "demo"])
    assert media無し.returncode == 2, media無し.stdout + media無し.stderr
    assert "--media" in media無し.stderr


def test_名前だけのaccountは今までどおり1本の状態を出す(置き場):
    """**既存の呼び方を壊さない**（`add`・`migrate` は予約語として振り分ける）。

    rc は見ない——`thth account <name>` は「投稿できません」のとき 1 を返す
    （token も repo も置いていないこの土台では必ずそうなる）。ここで確かめたいのは
    **`add`・`migrate` を足したことで名前がそちらに吸い込まれていないこと**なので、
    「一枚の状態が出たか」と「rc=2（＝振り分けに失敗）ではないこと」を見る。
    """
    台帳を置く(置き場["外"], "demo-threads")
    r = run_thth(["account", "demo-threads", "--no-remote"])
    assert r.returncode != 2, r.stdout + r.stderr
    assert "demo-threads（demo / threads / @demo）" in r.stdout, r.stdout
    assert "token       :" in r.stdout, r.stdout


def test_読めないaccountの呼び方はloudに断る(置き場):
    台帳を置く(置き場["外"], "demo-threads")
    r = run_thth(["account", "demo-threads", "余分"])
    assert r.returncode == 2, r.stdout + r.stderr
    assert "読めません" in r.stderr


# --------------------------------------------------------------------------
# 置き場が読めないとき（「無い」と言わない・監査 1・P2-2）
# --------------------------------------------------------------------------

@pytest.fixture
def 読めない置き場(置き場):
    """台帳の置き場を `chmod 000` にする（後で必ず戻す）。

    root では権限が効かないので skip。
    """
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root では chmod 000 が効かない")
    d = 置き場["外"]
    台帳を置く(d, "demo-threads")
    os.chmod(d, 0o000)
    try:
        if os.access(d, os.R_OK):
            pytest.skip("この環境では chmod 000 が効かない")
        yield d
    finally:
        os.chmod(d, 0o700)


def test_読めない置き場は0本ではなく読めないと言う(読めない置き場):
    """**黙って「0 本」と返さない。** 本番 6 本が消えたように見える。"""
    with pytest.raises(accounts_mod.AccountError) as e:
        accounts_mod.list_account_names()
    assert "権限" in str(e.value)
    assert "0 本" in str(e.value)     # 「0 本なのではない」と明言している


def test_読めない置き場でload_accountは不在と嘘をつかない(読めない置き場):
    """`os.path.exists()` の False をそのまま「無い」と言っていた（P2-2）。

    台帳はそこに在る。**作り直しに行かせてはいけない。**
    """
    with pytest.raises(accounts_mod.AccountError) as e:
        accounts_mod.load_account("demo-threads")
    assert "権限" in str(e.value)
    assert "台帳が無い" not in str(e.value)


def test_読めない置き場でboardはtracebackにならず置き場を1行出す(読めない置き場):
    r = run_thth(["board"])
    assert "Traceback" not in r.stderr, r.stderr
    assert "PermissionError" not in r.stderr, r.stderr
    assert r.returncode != 0, r.stdout
    assert f"台帳の置き場: {読めない置き場}" in r.stdout, r.stdout
    assert "権限" in r.stdout, r.stdout


def test_読めない置き場でaccountはtracebackにならない(読めない置き場):
    r = run_thth(["account", "--no-remote"])
    assert "Traceback" not in r.stderr, r.stderr
    assert r.returncode != 0, r.stdout
    assert "権限" in r.stdout, r.stdout


def test_読めない置き場でdoctorは不在と言わない(読めない置き場):
    r = run_thth(["doctor", "demo-threads"])
    assert "Traceback" not in r.stderr, r.stderr
    assert r.returncode != 0
    両方 = r.stdout + r.stderr
    assert "権限" in 両方, 両方
    assert "台帳が無い" not in 両方, 両方


# --------------------------------------------------------------------------
# loud reject であって traceback ではない（監査 1・P3）
# --------------------------------------------------------------------------

def test_置き場がファイルならaddは言葉で断る(置き場):
    """`$THTH_ROOT/accounts` がファイルだと `FileExistsError` が素通りしていた。"""
    with open(置き場["外"], "w", encoding="utf-8") as f:
        f.write("これはディレクトリではない\n")

    r = run_thth(["account", "add", "demo-threads", "--media", "threads",
                  "--project", "demo"])
    assert "Traceback" not in r.stderr, r.stderr
    assert r.returncode == 2, r.stdout + r.stderr
    assert "台帳を書けませんでした" in r.stderr, r.stderr
    assert "ファイルになっています" in r.stderr, r.stderr


# --------------------------------------------------------------------------
# THTH_ACCOUNTS_DIR の相対パス（監査 1・P3）
# --------------------------------------------------------------------------

def test_環境変数の相対パスはその場で絶対になる(置き場, tmp_path, monkeypatch):
    """**相対のまま持ち回ると、cwd が変わった瞬間に別の場所を指す。**

    timer（`WorkingDirectory` 次第）と手打ちで置き場が割れるし、`thth board` が
    出す 1 行が、読んだ人がそのまま `ls` できる綴りでなくなる。
    """
    明示 = tmp_path / "相対の先"
    明示.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(accounts_mod.ACCOUNTS_DIR_ENV, "相対の先")

    info = accounts_mod.accounts_dir_info()
    assert os.path.isabs(info["path"]), info["path"]
    assert os.path.realpath(info["path"]) == os.path.realpath(str(明示))
    # **書く先も同じ綴り。**
    assert os.path.isabs(account_cli_mod.target_accounts_dir())
    # **画面に出る 1 行も絶対。**
    line = account_cli_mod.where_line()
    assert str(明示) in line or os.path.realpath(str(明示)) in line, line
    assert line.startswith("台帳の置き場: " + os.sep), \
        f"1 行が相対パスのまま（そのまま ls できない）: {line}"


def test_環境変数のチルダも展開する(置き場, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv(accounts_mod.ACCOUNTS_DIR_ENV, "~/台帳")
    path = accounts_mod.accounts_dir_info()["path"]
    assert path == str(tmp_path / "home" / "台帳"), path


# --------------------------------------------------------------------------
# doctor・board が置き場を 1 行で言う
# --------------------------------------------------------------------------

def test_boardが置き場を1行で言う(置き場):
    台帳を置く(置き場["外"], "demo-threads")
    r = run_thth(["board"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"台帳の置き場: {置き場['外']}" in r.stdout


def test_boardは互換のとき画面にもそう書く(置き場):
    """警告は stderr だが、**画面（stdout）にも 1 行**——`2>/dev/null` で
    読んでいる人・ログに stdout しか残していない人に届かせるため。"""
    台帳を置く(置き場["repo の中"], "repo-threads")
    r = run_thth(["board"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "互換" in r.stdout, r.stdout
    assert "thth account migrate" in r.stdout


def test_board_jsonは置き場を持つ_stdoutはJSON1個のまま(置き場):
    """互換のときも **stdout は JSON 1 個だけ**（設計 §6 の出力契約）。
    警告が stdout に混ざると、`--json` を読む相手がここで壊れる。"""
    台帳を置く(置き場["repo の中"], "repo-threads")
    r = run_thth(["board", "--json"])
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)      # 混ざっていればここで落ちる
    assert data["accounts_dir"]["source"] == accounts_mod.SOURCE_APP_REPO
    assert data["accounts_dir"]["path"] == 置き場["repo の中"]
    # 警告は捨てられていない——stderr には出ている。
    assert accounts_mod.LEGACY_WARNING in r.stderr


def test_doctorが置き場を1行で言う_台帳が読めないときも(置き場):
    """**台帳が読めなかったときこそ、どこを読んだかを言う。**

    「台帳が無い」とだけ言われても、外を見に行ったのか repo の中を見に行ったのかが
    判らないと直しようがない。
    """
    r = run_thth(["doctor", "居ない-threads"])
    assert r.returncode == 2, r.stdout + r.stderr
    assert f"台帳の置き場: {置き場['外']}" in r.stdout, r.stdout
    assert "台帳が無い" in r.stdout


def test_doctor_jsonは置き場を持つ(置き場):
    台帳を置く(置き場["外"], "demo-threads")
    r = run_thth(["doctor", "demo-threads", "--json"])
    data = json.loads(r.stdout)
    assert data["accounts_dir"]["source"] == accounts_mod.SOURCE_ROOT
    assert data["accounts_dir"]["path"] == 置き場["外"]


# --------------------------------------------------------------------------
# repo に配る雛形
# --------------------------------------------------------------------------

@pytest.mark.parametrize("media", ["threads", "bluesky", "mastodon"])
def test_雛形は3本ともダミーでproduction_false(media):
    """**repo に配る `accounts.example/` に本物の値を入れない**（そして
    `production: true` にしない）。"""
    path = os.path.join(REPO_ROOT, "accounts.example", f"{media}.json")
    assert os.path.exists(path), path
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["production"] is False
    assert data["scheduled"] is False
    assert data["project"] == "demo"
    text = json.dumps(data, ensure_ascii=False)
    # 実物の project 名・handle が紛れ込んでいない。
    for 本物 in ("nigamilab", "kopicha", "asmon-kanto", "aoking", "masaru"):
        assert 本物 not in text, f"雛形に実物の綴りが入っている: {本物}"


def test_repoのaccountsはこの版ではまだ残っている():
    """**この版では消さない**（設計 v2 §8）。VM の migrate が済んで運用が
    確認した後の、別の日の commit。ここが空になったら、それは早すぎる。
    """
    d = os.path.join(REPO_ROOT, "accounts")
    残り = sorted(n for n in os.listdir(d) if n.endswith(".json"))
    assert len(残り) == 6, f"repo の accounts/ が 6 本でない: {残り}"
