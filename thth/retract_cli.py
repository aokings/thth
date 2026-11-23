"""`thth retract` と `thth location search`（設計 v2 §4.3・v2.1-B・2026-09-14）。

**`build_parser()` には 1 行しか足さない**（`retract_cli.register(sub)`）。
`thth/cli.py` は並行して別の Track が触るので、口はこちら側に閉じる。

**THTH の芯は「人が承認していない公開行為は起きない」。** 取り下げは公開の側を
変える行為なので、承認の門（二段）を通す:

1. `thth retract <account> <post_id> --reason … --by …`（`--confirm` 無し）は、
   **該当投稿の本文と URL と理由を見せて digest を出し、何もしないで rc=1**。
2. `--confirm <digest>` で **DELETE を 1 回**呼ぶ。成功したら queue の
   front-matter（無ければ `sent/<post_id>.json`）に `retracted_at`・`retracted_by`・
   `retract_reason` を**足す**。**`sent/`・runs・返信の台帳は消さない。**

fail-closed:
  - 台帳に `production: true` が無ければ**二段目でも DELETE を呼ばない**。
  - 手元に記録（queue か `sent/`）の無い `post_id` は、**その口座の鍵で id 1 件を読み
    （`outside_post`→ adapter の `own_post`）、自分の投稿だと確かめたときだけ**取り下げる
    （11/23 masaru「自分のアカウントでしょ」・設計 持ち主の依頼で消す §3）。自分の物でなければ
    `post_not_own`、読めなければ `post_unreadable` で断る（古いかどうかは理由にしない）。
    digest には一段目で読んだ本文の指紋を入れる（`approval.compute_outside_retract_digest`）。
    記録は `sent/` に入れず `retracted_outside/` に置く（THTH が出したものに数えない）。
  - トークンに媒体の取り下げ権限（Threads `threads_delete`・X `tweet.write`・
    Mastodon `write:statuses`）が乗っていなければ rc=2（`thth auth` のやり直し）。
  - 取り下げを持たない媒体は rc=2「この媒体の取り下げは未対応」。
    Mastodon は 3.1.1、Bluesky は 3.22.0 から対応。
  - 手元の MCP には出さない（`approve` と同じ線）。サーバ型 MCP と遠くの道には
    `thth_retract_request` がある（承認の頁は無く、持ち主が発行した鍵で頼む・3.13.0）。
    記録の無い投稿は、そちらでも `confirm` が合うまで消さない（一段目を返すだけ）。

**DELETE が飛ぶ経路はこのモジュールの `_do_retract()` が呼ぶ `delete_post` だけ**
（各 adapter の `delete_post` は他のどこからも呼ばれない——
`tests/test_v21b_approved_writes.py` が source を見て固定する）。記録の無い投稿の持ち主を
確かめる `own_post` を呼ぶのも、このモジュールの `outside_post()` の 1 か所だけ。
"""
from __future__ import annotations

import json
import os
import sys

from . import article_media
from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import approval as approval_mod
from . import core
from . import jst
from . import lock as lock_mod
from . import queuefile
from . import redact as redact_mod
from . import sent as sent_mod
from . import writeback as writeback_mod
from .adapters import base as adapter_base

# threads_location_tagging は 2026-09-23 の審査で承認済み。承認前に出していた
# 「標準アクセスでは "Menlo Park" しか検索できません」の注意書きは外した（設計 3.11.0 §3）。
LOCATION_LIMIT = 5


def register(sub) -> None:
    """`thth/cli.py` の `build_parser()` から 1 行で呼ばれる。"""
    p = sub.add_parser(
        "retract",
        help="公開済みの投稿を取り下げる（二段確認・記録は消さない・"
             "Threads／X／Mastodon／Bluesky／Facebook／Instagram）")
    p.add_argument("account")
    p.add_argument("post_id")
    p.add_argument("--reason", default=None, help="なぜ取り下げるか（記録に残す・必須）")
    p.add_argument("--by", default=None, help="誰が取り下げたか（記録に残す・必須）")
    p.add_argument("--confirm", default=None,
                   help="一段目が表示した digest。これが無いと何もしない")
    p.add_argument("--json", action="store_true")
    p.add_argument("--wait", type=lock_mod.wait_seconds, default=0, help="ロックを待つ秒数（既定 0）")
    p.add_argument("--already-gone", dest="already_gone", action="store_true",
                   help="媒体の画面で既に消した投稿に、記録だけ足す（DELETE は呼ばない・二段確認は同じ）")
    p.set_defaults(func=cmd_retract)

    p_loc = sub.add_parser(
        "location", help="場所の検索（`thth location search <account> <語>`・読み取りだけ）")
    p_loc.add_argument("action", choices=["search"])
    p_loc.add_argument("account")
    p_loc.add_argument("query", help="人が書く語（例: 渋谷駅）")
    p_loc.add_argument("--json", action="store_true")
    p_loc.set_defaults(func=cmd_location)


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _fail(args, code: int, message: str, **extra) -> int:
    if getattr(args, "result_sink", None) is not None:
        args.result_sink({"ok": False})
    elif getattr(args, "json", False):
        _print_json({"ok": False, "error": message, **extra})
    else:
        print(message, file=sys.stderr)
    return code


def _not_granted(account_name: str, e: adapter_base.PermissionMissing) -> str:
    return str(e).replace("<account>", account_name)


# --------------------------------------------------------------------------
# thth location search
# --------------------------------------------------------------------------

class LocationFailed(Exception):
    """場所の検索を断った（`location_rows()` が投げる）。`rc` は CLI の終了コード。

    `kind` はサーバの読む口が静的な符丁に写すための種類（`account`・`unsupported`・
    `no_token`・`not_granted`・`upstream`）。
    """

    def __init__(self, rc: int, message: str, kind: str):
        super().__init__(message)
        self.rc = rc
        self.message = message
        self.kind = kind


def _record_location(account_name: str, media, ok: bool) -> None:
    """runs に 1 行（命令の名前・媒体・結果だけ。検索の語と場所は入れない）。`permission_watch` が数える。"""
    from . import meta_public
    meta_public.record_read(account_name, "location_search", media, ok)


def location_rows(account_name: str, query: str) -> list:
    """場所の候補を引く（CLI とサーバの読む口が共有）。断りは `LocationFailed`。"""
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        raise LocationFailed(2, str(e), "account") from None
    adapter_cls = adapters_mod.adapter_class(account_cfg.get("media"))
    permission = getattr(adapter_cls, "LOCATION_PERMISSION", None)
    if not permission:
        raise LocationFailed(2, f"{account_cfg.get('media')}: この媒体に場所の検索はありません",
                             "unsupported")
    token = accounts_mod.load_token(account_cfg)
    if not adapter_cls.has_token(token):
        raise LocationFailed(2, f"{account_name}: token がありません（{adapter_cls.TOKEN_SETUP_HINT}）",
                             "no_token")
    missing = adapter_cls.missing_permissions(token, [permission])
    if missing:
        raise LocationFailed(2, adapter_base.not_granted_message(missing[0]).replace(
            "<account>", account_name), "not_granted")
    try:
        adapter = adapters_mod.make_adapter(account_cfg, token)
        rows = adapter.location_search(query, limit=LOCATION_LIMIT)
        _record_location(account_name, account_cfg.get("media"), True)
        return rows
    except adapter_base.PermissionMissing as e:
        _record_location(account_name, account_cfg.get("media"), False)
        raise LocationFailed(2, _not_granted(account_name, e), "not_granted") from None
    except Exception as e:
        _record_location(account_name, account_cfg.get("media"), False)
        raise LocationFailed(1, "場所の検索に失敗しました: " + redact_mod.redact(str(e)),
                             "upstream") from None


def location_json(account_name: str, query: str, rows: list) -> dict:
    """`thth location search --json` の形。"""
    return {"ok": True, "account": account_name, "query": query, "locations": rows}


def cmd_location(args) -> int:
    """`thth location search <account> <語>`: 候補を 5 件まで名前と id で。読むだけ。"""
    try:
        rows = location_rows(args.account, args.query)
    except LocationFailed as failed:
        return _fail(args, failed.rc, failed.message)

    if args.json:
        _print_json(location_json(args.account, args.query, rows))
        return 0
    return show_locations(args.query, rows)


def show_locations(query: str, rows: list) -> int:
    """`thth location search` の人向けの表示（手元の道と遠くの道が共有・`--json` の `locations` を受ける）。"""
    if not rows:
        print(f"「{query}」に一致する場所はありませんでした（0 件）")
    else:
        print(f"「{query}」の候補（{len(rows)} 件・最大 {LOCATION_LIMIT} 件）:")
        for row in rows:
            where = "・".join(x for x in (row.get("address"), row.get("city"),
                                       row.get("country")) if x)
            # 郵便番号と緯度・経度（10/15 点検 §B-8）は付いていたときだけ。
            extra = "・".join(str(x) for x in (
                row.get("postal_code"),
                (f"{row['latitude']},{row['longitude']}"
                 if row.get("latitude") is not None and row.get("longitude") is not None else None))
                if x)
            print(f"  {row.get('name') or '（名前なし）'}  id {row.get('id')}"
                  + (f"  （{where}）" if where else "") + (f"  {extra}" if extra else ""))
        print("front-matter に `location: <名前>` と `location_id: <id>` を書いてください")
    return 0


# --------------------------------------------------------------------------
# thth retract
# --------------------------------------------------------------------------

def _find_record(account_cfg: dict, account_name: str, post_id: str) -> dict | None:
    """queue の front-matter（`post_id` 一致）→ `sent/<post_id>.json` の順に探す。

    返り値: `{"source": "queue"|"sent", "path", "text", "front_matter", "url"}`
    無ければ `None`（**THTH を通していない投稿は取り下げない**）。
    """
    tree_sha = writeback_mod.upstream_sha(account_cfg.get("repo_dir"))
    for qf in core.list_queue_files(account_cfg, tree_sha=tree_sha):
        if qf.malformed:
            continue
        fm = qf.front_matter
        if fm.get("post_id") == post_id and fm.get("account") == account_name:
            section = queuefile.extract_section(qf.body, account_cfg["media"])
            return {"source": "queue", "path": qf.path, "text": section or "",
                    "front_matter": fm, "url": fm.get("url")}
    state_dir = accounts_mod.state_dir_for(account_name)
    row = sent_mod.read(state_dir, post_id)
    if row is not None and row.get("post_id") == post_id:
        return {"source": "sent", "path": sent_mod.path_for(state_dir, post_id),
                "text": row.get("text") or "", "front_matter": row, "url": row.get("url")}
    return None


# 種類の言い方（一段目に出す）。再投稿の id に DELETE すると、再投稿の取り消しになる。
KIND_WORDS = {"post": "投稿", "story": "ストーリーズ", "repost": "再投稿"}


def outside_post(account_cfg: dict, token, post_id: str) -> dict:
    """THTH の記録が無い `post_id` を、**その口座の鍵で 1 件読み、自分の投稿か確かめる**（設計 §3）。

    adapter の `own_post` を呼ぶのは**ここ 1 か所だけ**（手元の CLI も thth.me の口もここを通る）。
    **読むだけ**（DELETE はここで呼ばない）。返り値は `_find_record` と同じ形に `kind`・`posted_at` を
    足したもの（`source: "media"`）。

    断り: 自分の物でない → `PostNotOwn`、読めない → `PostUnreadable`、権限 → `PermissionMissing`。
    """
    try:
        adapter = adapters_mod.make_adapter(account_cfg, token)
        row = adapter.own_post(post_id)
    except (adapter_base.PostNotOwn, adapter_base.PostUnreadable, adapter_base.PermissionMissing):
        raise
    except Exception as e:
        raise adapter_base.PostUnreadable(redact_mod.redact(str(e))) from None
    if not isinstance(row, dict) or str(row.get("post_id") or "") != post_id:
        raise adapter_base.PostUnreadable("読んだ投稿の id が要求と一致しません")
    kind = row.get("kind") if row.get("kind") in KIND_WORDS else "post"
    text = row.get("text") if isinstance(row.get("text"), str) else ""
    posted_at = row.get("timestamp") if isinstance(row.get("timestamp"), str) else None
    return {"source": "media", "kind": kind,
            "path": f"（THTH の記録なし・媒体から読んだ自分の{KIND_WORDS[kind]}）",
            "text": text, "front_matter": {}, "url": row.get("url") if isinstance(row.get("url"), str) else None,
            "posted_at": posted_at}


def retract_digest(account_name: str, post_id: str, reason: str, record: dict) -> str:
    """二段確認の digest。記録の無い投稿（`source: media`）は本文の指紋を入れた別の版の語。"""
    if record.get("source") == "media":
        return approval_mod.compute_outside_retract_digest(
            post_id=post_id, account=account_name, reason=reason, text=record.get("text") or "",
            posted_at=record.get("posted_at"), kind=record.get("kind") or "post")
    return approval_mod.compute_retract_digest(post_id=post_id, account=account_name, reason=reason)


def not_own_message(account_name: str, post_id: str, detail: str = "") -> str:
    """`post_not_own` の 1 行（なぜ・直し方・より良く）。"""
    tail = f"（{detail}）" if detail else ""
    return (f"post_not_own: post_id {post_id} を取り下げません — なぜ: THTH の記録（queue・sent/）に無く、"
            f"{account_name} の鍵でその id を読んだら、この口座の投稿ではありませんでした{tail}。"
            "他の人の投稿には触りません。"
            f"直し方: thth posts {account_name}（IG のストーリーズは --stories も）で自分の投稿の id を"
            "確かめて打ち直してください。より良く: 消したい投稿の URL から id を写すと、"
            "別の口座の id との取り違えが起きません")


def unreadable_message(account_name: str, post_id: str, detail: str = "") -> str:
    """`post_unreadable` の 1 行（なぜ・直し方・より良く）。"""
    tail = f"（{detail}）" if detail else ""
    return (f"post_unreadable: post_id {post_id} を取り下げません — なぜ: THTH の記録（queue・sent/）に無く、"
            f"{account_name} の鍵でその投稿を読めませんでした{tail}。本文を見せられない削除はしません。"
            f"直し方: 少し置いて打ち直すか、thth doctor {account_name} で鍵を確かめてください。"
            "すぐ消すなら媒体の画面から手で消せます。"
            f"より良く: thth posts {account_name} で id を確かめてから打つと、打ち間違いで読めないことを避けられます")


def _lookup_url(account_cfg: dict, token, post_id: str) -> str | None:
    """URL を**読み取りだけ**で引く（`recent_posts`）。引けなければ `None`。"""
    try:
        if "recent_posts" not in adapters_mod.capabilities_for(account_cfg.get("media")):
            return None
        adapter = adapters_mod.make_adapter(account_cfg, token)
        for row in adapter.recent_posts(limit=25):
            if row.get("post_id") == post_id:
                return row.get("url")
    except Exception:
        return None
    return None


def cmd_retract(args) -> int:
    try:
        return _cmd_retract(args)
    except adapter_base.PermissionMissing as e:
        return _fail(args, 2, _not_granted(args.account, e))


def _cmd_retract(args) -> int:
    post_id = (args.post_id or "").strip()
    reason = (args.reason or "").strip()
    by = (args.by or os.environ.get("THTH_ACTOR") or "").strip()
    if not reason:
        return _fail(args, 1, "--reason を付けてください（なぜ取り下げるかを記録します）")
    if not by:
        return _fail(args, 1, "--by を付けてください（誰が取り下げたかを記録します）。"
                              "環境変数 THTH_ACTOR でも指定できます")
    try:
        writeback_mod.check_front_matter_field("retracted_by", by)
        writeback_mod.check_front_matter_field("retract_reason", reason)
    except ValueError as e:
        return _fail(args, 2, str(e))
    if not post_id:
        return _fail(args, 1, "post_id が空です")

    try:
        account_cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as e:
        return _fail(args, 2, str(e))
    media = account_cfg.get("media")
    if article_media.is_article_media(media):
        # はてなブログの記事を消すのは配信原稿（op: delete）の承認と timer だけ（設計 4.0 S2 §9 の 7）。
        return _fail(args, 2, "unsupported_operation: はてなブログの記事は thth retract で消せません。"
                              "配信原稿を op: delete で書いて thth approve で承認し、timer が消します")
    adapter_cls = adapters_mod.adapter_class(media)
    if not getattr(adapter_cls, "DELETE_PERMISSION", None):
        able = "／".join(name for name in adapters_mod.known_media()
                        if getattr(adapters_mod.adapter_class(name), "DELETE_PERMISSION", None))
        return _fail(args, 2, f"{media}: この媒体の取り下げは未対応です"
                              f"（取り下げられるのは {able}）。消すなら媒体の画面から手で")

    already_gone = bool(getattr(args, "already_gone", False))
    token = accounts_mod.load_token(account_cfg)
    record = _find_record(account_cfg, args.account, post_id)
    if record is None:
        done = sent_mod.read_outside_retraction(accounts_mod.state_dir_for(args.account), post_id)
        if done is not None:
            return _fail(args, 1, f"post_id {post_id} は既に取り下げ済みです"
                                  f"（{done.get('retracted_at')}・{done.get('retracted_by')}）")
        if already_gone:
            return _fail(args, 1, f"post_id {post_id} に記録だけ足すことはしません — なぜ: --already-gone は "
                                  "THTH の記録（queue・sent/）に取り下げの印を足す口で、この id には足す先の記録が"
                                  f"ありません。直し方: まだ媒体に残っているなら --already-gone を外して "
                                  f"thth retract {args.account} {post_id} で消してください。より良く: 媒体の画面で"
                                  "消えていれば、THTH の側で足す物はありません（そのままで構いません）")
        # THTH の記録が無くても、自分の口座の投稿なら消せる（11/23 masaru）。id 1 件を読んで確かめる。
        try:
            record = outside_post(account_cfg, token, post_id)
        except adapter_base.PostNotOwn as e:
            return _fail(args, 1, not_own_message(args.account, post_id, redact_mod.redact(str(e))))
        except adapter_base.PermissionMissing:
            raise
        except adapter_base.PostUnreadable as e:
            return _fail(args, 1, unreadable_message(args.account, post_id, redact_mod.redact(str(e))))
    fm = record["front_matter"]
    if fm.get("retracted_at"):
        return _fail(args, 1, f"post_id {post_id} は既に取り下げ済みです"
                              f"（{fm.get('retracted_at')}・{fm.get('retracted_by')}）")

    # 媒体の画面で既に消した投稿（要望 #1560・関東 10/8）。記録だけ足し、DELETE は呼ばない。
    # 消す取り下げの digest を流用できないよう、digest の材料に印を足す。
    digest = (retract_digest(args.account, post_id, reason, record) if record["source"] == "media"
              else approval_mod.compute_retract_digest(
                  post_id=post_id, account=args.account,
                  reason=reason + ("\x1falready_gone" if already_gone else "")))

    url = record.get("url") or _lookup_url(account_cfg, token, post_id)

    if not args.confirm:
        # ---- 一段目: 見せるだけ。何もしない・DELETE 0 回 ----
        from . import refusals
        refusals.mark_first_stage()   # 一段目の rc 1 は断りではない
        if args.json:
            _print_json({"ok": False, "retracted": False, "stage": 1,
                         "account": args.account, "post_id": post_id, "url": url,
                         "permalink": url,  # 他の答えと同じ名前（10/15 調べ_JSONの欄 §5 1-3・url は残す）
                         "source": record["source"], "file": record["path"],
                         "kind": record.get("kind") or "post", "posted_at": record.get("posted_at"),
                         "text": record["text"], "reason": reason, "by": by,
                         "digest": digest})
            return 1
        print(f"取り下げません（確認の一段目です）: {args.account} post_id {post_id}")
        if already_gone:
            print("  媒体の画面で既に消えている投稿として、記録だけ足します（DELETE は呼びません）")
        print(f"  記録: {record['source']}（{record['path']}）")
        if record["source"] == "media":
            kind = record.get("kind") or "post"
            print("  THTH を通していない自分の投稿です。消したことは retracted_outside/ に残します")
            print(f"  種類: {KIND_WORDS.get(kind, kind)}"
                  + ("（消すと再投稿の取り消しになります。元の投稿は残ります）" if kind == "repost" else ""))
            print(f"  日時: {record.get('posted_at') or '（媒体の答えに無し）'}")
        print(f"  URL : {url or '（記録に無く、引けませんでした。thth posts で確かめられます）'}")
        print(f"  理由: {reason}")
        print(f"  by  : {by}")
        print("--- 取り下げる本文 ---")
        text = record["text"]
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        print("--- ここまで ---")
        if not already_gone and not approval_mod.is_true(account_cfg.get("production")):
            print("  ⚠ 台帳に production: true が無いので、二段目でも DELETE は呼びません")
        print(f"digest: {digest}")
        print(f"この投稿を取り下げるなら: thth retract {args.account} {post_id} "
              f"--reason {json.dumps(reason, ensure_ascii=False)} --by "
              f"{json.dumps(by, ensure_ascii=False)}"
              f"{' --already-gone' if already_gone else ''} --confirm {digest}")
        return 1

    # ---- 二段目 ----
    if args.confirm != digest:
        return _fail(args, 1, f"digest が一致しないので取り下げません（表示したものと"
                              f"中身が違います）。いまの digest は {digest} です。"
                              "もう一度 thth retract からやり直してください")
    if already_gone:
        return _do_retract(args, account_cfg, adapter_cls, token, record, post_id,
                           reason=reason + "（媒体の画面で既に消えていた・DELETE は呼んでいない）",
                           by=by, url=url, delete=False)
    # **production: true が無ければ DELETE を呼ばない**（dry-run と同じ fail-closed）。
    if not approval_mod.is_true(account_cfg.get("production")):
        return _fail(args, 1, f"{args.account}: 台帳に production: true が無いので取り下げ"
                              "ません（DELETE は呼んでいません・dry-run と同じ fail-closed）")
    if not adapter_cls.has_token(token):
        return _fail(args, 2, f"{args.account}: token がありません（{adapter_cls.TOKEN_SETUP_HINT}）")
    # **公開要求の手前で権限を見る**（doctor と同じ物差し）。
    missing = adapter_cls.missing_permissions(token, [adapter_cls.DELETE_PERMISSION])
    if missing:
        return _fail(args, 2, adapter_base.not_granted_message(missing[0]).replace(
            "<account>", args.account))

    return _do_retract(args, account_cfg, adapter_cls, token, record, post_id,
                       reason=reason, by=by, url=url)


def _guard_refusal(account_name, account_cfg):
    """安全装置に掛かれば `(符丁, 人向けの 1 行)`、掛からなければ None。

    止めの印を置けない置き場（THTH_ROOT の外・モードの緩い置き場）では止めずに 1 件断る
    （`guard.check(cli=True)`・send と同じ）。
    """
    from . import guard as guard_mod
    try:
        guard_mod.check(account_name, account_cfg, "retract", via="cli", cli=True)
    except guard_mod.GuardRefused as refused:
        return str(refused), core.guard_refusal_message(account_name, refused, kind="retract")
    return None


def _do_retract(args, account_cfg, adapter_cls, token, record, post_id, *,
                reason, by, url, before_execute=None, lock_context=None, origin=None,
                delete=True) -> int:
    """**DELETE を 1 回**。成功したら記録に 3 項目を足す（消さない）。"""
    account_name = args.account
    # 公開の経路と同じロック（repo → account）。取り下げの最中に同じ clone を
    # 別の実行が触らないように。
    state_dir = accounts_mod.state_dir_for(account_name)
    try:
        with (lock_context or core._account_locks(account_name, account_cfg, state_dir, wait=getattr(args, "wait", 0))):
            repo_dir = None
            rel_path = None
            if record["source"] == "queue":
                repo_dir = writeback_mod.repo_toplevel(record["path"])
                if repo_dir is None:
                    return _fail(args, 1, f"git repo の中のファイルではないので記録できません: "
                                          f"{record['path']}")
                synced, sync_err, _sha = writeback_mod.sync_repo(repo_dir)
                if not synced:
                    return _fail(args, 1, f"repo を同期できないので取り下げません: {sync_err}")
                rel_path = os.path.relpath(os.path.realpath(record["path"]),
                                           os.path.realpath(repo_dir))
                # 同期したあとにもう一度見る（別 clone で既に取り下げられていないか）。
                fm_now = queuefile.parse(record["path"]).front_matter
                if fm_now.get("retracted_at"):
                    return _fail(args, 1, f"post_id {post_id} は既に取り下げ済みです"
                                          f"（{fm_now.get('retracted_at')}）")

            if not delete:
                result = {"deleted_id": None}              # 媒体の画面で既に消えている
            else:
                if before_execute is None:
                    # **運営者の CLI の取り下げにも安全装置を掛ける**（10/15・`thth send` と同じ作り）。
                    # それまでは止まった口座でも、daily_max_retracts を超えても、急な連投でも消せた。
                    # 確認の番号が一致したあと・DELETE の直前（ロックの中）で見る。thth.me の道は
                    # `before_execute` の中で見るので二重にしない。--already-gone は DELETE を呼ばないので掛けない。
                    refused = _guard_refusal(account_name, account_cfg)
                    if refused is not None:
                        return _fail(args, 1, refused[1], guard=refused[0])
                if before_execute is not None:
                    token = before_execute(account_cfg)
                adapter = adapters_mod.make_adapter(account_cfg, token)
                try:
                    result = adapter.delete_post(post_id)      # ← DELETE はここ 1 回だけ
                except adapter_base.PermissionMissing as e:
                    return _fail(args, 2, _not_granted(account_name, e))
                except Exception as e:
                    return _fail(args, 1, "取り下げできませんでした（記録は変えていません）: "
                                          + redact_mod.redact(str(e)))

            retracted_at = jst.iso()
            fields = {"retracted_at": retracted_at, "retracted_by": by,
                      "retract_reason": reason}
            if origin is not None:
                # 直接の削除（設計 3.12.0 §3.2）: 誰の依頼か（via と資格の id）。
                fields.update(sent_mod._origin_fields(origin, prefix="retracted_"))
            wrote = []
            push_err = None
            if record["source"] == "queue":
                writeback_mod.set_front_matter_fields(record["path"], fields)
                wrote.append(record["path"])
                pushed, push_err = writeback_mod.commit_and_push(
                    repo_dir, rel_path=rel_path,
                    message=f"取り下げ: {os.path.basename(record['path'])} post_id {post_id}（{by}）")
                if pushed:
                    push_err = None
            if record["source"] == "media":
                # THTH を通していない自分の投稿（11/23）。`sent/` には入れない（THTH が出したものに数えない）。
                wrote.append(sent_mod.write_outside_retraction(
                    state_dir, post_id, kind=record.get("kind") or "post", url=url,
                    text=record.get("text") or "", retracted_at=retracted_at,
                    retracted_by=by, retract_reason=reason, origin=origin,
                    posted_at=record.get("posted_at")))
            # `sent/` の記録があれば、こちらにも同じ 3 項目を足す（消さない）。
            elif sent_mod.read(state_dir, post_id) is not None:
                sent_mod.mark_retracted(state_dir, post_id, retracted_at=retracted_at,
                                        retracted_by=by, retract_reason=reason, origin=origin)
                wrote.append(sent_mod.path_for(state_dir, post_id))
    except lock_mod.LockBusy:
        return _fail(args, 1, f"{account_name} は既に実行中です（ロック取得失敗）。"
                              "--wait <秒> で空くのを待てます")

    payload = {"ok": True, "retracted": True, "account": account_name, "post_id": post_id,
               "deleted_id": result.get("deleted_id"), "url": url, "permalink": url,
               "retracted_at": retracted_at, "retracted_by": by, "retract_reason": reason,
               "records": wrote, "push_error": push_err}
    if getattr(args, "result_sink", None) is not None:
        args.result_sink(payload)
    elif args.json:
        _print_json(payload)
    else:
        print(f"{'記録しました（媒体の画面で既に消えていた）' if not delete else '取り下げました'}: "
              f"{account_name} post_id {post_id}（{retracted_at}・{by}）")
        for path in wrote:
            print(f"  記録に retracted_at / retracted_by / retract_reason を足しました: {path}")
        print("  sent/・runs・返信の台帳は消していません")
        if push_err:
            print(f"  ⚠ commit は残っていますが push できませんでした: {push_err}"
                  "（手で push してください）")
    return 0 if not push_err else 1
