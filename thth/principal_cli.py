"""`thth principal add|show|set|resume|auth`（設計 4.0 S1 §3.2・§4.1・§4.2・§4.4）。

主体（はてな ID 1 つ）の台帳を書く・見る・上限を変える・止めたのを戻す口。どれも
**運営者の CLI だけ**——MCP の道具にも thth.me（/activity）にも出さない（T-2f）。
暴走して止まった LLM が自分で戻せないように（`guard.resume` と同じ考え）。

    thth principal add <name> --hatena-id <id> [--blog <blog_id>=<lane>[,<lane>…]]… --by <名前>
    thth principal show <name> [--json]
    thth principal set <name> <名前> <値> --by <名前>   （burst は `burst <件数> <分>` でも）
    thth principal resume <name> --by <名前>
    thth principal auth <name> --by <名前> [--force|--forget|--forget-consumer] [--consumer]
                                                        （OAuth 1.0a・PIN（oob）。端末から・S2 §2.4）

**秘密の値は表示しない**（token・consumer はパスだけ。`show` は「ある／ない」だけ言う）。`auth` だけが
`getpass` で受けて 0600 で書く（値は画面・ログ・admin_log に出さない）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import accounts, admin_log, principals, secrets_fs

# `thth principal set` で変えられる名前（`thth account set` と同じ語）。
KEYS = ("daily_max_posts", "daily_max_retracts", "daily_max_bookmarks", "burst_count", "burst_minutes",
        # 4.0 S4 §5.3: 自分の記事のブックマーク・同じホストへのブックマークの 1 日の上限。
        "daily_max_self_bookmarks", "bookmark_same_host_daily")
# 4.0 S3 §9: 主体の台帳の `fotolife` の上限（`fotolife.<欄>`・範囲は `hatena_fotolife.LIMIT_RANGES`）。
FOTOLIFE_KEYS = ("fotolife.max_image_bytes", "fotolife.max_images_per_entry", "fotolife.max_total_bytes",
                 "fotolife.daily_max_uploads", "fotolife.feed_pages")


def _find_example_dir() -> str:
    from .account_cli import EXAMPLE_DIR
    return EXAMPLE_DIR


def example_path() -> str:
    return os.path.join(_find_example_dir(), "principal-hatena.json")


def _fail(reason, *, json_output=False) -> int:
    print(reason, file=sys.stderr)
    if json_output:
        print(json.dumps({"error": reason.split(":")[0]}, ensure_ascii=False))
    return 2


def _parse_blog(value: str) -> dict:
    """`<blog_id>=<lane>[,<lane>…]` を blogs[] の 1 行に。"""
    blog_id, sep, lanes = (value or "").partition("=")
    blog_id = blog_id.strip().lower()
    lanes = [lane.strip() for lane in lanes.split(",") if lane.strip()]
    if not sep or not principals.BLOG_ID_RE.fullmatch(blog_id) or not lanes:
        raise ValueError("blogs_invalid")
    return {"blog_id": blog_id, "lanes": lanes, "syntax": "markdown"}


def build_ledger(name: str, *, hatena_id: str, blogs: list | None = None, owner: str | None = None) -> dict:
    """雛形 `accounts.example/principal-hatena.json` から 1 本ぶんを組み立てる。

    `owner`（4.0 S4 §4.1）は `--owner` で渡したときだけ書く。渡さなければ雛形の値も落とす
    （owner 無し＝主体の名が owner＝今までと同じ範囲）——雛形の名を黙って写さない。
    """
    with open(example_path(), encoding="utf-8") as f:
        data = json.load(f)
    data.pop("owner", None)
    if owner is not None:
        data["owner"] = owner
    data["principal"] = name
    data["hatena_id"] = hatena_id
    data["token"] = f"~/.config/thth/{name}.token"
    data["consumer"] = f"~/.config/thth/{name}.consumer"
    if blogs:
        data["blogs"] = blogs
        data["bookmark"] = {"lanes": []}
    return data


def cmd_add(args) -> int:
    try:
        by = admin_log.actor(getattr(args, "by", None))
    except ValueError as exc:
        return _fail(str(exc))
    name = args.name
    if not accounts.name_is_safe(name):
        return _fail(f"invalid_principal_name: 主体の名前に使えない字が入っています: {name!r}"
                     "（英数字と `_`・`.`・`-` だけ）")
    if not args.hatena_id or not principals.HATENA_ID_RE.fullmatch(args.hatena_id):
        return _fail("hatena_id_invalid: --hatena-id が要ります（英数と `-`・`_` の 1〜32 字）")
    try:
        blogs = [_parse_blog(value) for value in (args.blog or [])]
    except ValueError:
        return _fail("blogs_invalid: --blog は <blog_id>=<lane>[,<lane>…]（blog_id は小文字のホスト名）")
    try:
        data = build_ledger(name, hatena_id=args.hatena_id, blogs=blogs, owner=getattr(args, "owner", None))
        principals.validate(name, data)
    except FileNotFoundError as e:
        return _fail(f"雛形がありません: {e}")
    except principals.PrincipalError as e:
        return _fail(f"{e.code}: {e}")
    path = principals.path_for(name)
    if os.path.exists(path):
        return _fail(f"既にあります。上書きしません: {path}")
    data["provenance"] = admin_log.provenance(by)
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as e:
        return _fail(f"主体の台帳を書けませんでした: {path}（{e.strerror or e}）")
    try:
        admin_log.append("principal_added", name, {"media": principals.MEDIA}, by=by,
                         diff=admin_log.difference({}, data))
    except (OSError, ValueError, admin_log.AdminLogError):
        # 変更ログに残せない書き込みは残さない（`admin_log.guarded` と同じ規律）。
        try:
            os.unlink(path)
        except OSError:
            pass
        return _fail("admin_change_refused: --by and a private writable event log are required")
    if getattr(args, "json", False):
        print(json.dumps({"path": path, "principal": data}, ensure_ascii=False))
        return 0
    print(f"書きました: {path}")
    for d in accounts.dummy_fields(data):
        print(f"**{d['field']} はダミーのままです**（{d['value']}）。{d['next']}")
    print(f"次の一手: token と consumer を置く（ssh -t で thth principal auth {name} --by <名前>）→ "
          f"thth account add <口座> --media hatenablog --project <lane> --principal {name} --blog <blog_id> --by <名前>")
    return 0


def status(name) -> dict:
    """主体の台帳・止め・今日の数・使う口座（読むだけ・秘密は「ある／ない」だけ）。"""
    from . import guard, jst
    p = principals.load_principal(name)
    now = jst.now_jst()
    counts = guard.principal_summary(name, p, now=now)
    halted = principals.stopped(name)
    return {
        "principal": name,
        "kind": p["kind"],
        "hatena_id": p["hatena_id"],
        "owner": principals.owner_of(name, p),
        "auth": p.get("auth", "oauth"),
        "token": "present" if os.path.exists(p["token"]) else "absent",
        "consumer": ("present" if p.get("consumer") and os.path.exists(p["consumer"]) else "absent"),
        "blogs": [{k: v for k, v in row.items()} for row in p["blogs"]],
        "bookmark_lanes": list((p.get("bookmark") or {}).get("lanes") or []),
        "limits": principals.limits(p),
        "stopped": ({"reason": halted.get("reason"), "stopped_at": halted.get("stopped_at")}
                    if halted is not None else None),
        "accounts": [acct for acct, _cfg in principals.accounts_of(name)],
        "today": {"date": now.date().isoformat(), "posts": counts["posts_today"],
                  "retracts": counts["retracts_today"], "bookmarks": counts["bookmarks_today"]},
    }


def cmd_show(args) -> int:
    try:
        row = status(args.name)
    except principals.PrincipalError as e:
        return _fail(f"{e.code}: {e}", json_output=getattr(args, "json", False))
    except accounts.AccountError:
        return _fail("ledger_unavailable", json_output=getattr(args, "json", False))
    if getattr(args, "json", False):
        print(json.dumps(row, ensure_ascii=False))
        return 0
    lim = row["limits"]
    print(f"{row['principal']}（{row['kind']}・はてな ID {row['hatena_id']}・owner {row['owner']}・auth {row['auth']}）")
    print(f"  token: {'ある' if row['token'] == 'present' else 'ない'}・consumer: "
          f"{'ある' if row['consumer'] == 'present' else 'ない'}")
    for blog in row["blogs"]:
        extra = "・".join(f"{k} {blog[k]}" for k in principals.BLOG_LIMIT_KEYS if k in blog)
        print(f"  ブログ {blog['blog_id']}: レーン {'・'.join(blog['lanes'])}" + (f"（{extra}）" if extra else ""))
    print(f"  ブックマークを打てるレーン: {'・'.join(row['bookmark_lanes']) or 'なし'}")
    print(f"  上限: 公開 {lim['daily_max_posts']}・削除 {lim['daily_max_retracts']}・"
          f"ブックマーク {lim['daily_max_bookmarks']}・急な連投 {lim['burst']['minutes']} 分に "
          f"{lim['burst']['count']} 件を超えたら止める")
    print(f"  ブックマークの枠: 自分の記事 1 日 {lim['daily_max_self_bookmarks']}・同じホスト 1 日 "
          f"{lim['bookmark_same_host_daily']}")
    print(f"  口座: {'・'.join(row['accounts']) or 'なし'}")
    t = row["today"]
    print(f"  今日（{t['date']}）: 公開 {t['posts']} 件・削除 {t['retracts']} 件・ブックマーク {t['bookmarks']} 件")
    if row["stopped"]:
        print(f"  → **止まっています**（{row['stopped']['reason']}・{row['stopped'].get('stopped_at') or '時刻不明'}）。"
              f"戻すのは thth principal resume {row['principal']} --by <名前>")
    else:
        print("  → 動いています")
    return 0


def _pairs(rest):
    if len(rest) == 3 and rest[0] == "burst":
        return [("burst_count", rest[1]), ("burst_minutes", rest[2])]
    if len(rest) == 2:
        return [(rest[0], rest[1])]
    raise ValueError("invalid_setting")


def _parse_value(key, text):
    from . import account_settings, hatena_fotolife
    if key in FOTOLIFE_KEYS:
        try:
            value = int(str(text).strip())
        except ValueError:
            raise ValueError("invalid_setting") from None
        if not hatena_fotolife.valid_limit(key.split(".", 1)[1], value):
            raise ValueError("invalid_setting")
        return value
    if key not in KEYS:
        raise ValueError("invalid_setting")
    if key in ("daily_max_bookmarks", "daily_max_self_bookmarks", "bookmark_same_host_daily"):
        value = account_settings.parse("daily_max_posts", text)
    else:
        value = account_settings.parse(key, text)
    return value


def change(name, key, value, *, by) -> dict:
    """主体の上限を 1 つ変える（運営者の CLI だけ・どちらの向きも）。"""
    admin_log.actor(by)
    after = _parse_value(key, value)
    p = principals.load_principal(name)
    before_limits = principals.limits(p)
    if key in FOTOLIFE_KEYS:
        from . import hatena_fotolife
        before = hatena_fotolife.limits(p)[key.split(".", 1)[1]]
    else:
        before = (before_limits["burst"]["count"] if key == "burst_count"
                  else before_limits["burst"]["minutes"] if key == "burst_minutes" else before_limits[key])
    if before == after:
        return {"principal": name, "key": key, "before": before, "after": after, "changed": False}
    path = principals.path_for(name)
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    original = json.loads(json.dumps(raw))
    if key in FOTOLIFE_KEYS:
        block = raw.get("fotolife") if isinstance(raw.get("fotolife"), dict) else {}
        raw["fotolife"] = dict(block, **{key.split(".", 1)[1]: after})
    elif key in ("burst_count", "burst_minutes"):
        burst = dict(before_limits["burst"])
        burst["count" if key == "burst_count" else "minutes"] = after
        raw["burst"] = burst
    else:
        raw[key] = after
    secrets_fs.atomic_write_json(path, raw)
    try:
        principals.load_principal(name)
    except accounts.AccountError:
        secrets_fs.atomic_write_json(path, original)
        raise ValueError("invalid_setting") from None
    admin_log.append("settings_set", name, {"media": principals.MEDIA}, by=by, via="cli",
                     diff={key: [before, after]})
    return {"principal": name, "key": key, "before": before, "after": after, "changed": True}


def cmd_set(args) -> int:
    from . import account_settings
    try:
        admin_log.actor(getattr(args, "by", None))
    except ValueError as exc:
        return _fail(str(exc))
    try:
        results = [change(args.name, key, value, by=args.by) for key, value in _pairs(list(args.rest or []))]
    except principals.PrincipalError as e:
        return _fail(f"{e.code}: {e}", json_output=getattr(args, "json", False))
    except (ValueError, account_settings.SettingsError):
        return _fail(f"invalid_setting: 名前は {'・'.join(KEYS + FOTOLIFE_KEYS)}（burst は `burst <件数> <分>` でも）",
                     json_output=getattr(args, "json", False))
    except (OSError, admin_log.AdminLogError):
        return _fail("settings_unavailable", json_output=getattr(args, "json", False))
    if getattr(args, "json", False):
        print(json.dumps(results if len(results) > 1 else results[0], ensure_ascii=False))
        return 0
    for row in results:
        if row["changed"]:
            print(f"{row['principal']}: {row['key']} {row['before']} → {row['after']}")
        else:
            print(f"{row['principal']}: {row['key']} は既に {row['after']} です（変えていません）")
    return 0


def cmd_resume(args) -> int:
    try:
        admin_log.actor(getattr(args, "by", None))
    except ValueError as exc:
        return _fail(str(exc))
    try:
        principals.load_principal(args.name)
        resumed = principals.resume(args.name, by=args.by, via="cli")
    except principals.PrincipalError as e:
        return _fail(f"{e.code}: {e}")
    except (OSError, ValueError):
        return _fail("guard_state_unavailable")
    if getattr(args, "json", False):
        print(json.dumps({"principal": args.name, "resumed": resumed}, ensure_ascii=False))
        return 0
    if resumed:
        print(f"{args.name}: 止めた印を外しました。この主体のブログの公開・更新・削除とブックマークを再び受け付けます")
    else:
        print(f"{args.name}: 止まっていません（何も変えていません）")
    return 0


# --------------------------------------------------------------------------
# `thth principal auth`（設計 4.0 S2 §2.4）。運営者の CLI だけ（MCP・thth.me に出さない）。
# --------------------------------------------------------------------------

INITIATE_URL = "https://www.hatena.com/oauth/initiate"
TOKEN_URL = "https://www.hatena.com/oauth/token"
AUTHORIZE_URL = "https://www.hatena.ne.jp/oauth/authorize"
# scope は 4 つ固定（手順 §3・設計 §2.4。S3・S4 で分かったら変える）。
SCOPES = ("read_public", "write_public", "read_private", "write_private")
NOT_A_TTY = ("not_a_tty: 標準入力が端末ではありません。consumer secret と確認の文字列が画面に出てしまうので"
             "読みません。\n  ssh に -t を付けてください（例: ssh -t wt '...thth principal auth <主体> --by <名前>'）")


def _getpass(label: str) -> str:
    import getpass
    return getpass.getpass(label)


def _http(method, url, headers, body):
    """`(status, headers, bytes)`。4xx・5xx も status で返す（本文は読み捨てる）。

    つながらない（DNS・接続拒否・時間切れ・接続の前の断り）は status `None`（traceback にしない。
    例外の文は値を含みうるので出さない）。
    """
    import http.client
    import urllib.error
    import urllib.request
    from . import httpsafe
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with httpsafe.urlopen(req, timeout=30) as response:
            return response.status, dict(response.headers.items()), response.read(65537)
    except urllib.error.HTTPError as exc:
        return exc.code, {}, b""
    except (urllib.error.URLError, OSError, TimeoutError, httpsafe.EndpointRejected,
            http.client.HTTPException):
        return None, {}, b""


def _status_text(status) -> str:
    """失敗の文の括弧の中。つながらなければ「つながらない」と言う。"""
    if status is None:
        return "はてなにつながりません。ネットワーク・DNS・時間切れを確かめてください"
    return f"HTTP {status}"


def _form(raw) -> dict:
    import urllib.parse
    try:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    except UnicodeDecodeError:
        return {}
    return dict(urllib.parse.parse_qsl(text, keep_blank_values=True))


def _secret_ok(value) -> bool:
    return (isinstance(value, str) and bool(value) and len(value) <= 1024
            and not any(ord(c) < 32 or ord(c) == 127 or c.isspace() for c in value))


def _user_agent(principal_cfg) -> str:
    from . import __version__
    return f"thth/{__version__} (+{principal_cfg.get('user_agent_contact') or 'https://thth.me/'})"


def _signed_post(http, url, *, consumer, principal_cfg, token=None, token_secret=None,
                 extra_oauth=None, form=None):
    import urllib.parse
    from . import oauth1
    headers = {"Authorization": oauth1.authorization_header(
        consumer_key=consumer["consumer_key"], consumer_secret=consumer["consumer_secret"],
        token=token, token_secret=token_secret, method="POST", url=url,
        body_params=list((form or {}).items()), extra_oauth=extra_oauth),
        "User-Agent": _user_agent(principal_cfg)}
    body = None
    if form:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = urllib.parse.urlencode(form, quote_via=urllib.parse.quote, safe="").encode("ascii")
    return http("POST", url, headers, body)


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def probe_blogs(name, principal_cfg, *, out=print) -> bool:
    """段 7: 主体の `blogs[]` を順に一覧の GET で読む。全部読めたら True。"""
    from .adapters import hatenablog
    from . import hatena_publish
    ok = True
    for row in principal_cfg["blogs"]:
        blog_id = row["blog_id"]
        try:
            token = hatenablog.check_token(_read_json(principal_cfg["token"]), principal_cfg["hatena_id"])
            consumer = hatenablog.read_consumer(principal_cfg.get("consumer"))
            adapter = hatenablog.HatenaBlogAdapter({"blog_id": blog_id, "timeout": 30}, principal_cfg,
                                                   token=token, consumer=consumer)
            result = adapter.probe()
        except (hatena_publish.AdapterUnavailable, OSError, ValueError) as exc:
            result = {"ok": False, "status": None, "error": getattr(exc, "code", exc.__class__.__name__)}
        if result["ok"]:
            syntax = result["syntax"] or "不明（記事が無い）"
            out(f"ブログ {blog_id}: 読めました（記事 {result['entries']} 件・記法 {syntax}）")
        else:
            ok = False
            what = result.get("status") or "応答なし"
            out(f"ブログ {blog_id}: 読めません（{what}・{result.get('error')}）。"
                "blog_id の打ち違いか、まだブログが無いかを確かめてください（token は残しました）")
    return ok


def run_auth(name, *, by, force=False, forget=False, forget_consumer=False, consumer=False,
             prompts=None, http=None, isatty=None, out=print, err=None) -> int:
    """`thth principal auth` の本体（§2.4 の段 0〜7）。

    `prompts(label) -> str`（本番は `getpass`）・`http(method, url, headers, body) -> (status, headers, bytes)`
    （本番は `httpsafe.urlopen`）・`isatty() -> bool` を差し込める（試験用）。値はどこにも出さない。
    """
    import urllib.parse
    from . import jst, redact
    from .adapters import hatenablog
    from .lock import LockBusy
    err = err or (lambda line: print(line, file=sys.stderr))
    prompts = prompts or _getpass
    http = http or _http
    isatty = isatty or sys.stdin.isatty
    try:
        admin_log.actor(by)
    except ValueError as exc:
        err(str(exc))
        return 2
    try:
        p = principals.load_principal(name)
    except principals.PrincipalError as e:
        err(f"{e.code}: {e}")
        return 2
    if p.get("auth", "oauth") != "oauth":
        err("auth_unsupported: この主体の auth は oauth ではありません（wsse は作っていません）")
        return 2
    token_path, consumer_path = p["token"], p.get("consumer")
    subject = {"media": principals.MEDIA}

    # --forget／--forget-consumer（端末は要らない・値は読まない）
    if forget or forget_consumer:
        try:
            with principals.token_lock(name):
                gone = principals.forget_token(name, consumer=forget_consumer, locked=True)
        except LockBusy:
            err("principal_token_busy: ほかの処理が token を書いています。少し待ってから")
            return 2
        diff = {"token": [gone["token"], False]}
        if forget_consumer:
            diff["consumer"] = [gone["consumer"], False]
        admin_log.append("principal_auth_forgotten", name, subject, by=by, diff=diff)
        out(f"消しました: token {'あり→なし' if gone['token'] else '（もともと無い）'}"
            + (f"・consumer {'あり→なし' if gone['consumer'] else '（もともと無い）'}" if forget_consumer else "")
            + f"。口座の道は token_missing で出ない側です。入れ直すのは thth principal auth {name} --by <名前>")
        return 0

    # 段 0: 端末でなければ読まない
    if not isatty():
        err(NOT_A_TTY)
        return 2
    if not isinstance(consumer_path, str) or not consumer_path:
        err("consumer_missing: 主体の台帳に consumer のパスがありません")
        return 2
    token_present = os.path.exists(token_path)
    # 段 2（先に見る）: token があって --force が無ければ、--consumer のときを除いて何も書かない
    if token_present and not force and not consumer:
        err(f"token_present: token が既にあります。入れ替えるなら --force、消すなら --forget"
            f"（thth principal auth {name} --force --by <名前>）")
        return 2
    lock = principals.token_lock(name)
    try:
        lock.__enter__()
    except LockBusy:
        err("principal_token_busy: ほかの処理が token を書いています。少し待ってから")
        return 2
    try:
        # 段 1: consumer
        if consumer or not os.path.exists(consumer_path):
            key = (prompts("consumer key（表示されません）: ") or "").strip()
            secret = (prompts("consumer secret（表示されません）: ") or "").strip()
            if not _secret_ok(key) or not _secret_ok(secret):
                err("consumer_invalid: consumer key／secret が空か、空白・制御文字が入っています。何も書いていません")
                return 2
            redact.register_secret(key)
            redact.register_secret(secret)
            secrets_fs.atomic_write_json(consumer_path, {"schema": hatenablog.CONSUMER_SCHEMA,
                                                         "consumer_key": key, "consumer_secret": secret})
            out(f"consumer を書きました: {consumer_path}")
            if consumer and token_present and not force:
                admin_log.append("principal_auth", name, subject, by=by, diff={"consumer": [True, True]})
                out("token は触っていません（入れ替えるなら --force）")
                return 0
        try:
            consumer_data = hatenablog.read_consumer(consumer_path)
        except Exception as exc:  # noqa: BLE001 — AdapterUnavailable（符丁だけ言う）
            err(f"{getattr(exc, 'code', 'consumer_invalid')}: consumer のファイルが読めません。"
                "--consumer で入れ直してください")
            return 2
        for value in (consumer_data["consumer_key"], consumer_data["consumer_secret"]):
            redact.register_secret(value)

        # 段 3: request token（oob・scope は form の body）
        out("はてなに request token を頼みます…")
        status, _h, raw = _signed_post(http, INITIATE_URL, consumer=consumer_data, principal_cfg=p,
                                       extra_oauth={"oauth_callback": "oob"},
                                       form={"scope": ",".join(SCOPES)})
        temp = _form(raw) if status == 200 else {}
        if (temp.get("oauth_callback_confirmed") != "true" or not _secret_ok(temp.get("oauth_token"))
                or not _secret_ok(temp.get("oauth_token_secret"))):
            err(f"initiate_failed: request token をもらえませんでした（{_status_text(status)}）。"
                "consumer key／secret と、時計のずれを確かめてください")
            return 1
        redact.register_secret(temp["oauth_token_secret"])

        # 段 4: 認可の URL（PC 用）
        out(f"次の URL をブラウザで開き、はてな ID {p['hatena_id']} でログインして許可してください:")
        out(f"{AUTHORIZE_URL}?oauth_token={urllib.parse.quote(temp['oauth_token'], safe='')}")

        # 段 5: verifier → access token
        verifier = (prompts("画面に出た確認の文字列（oauth_verifier・表示されません）: ") or "").strip()
        if not _secret_ok(verifier):
            err("verifier_invalid: 確認の文字列が空か、空白・制御文字が入っています。token は書いていません")
            return 2
        redact.register_secret(verifier)
        status, _h, raw = _signed_post(http, TOKEN_URL, consumer=consumer_data, principal_cfg=p,
                                       token=temp["oauth_token"], token_secret=temp["oauth_token_secret"],
                                       extra_oauth={"oauth_verifier": verifier})
        got = _form(raw) if status == 200 else {}
        if not _secret_ok(got.get("oauth_token")) or not _secret_ok(got.get("oauth_token_secret")):
            err(f"token_failed: access token をもらえませんでした（{_status_text(status)}）。もう一度最初から")
            return 1
        redact.register_secret(got["oauth_token"])
        redact.register_secret(got["oauth_token_secret"])
        if got.get("url_name") != p["hatena_id"]:
            err(f"hatena_id_mismatch: 許可したはてな ID が主体の hatena_id（{p['hatena_id']}）と違います。"
                "token は書いていません。正しい ID でログインし直してください")
            return 1

        # 段 6: 書く（token.lock の中・0600）
        data = {"schema": hatenablog.TOKEN_SCHEMA, "hatena_id": p["hatena_id"],
                "oauth_token": got["oauth_token"], "oauth_token_secret": got["oauth_token_secret"],
                "scopes": list(SCOPES), "obtained_at": jst.iso()}
        path = principals.write_token_locked(name, data)
        admin_log.append("principal_auth", name, subject, by=by,
                         diff={"token": [token_present, True], "scopes": [None, list(SCOPES)],
                               "url_name_matches": [None, True]})
        out(f"token を書きました: {path}")
    finally:
        lock.__exit__(None, None, None)

    # 段 7: 各ブログが読めるか（read_private の確かめ）。1 つでも読めなければ rc 1（token は残す）。
    return 0 if probe_blogs(name, principals.load_principal(name), out=out) else 1


def cmd_auth(args) -> int:
    """`thth principal auth <主体> --by <名前> [--force|--forget|--forget-consumer] [--consumer]`。"""
    return run_auth(args.name, by=args.by, force=args.force, forget=args.forget,
                    forget_consumer=args.forget_consumer, consumer=args.consumer)


def register(sub) -> None:
    """`build_parser()` に `principal` を足す（**cli.py に足すのはこの呼び出しの 1 行だけ**）。"""
    p = sub.add_parser("principal", help="主体（はてな ID）の台帳を書く・見る・上限を変える・止めたのを戻す（CLI だけ）",
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    verbs = p.add_subparsers(dest="principal_verb", required=True)
    p_add = verbs.add_parser("add", help="雛形から主体の台帳を 1 本書く")
    p_add.add_argument("name")
    p_add.add_argument("--hatena-id", dest="hatena_id", required=True)
    p_add.add_argument("--blog", action="append",
                       help="<blog_id>=<lane>[,<lane>…]（何度でも）。省くと雛形のダミー（demo.hatenablog.com）")
    p_add.add_argument("--owner", default=None,
                       help="主体を束ねる名（例 masaru・4.0 S4）。同じ owner の主体は同じページを 1 回だけブックマークする")
    p_add.add_argument("--by", required=True)
    p_add.add_argument("--json", action="store_true")
    p_add.set_defaults(func=cmd_add)
    p_show = verbs.add_parser("show", help="台帳・止め・今日の数・使う口座（秘密は出さない）")
    p_show.add_argument("name")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)
    p_set = verbs.add_parser("set", help="主体の上限を変える")
    p_set.add_argument("name")
    p_set.add_argument("rest", nargs="+", help="<名前> <値>（" + "・".join(KEYS + FOTOLIFE_KEYS) + "）")
    p_set.add_argument("--by", required=True)
    p_set.add_argument("--json", action="store_true")
    p_set.set_defaults(func=cmd_set)
    p_resume = verbs.add_parser("resume", help="止めた主体を戻す（MCP からはできない）")
    p_resume.add_argument("name")
    p_resume.add_argument("--by", required=True)
    p_resume.add_argument("--json", action="store_true")
    p_resume.set_defaults(func=cmd_resume)
    p_auth = verbs.add_parser("auth", help="はてなの OAuth で token を取る（端末から・ssh -t。MCP からはできない）")
    p_auth.add_argument("name")
    p_auth.add_argument("--by", required=True)
    mode = p_auth.add_mutually_exclusive_group()
    mode.add_argument("--force", action="store_true", help="token があっても取り直す")
    mode.add_argument("--forget", action="store_true", help="token を消す")
    mode.add_argument("--forget-consumer", dest="forget_consumer", action="store_true",
                      help="token と consumer を消す")
    p_auth.add_argument("--consumer", action="store_true", help="consumer key／secret を入れ直す（token は触らない）")
    p_auth.set_defaults(func=cmd_auth)
