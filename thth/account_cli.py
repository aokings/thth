"""`thth account` の枝分かれ（`add`・`migrate`）。設計 v2 §3「台帳を repo の外へ」。

なぜ別 module か（2026-09-13・並行 Track との境界）: `thth/cli.py` の
`build_parser()` は別の Track が同時に触る。ここに足す行を **1 行だけ**にして
衝突を作らない。`build_parser()` は `account_cli.register(sub)` を呼ぶだけで、
`thth account` の枝は全部この file にある。

**`thth account` の形**（既存の呼び方を壊さない）:

    thth account                       全アカウントの状態を一枚で（従来どおり）
    thth account <name>                1 本の状態を一枚で（従来どおり）
    thth account migrate [--dry-run]   repo の中の台帳を $THTH_ROOT/accounts/ へ写す
    thth account add <name> --media … --project …   雛形から 1 本書く

`migrate` と `add` は**予約語**。同じ名前のアカウントは持てない（`account` は
`<project>-<media>` の綴りなので、実際に当たることはない）。

argparse の subparsers を使わないのは、既存の `account` 位置引数（`nargs="?"`）と
subparsers が同じ位置を取り合って `thth account <name>` が壊れるため。位置引数を
2 本にして、1 本目が予約語かどうかで振り分ける。
"""
from __future__ import annotations

from . import admin_log, leave_gate

import argparse
import json
import os
import shutil
import sys

from . import accounts as accounts_mod

# 雛形の置き場。**`THTH_APP_DIR` を見ない**——雛形は道具に同梱されているもので、
# 台帳の置き場（差し替え可能）とは別。この module 自身の場所から引く。
#
# **2 か所を順に見る**（監査 2・2026-09-13）。前は「package の親／accounts.example」
# だけを見ていた。repo から走らせる分には当たるが、**`pip install thth` した人の
# 手元では package の親は `site-packages/` で、そこに雛形は無い**——
# `thth account add` が `雛形がありません: …/site-packages/accounts.example/threads.json`
# で rc=2 になっていた（wheel にも sdist にも雛形が入っていなかった）。
#
#   (a) `thth/accounts.example/` … 配布物の中（`pyproject.toml` の `force-include`）
#   (b) `../accounts.example/`   … repo から走らせたとき（repo の置き場は変えない）
#
# **両方効かせる。** (a) だけにすると repo での開発が止まり、(b) だけにすると
# 配った先で止まる。
def _find_example_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    同梱 = os.path.join(here, "accounts.example")
    if os.path.isdir(同梱):
        return 同梱
    return os.path.join(os.path.dirname(here), "accounts.example")


EXAMPLE_DIR = _find_example_dir()

MEDIA_CHOICES = ("threads", "bluesky", "mastodon", "x")

# **アカウント名はファイル名になる**（`<accounts_dir>/<name>.json`）。区切りや
# `..` を混ぜると置き場の外に書けてしまう——`thth account add ../pwned` が
# `accounts/../pwned.json` を書いて rc=0 で終わり、`list_account_names()` には
# 出ないので board からも見えなかった（監査 1・P2-1）。`thth posts` が post_id を
# 検査するのと同じ守り方（`tests/test_posts.py::test_post_idにパス区切りがあれば書かない`）。
#
# **知識は `thth/accounts.py` に 1 つ**（セキュリティ監査 2026-09-14・P2-2）。
# 書く側（ここ）だけが検査していて、読む側（`accounts.load_account()`）には
# 検査が無かった。同じ規則が 2 か所にあると、片方だけ直る。
NAME_RE = accounts_mod.NAME_RE
name_is_safe = accounts_mod.name_is_safe

# `thth account` の 1 本目の位置引数が、アカウント名ではなく枝の名前になるもの。
VERBS = ("add", "migrate")


# --------------------------------------------------------------------------
# 置き場
# --------------------------------------------------------------------------

def target_accounts_dir() -> str:
    """**書き込む先**。`$THTH_ACCOUNTS_DIR` があればそこ、無ければ
    `$THTH_ROOT/accounts/`。

    **repo の中（互換の (c)）には絶対に書かない。** 読むほうは 1 版だけ互換を
    残すが（`accounts.accounts_dir_info()`）、道具が新しく台帳を作るときは常に
    外。そうしないと「外へ出す」作業のさなかに repo の中が増える。
    """
    # **相対パスはその場で絶対にする**（監査 1・P3・`accounts.env_accounts_dir()`）。
    env = accounts_mod.env_accounts_dir()
    return env if env else accounts_mod.root_accounts_dir()


def where_line() -> str:
    """台帳の置き場を **1 行**で言う（`thth doctor`・`thth board` が出す）。"""
    info = accounts_mod.accounts_dir_info()
    source = info["source"]
    if source == accounts_mod.SOURCE_APP_REPO:
        return (f"台帳の置き場: {info['path']}（**repo の中・互換**。"
                f"`thth account migrate` で {target_accounts_dir()} へ出してください）")
    if source == accounts_mod.SOURCE_ENV:
        return f"台帳の置き場: {info['path']}（${accounts_mod.ACCOUNTS_DIR_ENV}）"
    return f"台帳の置き場: {info['path']}（$THTH_ROOT/accounts）"


# --------------------------------------------------------------------------
# thth account migrate
# --------------------------------------------------------------------------

def plan_migration() -> dict:
    """repo の中の台帳を外へ写す計画を立てる（**何も変えない**）。

    - `copy`   … 外に無いので写すもの
    - `same`   … 外に同じ中身で既にあるもの（＝写し済み・何もしない）
    - `differ` … 外にあるが**中身が違う**もの（**上書きしない**・名指しで断る）

    `differ` を上書きしない理由: 外へ出した後は**外が正**で、運用がそこを直す。
    repo の中は古いまま残る（消すのは別の日）。上書きすると、**運用が外で直した
    ものを、消し忘れた repo の台帳が黙って巻き戻す。**
    """
    src = accounts_mod.legacy_accounts_dir()
    dst = target_accounts_dir()
    out = {"src": src, "dst": dst, "copy": [], "same": [], "differ": [],
           "src_missing": False, "same_place": False}
    if os.path.realpath(src) == os.path.realpath(dst):
        out["same_place"] = True
        return out
    if not os.path.isdir(src):
        out["src_missing"] = True
        return out
    for name in sorted(n for n in os.listdir(src) if n.endswith(".json")):
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if not os.path.exists(d):
            out["copy"].append(name)
            continue
        with open(s, "rb") as f:
            a = f.read()
        with open(d, "rb") as f:
            b = f.read()
        (out["same"] if a == b else out["differ"]).append(name)
    return out


@leave_gate.protect_migration
def cmd_migrate(args) -> int:
    """`thth account migrate [--dry-run]`: repo の中の台帳を外へ **copy** する。

    **移動しない・repo は触らない**（設計 v2 §7-1「`git mv` ではなく copy」）。
    VM は `/srv/thth/app` を `merge --ff-only` で更新する clone なので、そこの
    作業ツリーを道具が動かすと次の更新が止まる。**repo の `accounts/` を消すのは
    別の日、人の手**。

    冪等: 2 回目からは全部 `same` になり、何も書かない。
    """
    plan = plan_migration()
    as_json = getattr(args, 'json', False)
    log = (lambda line: print(line, file=sys.stderr)) if as_json else print
    def finish(rc):
        if as_json:
            print(json.dumps({'migration': plan, 'dry_run': getattr(args, 'dry_run', False),
                              'ok': rc == 0}, ensure_ascii=False))
        return rc
    if plan["same_place"]:
        log(f"写す先と写し元が同じです（{plan['dst']}）。することはありません。")
        return finish(0)
    if plan["src_missing"]:
        log(f"repo の中に台帳はありません（{plan['src']}）。することはありません。")
        return finish(0)

    dry = getattr(args, "dry_run", False)
    log(f"写し元（repo の中）: {plan['src']}")
    log(f"写し先（正）　　　: {plan['dst']}")
    log("")
    if not plan["copy"] and not plan["same"] and not plan["differ"]:
        log("台帳が 1 本もありません。することはありません。")
        return finish(0)

    for name in plan["copy"]:
        log(f"  {'写す（予定）' if dry else '写した'}: {name}")
    for name in plan["same"]:
        log(f"  写し済み（同じ中身なので触りません）: {name}")
    for name in plan["differ"]:
        log(f"  **中身が違います。上書きしません**: {name}")

    if not dry:
        if any(leave_gate.stopped(name[:-5]) for name in plan['copy']):
            log('account_stopped: 停止済み台帳は migrate で再作成しません')
            return finish(2)
        os.makedirs(plan["dst"], exist_ok=True)
        for name in plan["copy"]:
            shutil.copy2(os.path.join(plan["src"], name),
                         os.path.join(plan["dst"], name))

    log("")
    if plan["differ"]:
        log(f"**{len(plan['differ'])} 本は写していません**——外の台帳と repo の台帳の"
            f"中身が違います。外が正です。repo の側が古いだけなら、"
            f"repo の `accounts/` を消す日にまとめて片付けてください。")
        return finish(1)
    総数 = len(plan["copy"]) + len(plan["same"])
    if dry:
        log(f"--dry-run なので何も書いていません。"
            f"よければ `thth account migrate` を打ってください。")
    elif plan["copy"]:
        log(f"{len(plan['copy'])} 本を写しました。`thth board` で {総数} 本が"
            f"変わらず見えることを確かめてください。"
            f"**repo の `accounts/` はそのまま残っています**（消すのは別の日）。")
    else:
        # **冪等。** 2 回目からはここに来る——「写した」と言わない。
        log(f"写すものはありませんでした（{総数} 本とも写し済み）。")
    return finish(0)


# --------------------------------------------------------------------------
# thth account add
# --------------------------------------------------------------------------

def example_path(media: str) -> str:
    return os.path.join(EXAMPLE_DIR, f"{media}.json")


def build_ledger(name: str, *, media: str, project: str, handle: str | None = None,
                 instance: str | None = None, repo_dir: str | None = None,
                 redirect_uri: str | None = None) -> dict:
    """`accounts.example/<media>.json` の雛形から 1 本ぶんを組み立てる。

    **`production` と `scheduled` は必ず false**（設計 §4.2「`production: true` を
    自分で書かない限り dry-run」）。道具が作ったものが、いきなり本物を投げる形で
    生まれてはいけない。雛形が万一 true でも、ここで落とす。
    """
    path = example_path(media)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["account"] = name
    data["project"] = project
    data["media"] = media
    # 既定の handle は **project**（`nigamilab-threads` の handle は `nigamilab`）。
    # アカウント名をそのまま入れると `@nigamilab-threads` という実在しない綴りが
    # board に並ぶ。
    #
    # **既定を許すのは Threads だけ**（監査 2・C10・2026-09-13）。Threads の
    # handle は利用者名そのものなので `--project` の値がだいたい当たるが、
    # **Bluesky は `name.bsky.social`、Mastodon は `@` を除いた利用者名＋instance**
    # なので、`--project` の値はほぼ外れる。外れた handle は board に実在しない
    # 綴りで並ぶだけでなく、**Bluesky では `thth auth` の取り違え検査に引っかかって
    # 認可が保存されない**（`oauth.run_auth_bluesky()`）。だから `cmd_add()` は
    # その 2 媒体で `--handle` を必須にする——ここは受け取った値を入れるだけ。
    data["handle"] = handle or project
    if redirect_uri is not None:
        # Threads だけ（`cmd_add()` が他媒体を断る）。省略時は雛形のダミーのまま。
        data["redirect_uri"] = redirect_uri
    if instance is not None:
        # Mastodon は `instance`、Bluesky は `service`（既存の台帳の綴り）。
        data["service" if media == "bluesky" else "instance"] = instance
    data["repo_dir"] = repo_dir or f"$THTH_ROOT/repos/{project}"
    data["env"] = f"~/.config/thth/{name}.env"
    data["token"] = f"~/.config/thth/{name}.token"
    data["production"] = False
    data["scheduled"] = False
    return data


def _互換の台帳() -> dict | None:
    """いま読んでいるのが **repo の中（互換 (c)）** なら、その置き場と台帳の名前。

    そうでなければ `None`。`add` が「この N 本が読まれなくなる」と言うために使う。
    """
    info = accounts_mod.accounts_dir_info()
    if info["source"] != accounts_mod.SOURCE_APP_REPO:
        return None
    try:
        names = sorted(n for n in os.listdir(info["path"]) if n.endswith(".json"))
    except OSError:
        names = []
    return {"path": info["path"], "names": names}


@leave_gate.protect_account_add
@admin_log.guarded
def cmd_add(args) -> int:
    """`thth account add <name> --media … --project … [--handle …] [--instance …] [--repo-dir …]`。

    書く先は **`$THTH_ROOT/accounts/<name>.json`**（repo の中ではない）。
    既にあれば**上書きしない**（loud reject・作法 5）。
    """
    from . import admin_log
    try:
        by = admin_log.actor(getattr(args, "by", None))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    name = args.name
    if not name:
        print("account add には名前が要ります: thth account add <name> --media … --project …",
              file=sys.stderr)
        return 2
    if not name_is_safe(name):
        # **置き場の外に書かせない**（監査 1・P2-1）。`thth account add ../pwned` が
        # `accounts/../pwned.json` を書いて rc=0 で終わっていた——しかも
        # `list_account_names()` は `.json` の直下しか見ないので board に出ない。
        print(f"アカウント名に使えない字が入っています: {name!r}", file=sys.stderr)
        print("使えるのは英数字と `_`・`.`・`-` だけです"
              "（名前はそのままファイル名になります。`/` や `..` は置き場の外を指せます）。",
              file=sys.stderr)
        return 2
    if not args.media:
        print(f"--media が要ります（{'|'.join(MEDIA_CHOICES)}）", file=sys.stderr)
        return 2
    if args.media not in MEDIA_CHOICES:
        print(f"--media は {'|'.join(MEDIA_CHOICES)} のどれか（受け取った: {args.media}）",
              file=sys.stderr)
        return 2
    if not args.project:
        print("--project が要ります（clone の dir 名・board の見出し）", file=sys.stderr)
        return 2

    # **媒体ごとに、既定で当たらない欄は必須にする**（監査 2・C10・2026-09-13）。
    #
    # handle の既定は `--project` の値。**Threads は利用者名がそのまま handle** な
    # ので当たることが多いが、**Bluesky は `name.bsky.social`**（ドメイン形）、
    # **Mastodon は利用者名＋instance** なので、既定はほぼ外れる。外れたまま書くと:
    #   - board に実在しない綴りが並ぶ（見た人が直せない）
    #   - Bluesky は `thth auth` の取り違え検査が「台帳の handle と App Password の
    #     handle が違う」と言って**認可を保存しない**（`oauth.run_auth_bluesky()`）
    # **黙って外れた値を書くより、ここで 1 回止まって聞くほうが安い。**
    if args.media in ("bluesky", "mastodon", "x") and not args.handle:
        print(f"--handle が要ります（{args.media} は `--project` の値では当たりません）",
              file=sys.stderr)
        if args.media == "bluesky":
            print(f"  例: thth account add {name} --media bluesky "
                  f"--project {args.project} --handle name.bsky.social", file=sys.stderr)
        elif args.media == "x":
            print(f"  例: thth account add {name} --media x --project {args.project} --handle user --by <actor>", file=sys.stderr)
        else:
            print(f"  例: thth account add {name} --media mastodon "
                  f"--project {args.project} --handle user "
                  f"--instance https://mastodon.social", file=sys.stderr)
        return 2
    if args.media == "mastodon" and not args.instance:
        # instance が無いと、雛形の `https://mastodon.example`（存在しない）が
        # そのまま残る。**どのインスタンスかは道具には推測できない。**
        print("--instance が要ります（Mastodon はインスタンスごとに口が違います）",
              file=sys.stderr)
        print(f"  例: thth account add {name} --media mastodon "
              f"--project {args.project} --handle user "
              f"--instance https://mastodon.social", file=sys.stderr)
        return 2
    if args.redirect_uri and args.media != "threads":
        # **黙って捨てない**（作法 5）。`redirect_uri` は Threads の OAuth 往復
        # （`thth auth`）だけが読む欄で、他媒体の雛形には無い。
        print(f"--redirect-uri は threads のときだけ使えます（受け取った媒体: {args.media}）",
              file=sys.stderr)
        return 2

    # **互換 (c) のまま `add` を打たせない**（監査 1・P1-3）。
    #
    # 読みが repo の中に落ちている機械（＝VM）で `add` を 1 本打つと、書く先の
    # `$THTH_ROOT/accounts/` が**その瞬間に出来る**。解決順は「ディレクトリが
    # あるか」だけで (b) を正とするので、**次の実行から repo の N 本は一切
    # 読まれない**——`thth run kopicha-threads` が「台帳が無い」の rc=2 になる。
    # 前はこれを何も言わずにやっていた。**順番は `migrate` → `add`。**
    互換 = _互換の台帳()
    if 互換 and not getattr(args, "force", False):
        print(f"**先に `thth account migrate` を打ってください。**", file=sys.stderr)
        print(f"いま台帳を読んでいるのは repo の中です: {互換['path']}（{len(互換['names'])} 本）",
              file=sys.stderr)
        for n in 互換["names"]:
            print(f"  - {n}", file=sys.stderr)
        print(f"ここで `add` を打つと {target_accounts_dir()} が出来て、"
              f"**この {len(互換['names'])} 本は以後読まれません**"
              f"（次の実行で「台帳が無い」になります）。", file=sys.stderr)
        print(f"  1) thth account migrate   （repo の中を外へ copy・repo は触りません）",
              file=sys.stderr)
        print(f"  2) thth account add {name} --media {args.media} --project {args.project}",
              file=sys.stderr)
        print(f"**上の台帳が自分のものでなければ**（clone に同梱されていた他人の台帳）、"
              f"`--force` を付けて進んでかまいません。", file=sys.stderr)
        return 1

    try:
        data = build_ledger(name, media=args.media, project=args.project,
                            handle=args.handle, instance=args.instance,
                            repo_dir=args.repo_dir, redirect_uri=args.redirect_uri)
    except FileNotFoundError as e:
        print(f"雛形がありません: {e}", file=sys.stderr)
        return 2

    email_file = getattr(args, "notification_email_file", None)
    if email_file:
        from . import incident
        try:
            incident.require_private_directory(target_accounts_dir())
            with open(email_file, encoding="utf-8") as f:
                recipient = f.read().strip()
            if not incident.address(recipient):
                raise ValueError("invalid_email")
            data["notification_email"] = recipient
        except incident.ConfigLocationError:
            print("通知先を含む台帳は Git repo 外の THTH_ROOT に作成してください。台帳は作成しませんでした。", file=sys.stderr)
            return 2
        except (OSError, ValueError):
            print("通知先ファイルを確認してください（単一メールアドレス）", file=sys.stderr)
            return 2

    dst_dir = target_accounts_dir()
    path = os.path.join(dst_dir, f"{name}.json")
    previous = {}
    if os.path.exists(path):
        if not getattr(args, "force", False):
            print(f"既にあります。上書きしません: {path}", file=sys.stderr)
            return 1
        try:
            with open(path, encoding="utf-8") as stream:
                previous = json.load(stream)
            if not isinstance(previous, dict):
                raise ValueError("invalid_ledger")
        except (OSError, ValueError):
            print("既存台帳を読めません", file=sys.stderr)
            return 2
    data["provenance"] = previous.get("provenance") if previous else admin_log.provenance(by)
    try:
        os.makedirs(dst_dir, exist_ok=True)
        if previous:
            from . import secrets_fs
            secrets_fs.atomic_write_json(path, data)
        else:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
        admin_log.append("account_updated" if previous else "account_added", name, data,
                         by=by, diff=admin_log.difference(previous, data))
        if previous and previous.get("production") != data.get("production"):
            admin_log.append("production_enabled" if data.get("production") else "production_disabled",
                             name, data, by=by, diff={"production": [previous.get("production"), data.get("production")]})
    except OSError as e:
        # **traceback にしない**（監査 1・P3）。`$THTH_ROOT/accounts` がファイル
        # だと `FileExistsError`、書けない場所だと `PermissionError` が素通りして
        # いた。どちらも「打った人が直せること」なので、言葉で言う。
        print(f"台帳を書けませんでした: {path}（{e.strerror or e}）", file=sys.stderr)
        if os.path.exists(dst_dir) and not os.path.isdir(dst_dir):
            print(f"  台帳の置き場がファイルになっています: {dst_dir}"
                  f"（ディレクトリでなければなりません）", file=sys.stderr)
        return 2

    if args.media == 'mastodon':
        from . import scopes
        print(scopes.mastodon_guidance(name), file=sys.stderr)

    if args.json:
        print(json.dumps({"path": path, "account": {k: v for k, v in data.items() if k != "notification_email"}}, ensure_ascii=False))
        return 0
    print("本番有効化前に thth notifications config/status/test で利用者・管理者への到達と外部死活監視を確認してください。")
    print(f"書きました: {path}")
    print(f"  production: false（**このままでは投げません**。"
          f"本番にするときだけ手で true に）")
    print(f"  scheduled: false（timer に載せるときだけ手で true に）")
    print(f"  repo_dir: {data['repo_dir']}")
    print("")
    # **雛形のダミーが残っているなら、書いたその場で名指しする**（監査 2・C10・
    # 2026-09-13）。前はここが何も言わず、`thth auth` を打った人が
    # `https://example.invalid/` の認可 URL をブラウザで開いて初めて詰まった
    # ——**どこにも書かれていない手作業**が `add` と `auth` の間に挟まっていた。
    for d in accounts_mod.dummy_fields(data):
        前置き = "**`thth auth` の前に。** " if d["field"] == "redirect_uri" else ""
        print(f"**{d['field']} はダミーのままです**（{d['value']}）。{前置き}{d['next']}")
    print(f"次の一手: `thth doctor {name}`（トークンがまだなので rc=2 で止まります）"
          f" → `thth token set {name}` → `thth board`")
    return 0


# --------------------------------------------------------------------------
# 振り分けと登録
# --------------------------------------------------------------------------

def dispatch(args) -> int:
    verb = getattr(args, "account", None)
    if verb == 'leave':
        from . import leave
        return leave.command(args)
    if verb == "migrate":
        if args.name:
            print(f"`thth account migrate` は名前を取りません（受け取った: {args.name}）",
                  file=sys.stderr)
            return 2
        return cmd_migrate(args)
    if verb == "add":
        return cmd_add(args)
    # 設計 3.12.0 段 3: 安全装置の数値・止めたのを戻す・状態を見る。
    if verb in ("set", "resume", "status"):
        from . import account_settings
        return {"set": account_settings.cmd_set, "resume": account_settings.cmd_resume,
                "status": account_settings.cmd_status}[verb](args)
    if getattr(args, "rest", None):
        print(f"`thth account {verb} {args.name}` の後ろの語が読めません: {' '.join(args.rest)}",
              file=sys.stderr)
        return 2
    if args.name:
        print(f"`thth account` が読めません（`{verb} {args.name}`）。"
              f"使い方: thth account [<name>] / thth account add <name> … / "
              f"thth account migrate", file=sys.stderr)
        return 2
    # 従来どおり「1 アカウント（省略時は全部）の状態を一枚で述べる」。
    from . import cli
    return cli.cmd_account(args)


def register(sub) -> None:
    """`build_parser()` が作った `account` の parser に `add`・`migrate` を足す。

    **`build_parser()` に足すのはこの呼び出しの 1 行だけ**（並行 Track との衝突を
    作らない）。既存の `account` 位置引数・`--json`・`--no-remote` はそのまま。
    """
    p = sub.choices["account"]
    # epilog の改行をそのまま出す（既定の formatter は詰めてしまい、4 つの
    # 呼び方が 1 段落になって読めなくなる）。
    p.formatter_class = argparse.RawDescriptionHelpFormatter
    p.add_argument("name", nargs="?",
                   help="`add` のときのアカウント名（`<project>-<media>`）")
    p.add_argument("rest", nargs="*",
                   help="`set` のとき: <名前> <値>（daily_max_posts N など）")
    p.add_argument("--by", help="作成・変更した人（add / leave / set / resume では必須）")
    p.add_argument("--media", default=None, choices=MEDIA_CHOICES,
                   help="`add` のとき: 媒体")
    p.add_argument("--project", default=None,
                   help="`add` のとき: clone の dir 名・board の見出し")
    p.add_argument("--handle", default=None,
                   help="`add` のとき: 表示上の handle（bluesky・mastodon では必須）")
    p.add_argument("--instance", default=None,
                   help="`add` のとき: Mastodon の instance（必須）/ Bluesky の service")
    p.add_argument("--redirect-uri", default=None, dest="redirect_uri",
                   help="`add`（threads）のとき: Meta アプリに登録した認可の戻り先。"
                        "省略すると雛形のダミーのままで、`thth auth` が rc=2 で断ります")
    p.add_argument("--notification-email-file", help="利用者の通知先メール1件を記した private file")
    p.add_argument("--repo-dir", default=None, dest="repo_dir",
                   help="`add` のとき: 原稿 repo（既定 `$THTH_ROOT/repos/<project>`）")
    p.add_argument("--plaza-open", default="delete", choices=("delete", "keep"), dest="plaza_open",
                   help="`leave` のとき: 施策の広場の open の書き込みを消すか（delete・既定）、"
                        "「退出した持ち主」の名義で残すか（keep）。project 範囲の書き込みは常に消す")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="`migrate` のとき: 何も書かずに計画だけ出す")
    p.add_argument("--force", action="store_true",
                   help="`add` のとき: repo の中の台帳が読めなくなるのを承知で進む")
    p.epilog = ("thth account                     全アカウントの状態を一枚で\n"
                "thth account leave <name> --by <actor> [--plaza-open delete|keep] 停止・失効・所有物の削除（CLIのみ）\n"
                "thth account <name>              1 本の状態を一枚で\n"
                "thth account set <name> <名前> <値> --by <actor>  安全装置の数値を変える\n"
                "  名前: daily_max_posts・daily_max_retracts・\n"
                "        burst_count・burst_minutes（burst <件数> <分> でも）・hold_minutes・min_interval_hours\n"
                "thth account resume <name> --by <actor>  安全装置が止めた口座を戻す（MCP からはできない）\n"
                "thth account status <name> [--json]      数値・止まっているか・今日の公開数/削除数\n"
                "thth account migrate [--dry-run] repo の中の台帳を "
                "$THTH_ROOT/accounts/ へ写す（copy・repo は触らない）\n"
                "thth account add <name> --media threads|bluesky|mastodon "
                "--project <p> [--handle …] [--instance …] [--redirect-uri …] "
                "[--repo-dir …]\n"
                "  threads  : --handle は省略可（既定は --project の値）。"
                "--redirect-uri を省くと雛形のダミーのまま\n"
                "  bluesky  : --handle name.bsky.social が必須\n"
                "  mastodon : --handle user と --instance https://… が必須")
    p.set_defaults(func=dispatch)
