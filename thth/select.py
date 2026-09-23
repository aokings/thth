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
from . import postid as postid_mod
from . import queuefile
from . import tags as tags_mod


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
    # `chosen` が `reply_to_file` を持つときだけ `{"file": 名前, "post_id": 解決した
    # post_id}`（設計 3.2.0 §2）。**解決できていない候補は chosen にならない**ので、
    # `reply_to_file` を持つ chosen には必ずこれが付く（`core._throw_chosen()` も
    # 付いていなければ出さない）。
    resolution: dict | None = None


# --------------------------------------------------------------------------
# reply_to_file の解決（設計 3.2.0 §2）
# --------------------------------------------------------------------------

# 解決できない理由の前置き（静的）。後ろに `waiting_for <名前>`・`target_retracted`・
# `target_unreadable`・`target_missing` のどれかが付く。
UNRESOLVED = "reply_to_unresolved"
WAITING_FOR = "waiting_for"
TARGET_RETRACTED = "target_retracted"
TARGET_UNREADABLE = "target_unreadable"
TARGET_MISSING = "target_missing"


def unresolved(detail: str) -> str:
    return f"{UNRESOLVED}: {detail}"


def waiting_target(reason) -> str | None:
    """理由が「指した原稿が出るのを待っている」なら、その原稿の名前。違えば None。"""
    prefix = unresolved(WAITING_FOR) + " "
    if isinstance(reason, str) and reason.startswith(prefix):
        return reason[len(prefix):]
    return None


@dataclasses.dataclass
class ReplyResolution:
    name: str | None          # reply_to_file の値（併用・名前の形の誤りでも入れる）
    post_id: str | None       # 解決した post_id（解決できなければ None）
    reason: str | None        # None なら解決・それ以外は断る理由（静的な符丁）


def _target_from_pool(qf, name: str, pool):
    """候補と同じ queue（同じディレクトリ）の `name` を、読んである一覧から探す。

    **一覧から探す**のは、`core.list_queue_files()` が同期を確かめた commit と
    照合した結果（`verified`）をそのまま使うため。型外なら束かどうかだけ読み直す
    （`QueueFile` は元の文字列を持たない）。
    """
    directory = os.path.dirname(os.path.abspath(qf.path))
    for other in pool:
        if (os.path.basename(other.path) == name
                and os.path.dirname(os.path.abspath(other.path)) == directory):
            if not other.malformed:
                return "ok", other
            kind, _ = queuefile.read_reply_target(qf.path, name)
            return ("bundle" if kind == "bundle" else "unreadable"), other
    return "missing", None


def resolve_reply_to_file(qf, *, account_name: str, pool=None,
                          require_verified: bool = True) -> ReplyResolution | None:
    """`reply_to_file` を解決する。持たない原稿は None（`reply_to` の道は従前どおり）。

    指した原稿の状態で決める（設計 §2 の表）:

    - `status: posted`・`post_id` が usable・`retracted_at` 無し → 解決
    - `post_id` がまだ無い → `reply_to_unresolved: waiting_for <名前>`（待つ）
    - `retracted_at` あり → `reply_to_unresolved: target_retracted`
    - 型外・`post_id` が usable でない → `reply_to_unresolved: target_unreadable`
    - 無い（承認後に消された） → `reply_to_unresolved: target_missing`

    lint と同じ門（併用・名前の形・自己参照・account 違い・束）もここでもう一度
    見る——指した原稿は承認の指紋の外なので、承認のあとに account を書き換えられ
    たり、束に差し替えられたりしうる。

    `require_verified=True`（select）のとき、同期を確かめた commit と一致しない
    指した原稿は**信じない**（まだ出ていない扱いで待つ）。手元で `post_id:` を
    書き足しただけの原稿に、答えの返信先を向けさせないため。同じ run の中で
    THTH 自身が書き戻した直後もここに当たり、答えは次の run で出る。
    """
    fm = qf.front_matter
    name = queuefile.reply_to_file_of(fm)
    static = queuefile.reply_to_file_static_problem(qf.path, fm)
    if static is not None:
        return ReplyResolution(name, None, static)
    if name is None:
        return None
    if pool is None:
        kind, target = queuefile.read_reply_target(qf.path, name)
    else:
        kind, target = _target_from_pool(qf, name, pool)
    if kind == "missing":
        return ReplyResolution(name, None, unresolved(TARGET_MISSING))
    if kind == "bundle":
        return ReplyResolution(name, None, queuefile.REPLY_TO_FILE_BUNDLE)
    if kind != "ok":
        return ReplyResolution(name, None, unresolved(TARGET_UNREADABLE))
    if require_verified and not target.verified:
        return ReplyResolution(name, None, unresolved(f"{WAITING_FOR} {name}"))
    target_fm = target.front_matter
    problem = queuefile.reply_to_file_target_problem(target_fm, account=account_name)
    if problem is not None:
        return ReplyResolution(name, None, problem)
    if target_fm.get("retracted_at"):
        return ReplyResolution(name, None, unresolved(TARGET_RETRACTED))
    post_id = target_fm.get("post_id")
    if post_id:
        if target_fm.get("status") == "posted" and postid_mod.is_usable(post_id):
            return ReplyResolution(name, post_id, None)
        return ReplyResolution(name, None, unresolved(TARGET_UNREADABLE))
    return ReplyResolution(name, None, unresolved(f"{WAITING_FOR} {name}"))


# --------------------------------------------------------------------------
# 承認済みなのに出られない原稿（設計 3.3.0 A1）
# --------------------------------------------------------------------------

# `held` の理由の区分（**この 4 語だけ**を外へ出す・並びは通知の文面の順）。
# - approval_stale: 承認のあとで内容か指紋の計算が変わった（再承認で直る）
# - reply_to_unresolved: reply_to_file の指した原稿が取り下げ・読めない・消えた
#   （**待ち（waiting_for）は入れない**——相手が出れば出る・3.2.0 の「待ち」）
# - stale: publish_at から stale_days を過ぎた（もう出さない）
# - invalid: それ以外の要確認（型外・文字数・重複本文・未照合の中身 等）
HELD_CATEGORIES = ("approval_stale", "reply_to_unresolved", "stale", "invalid")
HELD_PREFIX = "approved_but_held"


def held_category(reason) -> str | None:
    """要確認の理由を `held` の区分に写す。**待ちは None**（数えない）。"""
    if not isinstance(reason, str):
        return "invalid"
    if waiting_target(reason) is not None:
        return None
    if reason == "approval_stale":
        return "approval_stale"
    if reason.startswith(UNRESOLVED):
        return "reply_to_unresolved"
    if reason == "stale":
        return "stale"
    return "invalid"


def held_items(result: SelectResult, files, now: datetime.datetime) -> list:
    """select の要確認から、承認済みなのに出られない原稿を `{file, reason, …}` で。

    `due` は publish_at を過ぎたか（壊れた publish_at は「いつまでも出ない」ので
    True）。**通知（`held`）に数えるのは `due` だけ**——時刻前の approval_stale は
    まだ出る時刻でないので黙る。ただし board と morning は `due` を問わず名前を
    出す（設計 A1・A2）。本文は持たない（名前と理由と時刻だけ）。
    """
    reason_by_path = {rej.file: rej.reason for rej in result.rejections}
    by_path = {qf.path: qf for qf in files}
    out, seen = [], set()
    for path in result.needs_review:
        if path in seen:
            continue
        seen.add(path)
        reason = reason_by_path.get(path, "needs_review")
        category = held_category(reason)
        if category is None:
            continue
        qf = by_path.get(path)
        raw = qf.front_matter.get("publish_at") if qf is not None else None
        try:
            publish_at = queuefile.parse_publish_at(raw) if raw else None
        except ValueError:
            publish_at = None
        due = publish_at is None or publish_at <= now
        out.append({
            "file": os.path.basename(path), "reason": reason, "category": category,
            "publish_at": publish_at.isoformat() if publish_at else None,
            "due": due,
            "elapsed_hours": (round((now - publish_at).total_seconds() / 3600.0, 1)
                              if publish_at is not None and due else None),
        })
    out.sort(key=lambda row: (row["publish_at"] or "", row["file"]))
    return out


def held_reason_code(items) -> str | None:
    """`due` の held を静的な理由 1 つに（`approved_but_held: approval_stale 46`）。

    区分が混ざれば `HELD_CATEGORIES` の順に `, ` でつなぐ。0 本なら None。
    """
    counts = {}
    for row in items or []:
        if row.get("due") and row.get("category") in HELD_CATEGORIES:
            counts[row["category"]] = counts.get(row["category"], 0) + 1
    if not counts:
        return None
    parts = [f"{name} {counts[name]}" for name in HELD_CATEGORIES if name in counts]
    return f"{HELD_PREFIX}: " + ", ".join(parts)


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
        section = queuefile.extract_section(qf.body, media, allow_empty=bool(fm.get("media") or fm.get("attachments")))
        if section:
            out.add(section.strip())
    return out


def select_one(files, *, account_name: str, account_cfg: dict,
                now: datetime.datetime, last_post_at: datetime.datetime | None,
                recent_texts: set, bypass_pace: bool = False, pool=None) -> SelectResult:
    """§3.3 の 11 条件。`bypass_pace=True` は `--now`（静かな時間帯・最短間隔だけ無視。
    承認 gate は無視しない・§3.7）。

    `pool` は `reply_to_file` の指した原稿を探す一覧（省略時は `files`）。
    `core._throw_locked()` は dry-run で一度選んだ原稿を候補から外すが、指した
    原稿の探し先からは外さない（外すと「指した原稿が無い」に化ける）。

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
        recent_texts=recent_texts, pool=pool)

    if not validated:
        return SelectResult(None, rejections, type_mismatch, needs_review)

    return _apply_timing_gate(
        validated, rejections=rejections, type_mismatch=type_mismatch,
        needs_review=needs_review, account_cfg=account_cfg, now=now,
        last_post_at=last_post_at, bypass_pace=bypass_pace)


def _validate_all(files, *, account_name: str, account_cfg: dict, recent_texts: set,
                  pool=None):
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
    `validated` は `(qf, publish_at, section, resolution)` のリスト（publish_at は
    まだ「未来かどうか」を判定していない・型として妥当なだけ。`resolution` は
    `reply_to_file` を解決したときだけ `{"file", "post_id"}`・それ以外は None）。
    """
    pool = files if pool is None else pool
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
        section = queuefile.extract_section(qf.body, media, allow_empty=bool(fm.get("media") or fm.get("attachments")))
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
        from . import media as media_mod
        try:
            manifest = media_mod.manifest_for(fm, account_cfg)
        except media_mod.MediaError as exc:
            rejections.append(Rejection(path, "approval_stale" if fm.get('approved_sha') else str(exc)))
            needs_review.append(path)
            continue
        approved_sha = fm.get("approved_sha")
        expected_sha = approval_mod.compute_approved_sha(
            section=approval_mod.effective_section(section, account_cfg, fm.get("topic")), account=account_name,
            reply_to=approval_mod.reply_to_for_fingerprint(fm),
            topic=fm.get("topic"), publish_at=publish_at,
            media_manifest=manifest, **approval_mod.publish_options(fm))
        if not approved_sha or approved_sha != expected_sha:
            rejections.append(Rejection(path, "approval_stale"))
            needs_review.append(path)
            continue

        from . import media_delivery
        media_error=media_delivery.error_for(account_cfg,manifest)
        if media_error:
            rejections.append(Rejection(path, media_error))
            needs_review.append(path)
            continue

        # 6c-2. 本文に制御文字が混じっていれば公開しない（セキュリティ監査
        # 2026-09-16・B-2「連投指紋の境界の曖昧さ」）。承認時（`thth approve` →
        # `lint`）に混ざっていなくても、承認後の改竄で本文に紛れ込むことがある
        # ——承認と本文が一致するかを見る 6b のすぐ後、公開直前にもう一度見る。
        # ハッシュの定義（`compute_approved_sha()`）は変えない。
        control = queuefile.find_control_char(section)
        if control is not None:
            pos, cp = control
            rejections.append(Rejection(path, f"control_char(位置{pos}・U+{cp:04X})"))
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
        topic = queuefile.normalize_topic(fm.get("topic"))
        effective = tags_mod.prepared(media, section, topic, hashtags=hashtags_allowed)
        n = queuefile.count_for(media, effective)
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

        tag_issues = [e for e in tags_mod.errors(media, section, topic, account_cfg)
                      if not e.startswith("warning:")]
        if tag_issues:
            rejections.append(Rejection(path, tag_issues[0]))
            needs_review.append(path)
            continue

        # 8. 直近 30 日の投稿済み本文と完全一致
        if section.strip() in recent_texts:
            rejections.append(Rejection(path, "duplicate_text"))
            needs_review.append(path)
            continue

        # 9. reply_to_file（設計 3.2.0 §2）。**解決できない候補は 1 文字も出さない**
        # ——`reply_to` 無しで送る（root に落とす）分岐はここにも core にも無い。
        # 待ち（`waiting_for`）も要確認に積む（名前と待ち先が board に出る・§2）。
        # 指した原稿の front-matter だけで決まり時刻に依存しないのでこの段に置く。
        # 本文・指紋などの直せる誤りを先に言うため、この段の最後に置く。
        resolution = None
        reply = resolve_reply_to_file(qf, account_name=account_name, pool=pool)
        if reply is not None:
            if reply.reason is not None:
                rejections.append(Rejection(path, reply.reason))
                needs_review.append(path)
                continue
            resolution = {"file": reply.name, "post_id": reply.post_id}

        validated.append((qf, publish_at, section, resolution))

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

    for qf, publish_at, section, resolution in validated:
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

        survivors.append((qf, publish_at, section, resolution))

    if not survivors:
        return SelectResult(None, rejections, type_mismatch, needs_review)

    # 11. 静かな時間帯 → 妥当性を通った候補（survivors）を全部落とす（出せる時刻
    # ではない、というだけ。妥当性の診断はすでに確定しているので変えない）。
    if not bypass_pace and in_quiet_hours(now, account_cfg["quiet_hours"]):
        for qf, *_rest in survivors:
            rejections.append(Rejection(qf.path, "quiet_hours"))
        return SelectResult(None, rejections, type_mismatch, needs_review)

    # 12. 前回投稿から min_interval_hours 未満 → 同じく妥当性を通った候補を全部落とす
    if not bypass_pace and last_post_at is not None:
        min_interval = datetime.timedelta(hours=account_cfg["min_interval_hours"])
        if now - last_post_at < min_interval:
            for qf, *_rest in survivors:
                rejections.append(Rejection(qf.path, "min_interval"))
            return SelectResult(None, rejections, type_mismatch, needs_review)

    survivors.sort(key=lambda t: (t[1], os.path.basename(t[0].path)))
    chosen_qf, _chosen_publish_at, chosen_section, chosen_resolution = survivors[0]
    return SelectResult(chosen_qf, rejections, type_mismatch, needs_review,
                        section=chosen_section, resolution=chosen_resolution)
