"""`thth inflight <account> [show|resolve]`（設計 3.3.1 §4）。人が inflight を見て、決める口。

09-23、asmon-kanto-threads の inflight を masaru が `inflight.json` を手で消して
解いた。道具が自分で解けない（媒体に訊いても決まらない）ときに、人がその操作を
**記録の残る形で**するための口がここ。

- `show`: inflight の中身（原稿・始まり・container_id と post_id の有無・最後の
  問い合わせ結果）。**本文と token は出さない**（inflight には本文の指紋しか無い）。
- `resolve --not-published --by <名前>`: 人が媒体の画面で「出ていない」を確かめた。
  原稿は approved のまま残り、次の run が出し直す。
- `resolve --published <post_id> --by <名前>`: 出ていたのを人が見つけた。post_id を
  書き戻して解く（道具の自己解決と同じ 1 か所・`inflight_resolve.record_published()`）。

**二段確認**（`thth approve`・`thth retract` と同じ線）: `--confirm` が無ければ
何を解くかと digest を見せて何もしない（rc=1）。「出ていない」を取り違えると
次の run が 2 度出すので、打ち間違い 1 回で解けないようにする。解くときは
控え（`inflight.resolved-<日時>.json`）を state に残し、変更ログに
`inflight_resolved` を書く。**MCP には出さない**（人の判断・`approve` と同じ線）。

`build_parser()` には 1 行しか足さない（`inflight_cli.register(sub)`）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid

from . import accounts as accounts_mod
from . import approval as approval_mod
from . import inflight as inflight_mod
from . import inflight_resolve
from . import jst
from . import lock as lock_mod
from . import postid as postid_mod

_DIGEST_VERSION = "thth-inflight-resolve-1"
_SEP = "\x1f"

# 人が決めた解き方（runs の `inflight_resolution`・控えの `resolution`）。
HUMAN_NOT_PUBLISHED = "human_not_published"
HUMAN_PUBLISHED = "human_published"


def register(sub) -> None:
    p = sub.add_parser(
        "inflight",
        help="inflight を見る・人が確かめて解く（二段確認・控えと変更ログを残す）")
    p.add_argument("account")
    p.add_argument("action", nargs="?", choices=("show", "resolve"), default="show")
    decision = p.add_mutually_exclusive_group()
    decision.add_argument("--not-published", action="store_true",
                          help="媒体の画面で出ていないことを確かめた（原稿は approved のまま・次の run が出し直す）")
    decision.add_argument("--published", metavar="POST_ID", default=None,
                          help="出ていた投稿の post_id（書き戻して解く）")
    p.add_argument("--by", default=None, help="誰が確かめたか（記録に残す・resolve で必須）")
    p.add_argument("--confirm", default=None, help="一段目が表示した digest。これが無いと何もしない")
    p.add_argument("--json", action="store_true")
    p.add_argument("--wait", type=lock_mod.wait_seconds, default=0, help="ロックを待つ秒数（既定 0）")
    p.set_defaults(func=cmd_inflight)


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _fail(args, code: int, reason: str, message: str) -> int:
    """断るときは静的な理由と次の一手（`reason: message` の 1 行）。"""
    if args.json:
        _print_json({"ok": False, "error": reason, "message": message})
    else:
        print(f"{reason}: {message}", file=sys.stderr)
    return code


def summary(record: dict, *, now=None) -> dict:
    """`show` が出す中身。**本文・token・container の id そのものは出さない**。"""
    now = now if now is not None else jst.now_jst()
    started = jst.parse(record.get("started"))
    age = round((now - started).total_seconds() / 3600, 1) if started else None
    state = record.get("remote_state")
    return {
        "file": inflight_resolve.display_file(record),
        "started": jst.iso(started) if started else None,
        "age_hours": age,
        "container_id_present": bool(record.get("container_id")),
        "post_id_present": bool(record.get("post_id")),
        "remote_state": state if state in inflight_resolve.REMOTE_STATES else None,
        "remote_checked_at": record.get("remote_checked_at") if jst.parse(
            record.get("remote_checked_at")) else None,
        "remote_post_id_present": bool(record.get("remote_post_id")),
        "mismatch_fields": record.get("mismatch_fields") or None,
        "text_fingerprint_present": bool(record.get("text_fingerprint")),
        "media": bool(record.get("media")),
        "self_resolvable": inflight_resolve.is_self_resolvable(record),
    }


def digest(account: str, record: dict, decision: str, post_id: str | None) -> str:
    """いまの inflight（始まりの時刻と原稿）と決めたことに結ぶ digest。

    別の inflight（次に残ったもの）に古い digest を流用できないよう、`started`
    を入れる。先頭の版の語は他の口の digest と偶然にも一致させないため。
    """
    parts = [_DIGEST_VERSION, account, str(record.get("started") or ""),
             str(record.get("file") or ""), decision, post_id or ""]
    return hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()[
        :approval_mod.APPROVE_DIGEST_LENGTH]


def _next_step(account: str, info: dict) -> str:
    if info["media"]:
        return ("添付の inflight です。docs/原稿_添付_2.13.md の手順で確かめてください"
                "（この口では解きません）")
    if info["post_id_present"] or info["mismatch_fields"]:
        return (f"出ていますが記録できていません。原稿を確かめてから thth inflight {account}"
                " resolve --published <post_id> --by <名前>")
    return (f"媒体の画面で出たかどうかを確かめ、thth inflight {account} resolve"
            " --not-published か --published <post_id> で --by <名前> を付けて解いてください"
            "（次の run も 1 回だけ媒体に訊きます）")


def cmd_inflight(args) -> int:
    try:
        accounts_mod.validate_name(args.account)
        account_cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as e:
        return _fail(args, 2, "account_unreadable", str(e))
    state_dir = accounts_mod.state_dir_for(args.account)
    try:
        record = inflight_mod.read(state_dir)
    except (OSError, ValueError):
        return _fail(args, 2, "inflight_unreadable",
                     "inflight.json が読めません。壊れた inflight は道具では解きません"
                     "（中身を確かめてから手で直してください）")
    if args.action == "show":
        return _show(args, record)
    return _resolve(args, account_cfg, state_dir, record)


def _show(args, record) -> int:
    if record is None:
        if args.json:
            _print_json({"ok": True, "account": args.account, "inflight": None})
        else:
            print(f"{args.account}: inflight はありません")
        return 0
    info = summary(record)
    step = _next_step(args.account, info)
    if args.json:
        _print_json({"ok": True, "account": args.account, "inflight": info, "next": step})
        return 0
    print(f"{args.account}: inflight があります（公開の結果が分かっていません）")
    print(f"  原稿: {info['file'] or '（不明）'}")
    print(f"  始まり: {info['started'] or '（不明）'}"
          + (f"（{info['age_hours']} 時間前）" if info["age_hours"] is not None else ""))
    print(f"  container_id: {'あり' if info['container_id_present'] else 'なし'}"
          f" / post_id: {'あり（出ている・記録できていない）' if info['post_id_present'] else 'なし'}")
    if info["remote_state"]:
        print(f"  最後の問い合わせ: {info['remote_state']}（{info['remote_checked_at'] or '時刻不明'}）")
    else:
        print("  最後の問い合わせ: まだありません")
    if info["mismatch_fields"]:
        print(f"  食い違った項目: {', '.join(map(str, info['mismatch_fields']))}")
    if not info["text_fingerprint_present"]:
        print("  本文の指紋: なし（3.3.1 より前の inflight。一覧照合はできません）")
    print(f"  次の一手: {step}")
    return 0


def _resolve(args, account_cfg, state_dir, record) -> int:
    by = (args.by or os.environ.get("THTH_ACTOR") or "").strip()
    if not by:
        return _fail(args, 1, "by_required",
                     "--by <名前> を付けてください（誰が確かめたかを記録します）。"
                     "環境変数 THTH_ACTOR でも指定できます")
    if not args.not_published and not args.published:
        return _fail(args, 1, "decision_required",
                     "--not-published（出ていない）か --published <post_id>（出ていた）の"
                     "どちらかを付けてください。先に thth inflight "
                     f"{args.account} show で中身を見られます")
    if record is None:
        return _fail(args, 1, "no_inflight", f"{args.account} に inflight はありません（解くものが無い）")
    if record.get("media"):
        return _fail(args, 2, "media_inflight_unsupported",
                     "添付の inflight はこの口では解きません。docs/原稿_添付_2.13.md の手順で確かめてください")
    post_id = (args.published or "").strip() or None
    if post_id is not None and not postid_mod.is_usable(post_id):
        return _fail(args, 1, "post_id_invalid", "post_id の形が台帳に書けません。媒体の画面の数字（id）を確かめてください")
    if args.not_published and (record.get("post_id") or record.get("remote_post_id")):
        return _fail(args, 1, "published_known",
                     "この inflight は出たことが分かっています（post_id が記録にあります）。"
                     f"thth inflight {args.account} resolve --published <post_id> で解いてください")
    known = record.get("post_id") or record.get("remote_post_id")
    if post_id is not None and known and known != post_id:
        return _fail(args, 1, "post_id_differs",
                     "inflight に記録された post_id と違います。媒体の画面で確かめ、"
                     f"thth inflight {args.account} show の内容と合わせてください")
    decision = "not_published" if args.not_published else "published"
    expected = digest(args.account, record, decision, post_id)
    if not args.confirm:
        return _stage_one(args, record, decision, post_id, by, expected)
    if args.confirm != expected:
        return _fail(args, 1, "confirm_mismatch",
                     f"digest が一致しないので解きません（表示したものと中身が違います）。"
                     f"いまの digest は {expected} です。もう一度 thth inflight {args.account} resolve から")
    return _execute(args, account_cfg, state_dir, decision, post_id, by)


def _stage_one(args, record, decision, post_id, by, expected) -> int:
    info = summary(record)
    flag = "--not-published" if decision == "not_published" else f"--published {post_id}"
    command = (f"thth inflight {args.account} resolve {flag} --by "
               f"{json.dumps(by, ensure_ascii=False)} --confirm {expected}")
    if args.json:
        _print_json({"ok": False, "resolved": False, "stage": 1, "account": args.account,
                     "inflight": info, "decision": decision, "post_id": post_id, "by": by,
                     "digest": expected, "command": command})
        return 1
    print(f"解きません（確認の一段目です）: {args.account} の inflight（{info['file']}・{info['started']}）")
    if decision == "not_published":
        print("  決めること: 媒体に**出ていない**。原稿は approved のまま残り、次の run が出し直します")
        print("  ⚠ 出ていたのに解くと 2 度出ます。媒体の画面（自分の投稿の一覧）で確かめてから")
    else:
        print(f"  決めること: 媒体に出ていた（post_id {post_id}）。原稿に書き戻して解きます")
    print(f"  by: {by}")
    print(f"digest: {expected}")
    print(f"解くなら: {command}")
    return 1


def _execute(args, account_cfg, state_dir, decision, post_id, by) -> int:
    from . import admin_log, core
    run_id = uuid.uuid4().hex[:12]
    now = jst.now_jst()
    try:
        with core._account_locks(args.account, account_cfg, state_dir, wait=args.wait):
            # ロックの中でもう一度読む（別の run が解いた・別の inflight に替わった）。
            record = inflight_mod.read(state_dir)
            if record is None:
                return _fail(args, 1, "no_inflight", "もう inflight はありません（別の run が解きました）")
            if args.confirm != digest(args.account, record, decision, post_id):
                return _fail(args, 1, "inflight_changed",
                             "表示したあとに inflight が替わりました。もう一度 show から")
            if decision == "not_published":
                path = inflight_mod.archive(state_dir, record, resolved_at=jst.iso(),
                                            resolved_by=by, resolution=HUMAN_NOT_PUBLISHED)
                inflight_mod.clear(state_dir)
                inflight_resolve._run(args.account, state_dir, run_id, now, record,
                                      post_id=None, status="error",
                                      error="inflight_resolved_not_published",
                                      resolution=HUMAN_NOT_PUBLISHED)
                resolution = HUMAN_NOT_PUBLISHED
            else:
                ok, error = inflight_resolve.record_published(
                    args.account, account_cfg, state_dir, record, post_id,
                    posted_at=record.get("started") or jst.iso(), resolution=HUMAN_PUBLISHED,
                    resolved_by=by, run_id=run_id, now=now, log=lambda line: None)
                if not ok:
                    return _fail(args, 1, error or "writeback_failed", _WRITEBACK_NEXT.get(
                        error, "記録を書き戻せませんでした（inflight は残っています）。"
                               "thth board と repo の状態を確かめてください"))
                archived = inflight_mod.archives(state_dir)
                path = archived[-1] if archived else None
                resolution = HUMAN_PUBLISHED
    except lock_mod.LockBusy:
        return _fail(args, 1, "account_locked",
                     f"{args.account} は実行中です（ロック取得失敗）。--wait <秒> で空くのを待てます")
    try:
        admin_log.append("inflight_resolved", args.account, account_cfg, by=by,
                         diff={"inflight": ["present", "absent"],
                               "resolution": [None, resolution],
                               "post_id": [None, post_id]}, run_id=run_id)
        logged = True
    except Exception:  # noqa: BLE001 — 解いたことは取り消さない。書けなかったことだけ言う
        logged = False
    payload = {"ok": True, "resolved": True, "account": args.account, "resolution": resolution,
               "post_id": post_id, "by": by, "archive": path, "admin_log": logged}
    if args.json:
        _print_json(payload)
    else:
        print(f"inflight を解きました: {args.account}（{resolution}・{by}）")
        print(f"  控え: {path}")
        if decision == "not_published":
            print("  原稿は approved のまま残っています。次の run が出し直します")
        if not logged:
            print("  ⚠ 変更ログに書けませんでした（inflight は解いてあります）")
    return 0


_WRITEBACK_NEXT = {
    "text_mismatch_before_writeback": (
        "原稿が公開した時の内容と違うので書き戻しません。原稿に status: posted と"
        " post_id を手で書いて push してから、もう一度 resolve --published"),
    "repo_sync_failed": "repo を同期できません。thth pull <account> で原因を見てから、もう一度",
    "writeback_push_failed": ("記録の commit はできましたが push できませんでした"
                              "（inflight は残っています）。repo を直して push してから、もう一度"),
    "text_mismatch_after_rebase": "同期のあとに原稿が食い違いました。原稿を確かめてから、もう一度",
    "unusable_post_id": "post_id の形が台帳に書けません",
}
