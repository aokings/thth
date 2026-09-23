"""`thth who (<account>|--project P) (<author_key>|@<username>)` / MCP
`who_is_this`——仮名の履歴（設計「自分の泉」§2.4・§4・発注 T3-1）。

芯（設計 §0・§2.4）: この仮名（`author_key`）と自分のアカウントが**何度・いつ・
どんな反応で**接触したかを返す。**発言の内容は 1 文字も持たない・出さない。**
人物像は作らない（立場のラベル・傾向の要約を THTH が書かない）。

材料は 2 方向・どちらも既存の台帳（**新しい台帳は作らない**・発注 T3-1）:

  1. **自分 → 相手**（`i_replied_to_them`）: 絡みの台帳（`thth/engagements.py`）
     のうち `author_key` が一致する行。反応は `thth/after_cli.py` の
     `reaction_metrics()`（**既存の関数** `_measured_24h_metrics`・
     `_replies_back_from_ledger` を組み立てる、この口の中の唯一の場所）を
     **import して**使う——`thread_read`・`where_cli` の `you_and_them.
     last_reaction`（T3-2）も同じ関数を呼ぶ（`engagements.author_summary()`
     の `reaction_for` 引数）。**計算は 1 か所**（T3-2 発注書）。

  2. **相手 → 自分**（`they_replied_to_me`）: 返信台帳（`thth/replies.py`）の
     うち**この account の投稿の返信**（`owned_only`・3.5.1）で `own is False` かつ
     `author_key(medium, username)` が一致する行。
     出すのは `root`（`post_id`＝自分の投稿）・`message_id`・`at`
     （`timestamp`）だけ——**`text`・`username` は捨てる**。

`@username` を渡されたら**その場で** `adapters.base.author_key(medium,
username)` に写す（account の媒体で）。**username は出力にも runs にも
残さない**（`provenance.resolved_from: "username"` とだけ書く）。
"""
from __future__ import annotations

import json
import sys

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import after_cli as after_cli_mod
from . import engagements as engagements_mod
from . import jst
from . import measured as measured_mod
from . import redact as redact_mod
from . import replies as replies_mod
from . import runs as runs_mod
from . import threads_read_cli as threads_read_cli_mod
from .adapters import base as adapter_base

# `after_cli.SCHEMA_SOURCE` と同じ値（絡みの台帳・返信台帳しか読まない口）。
SCHEMA_SOURCE = after_cli_mod.SCHEMA_SOURCE


class WhoError(Exception):
    """問いが受け取れない（account/project どちらも無い・author_key/username
    どちらも無い・両方ある・author_key の形が違う 等）。

    **黙って空の答えを返さない**（`where_cli.WhereError` と同じ筋）。
    """


def _reject(message: str) -> None:
    raise WhoError(message)


def _permission_message(account_name: str, adapter, e: adapter_base.PermissionMissing) -> str:
    """`where_cli._permission_message()` と同じ言い分け——`threads_read_cli`
    の「乗っていない」／「標準アクセスでは絞られる」をここで直に組む。
    """
    source = threads_read_cli_mod._granted_source(adapter, e.permission)
    if source is None:
        return threads_read_cli_mod._not_granted(account_name, e)
    return threads_read_cli_mod._narrowed_by_standard_access(
        account_name, e, source, threads_read_cli_mod.STANDARD_ACCESS_NOTE)


def _profile_for(account_cfg: dict, account_name: str, *, username: str | None):
    """`--profile` のときだけ、その account の adapter に `profile_lookup` を
    その場で叩く（設計「自分の泉」§2.4）。**保存しない**。戻りは
    `(profile, cannot_say理由)`——どちらか一方だけが非 `None`。

    `profile_lookup` を持つのは Threads だけ（`ThreadsAdapter.CAPABILITIES`）。
    `author_key` は非可逆なので、**username が無ければ引きようがない**——
    `--author-key` だけで呼んだときは、その旨を `cannot_say` に出す（引けた
    ことにしない）。
    """
    media = account_cfg.get("media")
    if "profile_lookup" not in adapters_mod.capabilities_for(media):
        return None, f"{account_name}: この媒体（{media}）では引けません"
    if username is None:
        return None, (f"{account_name}: author_key だけでは公開プロフィールを"
                      f"引けません（username で呼んでください）")
    token = accounts_mod.load_token(account_cfg)
    adapter_cls = adapters_mod.adapter_class(media)
    if not adapter_cls.has_token(token):
        return None, f"{account_name}: token が無いので引けません"
    adapter = adapters_mod.make_adapter(account_cfg, token)
    try:
        profile = adapter.profile_lookup(username)
    except adapter_base.PermissionMissing as e:
        return None, _permission_message(account_name, adapter, e)
    except adapter_base.AdapterError as e:
        return None, redact_mod.redact(str(e))
    except RuntimeError as e:
        # **`AdapterError` ではない素の `RuntimeError`**（Bluesky の
        # `_request` 周りなど）を、`AdapterError` と同じ出し方で受ける
        # （T9-2）。`AdapterError` は `RuntimeError` の子なので、上の
        # `except AdapterError` をすり抜けたものだけがここに来る。呼び出し側
        # （`_account_node`）はこれを `cannot_say` に足して account 全体は
        # 落とさない。
        return None, redact_mod.redact(str(e))
    return profile, None


def _sort_key(entry: dict) -> tuple:
    """`at` の古い順。**時刻が読めない行は末尾**（`thread_read._sort_key()` と
    同じ考え方・落とさない）。"""
    at = entry.get("at")
    if isinstance(at, str) and at:
        return (0, at)
    return (1, "")


def _account_node(account_name: str, *, author_key: str | None, username: str | None,
                  profile: bool, now) -> tuple[dict | None, Exception | None]:
    """1 account 分の節。戻りは `(node, 例外)`——どちらか一方だけが非 `None`。

    account 自体が読めない（`AccountError`）ときは `node=None` と、その
    例外をそのまま返す——**単一 account 呼び出しはそのまま投げ直し**、
    `--project` は他の account を続けるために文字列にして `cannot_say` へ
    （`where_cli._account_node()` と同じ役割分担）。
    """
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        return None, e
    media = account_cfg.get("media")

    if username is not None:
        resolved_key = adapter_base.author_key(media, username)
        resolved_from = "username"
    else:
        resolved_key = author_key
        resolved_from = "author_key"

    cannot_say: list = []
    threads: list = []

    # --- 自分 → 相手（絡みの台帳・`after_cli` の既存関数を import して使う） ---
    eng = engagements_mod.load(account_cfg, account_name)
    if eng["broken"]:
        cannot_say.append(f"絡みの台帳の一部が読めません（{eng['broken']} 本）")
    measured_result = measured_mod.load(account_name)
    measured_by_post = {p["post_id"]: p for p in measured_result["posts"]}

    uncovered = 0
    for row in eng["rows"]:
        if row.get("author_key") != resolved_key:
            continue
        post_id = row.get("post_id")
        # **`after_cli` の計算をそのまま import して使う**（写し取らない・
        # 発注 T3-1「同じ計算を 2 か所に置かない」）。T3-2 で `thread_read`・
        # `where_cli` の `last_reaction` もここと同じ `after_cli.
        # reaction_metrics()` を呼ぶよう揃えた——**計算は 1 か所**。
        reaction = after_cli_mod.reaction_metrics(account_name, measured_by_post, post_id)
        if not reaction["covered"]:
            uncovered += 1
        threads.append({
            "root": row.get("root_post"), "role": "i_replied_to_them",
            "at": row.get("posted_at"), "post_id": post_id,
            "reply_to": row.get("reply_to"),
            "reaction": reaction,
        })
    if uncovered:
        cannot_say.append(f"24h の刻みが未採取: {uncovered} 本")

    # --- 相手 → 自分（返信台帳・自分の投稿への他者返信だけ） ---
    # **この account の投稿の返信だけを読む**（`owned_only`・3.5.1 件 2）。返信の置き場は
    # 同じ repo の他 account と共有で、他の媒体の返信行まで読むと、下の `author_key` を
    # **この account の媒体で**計算するので、同じ username の別媒体の人が「相手→自分」に
    # 混ざっていた。母集団は `thth replies` と同じ（3.1.1）。
    replies_result = replies_mod.load(account_name, owned_only=True)
    replies_broken = len(replies_result["broken"])
    if replies_result["broken"]:
        cannot_say.append(f"返信の台帳の一部が読めません（{replies_broken} 本）")
    if replies_result.get("population_errors"):
        cannot_say.append(f"自分の投稿の記録の一部が読めません（{replies_result['population_errors']} 件・"
                          "その投稿への返信は数えていません）")
    for row in replies_result["replies"]:
        # **`own is False` の行だけ**（自分の返信を「相手→自分」に混ぜない）。
        if row.get("own") is not False:
            continue
        row_key = adapter_base.author_key(media, row.get("username"))
        if row_key != resolved_key:
            continue
        # Threads は `id`、Bluesky・Mastodon は `message_id`（`thth/collect.py::
        # reply_row_id()` と同じ判定。ここでは本文・username を持ち出さずに
        # 識別子だけを写す）。
        message_id = row.get("message_id") or row.get("id")
        threads.append({
            "root": row.get("post_id"), "role": "they_replied_to_me",
            "at": row.get("timestamp"), "message_id": message_id,
            "reaction": None,
        })

    unreadable_at = sum(1 for t in threads
                        if not isinstance(t.get("at"), str) or not t["at"])
    if unreadable_at:
        cannot_say.append(f"接触の時刻が読めない行: {unreadable_at} 本")

    threads.sort(key=_sort_key)
    stamps = sorted(t["at"] for t in threads if isinstance(t.get("at"), str) and t["at"])
    met = len(threads)

    profile_out = None
    if profile:
        profile_out, profile_reason = _profile_for(account_cfg, account_name, username=username)
        if profile_reason:
            cannot_say.append(profile_reason)

    node = {
        "author_key": resolved_key,
        "met": met,
        "first": stamps[0] if stamps else None,
        "last": stamps[-1] if stamps else None,
        "threads": threads,
        "profile": profile_out,
        "cannot_say": cannot_say,
        "provenance": {
            "source": SCHEMA_SOURCE, "resolved_from": resolved_from,
            "engagements_broken": eng["broken"], "replies_broken": replies_broken,
            # 分母: 置き場にあったが他 account の投稿なので読まなかったファイルの本数と、
            # 母集団（この account の投稿）を作るときに読めなかった記録の数。
            "replies_other_account_files": replies_result["counts"].get("other_account_files"),
            "replies_population_errors": replies_result.get("population_errors"),
            "updated": jst.iso(now),
        },
    }

    # runs に 1 行（username を入れない・`record_minimal()` が禁止鍵を検査する）。
    runs_mod.record_minimal(account_name, {
        "action": "who_is_this", "account": account_name, "medium": media,
        "author_key": resolved_key, "met": met,
        "profile_fetched": profile_out is not None,
        "status": "ok", "error": None,
    }, now=now)

    return node, None


def _resolve_names(*, account_name: str | None, project: str | None) -> tuple[list, list]:
    """`where_cli._resolve_names()` と同じ役割（account 名の一覧を求める。
    project の account 解決は who 専用にもう 1 本持つ——`where_cli` から
    import すると who と where が要らず結合するため、この repo の他の
    `_resolve_names` と同じ形をここにも持つ）。"""
    if account_name is not None:
        return [account_name], []

    top_cannot_say: list = []
    names: list = []
    for name in accounts_mod.list_account_names():
        try:
            cfg = accounts_mod.load_account(name)
        except accounts_mod.AccountError as e:
            top_cannot_say.append(f"{name}: {e}")
            continue
        if cfg.get("project") == project:
            names.append(name)
    if not names:
        top_cannot_say.append(f"project={project!r} に一致する account がありません")
    return names, top_cannot_say


def answer(*, account_name: str | None = None, project: str | None = None,
          author_key: str | None = None, username: str | None = None,
          profile: bool = False, now=None) -> dict:
    """`who_is_this` の答え（設計「自分の泉」§2.4）。**読むだけ**（`--profile`
    の `profile_lookup` を除く。どちらも書かない）。

    単一 account（`account_name`）なら §2.4 の形をそのまま返す（鍵は
    `author_key`・`met`・`first`・`last`・`threads`・`profile`・`cannot_say`・
    `provenance` の 8 つだけ）。`--project` なら `by_account` の下に account
    ごとに同じ形を並べる（発注 T3-1）。
    """
    if account_name and project:
        _reject("account と --project は同時に指定できません")
    if not account_name and not project:
        _reject("account か --project のどちらかが要ります")
    if author_key and username:
        _reject("author_key と username は同時に指定できません")
    if not author_key and not username:
        _reject("author_key か username のどちらかが要ります")
    if author_key is not None and not engagements_mod.AUTHOR_KEY_RE.match(author_key):
        _reject(f"author_key は 16 進 16 桁です: {author_key!r}")
    if username is not None and not username.strip():
        _reject(f"username が空です: {username!r}")
    if username is not None:
        username = username.strip().lstrip("@")

    now = now if now is not None else jst.now_jst()
    names, top_cannot_say = _resolve_names(account_name=account_name, project=project)

    by_account: dict = {}
    for name in names:
        node, err = _account_node(
            name, author_key=author_key, username=username, profile=profile, now=now)
        if node is None:
            if project is None:
                # 単一 account: `where_cli` と違い、そのまま投げ直す
                # （`after_cli`・`thread_read` と同じ「account が読めなければ
                # `AccountError`」の筋に揃える)。
                raise err
            top_cannot_say.append(f"{name}: {err}")
            continue
        by_account[name] = node

    if project is not None:
        return {
            "project": project, "by_account": by_account,
            "cannot_say": top_cannot_say,
            "provenance": {"fetched_at": jst.iso(now)},
        }

    return by_account[account_name]


# ---------------------------------------------------------------------- CLI

def _render_node(node: dict) -> None:
    """人向けの画面: 1 行目「仮名 …・接触 N 回（→ n・← n）・最初 …・最後 …」→
    接触ごとに 1 行（矢印・時刻・root の post_id・反応）。**傾向や立場の
    一言を書かない**（設計「自分の泉」§2.4・発注 T3-1）。
    """
    to_them = sum(1 for t in node["threads"] if t["role"] == "i_replied_to_them")
    from_them = sum(1 for t in node["threads"] if t["role"] == "they_replied_to_me")
    print(f"仮名 {node['author_key']}・接触 {node['met']} 回"
         f"（→ {to_them}・← {from_them}）"
         f"・最初 {node['first'] or '—'}・最後 {node['last'] or '—'}")
    for t in node["threads"]:
        arrow = "→" if t["role"] == "i_replied_to_them" else "←"
        at = t.get("at") or "—"
        root = t.get("root") or "—"
        reaction = t.get("reaction")
        if reaction:
            tail = (f"views={reaction['views_24h']} likes={reaction['likes_24h']} "
                   f"replies={reaction['replies_back_24h']} covered={reaction['covered']}")
        else:
            tail = "—"
        print(f"  {arrow} {at}  root={root}  {tail}")
    if node.get("profile"):
        print(f"  プロフィール @{node['profile'].get('username')}")
    provenance = node.get("provenance") or {}
    if provenance.get("replies_other_account_files"):
        print(f"  返信の台帳: 他 account の投稿のファイル "
              f"{provenance['replies_other_account_files']} 本は読んでいません（数えていません）")
    for line in node["cannot_say"]:
        print(f"  言えない: {line}")


def _render_human(result: dict) -> None:
    if "by_account" in result:
        print(f"who  project {result['project']}")
        for name, node in result["by_account"].items():
            print(f"\n[{name}]")
            _render_node(node)
        if result["cannot_say"]:
            print("")
            for line in result["cannot_say"]:
                print(f"言えない: {line}")
    else:
        _render_node(result)


def register(sub) -> None:
    """`thth who (<account>|--project P) (<author_key>|@<username>)` を親の
    subparsers にぶら下げる（`where_cli.register()` と同じ型: 1 本の
    `nargs='+'` 位置引数で `--project` の有無を見て後から割る）。
    """
    p = sub.add_parser(
        "who",
        help="仮名の履歴——接触の回数・時期・反応を返す。発言内容は持たない"
             "（設計「自分の泉」§2.4・読むだけ）")
    p.add_argument("targets", nargs="+", metavar="ACCOUNT_AND_KEY",
                   help="<account> <author_key|@username>。--project のときは"
                        "author_key|@username だけ")
    p.add_argument("--project", default=None, metavar="P",
                   help="account の代わりに、この project の account 全部を対象にする")
    p.add_argument("--profile", action="store_true",
                   help="その場で公開プロフィールを引く（Threads だけ・保存しない）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_who)


def cmd_who(args) -> int:
    """`thth who (<account>|--project P) (<author_key>|@<username>) [--profile] [--json]`。"""
    as_json = bool(getattr(args, "json", False))
    received = len(args.targets) if args.project else max(0, len(args.targets)-1)
    if received > 1:
        print(f"{received} 人を受け取りました。who は 1 度に 1 人を指定してください", file=sys.stderr)
        return 2
    if args.project:
        if len(args.targets) != 1:
            print("--project のときは author_key か @username を 1 つだけ渡してください"
                 "（例: thth who --project P @alice）", file=sys.stderr)
            return 2
        account_name = None
        target = args.targets[0]
    else:
        if len(args.targets) != 2:
            print("account と author_key（か @username）が要ります: "
                 "thth who <account> <author_key|@username>", file=sys.stderr)
            return 2
        account_name, target = args.targets

    if target.startswith("@"):
        author_key, username = None, target
    else:
        author_key, username = target, None

    try:
        result = answer(account_name=account_name, project=args.project,
                        author_key=author_key, username=username, profile=args.profile)
    except accounts_mod.AccountError as e:
        print(str(e), file=sys.stderr)
        return 1
    except WhoError as e:
        print(str(e), file=sys.stderr)
        return 2

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    _render_human(result)
    return 0
