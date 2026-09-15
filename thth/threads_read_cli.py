"""Threads の 11 権限を使う**読み取りの口 3 つ**（設計 v2 §4.3・v2.1-A・2026-09-14）。

  - `thth topics <account> --search <語> [--recent] [--json]`（`threads_keyword_search`）
  - `thth mentions <account> [--json]`（`threads_manage_mentions`）
  - `thth profile <account> <username> [--json]`（`threads_profile_discovery`）

**`thth/cli.py` の `build_parser()` に足すのは `threads_read_cli.register(sub)` の
1 行だけ**（`ask_cli` と同じ筋）。`--search`/`--recent` は `topics` の既存 parser の
flag で、`cli._cmd_topics()` の入口からここへ来る。

**読むだけ。** どの口も GET しか呼ばない。検索結果の**本文はどこにも保存しない**
（§4.3「入れないもの」——泉に落ちないものは手元にも溜めない）。観測の材料を
並べるだけで、`--note` の `--status`/`--audience` の判断は人がする。

exit code:
  - `0` … 引けた（0 件でも 0）
  - `1` … 台帳が無い・壊れている・トークンが無い・媒体に口が無い・API が断った。
    **権限は乗っているのに API が断った**（標準アクセスの範囲外）もここ
    ——`thth auth` をやり直しても直らないので rc=2 と分ける（2026-09-15）
  - `2` … **権限がトークンに乗っていない**（`thth auth <account>` をやり直す・受け入れ (c)）
"""
from __future__ import annotations

import collections
import json
import sys
import unicodedata

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import redact as redact_mod
from .adapters import base as adapter_base

# 人向け画面に出す本文の長さ（先頭から）。**表示するだけで保存しない。**
TEXT_PREVIEW_CHARS = 60
# 「上位 N 投稿者の占有率」の N。
TOP_AUTHORS = 3

# **権限が乗っていないときの断り**（設計 v2 §4.3 受け入れ (c)）。
REAUTH_HINT = "`thth auth {account}` をやり直してください"

# **権限は乗っているのに API が断ったときの断り**（引継ぎ 2026-09-15 §3-D）。
#
# `thth profile <他人>` は Meta が HTTP 400 を返し、その本文に `permission` の語が
# 入っているので `_is_permission_error()` が拾い、道具は「乗っていません・
# `thth auth` をやり直して」と言っていた。**乗っている**（`doctor` の
# `/debug_token` で 11 個・2026-09-15 実測）。原因は**標準アクセス（App Review
# 前）では対象が絞られる**こと——`thth auth` を何度やり直しても直らない。
#
# **「乗っていない」と「範囲外」を混ぜない**（doctor の「乗っていない／確かめ
# られない」を分けたのと同じ物差し）。判る材料があるときだけ言い分ける:
# `granted_scopes()`（`.token` の `scopes` か `/debug_token`）にその権限が
# **在ると分かっている**ときだけ、この文面に切り替える。判らなければ従来どおり。
STANDARD_ACCESS_DOC = "docs/手順_AppReview_2026-09-14.md §0′"
# 口ごとの「標準アクセスではここまで」（**L2**・同 §0′ の引用と同じ事実）。
SEARCH_NARROWED_NOTE = "検索の対象は**認証したユーザー自身の投稿だけ**です。"
MENTIONS_NARROWED_NOTE = "返るのは**テスターからの言及だけ**です。"


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
             "タグ付きの割合）を出す。本文は表示するだけで保存しない"
             "（threads_keyword_search）")
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
        return None, (f"{account}: 媒体 {media} にはこの口（{capability}）がありません")
    token = accounts_mod.load_token(account_cfg)
    if not adapters_mod.adapter_class(media).has_token(token):
        return None, f"{account}: token が無いので引けません"
    return adapters_mod.make_adapter(account_cfg, token), None


def _not_granted(account: str, e: adapter_base.PermissionMissing) -> str:
    return (f"{e.permission} がトークンに乗っていません。"
            + REAUTH_HINT.format(account=account)
            + (f"（{e.detail}）" if e.detail else ""))


def _granted_source(adapter, permission: str) -> str | None:
    """その権限が**トークンに在ると分かっている**なら、その出どころ。判らなければ None。

    媒体を問わない形で聞く（`granted_scopes()`／`scopes_source()` を持たない
    アダプタは None）。**取りに行って失敗しても None**——判らないことを
    「乗っている」にしない。
    """
    getter = getattr(adapter, "granted_scopes", None)
    if not callable(getter):
        return None
    try:
        granted = getter()
    except Exception:
        return None
    if not isinstance(granted, list) or permission not in granted:
        return None
    source = getattr(adapter, "scopes_source", None)
    try:
        return (source() if callable(source) else None) or "記録"
    except Exception:
        return "記録"


def _narrowed_by_standard_access(account: str, e: adapter_base.PermissionMissing,
                                 source: str, note: str | None) -> str:
    """「権限は乗っています。標準アクセスでは対象が絞られます」の 1 行。"""
    return (f"{e.permission} は**トークンに乗っています**（{source}）。"
            f"それでも API が断りました——**標準アクセス（App Review 前）では"
            f"対象が絞られます**（{STANDARD_ACCESS_DOC}）。"
            + (f"{note}" if note else "")
            + f"`thth auth {account}` をやり直しても直りません。"
            + (f"（{e.detail}）" if e.detail else ""))


def _run(account: str, *, capability: str, call, as_json: bool, render,
          narrowed_note: str | None = None) -> int:
    """口を 1 つ叩いて出す。失敗の種類ごとに rc を分ける（module docstring）。

    `narrowed_note` は「標準アクセスではこう絞られる」の 1 文（口ごとに違う）。
    **権限は乗っていると分かっている**ときの断りにだけ添える。
    """
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
        #
        # **ただし「乗っていない」と「範囲外」を言い分ける**（引継ぎ 2026-09-15
        # §3-D）。権限が在ると分かっているなら再認可の案内は嘘になるので、
        # 標準アクセスの話に切り替えて rc も 1（API が断った）にする。
        source = _granted_source(adapter, e.permission)
        if source is None:
            message, rc = _not_granted(account, e), 2
        else:
            message = _narrowed_by_standard_access(account, e, source, narrowed_note)
            rc = 1
        if as_json:
            print(json.dumps({"error": message, "permission": e.permission,
                              "account": account,
                              "granted": source is not None,
                              "scopes_source": source,
                              "standard_access": source is not None},
                             ensure_ascii=False, indent=2))
        else:
            print(message, file=sys.stderr)
        return rc
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
        if as_json:
            # **本文は出さない**（材料だけ）。
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
        print(f"  返信の件数      : {material['replies']['count']}/{material['replies']['denominator']}")
        if rows:
            print("")
            print(f"  本文（先頭 {TEXT_PREVIEW_CHARS} 字・**表示するだけで保存しません**）:")
            for r in rows:
                stamp = r.get("timestamp") or "—"
                who = r.get("username") or "—"
                print(f"    {stamp}  @{who}  {_one_line(r.get('text'))}")
        print("")
        print("  この材料で `thth topics <account> --note <語> --status ok "
              "--audience \"…\" --verdict … --by …` を記録するのは人です"
              "（道具は材料を並べるだけ・観測は人の目）。")
        return 0

    return _run(account, capability="keyword_search", call=call, as_json=as_json,
                render=render, narrowed_note=SEARCH_NARROWED_NOTE)


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
                render=render, narrowed_note=MENTIONS_NARROWED_NOTE)


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
                render=render, narrowed_note=STANDARD_ACCESS_NOTE)
