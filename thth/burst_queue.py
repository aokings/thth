"""返信の burst の順番待ち（3.25.0・kopicha 10/16）。

masaru が「ぜんぶOK」と言った返信 4 件を `thth send --reply-to` で続けて出したら、3 件目のあとに
burst（10 分に 3 件）で口座が止まり、予約まで止まった。**返信は口座を止めない**: burst を超える
返信は、その 1 件を queue に「承認済み」で置き、`publish_at` を burst の窓が空く時刻
（`guard.burst_slot`）にして timer に出させる。止めるのは投稿（返信でないもの）の連投だけ。

- 承認: `thth send --production`（手元は digest の `--confirm` を通ったあと）・thth.me の
  `send_request` で頼まれた時点で承認されている（3.12.0「利用者とその LLM を疑わない」）。queue に
  置くときは `approval.approved_fields` の形で刻み（4.4.1 から VM の承認の台帳に）、印 `queued_for: burst` を
  原稿に足す（指紋には入らない・
  `held_minutes` の前例と同じく /activity と `thth schedule` が見分けるための印）。
- timer に載っていない口座（`scheduled: false`）・添付のある返信は並べない（口座を止めずに
  `burst_limit` で断る）。はてなの口座は対象外（返信が無い・`guard._check_hatena`）。
- timer はこの印のある原稿に最短間隔（`min_interval_hours`）を掛けない（返信には掛けない
  裁定 (a) と同じ・`select._apply_timing`）。
"""
from __future__ import annotations

import os
import secrets

from . import jst, queuefile

MARK_KEY = "queued_for"
MARK_VALUE = "burst"


def is_queued(front_matter) -> bool:
    """burst の順番待ちに並べた原稿か（`queued_for: burst`）。"""
    return (front_matter or {}).get(MARK_KEY) == MARK_VALUE


def pending(account, cfg, *, now=None) -> list:
    """まだ出ていない順番待ちの返信の `publish_at`（読むだけ・読めない queue は空）。"""
    from . import core
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    try:
        files = core.list_queue_files(cfg, tree_sha=None)
    except (OSError, ValueError, KeyError, TypeError):
        return []
    out = []
    for qf in files:
        fm = qf.front_matter
        if (qf.malformed or fm.get("account") != account or fm.get("status") != "approved"
                or fm.get("post_id") or not is_queued(fm)):
            continue
        try:
            at = queuefile.parse_publish_at(fm.get("publish_at"))
        except (TypeError, ValueError):
            continue
        out.append(jst.to_jst(at))
    return out


def can_queue(cfg) -> bool:
    """timer に載っている口座か（`scheduled: false` は刻んでも出ない）。"""
    return cfg.get("scheduled", True) is not False


def message(account, publish_at) -> str:
    """人向けの 1 行。"""
    when = jst.to_jst(publish_at).strftime("%H:%M")
    return (f"{account}: burst に当たったので {when}（JST）に出す順番待ちに並べました。止めていません"
            "（取り消しは https://thth.me/activity か thth revoke）")


def _replies_on(account, cfg, day) -> int:
    """その日に刻んだ（承認済みでまだ出ていない）返信の原稿の数（順番待ちに限らない・thth.me の schedule と同じ数え方）。"""
    from . import core
    from .select import is_reply
    count = 0
    for qf in core.list_queue_files(cfg, tree_sha=None):
        fm = qf.front_matter
        if (qf.malformed or fm.get("account") != account or fm.get("status") != "approved" or fm.get("post_id")
                or not is_reply(fm)):
            continue
        try:
            at = queuefile.parse_publish_at(fm.get("publish_at"))
        except (TypeError, ValueError):
            continue
        if jst.to_jst(at).date() == day:
            count += 1
    return count


def queue_cli(account, cfg, *, text, topic, reply_to, publish_at, goal=None, reply_to_root=None,
              reply_to_author_key=None, found_by=None, now=None, gate=None) -> dict:
    """運営者の CLI（手元・VM の `thth send`）の道: queue に承認済みで置いて commit・push する。

    呼び手は account と repo のロックを持っている（`core._send_locked` の中）。戻り値は
    `{"file", "publish_at"}`。断るときは `guard.GuardRefused`（1 日の上限）か `ValueError`（符丁）。
    """
    from . import approval, guard, writeback
    from .cli import _prepare_one
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    # 1 日の上限は返信だけの日の上限（`reply.daily`・4.3 監査 中-1）: その日の返信 + 並べた返信。並べずに断る。
    day = jst.to_jst(publish_at).date()
    guard.check_reply_daily(account, cfg, day=day, now=now, queued=_replies_on(account, cfg, day))
    repo_dir = cfg["repo_dir"]
    queue_dir = os.path.join(repo_dir, cfg["queue_dir"])
    os.makedirs(queue_dir, exist_ok=True)
    head = {"thth": "1", "account": account, "status": "draft", "publish_at": jst.iso(publish_at)}
    for key, value in (("topic", topic), ("reply_to", reply_to), ("reply_to_root", reply_to_root),
                       ("reply_to_author_key", reply_to_author_key), ("found_by", found_by),
                       ("goal", goal if goal not in (None, "none") else None)):
        if value:
            head[key] = str(value)
    for key, value in head.items():
        writeback.check_front_matter_field(key, value)
    body = (text or "").strip()
    rendered = "---\n" + "".join(f"{k}: {v}\n" for k, v in head.items()) + "---\n\n## " + cfg["media"] + "\n\n" + body + "\n"
    if (queuefile.extract_section(queuefile.parse_text(rendered, "burst").body, cfg["media"]) or "").strip() != body:
        raise ValueError("body_not_representable")
    name = f"burst-reply-{jst.to_jst(now).strftime('%Y%m%d-%H%M')}-{secrets.token_hex(4)}.md"
    path = os.path.join(queue_dir, name)
    with open(path, "x", encoding="utf-8") as f:
        f.write(rendered)
    try:
        prepared, problem = _prepare_one(path)
        if problem or not prepared or prepared.get("bundle"):
            raise ValueError("invalid_draft")
        by = os.environ.get("THTH_ACTOR") or "thth send"
        try:
            writeback.check_front_matter_field("approved_by", by)
        except ValueError:
            by = "thth send"
        fields = approval.approved_fields(prepared, by, jst.iso(now))
        fields["approved_via"] = "cli"
        # 印（`queued_for: burst`）は原稿に書く（新しく置く原稿そのものなので commit は 1 本のまま）。
        writeback.set_front_matter_fields(path, {MARK_KEY: MARK_VALUE})
        # 承認は 4.4.1 から VM の台帳（repo に書かない）。書けなければ原稿も置かない。
        from . import approval_ledger
        approval_ledger.record([{"account": account, "file": approval_ledger.rel_key(path, repo_dir),
                                 "state": approval_ledger.APPROVED, "fields": fields, "via": "cli",
                                 "repo": repo_dir, "gate": gate}])
    except Exception:
        os.unlink(path)
        raise
    # 置いた原稿（下書きの形）だけを commit・push する（新しい原稿なので commit は今までどおり 1 本）。
    pushed, err = writeback.commit_and_push(
        repo_dir, rel_path=os.path.relpath(path, repo_dir),
        message=f"burst の順番待ち: {name}（{account}）")
    return {"file": path, "publish_at": jst.iso(publish_at), "pushed": pushed, "push_error": err or None}
