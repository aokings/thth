"""inflight を道具が自分で解く（設計 3.3.1 §2・§3）。

**約束は 1 行**: 結果が分からないまま止まるのは、媒体に問い合わせても分からない
ときだけ。問い合わせで決まるなら道具が決めて進む。決まらないなら止まって人に
知らせる（`thth inflight <account> resolve` で人が決める）。

ここに置くのは媒体をまたぐ部品（本文の指紋・自分の最近の投稿との照合）と、
毎 run の最初の自己解決（§3）。Threads の container の status を読むのは
アダプタ（`ThreadsAdapter.container_status()`）で、core は媒体名を知らない。

**2 度出す経路を作らない。** ここで決めるのは「出た（post_id が 1 件に決まった）」
「出ていない（container が ERROR・EXPIRED・FINISHED）」だけで、ここから公開の
要求は 1 度も飛ばない。出ていないと決めたら inflight を解き、次の公開は select
から（承認の検査を全部通って）やり直す。
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import os
import re
import unicodedata

from . import jst

# 自分の最近の投稿を、公開した時刻の前後この幅で見る（設計 §2・§3）。
LOCATE_WINDOW = datetime.timedelta(minutes=10)
# 引く件数。10 分の窓に 25 本を越えて出すアカウントは無い（max_per_run 既定 1）。
LOCATE_LIMIT = 25

# container の status（一次資料 troubleshooting・2026-09-23 参照）。
CONTAINER_STATUSES = frozenset({"EXPIRED", "ERROR", "FINISHED", "IN_PROGRESS", "PUBLISHED"})

# inflight に書く「最後の問い合わせ結果」（静的な語だけ）。
REMOTE_STATES = frozenset({
    "finished", "published", "error", "expired", "in_progress", "query_failed",
    "published_unlocated", "retry_failed", "retried", "published_located",
    "listing_none", "listing_many", "listing_failed", "listing_located",
    "unsupported",
})


def normalize(text) -> str:
    """指紋に掛ける前の形。媒体が改行や空白を詰め直しても同じ本文を同じと見る。

    NFC に揃え、空白の並び（改行を含む）を 1 つの空白にして前後を落とす。
    **語を足したり削ったりはしない**——Mastodon の HTML を剥がした本文のように
    媒体側で文字そのものが変わっていれば一致しない（その時は解かない側に倒れる）。
    """
    value = unicodedata.normalize("NFC", text if isinstance(text, str) else "")
    return re.sub(r"\s+", " ", value).strip()


def text_fingerprint(text) -> str:
    """送る本文の指紋（sha256）。inflight に本文そのものは書かない（指紋だけ）。"""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class Located:
    """自分の最近の投稿との照合の結果。`state` は静的な語。"""
    state: str                 # listing_located | listing_none | listing_many | listing_failed
    post_id: str | None = None
    timestamp: str | None = None


def locate(adapter, fingerprint: str | None, around) -> Located:
    """公開の時刻（`around`）の前後 10 分に、本文の指紋が一致する投稿が**ちょうど
    1 件**あるか。

    0 件なら `listing_none`（出ていないとは言い切らない——媒体が本文を変えて
    返すことがある）、2 件以上なら `listing_many`（どれか決められない）。
    引けなければ `listing_failed`。どれも「解かない」側の答え。
    """
    if not fingerprint or around is None:
        return Located("listing_failed")
    try:
        rows = adapter.recent_posts(limit=LOCATE_LIMIT)
    except Exception:  # noqa: BLE001 — 媒体の例外の形は問わない。引けなかった、だけを言う
        return Located("listing_failed")
    if not isinstance(rows, list):
        return Located("listing_failed")
    hits = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        when = jst.parse(row.get("timestamp"))
        if when is None or abs(when - around) > LOCATE_WINDOW:
            continue
        if text_fingerprint(row.get("text")) != fingerprint:
            continue
        post_id = row.get("post_id")
        if isinstance(post_id, str) and post_id:
            hits.append((post_id, jst.iso(jst.to_jst(when))))
    if len(hits) == 1:
        return Located("listing_located", hits[0][0], hits[0][1])
    return Located("listing_many" if hits else "listing_none")


def is_self_resolvable(record) -> bool:
    """道具が自分で解いてよい inflight か。

    **出たことが判っている inflight は触らない**（post_id が入っている・指紋の
    食い違い・媒体が返した post_id が書けない形）。それは「出たか分からない」
    ではなく「出たが記録できない」で、人が直す。添付の inflight（`media`）は
    journal が別の約束で動いているので対象外。
    """
    if not isinstance(record, dict):
        return False
    if record.get("media") or record.get("post_id") or record.get("mismatch_fields"):
        return False
    return isinstance(record.get("file"), str) and bool(record.get("file"))


def display_file(record) -> str | None:
    raw = (record or {}).get("file")
    if not isinstance(raw, str) or not raw:
        return None
    return raw if raw == "(send)" else os.path.basename(raw)


# ---------------------------------------------------------------- 毎 run の最初（§3）

@dataclasses.dataclass(frozen=True)
class Outcome:
    """自己解決の結果。

    `resolved` が真なら inflight は無くなっていて run は先へ進んでよい。
    `asked` は媒体に訊いたか（決まらなかったときの理由を `inflight_unresolved`
    として死活通知に出すかどうか）。`state` は最後の問い合わせ結果（静的な語）。
    `error` は「出たと分かったが記録できなかった」ときの静的な理由。
    """
    resolved: bool
    asked: bool
    state: str
    resolution: str | None = None
    post_id: str | None = None
    error: str | None = None


# 出ていないと決まったときの runs の error（静的な語・設計 §2 の表）。
_NOT_PUBLISHED_ERROR = {
    "error": "publish_failed_remote_error",
    "expired": "publish_failed_remote_error",
    # FINISHED: まだ公開されていない container。自己解決では**公開しない**
    # （承認の検査を通らずに出すことになる）。解いて select からやり直す。
    "finished": "inflight_resolved_not_published",
}


def _adapter_for(account_cfg, adapter_factory):
    from . import accounts as accounts_mod, leave_gate
    token = accounts_mod.load_token(account_cfg)
    return leave_gate.bind(adapter_factory(account_cfg, token), account_cfg)


def _ask(adapter, record) -> tuple:
    """媒体に 1 回訊く。返り値は (決まった答え, 状態の語, post_id, 公開時刻)。

    決まった答えは `published`（post_id が 1 件に決まった）・`not_published`・
    `None`（決まらない）。
    """
    container_id = record.get("container_id")
    around = jst.parse(record.get("started"))
    fingerprint = record.get("text_fingerprint")
    poll = getattr(adapter, "poll_container", None)
    if isinstance(container_id, str) and container_id and callable(poll):
        status = poll(container_id, attempts=1, wait=False)
        if status in ("ERROR", "EXPIRED", "FINISHED"):
            return "not_published", status.lower(), None, None
        if status == "PUBLISHED":
            found = locate(adapter, fingerprint, around)
            if found.state == "listing_located":
                return "published", "published", found.post_id, found.timestamp
            return None, "published_unlocated", None, None
        return None, status if status in REMOTE_STATES else "query_failed", None, None
    return None, "unsupported", None, None


def self_resolve(account_name: str, account_cfg: dict, state_dir: str, record: dict, *,
                 adapter_factory, run_id: str, now, log) -> Outcome:
    """inflight が残っている run の最初に 1 回だけ訊いて、決まれば解く（§3）。

    **公開の要求はここから 1 度も飛ばない**（再試行は公開した run の中の 1 回
    だけ・`ThreadsAdapter._settle_ambiguous()`）。決まらなければ inflight に
    「最後の問い合わせ結果」を写して止まる（従前どおり）。
    """
    from . import inflight as inflight_mod
    if not is_self_resolvable(record):
        return Outcome(False, False, "unsupported")
    try:
        adapter = _adapter_for(account_cfg, adapter_factory)
    except Exception:  # noqa: BLE001 — 組み立てられなければ訊いていない（従前どおり止まる）
        return Outcome(False, False, "unsupported")
    try:
        answer, state, post_id, posted_at = _ask(adapter, record)
    except Exception:  # noqa: BLE001 — 訊けなかった＝分からない
        answer, state, post_id, posted_at = None, "query_failed", None, None
    if state == "unsupported":
        return Outcome(False, False, state)
    if answer is None:
        _note(state_dir, state, log)
        log(f"inflight: 媒体に訊きましたが決まりませんでした（{state}）。"
            f"thth inflight {account_name} show で中身を見て、媒体の画面で確かめてから"
            f" thth inflight {account_name} resolve で解いてください")
        return Outcome(False, True, state)
    if answer == "not_published":
        resolution = "remote_" + state
        inflight_mod.archive(state_dir, record, resolved_at=jst.iso(), resolved_by="thth",
                             resolution=resolution)
        inflight_mod.clear(state_dir)
        _run(account_name, state_dir, run_id, now, record, post_id=None, status="error",
             error=_NOT_PUBLISHED_ERROR[state], resolution=resolution, remote_state=state)
        log(f"inflight を解きました: container は {state.upper()}（出ていません）。"
            "原稿は approved のままで、この run から出し直せます")
        return Outcome(True, True, state, resolution)
    resolution = "remote_published" if state == "published" else "listing_located"
    ok, error = record_published(account_name, account_cfg, state_dir, record, post_id,
                                 posted_at=posted_at or record.get("started") or jst.iso(),
                                 resolution=resolution, resolved_by="thth", run_id=run_id,
                                 now=now, log=log, remote_state=state)
    if not ok:
        return Outcome(False, True, state, resolution, post_id, error)
    log(f"inflight を解きました: 出ていました（post_id {post_id}）。記録を書き戻しました")
    return Outcome(True, True, state, resolution, post_id)


def _note(state_dir, state, log) -> None:
    from . import inflight as inflight_mod
    try:
        inflight_mod.update(state_dir, remote_state=state, remote_checked_at=jst.iso())
    except (OSError, ValueError):
        log("inflight に問い合わせ結果を書けませんでした（inflight は残っています）")


def _run(account_name, state_dir, run_id, now, record, *, post_id, status, error,
         resolution, remote_state=None, topic=None, mode="production") -> None:
    from . import core
    raw = record.get("file")
    core._append_run(state_dir, account_name, run_id, mode, "post",
                     None if raw == "(send)" else raw, post_id, now,
                     status=status, error=error, topic=topic,
                     extra={"inflight_resolution": resolution, "remote_state": remote_state})


def record_published(account_name: str, account_cfg: dict, state_dir: str, record: dict,
                     post_id: str, *, posted_at: str, resolution: str, resolved_by: str,
                     run_id: str, now, log, remote_state: str | None = None) -> tuple:
    """出ていた投稿の post_id を記録して inflight を解く（道具も人も同じ 1 か所）。

    返り値 `(ok, error)`。**書き戻せないときは inflight を解かない**——post_id を
    inflight に書いて止まる（出たことは判っているが記録できない、の扱い・
    `core._throw_chosen()` の書き戻しの失敗と同じ）。

    `thth send` の inflight（`(send)`）には原稿が無いので、runs に post_id を
    残して解くだけ（本文は inflight に無いので `sent/` は書かない）。
    """
    from . import approval as approval_mod
    from . import core, postid as postid_mod, queuefile, sent as sent_mod, writeback
    from . import inflight as inflight_mod
    from . import media as media_mod
    if not postid_mod.is_usable(post_id):
        return False, "unusable_post_id"
    raw = record.get("file")
    if raw == "(send)":
        inflight_mod.archive(state_dir, record, resolved_at=jst.iso(), resolved_by=resolved_by,
                             resolution=resolution, post_id=post_id)
        inflight_mod.clear(state_dir)
        _run(account_name, state_dir, run_id, now, record, post_id=post_id, status="ok",
             error=None, resolution=resolution, remote_state=remote_state)
        return True, None

    media = account_cfg["media"]
    repo_dir = account_cfg.get("repo_dir")
    synced, _err, _sha = writeback.sync_repo(repo_dir)
    if not synced:
        inflight_mod.update(state_dir, remote_state=remote_state or "published",
                            remote_checked_at=jst.iso(), remote_post_id=post_id)
        log("inflight: 出ていたことは分かりましたが、repo を同期できないので記録できません"
            "（inflight は残します）")
        return False, "repo_sync_failed"
    try:
        qf = queuefile.parse(raw)
    except OSError:
        qf = None
    fm = qf.front_matter if qf is not None and not qf.malformed else {}
    if fm.get("post_id") == post_id and fm.get("status") == "posted":
        # 人が手で記録を書いてから解いた（書き戻しは済んでいる）。
        inflight_mod.archive(state_dir, record, resolved_at=jst.iso(), resolved_by=resolved_by,
                             resolution=resolution, post_id=post_id)
        inflight_mod.clear(state_dir)
        _run(account_name, state_dir, run_id, now, record, post_id=post_id, status="ok",
             error=None, resolution=resolution, remote_state=remote_state)
        return True, None
    expected = record.get("approved_fingerprint")
    if qf is None or qf.malformed or not expected or not core._fingerprint_matches(
            raw, media, expected, account_cfg):
        # 公開した時の内容と、いまの原稿が違う（あるいは読めない）。書き戻さない。
        inflight_mod.update(state_dir, remote_state=remote_state or "published",
                            remote_checked_at=jst.iso(), remote_post_id=post_id)
        log("inflight: 出ていたことは分かりましたが、原稿が公開した時の内容と違うので"
            "書き戻しません（inflight は残します）")
        return False, "text_mismatch_before_writeback"

    section = queuefile.extract_section(qf.body, media) or ""
    effective = approval_mod.effective_section(section, account_cfg, fm.get("topic"))
    reply_to = record.get("reply_to")
    reply_file = queuefile.reply_to_file_of(fm)
    resolved_from = {"file": reply_file, "post_id": reply_to} if reply_file and reply_to else None
    topic = queuefile.normalize_topic(fm.get("topic"))
    inflight_mod.update(state_dir, post_id=post_id)
    sent_mod.write(state_dir, post_id=post_id, text=effective,
                   body_hash=approval_mod.compute_body_hash(effective), sent_at=posted_at,
                   approved_fingerprint=expected, reply_to=reply_to,
                   resolved_from=resolved_from,
                   attachment_kinds=media_mod.attachment_kinds(None))
    core._record_engagement(
        account_cfg, account_name, media=media, topic=topic, form=fm.get("form"),
        post_id=post_id, posted_at=posted_at, now=now, log=log, reply_to=reply_to,
        reply_to_root=fm.get("reply_to_root"), reply_to_author_key=fm.get("reply_to_author_key"),
        found_by=fm.get("found_by") or ("manual" if resolved_from else None))
    if resolved_from:
        writeback.set_front_matter_fields(raw, {
            "status": "posted", "post_id": post_id, "posted_at": posted_at,
            "reply_to_resolved": resolved_from["post_id"]})
    else:
        writeback.rewrite_front_matter(raw, status="posted", post_id=post_id, posted_at=posted_at)
    rel_path = os.path.relpath(raw, repo_dir)
    message = (f"thth: {account_name} {os.path.basename(raw)} を投稿（post_id {post_id}・"
               f"inflight を解いて記録）")
    try:
        ok, err = writeback.commit_and_push(
            repo_dir, rel_path=rel_path, message=message,
            validate=lambda: core._fingerprint_matches(raw, media, expected, account_cfg))
    except writeback.PushValidationFailed:
        inflight_mod.update(state_dir, mismatch_fields=["unknown"])
        _run(account_name, state_dir, run_id, now, record, post_id=post_id, status="error",
             error="text_mismatch_after_rebase", resolution=resolution,
             remote_state=remote_state, topic=topic)
        return False, "text_mismatch_after_rebase"
    if not ok:
        # push 失敗では inflight を消さない（post_id が origin に届くまで残す・§3.5）。
        _run(account_name, state_dir, run_id, now, record, post_id=post_id, status="error",
             error=err, resolution=resolution, remote_state=remote_state, topic=topic)
        log(f"inflight: 記録の push に失敗しました（inflight は残します）: {err}")
        return False, "writeback_push_failed"
    inflight_mod.archive(state_dir, record, resolved_at=jst.iso(), resolved_by=resolved_by,
                         resolution=resolution, post_id=post_id)
    inflight_mod.clear(state_dir)
    _run(account_name, state_dir, run_id, now, record, post_id=post_id, status="ok",
         error=None, resolution=resolution, remote_state=remote_state, topic=topic)
    return True, None
