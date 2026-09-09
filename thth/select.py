"""出す 1 件を決める（発注 §3・設計 §3.3・受け入れ 5）。

条件は **順序が意味を持つ**。上から順に落として、残った中で publish_at 昇順、
同時刻ならファイル名順で先頭 1 件。post_id が入っていれば status を見る前に落とす
（approved に戻されても出ない・§3.5）。

条件 8b（外部レビュー §1・受け入れ 1〜4）: `approved_sha`（`thth approve` が書く・
`thth.approval.compute_approved_sha()`）がいまの内容と食い違えば「承認が古い」として
落とす（`approval_stale`）。承認したあとに本文・account・reply_to・topic・
publish_at のどれかを書き換えると、masaru が見た本文とは別物として扱われる。
"""
from __future__ import annotations

import dataclasses
import datetime
import os

from . import approval as approval_mod
from . import queuefile


@dataclasses.dataclass
class Rejection:
    file: str
    reason: str


@dataclasses.dataclass
class SelectResult:
    chosen: queuefile.QueueFile | None
    rejections: list
    type_mismatch: list
    needs_review: list
    section: str | None = None


def _parse_hhmm(value: str) -> datetime.time:
    h, m = value.split(":")
    return datetime.time(int(h), int(m))


def in_quiet_hours(now: datetime.datetime, quiet_hours) -> bool:
    """`quiet_hours: [start, end]`。start > end なら日をまたぐ（例 22:00〜07:00）。

    `null`（None）または空配列は「静かな時間帯を設けない」の意味（設計 §3.6:
    頻度・時刻・本数は編集の判断であって基盤の礼儀ではない。台帳の `0`・`null`
    で外せる）。この場合は常に False を返す（=いつでも出してよい）。
    """
    if not quiet_hours:
        return False
    start = _parse_hhmm(quiet_hours[0])
    end = _parse_hhmm(quiet_hours[1])
    cur = datetime.time(now.hour, now.minute)
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end


def recent_posted_texts(files, *, account_name: str, media: str,
                         now: datetime.datetime, days: int = 30) -> set:
    """同一アカウントで直近 `days` 日に投稿済み（status: posted）の本文集合（§3.5）。"""
    out = set()
    cutoff = now - datetime.timedelta(days=days)
    for qf in files:
        if qf.malformed:
            continue
        fm = qf.front_matter
        if fm.get("account") != account_name or fm.get("status") != "posted":
            continue
        posted_at_raw = fm.get("posted_at")
        if not posted_at_raw:
            continue
        try:
            posted_at = queuefile.parse_publish_at(posted_at_raw)
        except ValueError:
            continue
        if posted_at < cutoff:
            continue
        section = queuefile.extract_section(qf.body, media)
        if section:
            out.add(section.strip())
    return out


def select_one(files, *, account_name: str, account_cfg: dict,
                now: datetime.datetime, last_post_at: datetime.datetime | None,
                recent_texts: set, bypass_pace: bool = False) -> SelectResult:
    """§3.3 の 11 条件。`bypass_pace=True` は `--now`（静かな時間帯・最短間隔だけ無視。
    承認 gate は無視しない・§3.7）。"""
    rejections: list[Rejection] = []
    type_mismatch: list = []
    needs_review: list = []
    candidates = []

    for qf in files:
        fm = qf.front_matter
        path = qf.path

        # 1. post_id が入っている → 落とす（status より先に見る）
        if fm.get("post_id"):
            rejections.append(Rejection(path, "post_id_present"))
            continue

        # 2. thth: が無い・front-matter が壊れている → 型外（失敗に数えない）
        if qf.malformed:
            type_mismatch.append(path)
            continue

        # 3. account が対象と違う
        if fm.get("account") != account_name:
            rejections.append(Rejection(path, "account_mismatch"))
            continue

        # 4. status != approved
        if fm.get("status") != "approved":
            rejections.append(Rejection(path, "not_approved"))
            continue

        # 5. publish_at 未来／+09:00 無し／壊れた文字列
        # `rejections` にも積む（`type_mismatch`・`needs_review` だけだと board が
        # 「なぜ要確認か」を機械可読な理由付きで拾えない・外部レビュー再レビュー C）。
        publish_at_raw = fm.get("publish_at")
        if not publish_at_raw or "+09:00" not in publish_at_raw:
            type_mismatch.append(path)
            needs_review.append(path)  # approved なのに型外
            rejections.append(Rejection(path, "publish_at_invalid"))
            continue
        try:
            publish_at = queuefile.parse_publish_at(publish_at_raw)
        except ValueError:
            type_mismatch.append(path)
            needs_review.append(path)
            rejections.append(Rejection(path, "publish_at_invalid"))
            continue
        if publish_at > now:
            rejections.append(Rejection(path, "future"))
            continue

        candidates.append((qf, publish_at))

    if not candidates:
        return SelectResult(None, rejections, type_mismatch, needs_review)

    # 6・7（静かな時間帯・最短間隔＝「いま出せるか」）は、8 以降（内容・承認の
    # 妥当性）とは**別の軸**として扱う（外部レビュー再々レビュー P2・3）。
    #
    # 以前はここで静かな時間帯・最短間隔を先に見て、全滅していれば即 return して
    # いた。そのため `approval_stale`（承認後に本文・account 等が書き換わった）
    # などの内容診断が、静かな時間帯にはまったく走らなかった——board の実行時刻
    # （06:20 JST）がちょうど静かな時間帯（22:00〜07:00）の中に入っているため、
    # 「朝に見る画面が、朝には必ず何も出さない」という事故になっていた
    # （`needs_review`・`approval_stale_count` が時刻に依存してしまう）。
    #
    # ここでは先に 8 以降の妥当性検査を**全候補について**行い、`needs_review` と
    # `rejections`（内容起因の理由）を時刻に関係なく確定させる。**そのあとで**
    # 静かな時間帯・最短間隔を見て、妥当性を通った候補（survivors）から実際に
    # 選ぶかどうかだけを決める（pacing は「いま出すかどうか」だけを決める）。
    # 静かな時間帯には出さない、という既存の挙動そのものは変えない
    # （`chosen` は引き続き None になる）。

    survivors = []
    media = account_cfg["media"]
    stale_days = account_cfg.get("stale_days", 7)
    hashtags_allowed = bool(account_cfg.get("hashtags", True))

    for qf, publish_at in candidates:
        fm = qf.front_matter

        # 8. 媒体の節が無い／文字数超過
        section = queuefile.extract_section(qf.body, media)
        if section is None:
            rejections.append(Rejection(qf.path, "no_section"))
            continue

        # 8b. 承認を「見た本文」に結び付ける（外部レビュー §1・受け入れ 1〜4）。
        # `status: approved` だけでは、承認したあとに本文・account・reply_to・
        # topic・publish_at を書き換えても検知できない。approved_sha が無い・
        # いまの内容と食い違う場合は「承認が古い」として落とし、needs_review にも
        # 入れる（黙って出さない・黙って通さない）。post_id を書く THTH 自身の
        # 書き戻し（status: posted にする）はこの検査より前（条件 1）で候補から
        # 落ちているので、ここで詰まることはない。
        approved_sha = fm.get("approved_sha")
        expected_sha = approval_mod.compute_approved_sha(
            section=section, account=account_name, reply_to=fm.get("reply_to"),
            topic=fm.get("topic"), publish_at=publish_at)
        if not approved_sha or approved_sha != expected_sha:
            rejections.append(Rejection(qf.path, "approval_stale"))
            needs_review.append(qf.path)
            continue

        limit = queuefile.MEDIA_LIMITS.get(media, 500)
        n = queuefile.char_count(section)
        if n > limit:
            rejections.append(Rejection(qf.path, f"too_long({n})"))
            continue

        # 9. hashtags: false なのに `#` 語がある
        if not hashtags_allowed and queuefile.has_hashtag(section):
            rejections.append(Rejection(qf.path, "hashtag"))
            continue

        # 9b. topic（`topic_tag`）が検査に落ちる（T2c・設計 §2.2・masaru 裁定
        # 2026-09-09: 全アカウントで使う）。省略・空はスキップ（許す）。条件 8・9 と
        # 同じ流儀: 切り詰めない・勝手に外さない。落として理由を runs に残す
        # （core._is_error_reason() が `topic_` 始まりを実エラーとして拾う）。
        topic = queuefile.normalize_topic(fm.get("topic"))
        if topic is not None:
            topic_err = queuefile.topic_error(topic)
            if topic_err is not None:
                rejections.append(Rejection(qf.path, topic_err))
                continue

        # 10. publish_at から stale_days 超
        if now - publish_at > datetime.timedelta(days=stale_days):
            rejections.append(Rejection(qf.path, "stale"))
            needs_review.append(qf.path)
            continue

        # 11. 直近 30 日の投稿済み本文と完全一致
        if section.strip() in recent_texts:
            rejections.append(Rejection(qf.path, "duplicate_text"))
            continue

        survivors.append((qf, publish_at, section))

    if not survivors:
        return SelectResult(None, rejections, type_mismatch, needs_review)

    # 6. 静かな時間帯 → 妥当性を通った候補（survivors）を全部落とす（出せる時刻
    # ではない、というだけ。妥当性の診断はすでに確定しているので変えない）。
    if not bypass_pace and in_quiet_hours(now, account_cfg["quiet_hours"]):
        for qf, _publish_at, _section in survivors:
            rejections.append(Rejection(qf.path, "quiet_hours"))
        return SelectResult(None, rejections, type_mismatch, needs_review)

    # 7. 前回投稿から min_interval_hours 未満 → 同じく妥当性を通った候補を全部落とす
    if not bypass_pace and last_post_at is not None:
        min_interval = datetime.timedelta(hours=account_cfg["min_interval_hours"])
        if now - last_post_at < min_interval:
            for qf, _publish_at, _section in survivors:
                rejections.append(Rejection(qf.path, "min_interval"))
            return SelectResult(None, rejections, type_mismatch, needs_review)

    survivors.sort(key=lambda t: (t[1], os.path.basename(t[0].path)))
    chosen_qf, _chosen_publish_at, chosen_section = survivors[0]
    return SelectResult(chosen_qf, rejections, type_mismatch, needs_review, section=chosen_section)
