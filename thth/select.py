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

from . import adapters as adapters_mod
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
    承認 gate は無視しない・§3.7）。

    段を 2 つに割り、順序を固定する（外部レビュー第 3 巡 P2）:

    1. **妥当性**（`_validate_all()`）: 時刻に一切依存しない。**全ファイルについて
       必ず計算する**。型外・account 違い・post_id あり・status 違い・
       approval_stale・節が無い・文字数超過・トピック不正・重複本文・publish_at
       が壊れている、のどれかに当たれば、ここで理由付きで落とす（`needs_review`・
       `rejections` は時刻に関係なく確定する）。
    2. **出せるか**（`_apply_timing_gate()`）: 1 を通った候補にだけ、publish_at が
       未来か・静かな時間帯か・最短間隔内か・`stale_days` を超えているか、という
       「いま出すか」の関門を適用する。診断（1）には一切影響しない。

    以前は 1 段目のループの中に `publish_at > now` の `continue` が混ざっていて、
    未来に予約された承認は**妥当性検査（approval_stale 等）に一度も届かずに**
    落ちていた。承認後に本文を書き換えても、publish_at が未来である間は
    `approval_stale` が見えない——board の実行時刻によって「見える／見えない」が
    変わる事故だった（外部レビュー第 3 巡 P2）。段を分けて順序を固定することで、
    diagnostics が時刻の関門より先に必ず走るようにする。

    **1 の前に時刻を見る条件を足さないこと。** 1 段目（`_validate_all()`）に
    `now` を渡していないのは事故ではない——渡せば「時刻を見る条件」を書ける
    余地ができてしまい、同じ穴がまた開く。時刻に関わる判定は必ず 2 段目
    （`_apply_timing_gate()`）に書く。
    """
    validated, rejections, type_mismatch, needs_review = _validate_all(
        files, account_name=account_name, account_cfg=account_cfg,
        recent_texts=recent_texts)

    if not validated:
        return SelectResult(None, rejections, type_mismatch, needs_review)

    return _apply_timing_gate(
        validated, rejections=rejections, type_mismatch=type_mismatch,
        needs_review=needs_review, account_cfg=account_cfg, now=now,
        last_post_at=last_post_at, bypass_pace=bypass_pace)


def _validate_all(files, *, account_name: str, account_cfg: dict, recent_texts: set):
    """妥当性検査（時刻に一切依存しない・全ファイルについて必ず計算する）。

    `now` を引数に取らない。ここに時刻を見る条件を足すと、1 段目で候補が
    落ちて 2 段目（時刻の関門）に届かなくなる——それが今回の穴の形そのもの
    なので、この関数のシグネチャで物理的に塞ぐ。

    **`approved` なのに内容起因で落ちたものは、理由が何であれ `needs_review` に
    積む**（外部レビュー第 4 巡 P2）。以前は `approval_stale` と
    `publish_at_invalid` だけを積んでいたので、条件が重なると board から診断が
    消えた——「8 日前の承認済み」と「同じ本文を昨日投稿済み」が重なると、
    `duplicate_text` で先に落ちて `stale`（要確認）まで届かず、board は
    `approved_waiting: 1`・要確認 0 件、つまり**何も問題が無いように見えた**。
    条件の並び順が board の見え方を変えてしまう形そのものを潰す: 承認済みなのに
    出ないものは、落ちた理由に関係なく必ず人に見える。**時間が経てば解決する
    理由（`future`・`quiet_hours`・`min_interval`）だけが要確認に積まれない**
    （それらは 2 段目・`_apply_timing_gate()` にある）。

    戻り値: `(validated, rejections, type_mismatch, needs_review)`。
    `validated` は `(qf, publish_at, section)` のリスト（publish_at はまだ
    「未来かどうか」を判定していない・型として妥当なだけ）。
    """
    rejections: list[Rejection] = []
    type_mismatch: list = []
    needs_review: list = []
    validated = []

    media = account_cfg["media"]
    hashtags_allowed = bool(account_cfg.get("hashtags", True))

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

        # 5. publish_at の型（+09:00 無し／壊れた文字列）。「未来かどうか」は
        # ここでは見ない（時刻の関門は 2 段目・`_apply_timing_gate()`）。
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

        # 6. 媒体の節が無い／文字数超過
        section = queuefile.extract_section(qf.body, media)
        if section is None:
            rejections.append(Rejection(path, "no_section"))
            needs_review.append(path)
            continue

        # 6b. 承認を「見た本文」に結び付ける（外部レビュー §1・受け入れ 1〜4）。
        # `status: approved` だけでは、承認したあとに本文・account・reply_to・
        # topic・publish_at を書き換えても検知できない。approved_sha が無い・
        # いまの内容と食い違う場合は「承認が古い」として落とし、needs_review にも
        # 入れる（黙って出さない・黙って通さない）。post_id を書く THTH 自身の
        # 書き戻し（status: posted にする）はこの検査より前（条件 1）で候補から
        # 落ちているので、ここで詰まることはない。**publish_at が未来でもここまで
        # 必ず届く**（時刻の関門より前・外部レビュー第 3 巡 P2）。
        # 任意項目（`location_id`・`share_to_instagram`・設計 v2 §4.3）も指紋に
        # 入る——承認したあとに場所や共有を足す・変えると、ここで落ちる。
        approved_sha = fm.get("approved_sha")
        expected_sha = approval_mod.compute_approved_sha(
            section=section, account=account_name, reply_to=fm.get("reply_to"),
            topic=fm.get("topic"), publish_at=publish_at,
            **approval_mod.publish_options(fm))
        if not approved_sha or approved_sha != expected_sha:
            rejections.append(Rejection(path, "approval_stale"))
            needs_review.append(path)
            continue

        # 6d. Instagram 共有は台帳に `instagram_linked: true` が要る（v2.1-B）。
        # lint も断るが、**承認のあとで台帳から外した**ときは lint を通らない
        # ——公開の側を変える指示を、台帳の前提が消えたまま出さない。
        if (approval_mod.is_true(fm.get("share_to_instagram"))
                and not approval_mod.is_true(account_cfg.get("instagram_linked"))):
            rejections.append(Rejection(path, "instagram_not_linked"))
            needs_review.append(path)
            continue

        # 6c. 読んだ中身が、同期を確認した commit の中身と一致するか（外部レビュー
        # 第 4 巡 P1・`core.list_queue_files()` が `writeback.matches_synced_commit()`
        # で立てる）。`sync_repo()` が確かめるのは **commit の一致**であって、
        # **これから読むファイルの一致**ではない——remote で承認を撤回して正常に
        # pull できたあと（`HEAD == @{u}` も成立）でも、作業ツリーに撤回前の
        # 承認済みファイルが残っていれば、それが選ばれて公開される（実際に再現した）。
        # 復元された古いファイルは `approved_sha` が自分の中身と整合しているので
        # 6b では捕まらない。未 commit・staged・追跡外・無視・HEAD に無い、の
        # どれであっても同じ理由で落とす。**何も消さない**——候補から外して
        # board に出すだけ。
        #
        # 6b（承認の古さ）より **後** に置くのは、承認したあとに手元で書き換えた
        # 場合に「承認が古い」という具体的な理由のほうを出したいから（どちらも
        # 落とすことに変わりはなく、要確認にも必ず積まれる）。
        if not qf.verified:
            rejections.append(Rejection(path, "unverified_content"))
            needs_review.append(path)
            continue

        # **上限は媒体の既定を台帳で上書きできる**（設計 v2 §4.2「台帳と登録」）。
        # Mastodon はインスタンスごとに上限が違うので、`MEDIA_LIMITS` の既定
        # （mastodon 500）を account ごとに `char_limit` で差し替える。
        limit = queuefile.limit_for(media, account_cfg)
        # **数え方も媒体ごと**（`limit_for()` と対・引継ぎ 2026-09-15 §3-D）。
        # Bluesky の 300 は grapheme——Threads の数え方で測ると、絵文字の多い
        # 本文が「超えている」と落ちていた。
        n = queuefile.count_for(media, section)
        if n > limit:
            rejections.append(Rejection(path, f"too_long({n})"))
            needs_review.append(path)
            continue

        # 7. hashtags: false なのに `#` 語がある
        if not hashtags_allowed and queuefile.has_hashtag(section):
            rejections.append(Rejection(path, "hashtag"))
            needs_review.append(path)
            continue

        # 7b. topic（`topic_tag`）が検査に落ちる（T2c・設計 §2.2・masaru 裁定
        # 2026-09-09: 全アカウントで使う）。省略・空はスキップ（許す）。条件 6・7 と
        # 同じ流儀: 切り詰めない・勝手に外さない。落として理由を runs に残す
        # （core._is_error_reason() が `topic_` 始まりを実エラーとして拾う）。
        #
        # **topic を持たない媒体では検査しない**（設計 v2 §4.2）。1 つの queue
        # ファイルを Threads と Bluesky の 2 account が拾う形（設計 v1 §8-16）で、
        # Threads 用に書いた `topic` が Bluesky 側で `topic_too_long` を出すと、
        # **同じ原稿が媒体によって落ちる**。**topic を使う媒体だけが検査する**
        # ——使わない媒体は黙って無視する（`base.Post.topic` の但し書きと同じ）。
        if "topic" in adapters_mod.capabilities_for(media):
            topic = queuefile.normalize_topic(fm.get("topic"))
            if topic is not None:
                topic_err = queuefile.topic_error(topic)
                if topic_err is not None:
                    rejections.append(Rejection(path, topic_err))
                    needs_review.append(path)
                    continue

        # 8. 直近 30 日の投稿済み本文と完全一致
        if section.strip() in recent_texts:
            rejections.append(Rejection(path, "duplicate_text"))
            needs_review.append(path)
            continue

        validated.append((qf, publish_at, section))

    return validated, rejections, type_mismatch, needs_review


def _apply_timing_gate(validated, *, rejections: list, type_mismatch: list,
                        needs_review: list, account_cfg: dict,
                        now: datetime.datetime,
                        last_post_at: datetime.datetime | None,
                        bypass_pace: bool) -> SelectResult:
    """「いま出せるか」だけを決める（診断にはもう影響しない）。

    `_validate_all()` を通った候補（`validated`）にだけ適用する。ここで落ちても
    `needs_review`／`rejections` には（`stale` を除いて）積まない——妥当性の
    診断は 1 段目で確定済みで、ここは純粋にタイミングの話だから。
    """
    stale_days = account_cfg.get("stale_days", 7)
    survivors = []

    for qf, publish_at, section in validated:
        # 9. publish_at が未来 → 出せる時刻ではない（診断には影響しない）
        if publish_at > now:
            rejections.append(Rejection(qf.path, "future"))
            continue

        # 10. publish_at から stale_days 超 → 時刻に依存するのでここに置くが、
        # 要確認としては出す（現行どおり）。
        if now - publish_at > datetime.timedelta(days=stale_days):
            rejections.append(Rejection(qf.path, "stale"))
            needs_review.append(qf.path)
            continue

        survivors.append((qf, publish_at, section))

    if not survivors:
        return SelectResult(None, rejections, type_mismatch, needs_review)

    # 11. 静かな時間帯 → 妥当性を通った候補（survivors）を全部落とす（出せる時刻
    # ではない、というだけ。妥当性の診断はすでに確定しているので変えない）。
    if not bypass_pace and in_quiet_hours(now, account_cfg["quiet_hours"]):
        for qf, _publish_at, _section in survivors:
            rejections.append(Rejection(qf.path, "quiet_hours"))
        return SelectResult(None, rejections, type_mismatch, needs_review)

    # 12. 前回投稿から min_interval_hours 未満 → 同じく妥当性を通った候補を全部落とす
    if not bypass_pace and last_post_at is not None:
        min_interval = datetime.timedelta(hours=account_cfg["min_interval_hours"])
        if now - last_post_at < min_interval:
            for qf, _publish_at, _section in survivors:
                rejections.append(Rejection(qf.path, "min_interval"))
            return SelectResult(None, rejections, type_mismatch, needs_review)

    survivors.sort(key=lambda t: (t[1], os.path.basename(t[0].path)))
    chosen_qf, _chosen_publish_at, chosen_section = survivors[0]
    return SelectResult(chosen_qf, rejections, type_mismatch, needs_review, section=chosen_section)
