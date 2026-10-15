"""`thth principal add|show|set|resume|auth`（設計 4.0 S1 §3.2・§4.1・§4.2・§4.4）。

主体（はてな ID 1 つ）の台帳を書く・見る・上限を変える・止めたのを戻す口。どれも
**運営者の CLI だけ**——MCP の道具にも thth.me（/activity）にも出さない（T-2f）。
暴走して止まった LLM が自分で戻せないように（`guard.resume` と同じ考え）。

    thth principal add <name> --hatena-id <id> [--blog <blog_id>=<lane>[,<lane>…]]… --by <名前>
    thth principal show <name> [--json]
    thth principal set <name> <名前> <値> --by <名前>   （burst は `burst <件数> <分>` でも）
    thth principal resume <name> --by <名前>
    thth principal auth <name>                          （S2 で中身を入れる。今は rc 2）

**秘密の値は扱わない**（token・consumer はパスだけ。`show` は「ある／ない」だけ言う）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import accounts, admin_log, principals, secrets_fs

# `thth principal set` で変えられる名前（`thth account set` と同じ語）。
KEYS = ("daily_max_posts", "daily_max_retracts", "daily_max_bookmarks", "burst_count", "burst_minutes")


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


def build_ledger(name: str, *, hatena_id: str, blogs: list | None = None) -> dict:
    """雛形 `accounts.example/principal-hatena.json` から 1 本ぶんを組み立てる。"""
    with open(example_path(), encoding="utf-8") as f:
        data = json.load(f)
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
        data = build_ledger(name, hatena_id=args.hatena_id, blogs=blogs)
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
    print(f"次の一手: token と consumer を置く（thth principal auth {name}・S2）→ "
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
    print(f"{row['principal']}（{row['kind']}・はてな ID {row['hatena_id']}・auth {row['auth']}）")
    print(f"  token: {'ある' if row['token'] == 'present' else 'ない'}・consumer: "
          f"{'ある' if row['consumer'] == 'present' else 'ない'}")
    for blog in row["blogs"]:
        extra = "・".join(f"{k} {blog[k]}" for k in principals.BLOG_LIMIT_KEYS if k in blog)
        print(f"  ブログ {blog['blog_id']}: レーン {'・'.join(blog['lanes'])}" + (f"（{extra}）" if extra else ""))
    print(f"  ブックマークを打てるレーン: {'・'.join(row['bookmark_lanes']) or 'なし'}")
    print(f"  上限: 公開 {lim['daily_max_posts']}・削除 {lim['daily_max_retracts']}・"
          f"ブックマーク {lim['daily_max_bookmarks']}・急な連投 {lim['burst']['minutes']} 分に "
          f"{lim['burst']['count']} 件を超えたら止める")
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
    from . import account_settings
    if key not in KEYS:
        raise ValueError("invalid_setting")
    if key == "daily_max_bookmarks":
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
    before = (before_limits["burst"]["count"] if key == "burst_count"
              else before_limits["burst"]["minutes"] if key == "burst_minutes" else before_limits[key])
    if before == after:
        return {"principal": name, "key": key, "before": before, "after": after, "changed": False}
    path = principals.path_for(name)
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    original = json.loads(json.dumps(raw))
    if key in ("burst_count", "burst_minutes"):
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
        return _fail(f"invalid_setting: 名前は {'・'.join(KEYS)}（burst は `burst <件数> <分>` でも）",
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


def cmd_auth(args) -> int:
    """認可のやり直し（S2 で中身を入れる）。今は何もせず rc 2。"""
    print(f"principal_auth_unimplemented: thth principal auth はまだありません（4.0 の S2 で入れます）。"
          f"{args.name} の token は何も変えていません", file=sys.stderr)
    return 2


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
    p_add.add_argument("--by", required=True)
    p_add.add_argument("--json", action="store_true")
    p_add.set_defaults(func=cmd_add)
    p_show = verbs.add_parser("show", help="台帳・止め・今日の数・使う口座（秘密は出さない）")
    p_show.add_argument("name")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)
    p_set = verbs.add_parser("set", help="主体の上限を変える")
    p_set.add_argument("name")
    p_set.add_argument("rest", nargs="+", help="<名前> <値>（" + "・".join(KEYS) + "）")
    p_set.add_argument("--by", required=True)
    p_set.add_argument("--json", action="store_true")
    p_set.set_defaults(func=cmd_set)
    p_resume = verbs.add_parser("resume", help="止めた主体を戻す（MCP からはできない）")
    p_resume.add_argument("name")
    p_resume.add_argument("--by", required=True)
    p_resume.add_argument("--json", action="store_true")
    p_resume.set_defaults(func=cmd_resume)
    p_auth = verbs.add_parser("auth", help="認可のやり直し（4.0 の S2 で入れる。今は rc 2）")
    p_auth.add_argument("name")
    p_auth.set_defaults(func=cmd_auth)
