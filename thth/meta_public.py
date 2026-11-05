"""計画 Meta 全部盛り M3: 読むだけの最小の口（審査の録画に映す）。

    thth embed <口座> <投稿の URL> [--json]     公開の投稿の埋め込みの HTML（Threads・Facebook・Instagram）
    thth upcoming <IG の口座> [--json]          その口座が主催する予定（Instagram）

`thth where` の Instagram（ハッシュタグ）・Facebook（ページの検索）は `where_cli.py` が呼び、ここは
ハッシュタグの 7 日 30 種の柵を持つ。

**保存**: 答えは画面に出すだけ。残すのは次の 2 つだけで、どちらも privacy の「run logs は検索語と件数まで」の中:

  - runs の 1 行（命令の名前・媒体・件数）。URL・本文・名前は入れない。
  - `state/<口座>/ig_hashtags.json`: 調べたタグと最初の時刻だけ（`{"tags": {タグ: 時刻}}`）。7 日で消す。
    7 日 30 種は Meta が IG の口座ごとに数える（L2: ig-hashtag/top-media「Requesting this edge counts
    against that limit, even if you already have the hashtag ID」）ので、網に出る前に数える。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import urllib.parse

from . import accounts as accounts_mod
from . import jst, refusal, runs as runs_mod, secrets_fs
from . import threads_read_cli as reads

HASHTAG_FILE = "ig_hashtags.json"
HASHTAG_LIMIT = 30
HASHTAG_WINDOW = datetime.timedelta(days=7)
# 空白・`#` と、URL や問い合わせを組める字を通さない。
HASHTAG_RE = re.compile(r"[^\s#@&?/\\,;]{1,100}")
URL_MAX = 2000
EMBED_HOSTS = {
    "threads": ("threads.net", "www.threads.net", "threads.com", "www.threads.com"),
    "facebook": ("facebook.com", "www.facebook.com", "m.facebook.com"),
    "instagram": ("instagram.com", "www.instagram.com"),
}


class Refused(Exception):
    """静的な符丁の断り（`code`）。`next_at` は通るようになる時刻。"""

    def __init__(self, code, message, *, next_at=None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.next_at = next_at


def _refuse(code, message, *, next_at=None):
    raise Refused(code, message, next_at=next_at)


# ------------------------------------------------------------------ ハッシュタグの柵

def hashtag(word: str) -> str:
    """語を Meta に渡すタグに（`#` を外し、大文字小文字を畳む）。タグにならない語は断る。"""
    tag = str(word or "").strip().lstrip("#").casefold()
    if not HASHTAG_RE.fullmatch(tag):
        _refuse("ig_hashtag_invalid", f"タグにならない語です: {word!r}")
    return tag


def _hashtag_path(account: str) -> str:
    return os.path.join(accounts_mod.state_dir_for(account), HASHTAG_FILE)


def _load_hashtags(path: str, now) -> dict:
    """7 日の内のタグ（`{タグ: 時刻}`）。壊れていれば数えられないので断る（0 種として通さない）。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        _refuse("ig_hashtag_ledger_unreadable", f"{HASHTAG_FILE} が読めません")
    tags = data.get("tags") if isinstance(data, dict) else None
    if not isinstance(tags, dict):
        _refuse("ig_hashtag_ledger_unreadable", f"{HASHTAG_FILE} の形が違います")
    out = {}
    for tag, at in tags.items():
        stamp = jst.parse(at)
        if stamp is None:
            _refuse("ig_hashtag_ledger_unreadable", f"{HASHTAG_FILE} の時刻が読めません")
        if now - stamp < HASHTAG_WINDOW:
            out[tag] = at
    return out


def reserve_hashtags(account: str, tags: list, *, now) -> dict:
    """今回のタグを 7 日の枠に入れる。**31 種目は網に出る前に断る**（1 つでも越えれば 1 つも入れない）。

    失敗した問い合わせも数える（Meta の側で数えられている見込みなので、少なく数えない）。
    戻りは `{"used", "limit"}`。
    """
    path = _hashtag_path(account)
    known = _load_hashtags(path, now)
    fresh = [tag for tag in dict.fromkeys(tags) if tag not in known]
    used = len(known) + len(fresh)
    if used > HASHTAG_LIMIT:
        oldest = min(jst.parse(at) for at in known.values()) if known else now
        _refuse("ig_hashtag_limit",
                f"この 7 日で {len(known)} 種を調べました（Meta の上限は {HASHTAG_LIMIT} 種）。"
                f"今回の新しいタグは {len(fresh)} 種です",
                next_at=jst.iso(oldest + HASHTAG_WINDOW))
    known.update({tag: jst.iso(now) for tag in fresh})
    secrets_fs.atomic_write_json(path, {"tags": known})
    return {"used": used, "limit": HASHTAG_LIMIT}


# ------------------------------------------------------------------ embed

def check_embed_url(media, url) -> str:
    """その口座の媒体の https の公開 URL だけを通す（鍵を持って他の宛先の話をしない）。"""
    text = str(url or "").strip()
    try:
        parts = urllib.parse.urlsplit(text)
        host, port = parts.hostname, parts.port
    except ValueError:
        host, port, parts = None, None, None
    if (parts is None or len(text) > URL_MAX or parts.scheme != "https" or parts.username or parts.password
            or port not in (None, 443) or host not in EMBED_HOSTS.get(media, ())):
        _refuse("embed_url_invalid", f"{media} の投稿の https の URL ではありません")
    return text


def embed_json(account: str, url: str, embed: dict) -> dict:
    return {"account": account, "url": url, "embed": embed, "storage": "display_only"}


def show_embed(value: dict) -> int:
    embed = value["embed"]
    print(f"{value['account']}  埋め込み（{embed.get('provider_name') or '—'}・保存しません）")
    print(embed["html"])
    return 0


def cmd_embed(args) -> int:
    as_json = bool(args.json)
    try:
        media = accounts_mod.load_account(args.account).get("media")
    except accounts_mod.AccountError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        url = check_embed_url(media, args.url)
    except Refused as exc:
        return refusal.emit(args, exc.code, line=str(exc))

    def render(embed):
        value = embed_json(args.account, url, embed)
        if as_json:
            print(json.dumps(value, ensure_ascii=False, indent=2))
            return 0
        return show_embed(value)

    rc = reads._run(args.account, capability="embed", call=lambda adapter: adapter.oembed(url),
                    as_json=as_json, render=render)
    record_read(args.account, "embed", media, rc == 0)
    return rc


# ------------------------------------------------------------------ upcoming

def upcoming_json(account: str, events: list) -> dict:
    return {"account": account, "events": events, "storage": "display_only"}


def show_upcoming(value: dict) -> int:
    print(f"{value['account']}  予定 {len(value['events'])} 件（広告マネージャか API で作った物だけ・保存しません）")
    for row in value["events"]:
        print(f"  {row.get('start_time') or '—'}  {row.get('title') or '—'}  id={row.get('id') or '—'}")
    return 0


def cmd_upcoming(args) -> int:
    as_json = bool(args.json)

    def render(events):
        value = upcoming_json(args.account, events)
        if as_json:
            print(json.dumps(value, ensure_ascii=False, indent=2))
            return 0
        return show_upcoming(value)

    rc = reads._run(args.account, capability="upcoming_events", call=lambda adapter: adapter.upcoming_events(),
                    as_json=as_json, render=render)
    record_read(args.account, "upcoming_events", "instagram", rc == 0)
    return rc


def record_read(account: str, action: str, media, ok: bool) -> None:
    """runs に 1 行（命令の名前と媒体だけ）。読めない口座では書かない（断りは呼ぶ前に出ている）。"""
    try:
        runs_mod.record_minimal(account, {"action": action, "account": account, "medium": media,
                                          "status": "ok" if ok else "error", "error": None})
    except (OSError, accounts_mod.AccountError):
        pass


def register(sub) -> None:
    p = sub.add_parser("embed", help="公開の投稿の埋め込みの HTML（Threads・Facebook・Instagram の oEmbed・読むだけ・保存しない）")
    p.add_argument("account", metavar="ACCOUNT")
    p.add_argument("url", metavar="URL", help="その口座の媒体の投稿の https の URL")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_embed)
    q = sub.add_parser("upcoming", help="Instagram の口座が主催する予定（読むだけ・保存しない）")
    q.add_argument("account", metavar="ACCOUNT")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_upcoming)
