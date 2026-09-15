"""Threads の 11 権限を使う**読み取りの口 3 つ**（設計 v2 §4.3・v2.1-A・2026-09-14）。

  - `thth topics <account> --search <語> [--recent] [--json]`（`threads_keyword_search`）
  - `thth mentions <account> [--json]`（`threads_manage_mentions`）
  - `thth profile <account> <username> [--json]`（`threads_profile_discovery`）

`--search` は**観測の材料**（誰がどれだけ居るか）に加えて、**絡みに行く先**
（`post_id`・`permalink`・投稿ごとの返信・「もう返した」印）を指す（設計 v2 §4.4・
2026-09-15）。**棚（`topics.json`）に書く内容は変えない**——`post_id` は棚にも
泉にも落ちない。

**`thth/cli.py` の `build_parser()` に足すのは `threads_read_cli.register(sub)` の
1 行だけ**（`ask_cli` と同じ筋）。`--search`/`--recent` は `topics` の既存 parser の
flag で、`cli._cmd_topics()` の入口からここへ来る。

**読むだけ。** どの口も GET しか呼ばない。検索結果の**本文はどこにも保存しない**
（§4.3「入れないもの」——泉に落ちないものは手元にも溜めない）。観測の材料を
並べるだけで、`--note` の `--status`/`--audience` の判断は人がする。

exit code:
  - `0` … 引けた（0 件でも 0）
  - `1` … 台帳が無い・壊れている・トークンが無い・媒体に口が無い・API が断った
  - `2` … **権限がトークンに乗っていない**（`thth auth <account>` をやり直す・受け入れ (c)）
"""
from __future__ import annotations

import collections
import json
import os
import sys
import unicodedata

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import queuefile
from . import redact as redact_mod
from .adapters import base as adapter_base

# 人向け画面に出す本文の長さ（先頭から）。**表示するだけで保存しない。**
TEXT_PREVIEW_CHARS = 60
# 「上位 N 投稿者の占有率」の N。
TOP_AUTHORS = 3

# **投稿ごとの返信の数を持ちうる鍵**（設計 v2 §4.4）。媒体によって綴りが違うので
# 順に見て、**最初に見つかった整数だけ**を使う。どれも無ければ数は作らない
# ——`has_replies`（真偽）が判れば `有` / `無`、それも無ければ `—`。
# **`n/a` を 0 と混ぜない**（設計 v1 §3.2.2）。
REPLY_COUNT_KEYS = ("replies", "reply_count", "replies_count", "reply_counts")

# 「もう返した」印（設計 v2 §4.4）。**強い順**——同じ先に複数の原稿があれば
# 先にあるものを出す。`withdrawn` は入れない（取り下げた原稿は「返した」ではない）。
REPLIED_STATUSES = ("posted", "approved", "draft")
REPLIED_MARKS = {"posted": "返信済", "approved": "承認済", "draft": "下書き"}

# **権限が乗っていないときの断り**（設計 v2 §4.3 受け入れ (c)）。
REAUTH_HINT = "`thth auth {account}` をやり直してください"

# 媒体に口が無いときの断りに使う和語（設計 v2 §4.4「媒体差」）。
CAPABILITY_LABELS = {
    "keyword_search": "語による公開投稿の検索",
    "mentions": "自分への言及の取得",
    "profile_lookup": "公開プロフィールの参照",
}


def register(sub) -> None:
    """`thth mentions` / `thth profile` を親の subparsers にぶら下げる。"""
    p_m = sub.add_parser(
        "mentions",
        help="自分への言及を一覧する（Threads・読むだけ・threads_manage_mentions）")
    p_m.add_argument("account")
    p_m.add_argument("--since", default=None,
                     help="この時刻以降（Unix 時刻か ISO の日付・資料どおり API に渡す）")
    p_m.add_argument("--json", action="store_true")
    p_m.set_defaults(func=cmd_mentions)

    p_p = sub.add_parser(
        "profile",
        help="公開プロフィールを引く（Threads・読むだけ・threads_profile_discovery）")
    p_p.add_argument("account")
    p_p.add_argument("username", help="引く相手の handle（@ は付けても付けなくてもよい）")
    p_p.add_argument("--json", action="store_true")
    p_p.set_defaults(func=cmd_profile)

    # `topics` の既存 parser に `--search`/`--recent` を足す。**`build_parser()` に
    # 足す行を 1 行に保つ**ため、ここから `sub.choices` 経由で触る（`topics` が
    # 先に登録されている前提。無ければ足さない——黙って通すのではなく、
    # `thth topics --search` が「そんな引数は無い」で止まる）。
    p_topics = getattr(sub, "choices", {}).get("topics")
    if p_topics is not None:
        register_topics_flags(p_topics)


def register_topics_flags(p_topics) -> None:
    """`topics` の既存 parser に `--search`/`--recent` を足す（他の flag は触らない）。"""
    p_topics.add_argument(
        "--search", default=None, metavar="語",
        help="語で公開投稿を検索し、観測の材料（投稿者の異なり数・直近の時刻・"
             "タグ付きの割合）と、絡みに行く先（post_id・permalink・投稿ごとの"
             "返信・queue に reply_to がある投稿の印）を出す。本文は表示する"
             "だけで保存しない（threads_keyword_search）")
    p_topics.add_argument(
        "--recent", action="store_true",
        help="--search と併用: TOP でなく RECENT（新しい順）で検索する")


# ---------------------------------------------------------------- 共通

def _pad(text, width: int) -> str:
    """表示幅で詰める（全角は 2）。`cli._pad()` と同じ規則（循環 import を避けて写し）。"""
    text = str(text)
    w = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)
    return text + " " * max(0, width - w)


def _one_line(text, n: int = TEXT_PREVIEW_CHARS) -> str:
    """本文を 1 行・先頭 n 字に。**画面に出すためだけ。**"""
    if not isinstance(text, str):
        return ""
    flat = " ".join(text.split())
    return flat[:n] + ("…" if len(flat) > n else "")


def _adapter_for(account: str, *, capability: str):
    """台帳とトークンから、その口を持つアダプタを 1 つ作る。

    戻りは `(adapter, None)` か `(None, 断りの文)`。**媒体名で分岐しない**——
    `capabilities()` の語で塞ぐ（`account_report.fetch_posts()` と同じ筋）。
    """
    account_cfg = accounts_mod.load_account(account)
    media = account_cfg.get("media")
    if capability not in adapters_mod.capabilities_for(media):
        # **黙って空にしない**（設計 v2 §4.4）。「この媒体には無い」と言って rc≠0。
        label = CAPABILITY_LABELS.get(capability, capability)
        return None, (f"{account}: この媒体（{media}）ではこの口"
                      f"（{label}・{capability}）は未対応です")
    token = accounts_mod.load_token(account_cfg)
    if not adapters_mod.adapter_class(media).has_token(token):
        return None, f"{account}: token が無いので引けません"
    return adapters_mod.make_adapter(account_cfg, token), None


def _not_granted(account: str, e: adapter_base.PermissionMissing) -> str:
    return (f"{e.permission} がトークンに乗っていません。"
            + REAUTH_HINT.format(account=account)
            + (f"（{e.detail}）" if e.detail else ""))


def _run(account: str, *, capability: str, call, as_json: bool, render) -> int:
    """口を 1 つ叩いて出す。失敗の種類ごとに rc を分ける（module docstring）。"""
    try:
        adapter, why = _adapter_for(account, capability=capability)
    except accounts_mod.AccountError as e:
        print(str(e), file=sys.stderr)
        return 1
    if adapter is None:
        print(why, file=sys.stderr)
        return 1
    try:
        result = call(adapter)
    except adapter_base.PermissionMissing as e:
        # **loud に断る**（受け入れ (c)）。500 を黙って返さない・0 件にしない。
        message = _not_granted(account, e)
        if as_json:
            print(json.dumps({"error": message, "permission": e.permission,
                              "account": account}, ensure_ascii=False, indent=2))
        else:
            print(message, file=sys.stderr)
        return 2
    except adapter_base.AdapterError as e:
        message = f"{account}: {redact_mod.redact(str(e))}"
        if as_json:
            print(json.dumps({"error": message, "account": account},
                             ensure_ascii=False, indent=2))
        else:
            print(message, file=sys.stderr)
        return 1
    return render(result)


# ---------------------------------------------------------------- --search

def search_material(rows: list, *, q: str, search_type: str, limit: int) -> dict:
    """検索結果から**観測の材料**を数える（純粋関数・本文を持たない）。

    返す dict に**本文は入らない**（`--json` にも出ない・§4.3「入れないもの」）。
    数はすべて分母つき。`topic_tag` が 1 行にも無ければ「タグ付きの割合」は
    `None`（判らない）——**0% と混ぜない**。
    """
    n = len(rows)
    authors = collections.Counter()
    for r in rows:
        key = r.get("author_key") or adapter_base.author_key(
            r.get("medium") or "threads", r.get("username"))
        if key:
            authors[key] += 1
    with_author = sum(authors.values())
    distinct = len(authors)
    top = authors.most_common(TOP_AUTHORS)
    top_share = (sum(c for _, c in top) / with_author) if with_author else None

    stamps = sorted((r.get("timestamp") for r in rows
                     if isinstance(r.get("timestamp"), str)), reverse=True)
    tagged_known = [r for r in rows if "topic_tag" in r]
    tagged = sum(1 for r in tagged_known if r.get("topic_tag"))
    tagged_ratio = (tagged / len(tagged_known)) if tagged_known else None
    replies = sum(1 for r in rows if r.get("is_reply") is True)

    return {
        "q": q,
        "search_type": search_type,
        "requested_limit": limit,
        "n": n,
        # **1 頁・要求上限 `limit` 件**であって、語の総数ではない。
        "note": f"先頭 1 頁・要求上限 {limit} 件の中の数です（語の総数ではありません）",
        "authors": {"distinct": distinct, "with_username": with_author,
                    "top_share": top_share, "top_k": TOP_AUTHORS,
                    "top_counts": [c for _, c in top]},
        "latest_timestamp": stamps[0] if stamps else None,
        "oldest_timestamp": stamps[-1] if stamps else None,
        "tagged": {"count": tagged, "denominator": len(tagged_known),
                   "ratio": tagged_ratio,
                   "known": bool(tagged_known)},
        "replies": {"count": replies, "denominator": n},
    }


def reply_count(row: dict):
    """その投稿への**返信の数**（無ければ None）。**数を作らない**（設計 v2 §4.4）。

    Threads の keyword-search が返すのは `has_replies`（真偽）だけで**数は返らない**
    （**L2**: 資料の field 一覧に count が無い）。数を持つ媒体が来たときのために
    `REPLY_COUNT_KEYS` を順に見るが、**無ければ None**——0 にしない。
    """
    for key in REPLY_COUNT_KEYS:
        value = row.get(key)
        # `True` は `int` の仲間なので弾く（`has_replies` 由来の値を数にしない）。
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, dict):
            # `{"count": 3}` のような入れ子（媒体差）。
            inner = value.get("count")
            if isinstance(inner, int) and not isinstance(inner, bool):
                return inner
    return None


def post_rows(rows: list, *, replied: dict | None) -> list:
    """**絡みに行く先**の一覧（設計 v2 §4.4・純粋関数）。**本文は入らない。**

    `replied` は `post_id → {"status", "file"}`（`replied_index()`）。**None は
    「台帳を読めなかった」**で、各行の `replied` も None になる——**印が無いことを
    「返していない」にしない。**
    """
    out = []
    for r in rows:
        post_id = r.get("message_id")
        has_replies = r.get("has_replies")
        out.append({
            "post_id": post_id,
            "permalink": r.get("permalink"),
            "timestamp": r.get("timestamp"),
            "author": r.get("username"),
            # **数と真偽を別の鍵にする**（§4.4）。数が無いことを 0 と混ぜない。
            "replies": reply_count(r),
            "has_replies": has_replies if isinstance(has_replies, bool) else None,
            "replied": (replied or {}).get(post_id) if replied is not None else None,
        })
    return out


# ------------------------------------------------- 「もう返した」印（自分の台帳）

def replied_index(account_cfg: dict, account: str):
    """その account の queue から `reply_to` → 状態の索引を作る（**読むだけ**）。

    戻りは `(index, 読めなかった理由)`。**読めなければ index は None**（空 dict に
    しない）——空 dict は「1 件も返していない」を意味してしまう（設計 v2 §4.4）。

    見るのは **front-matter の `account` が一致する原稿**だけ。状態は
    `REPLIED_STATUSES` の強い順で、同じ先に複数あれば強い方を残す。`withdrawn` は
    印にしない。**queue の本文は読まない**（front-matter だけ）。
    """
    repo_dir = accounts_mod.resolved_repo_dir(account_cfg)
    queue_rel = account_cfg.get("queue_dir") or ""
    if not repo_dir or not queue_rel:
        return None, "台帳に repo_dir / queue_dir が無いので「返信済み」印は出せません"
    queue_dir = os.path.join(repo_dir, queue_rel)
    if not os.path.isdir(queue_dir):
        return None, f"queue が読めないので「返信済み」印は出せません（{queue_dir}）"
    index: dict = {}
    try:
        names = sorted(os.listdir(queue_dir))
    except OSError as e:
        return None, f"queue が読めないので「返信済み」印は出せません（{type(e).__name__}）"
    for name in names:
        if not name.endswith(".md"):
            continue
        path = os.path.join(queue_dir, name)
        try:
            qf = queuefile.parse(path)
        except (OSError, UnicodeDecodeError):
            # **1 本読めなくても他の印は出す**（読めなかったことは下の行数で判る）。
            continue
        if qf.malformed:
            continue
        fm = qf.front_matter
        if fm.get("account") != account:
            continue
        target = (fm.get("reply_to") or "").strip()
        status = fm.get("status")
        if not target or status not in REPLIED_STATUSES:
            continue
        current = index.get(target)
        if current is not None and (REPLIED_STATUSES.index(current["status"])
                                    <= REPLIED_STATUSES.index(status)):
            continue
        index[target] = {"status": status, "file": name}
    return index, None


def _replied_cell(entry) -> str:
    """画面に出す印。**読めなかった／返していない**はどちらも空白にしない。"""
    if entry is None:
        return ""
    return f"[{REPLIED_MARKS.get(entry['status'], entry['status'])}]"


def _replies_cell(post: dict) -> str:
    """返信の欄。数 → 数字、数が無ければ `有` / `無`、判らなければ `—`。"""
    if post["replies"] is not None:
        return str(post["replies"])
    if post["has_replies"] is True:
        return "有"
    if post["has_replies"] is False:
        return "無"
    return "—"


def _pct(ratio) -> str:
    return "—" if ratio is None else f"{ratio * 100:.0f}%"


def cmd_topics_search(args) -> int:
    """`thth topics <account> --search <語> [--recent] [--json]`。"""
    q = (args.search or "").strip()
    if not q:
        print("語が空です（--search に語を書いてください）", file=sys.stderr)
        return 2
    account = getattr(args, "account_flag", None) or args.account
    if not account:
        print("account を指定してください（thth topics <account> --search <語>）",
              file=sys.stderr)
        return 2
    search_type = "RECENT" if getattr(args, "recent", False) else "TOP"
    limit = getattr(args, "limit", 25) or 25
    as_json = bool(getattr(args, "json", False))

    def call(adapter):
        rows = adapter.keyword_search(q, search_type=search_type, limit=limit)
        return rows

    def render(rows):
        material = search_material(rows, q=q, search_type=search_type, limit=limit)
        # **絡みに行く先**（設計 v2 §4.4）。台帳（queue）は**読むだけ**で、棚には
        # 何も書かない——`post_id` は `topics.json` にも泉にも落ちない。
        try:
            index, why = replied_index(accounts_mod.load_account(account), account)
        except accounts_mod.AccountError as e:
            index, why = None, str(e)
        posts = post_rows(rows, replied=index)
        material["posts"] = posts
        material["replied_lookup"] = {
            "available": index is not None,
            "reason": why,
            "n": len(index) if index is not None else None,
            "statuses": list(REPLIED_STATUSES),
        }
        if as_json:
            # **本文は出さない**（材料と、指す先だけ）。
            print(json.dumps(material, ensure_ascii=False, indent=2))
            return 0
        a = material["authors"]
        t = material["tagged"]
        print(f"{account}  検索 {q!r}（{search_type}・{material['note']}）")
        print(f"  件数            : {material['n']}")
        print(f"  投稿者の異なり数: {a['distinct']}"
              f"（username の判る {a['with_username']} 件のうち）")
        print(f"  上位 {a['top_k']} 投稿者の占有率: {_pct(a['top_share'])}"
              f"（分母 {a['with_username']}）")
        print(f"  直近の投稿時刻  : {material['latest_timestamp'] or '—'}"
              f"　最古: {material['oldest_timestamp'] or '—'}")
        if t["known"]:
            print(f"  タグ付きの割合  : {_pct(t['ratio'])}（{t['count']}/{t['denominator']}）")
        else:
            print("  タグ付きの割合  : —（応答に topic_tag が無いので判りません）")
        # **下の「絡みに行く先」の `返信` 欄とは別のこと**（設計 v2 §4.4）。ここは
        # 「検索結果のうち、それ自体が返信である投稿の数」で、投稿ごとの返信の数
        # ではない（`--json` も同じ——`replies` は集計、`posts[].replies` が投稿ごと）。
        print(f"  返信だった投稿  : {material['replies']['count']}"
              f"/{material['replies']['denominator']}"
              f"（その投稿への返信の数ではありません。それは下の一覧の `返信` 欄）")
        if rows:
            print("")
            print("  絡みに行く先（post_id・permalink・返信・印。"
                  f"本文は先頭 {TEXT_PREVIEW_CHARS} 字・**表示するだけで保存しません**）:")
            for r, post in zip(rows, posts):
                stamp = post["timestamp"] or "—"
                who = post["author"] or "—"
                print(f"    {stamp}  @{_pad(who, 16)} {_pad(post['post_id'] or '—', 20)}"
                      f" 返信 {_pad(_replies_cell(post), 4)} {_replied_cell(post['replied'])}")
                print(f"      {post['permalink'] or '（permalink 無し）'}"
                      f"  {_one_line(r.get('text'))}")
            print("")
            print("  返信: 数が返る媒体は数、Threads の検索は `有`／`無` だけ"
                  "（数は返りません）。`—` は判らない（0 ではありません）。")
            if index is None:
                # **印が無いことを「返していない」にしない**（設計 v2 §4.4）。
                print(f"  印: 出せません——{why}")
            else:
                print(f"  印: [返信済]=posted ／ [承認済]=approved ／ [下書き]=draft"
                      f"（同じ account の queue に `reply_to: <post_id>` を持つ原稿"
                      f"・{len(index)} 件）。印の無い行は、この queue に原稿が"
                      f"見当たらないという意味です。")
            print("  絡む道: 下書きに `reply_to: <post_id>` → `thth lint` → "
                  "`thth approve`（二段）→ `thth throw` → `thth collect`。")
        print("")
        print("  この材料で `thth topics <account> --note <語> --status ok "
              "--audience \"…\" --verdict … --by …` を記録するのは人です"
              "（道具は材料を並べるだけ・観測は人の目）。")
        return 0

    return _run(account, capability="keyword_search", call=call, as_json=as_json,
                render=render)


# ---------------------------------------------------------------- mentions

def cmd_mentions(args) -> int:
    """`thth mentions <account> [--since …] [--json]`。**台帳には書かない**
    （書くのは `collect` の `inbox` 経路）。"""
    account = args.account
    as_json = bool(args.json)

    def call(adapter):
        return adapter.mentions(since=args.since)

    def render(rows):
        if as_json:
            print(json.dumps({"account": account, "n": len(rows), "mentions": rows},
                             ensure_ascii=False, indent=2))
            return 0
        print(f"{account}  言及 {len(rows)} 件（全頁・読むだけ。"
              f"台帳への追記は `thth collect` の inbox 経路）")
        for r in rows:
            stamp = r.get("timestamp") or "—"
            who = r.get("username") or "—"
            mid = r.get("message_id") or "—"
            print(f"  {stamp}  @{_pad(who, 16)} {mid}  {_one_line(r.get('text'))}")
            if r.get("permalink"):
                print(f"      {r['permalink']}")
        if not rows:
            print("  （0 件。承認前は tester による言及だけが返ります・非公開の利用者の"
                  "投稿は返りません）")
        else:
            print("  返信は既存の門（queue の `reply_to: <message_id>`）を通します。")
        return 0

    return _run(account, capability="mentions", call=call, as_json=as_json,
                render=render)


# ---------------------------------------------------------------- profile

STANDARD_ACCESS_NOTE = ("標準アクセスでは Meta 公式の 4 つ（@meta・@threads・@instagram・"
                        "@facebook）しか引けません（公開かつフォロワー 100 以上のみ）。")


def cmd_profile(args) -> int:
    """`thth profile <account> <username> [--json]`。"""
    account = args.account
    as_json = bool(args.json)

    def call(adapter):
        return adapter.profile_lookup(args.username)

    def render(profile):
        if as_json:
            print(json.dumps({"account": account, "profile": profile,
                              "standard_access_note": STANDARD_ACCESS_NOTE},
                             ensure_ascii=False, indent=2))
            return 0
        print(f"{account}  プロフィール @{profile.get('username')}")
        for key, label in (("name", "名前"), ("biography", "自己紹介"),
                           ("follower_count", "フォロワー"), ("is_verified", "認証"),
                           ("likes_count", "いいね（累計）"), ("views_count", "表示（累計）"),
                           ("reposts_count", "再投稿（累計）"), ("quotes_count", "引用（累計）"),
                           ("profile_picture_url", "画像")):
            if key in profile:
                value = profile[key]
                if key == "biography":
                    value = _one_line(value, 200)
                print(f"  {_pad(label, 14)}: {value}")
        print(f"  {STANDARD_ACCESS_NOTE}")
        return 0

    return _run(account, capability="profile_lookup", call=call, as_json=as_json,
                render=render)
