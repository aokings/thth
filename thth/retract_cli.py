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
  - 手元に記録（queue か `sent/`）の無い `post_id` は取り下げない
    （THTH を通していない投稿には触らない）。
  - トークンに媒体の取り下げ権限（Threads `threads_delete`・X `tweet.write`・
    Mastodon `write:statuses`）が乗っていなければ rc=2（`thth auth` のやり直し）。
  - 取り下げを持たない媒体は rc=2「この媒体の取り下げは未対応」（Bluesky）。
    Mastodon は 3.1.1 から対応。
  - MCP には出さない（`approve` と同じ線）。

**DELETE が飛ぶ経路はこのモジュールの `_do_retract()` が呼ぶ `delete_post` だけ**
（各 adapter の `delete_post` は他のどこからも呼ばれない——
`tests/test_v21b_approved_writes.py` が source を見て固定する）。
"""
from __future__ import annotations

import json
import os
import sys

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
        help="公開済みの投稿を取り下げる（二段確認・記録は消さない・Threads／X／Mastodon）")
    p.add_argument("account")
    p.add_argument("post_id")
    p.add_argument("--reason", default=None, help="なぜ取り下げるか（記録に残す・必須）")
    p.add_argument("--by", default=None, help="誰が取り下げたか（記録に残す・必須）")
    p.add_argument("--confirm", default=None,
                   help="一段目が表示した digest。これが無いと何もしない")
    p.add_argument("--json", action="store_true")
    p.add_argument("--wait", type=lock_mod.wait_seconds, default=0, help="ロックを待つ秒数（既定 0）")
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

def cmd_location(args) -> int:
    """`thth location search <account> <語>`: 候補を 5 件まで名前と id で。読むだけ。"""
    try:
        account_cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as e:
        return _fail(args, 2, str(e))
    adapter_cls = adapters_mod.adapter_class(account_cfg.get("media"))
    permission = getattr(adapter_cls, "LOCATION_PERMISSION", None)
    if not permission:
        return _fail(args, 2, f"{account_cfg.get('media')}: この媒体に場所の検索はありません")
    token = accounts_mod.load_token(account_cfg)
    if not adapter_cls.has_token(token):
        return _fail(args, 2, f"{args.account}: token がありません（{adapter_cls.TOKEN_SETUP_HINT}）")
    missing = adapter_cls.missing_permissions(token, [permission])
    if missing:
        return _fail(args, 2, adapter_base.not_granted_message(missing[0]).replace(
            "<account>", args.account))
    try:
        adapter = adapters_mod.make_adapter(account_cfg, token)
        rows = adapter.location_search(args.query, limit=LOCATION_LIMIT)
    except adapter_base.PermissionMissing as e:
        return _fail(args, 2, _not_granted(args.account, e))
    except Exception as e:
        return _fail(args, 1, "場所の検索に失敗しました: " + redact_mod.redact(str(e)))

    if args.json:
        _print_json({"ok": True, "account": args.account, "query": args.query,
                     "locations": rows})
        return 0
    if not rows:
        print(f"「{args.query}」に一致する場所はありませんでした（0 件）")
    else:
        print(f"「{args.query}」の候補（{len(rows)} 件・最大 {LOCATION_LIMIT} 件）:")
        for row in rows:
            where = "・".join(x for x in (row.get("address"), row.get("city"),
                                       row.get("country")) if x)
            print(f"  {row.get('name') or '（名前なし）'}  id {row.get('id')}"
                  + (f"  （{where}）" if where else ""))
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
    adapter_cls = adapters_mod.adapter_class(media)
    if not getattr(adapter_cls, "DELETE_PERMISSION", None):
        return _fail(args, 2, f"{media}: この媒体の取り下げは未対応です"
                              "（取り下げられるのは Threads／X／Mastodon）。消すなら媒体の画面から手で")

    record = _find_record(account_cfg, args.account, post_id)
    if record is None:
        return _fail(args, 1, f"post_id {post_id} の記録が手元にありません"
                              "（queue にも sent/ にも無い）。**THTH を通していない投稿は"
                              "取り下げません**——消すなら媒体の画面から手で")
    fm = record["front_matter"]
    if fm.get("retracted_at"):
        return _fail(args, 1, f"post_id {post_id} は既に取り下げ済みです"
                              f"（{fm.get('retracted_at')}・{fm.get('retracted_by')}）")

    digest = approval_mod.compute_retract_digest(
        post_id=post_id, account=args.account, reason=reason)

    token = accounts_mod.load_token(account_cfg)
    url = record.get("url") or _lookup_url(account_cfg, token, post_id)

    if not args.confirm:
        # ---- 一段目: 見せるだけ。何もしない・DELETE 0 回 ----
        if args.json:
            _print_json({"ok": False, "retracted": False, "stage": 1,
                         "account": args.account, "post_id": post_id, "url": url,
                         "source": record["source"], "file": record["path"],
                         "text": record["text"], "reason": reason, "by": by,
                         "digest": digest})
            return 1
        print(f"取り下げません（確認の一段目です）: {args.account} post_id {post_id}")
        print(f"  記録: {record['source']}（{record['path']}）")
        print(f"  URL : {url or '（記録に無く、引けませんでした。thth posts で確かめられます）'}")
        print(f"  理由: {reason}")
        print(f"  by  : {by}")
        print("--- 取り下げる本文 ---")
        text = record["text"]
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        print("--- ここまで ---")
        if not approval_mod.is_true(account_cfg.get("production")):
            print("  ⚠ 台帳に production: true が無いので、二段目でも DELETE は呼びません")
        print(f"digest: {digest}")
        print(f"この投稿を取り下げるなら: thth retract {args.account} {post_id} "
              f"--reason {json.dumps(reason, ensure_ascii=False)} --by "
              f"{json.dumps(by, ensure_ascii=False)} --confirm {digest}")
        return 1

    # ---- 二段目 ----
    if args.confirm != digest:
        return _fail(args, 1, f"digest が一致しないので取り下げません（表示したものと"
                              f"中身が違います）。いまの digest は {digest} です。"
                              "もう一度 thth retract からやり直してください")
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


def _do_retract(args, account_cfg, adapter_cls, token, record, post_id, *,
                reason, by, url, before_execute=None, lock_context=None) -> int:
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
            # `sent/` の記録があれば、こちらにも同じ 3 項目を足す（消さない）。
            if sent_mod.read(state_dir, post_id) is not None:
                sent_mod.mark_retracted(state_dir, post_id, retracted_at=retracted_at,
                                        retracted_by=by, retract_reason=reason)
                wrote.append(sent_mod.path_for(state_dir, post_id))
    except lock_mod.LockBusy:
        return _fail(args, 1, f"{account_name} は既に実行中です（ロック取得失敗）。"
                              "--wait <秒> で空くのを待てます")

    payload = {"ok": True, "retracted": True, "account": account_name, "post_id": post_id,
               "deleted_id": result.get("deleted_id"), "url": url,
               "retracted_at": retracted_at, "retracted_by": by, "retract_reason": reason,
               "records": wrote, "push_error": push_err}
    if getattr(args, "result_sink", None) is not None:
        args.result_sink(payload)
    elif args.json:
        _print_json(payload)
    else:
        print(f"取り下げました: {account_name} post_id {post_id}（{retracted_at}・{by}）")
        for path in wrote:
            print(f"  記録に retracted_at / retracted_by / retract_reason を足しました: {path}")
        print("  sent/・runs・返信の台帳は消していません")
        if push_err:
            print(f"  ⚠ commit は残っていますが push できませんでした: {push_err}"
                  "（手で push してください）")
    return 0 if not push_err else 1
