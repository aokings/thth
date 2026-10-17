"""安全装置（設計 3.12.0 §3.3）。LLM の依頼で直接出す・消す・予約するときの機械の上限。

**疑わない、暴走は機械で止める**（§1）。持ち主が台帳に書いた数値（無ければ既定）を
道具が機械的に守る。守れないものは静的な理由で断り、急な連投は口座を止める。

数え方は口座ごと・JST の日付で、材料は**既存の記録**だけ:

- 公開: `state/<account>/sent/<post_id>.json` の `sent_at`（timer・`thth send`・直接の公開の
  どれも公開の直後にここへ書く）
- 削除: 同じ記録の `retracted_at`（`thth retract` と直接の削除が足す）

新しく持つ記録は、止めた印 `state/<account>/guard/stopped.json`（0600・0700 の置き場）
だけ。止まった口座は持ち主が戻す（`resume()`）まで公開・削除・予約を断る。

断りの理由（`server_writes.SAFE_ERRORS` に同じ名前で載る）:

- `too_soon`     : 前の公開から `min_interval_hours` が経っていない（次に出せる時刻を添える）
- `quiet_hours`  : 夜間（`quiet_hours`）に今すぐ出そうとした（明ける時刻を添える）
- `daily_limit`  : その日の公開が `daily_max_posts` に達した（翌日 0 時を添える）
- `retract_limit`: その日の削除が `daily_max_retracts` に達した（翌日 0 時を添える）
- `account_stopped`（詳細 `burst`）: 直近 `burst.minutes` 分の公開と削除の合計が
  `burst.count` を超える依頼だった → 口座を止めて断る。**同じ配信（はてな）への操作は窓の中で 1 件**
  （4.0 S5 §6・公開 → 直して更新 → 消す、で止まらない。別の記事の連投は今までどおり。日の数えは操作 1 回を 1 件）
- `burst_limit`（詳細 `burst`）: 口座を**止めずに**この 1 件だけ断る。**返信**が burst を超えたとき
  （3.25.0・`next_at` は窓が空く時刻・呼び手は queue の順番待ちに並べる）と、止めの印を置けない
  置き場の運営者の CLI（10/15）
- `burst_limit`（詳細 `bookmark_burst`）: はてなのブックマーク（付ける・消す）が主体の
  `bookmark_burst`（既定 10 分に 10 件）を超えた → 主体を**止めずに**この 1 件だけ断る（4.0.1・
  `next_at` は窓が空く時刻）。ブックマークは記事の burst に数えない
- `principal_stopped`（詳細 `burst`・段 2）: ブックマークが主体で 10 分に 30 件を超える（`bookmark.stop`・
  媒体の台帳の止め線）→ 主体を止める。`account_stopped`（詳細 `burst`・段 2）: 口座の書きが 10 分に 20 件を
  超える（`write.floor`・表に行の無い媒体・動作も受ける共通の最低線）→ 口座を止める
- `reaction_limit`: その日の反応（再投稿・いいね・その取り消し）が `daily_max_reactions`
  に達した（翌日 0 時を添える・設計 3.15.0 §2.5）。材料は `state/<account>/reactions/`。
  **反応は公開の枠（`daily_max_posts`・burst）に数えない**。夜間は再投稿にだけ掛ける
  （`check_reaction()`）。
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from . import accounts, assess, jst, limits_policy, sent as sent_mod, server_files
from .report_service import ReportServiceError

# 主体・ブログ・ブックマークの段（設計 4.0 S1 §4.1・§4.2）。はてなの口座だけに出る。
PRINCIPAL_REFUSALS = frozenset((
    "principal_stopped", "principal_daily_limit", "principal_retract_limit", "bookmark_limit",
    "blog_daily_limit", "blog_retract_limit", "bookmark_once", "bookmark_not_allowed_for_lane",
    # ロックの中で主体の台帳を読み直したときの突き合わせ（§3.3・§5.2 の 6）。
    "blog_not_allowed_for_lane", "blog_unknown", "principal_missing", "principal_unavailable",
    "principal_token_mismatch", "principal_handle_mismatch",
    # 4.0 S4 §4・§5.3: 自分の記事・同じホストの 1 日の枠、owner のロック、自分の主体で既に付いている。
    "self_bookmark_limit", "bookmark_host_limit", "bookmark_busy", "bookmark_exists_remote"))
REFUSALS = (frozenset(("too_soon", "quiet_hours", "daily_limit", "retract_limit", "reaction_limit",
                       # 止めずに 1 件だけ断る（返信の burst・3.25.0／止めの印を置けない置き場）。
                       "burst_limit",
                       # 媒体の台帳（thth/media_traits/）が検査を通らず、柵の表を作れない（断る・落ちない）。
                       "invalid_media_traits"))
            | PRINCIPAL_REFUSALS)
# burst: 急な連投で道具が止めた。owner: 持ち主が /activity から止めた（段 3）。
STOP_REASONS = frozenset(("burst", "owner"))
STOP_FILE = "stopped.json"
CREDENTIAL_ID_LENGTH = 12
# `entry_*`・`bookmark*` ははてなの口座だけ（設計 4.0 S1 §4.2）。はてなの口座に `publish`・
# `retract` が来たら `entry_create`・`entry_delete` として見る。
HATENA_KINDS = ("entry_create", "entry_update", "entry_delete", "bookmark", "bookmark_delete")
KINDS = ("publish", "retract", "schedule") + HATENA_KINDS


class GuardRefused(ReportServiceError):
    """安全装置の断り。`reason` は静的な詳細、`next_at` は次に出せる時刻（無ければ None）。

    `assessment`（設計 上限と連投の柵 §7.1・段 1）: どの行（`rule`）・信号・単位・数（`used`／`count`）・
    根拠（`trait`）で断ったかの構造。`check`／`check_reaction` が上げる断りに付く（ほかの所で作る断りは None）。
    符丁（`str(exc)`）・`reason`・`next_at` は今までどおり。
    """

    def __init__(self, code, *, reason=None, next_at=None, assessment=None):
        super().__init__(code)
        self.reason = reason
        self.next_at = next_at
        self.assessment = assessment


def _rules_for(*args, **kwargs) -> list:
    """`limits_policy.rules_for`。表を作れなければ（台帳が壊れている）`invalid_media_traits` で断る。

    例外で落とさず、ほかの断りと同じ形（`GuardRefused`・符丁は静的）にする。SNS の timer の予約投稿は
    柵を通らないので、この断りでは止まらない。
    """
    try:
        limits_policy.policy()
    except ValueError:
        raise GuardRefused("invalid_media_traits") from None
    return limits_policy.rules_for(*args, **kwargs)


def credential_id(context) -> str | None:
    """記録に残す資格の id（`credential_digest` の先頭 12 文字）。hash の頭であって秘密ではない。"""
    digest = getattr(context, "credential_digest", None)
    return digest[:CREDENTIAL_ID_LENGTH] if isinstance(digest, str) and len(digest) >= CREDENTIAL_ID_LENGTH else None


def _directory(account) -> Path:
    if not accounts.name_is_safe(account):
        raise ValueError("invalid_account")
    return Path(accounts.state_dir_for(account)) / "guard"


# --------------------------------------------------------------------------
# 止める・戻す
# --------------------------------------------------------------------------

def stopped(account) -> dict | None:
    """止まっていれば `{"reason", "stopped_at", ...}`、動いていれば None。

    読めない（置き場のモードが緩い・壊れている）ときは**止まっている側**に倒す。
    """
    try:
        with server_files.directory(_directory(account), private=True) as fd:
            raw = server_files.read_at(fd, STOP_FILE, private=True)
    except FileNotFoundError:
        return None
    except server_files.UnsafeFile as exc:
        if str(exc) in ("unsafe_server_directory", "unsafe_server_owner"):
            # 置き場のモード・持ち主が緩い: 断ることは同じだが、運営者が直せる理由のまま上げる
            # （呼び手は write_unavailable にし、運営者の CLI は 1 語と次の一手で言う・§6-5）。
            raise
        return {"reason": "guard_state_unreadable"}
    except (OSError, ValueError):
        return {"reason": "guard_state_unreadable"}
    try:
        value = json.loads(raw)
    except ValueError:
        return {"reason": "guard_state_unreadable"}
    if type(value) is not dict or value.get("reason") not in STOP_REASONS:
        return {"reason": "guard_state_unreadable"}
    return value


def stop(account, reason, *, now=None, actor=None, via=None, credential=None) -> dict:
    """口座を止める（既に止まっていれば上書きしない）。"""
    if reason not in STOP_REASONS:
        raise ValueError("invalid_stop_reason")
    value = {"reason": reason, "stopped_at": jst.iso(now)}
    for key, item in (("actor", actor), ("via", via), ("credential", credential)):
        if isinstance(item, str) and item:
            value[key] = item
    with server_files.directory(_directory(account), create=True, private=True) as fd:
        try:
            server_files.replace_at(fd, STOP_FILE, server_files.encode(value), new=True, private=True)
        except server_files.UnsafeFile:
            # 既に止まっている（先に止めた印を消さない）。
            pass
    try:
        from . import admin_log
        admin_log.append("guard_stopped", account, accounts.load_account(account),
                         by=actor if isinstance(actor, str) and actor else "thth-guard",
                         via=via if via in ("cli", "mcp", "http", "api") else "cli",
                         diff={"stopped": [False, True], "reason": [None, reason]})
    except Exception:
        # 変更ログに残せなくても止めることは止める（止めた印が正本）。
        pass
    return value


def resume(account, *, by, via="cli") -> bool:
    """止めたのを戻す（持ち主の口から呼ぶ）。止まっていなければ False。

    口は運営者の CLI（`thth account resume`）と持ち主の `/activity`（via http）だけ。
    **MCP には出さない**——暴走して止まった LLM が自分で戻せないように（段 3）。
    """
    from . import admin_log
    admin_log.actor(by)
    try:
        with server_files.directory(_directory(account), private=True) as fd:
            try:
                os.unlink(STOP_FILE, dir_fd=fd)
            except FileNotFoundError:
                return False
            os.fsync(fd)
    except FileNotFoundError:
        return False
    try:
        admin_log.append("guard_resumed", account, accounts.load_account(account), by=by,
                         via=via if via in ("cli", "http") else "cli", diff={"stopped": [True, False]})
    except Exception:
        pass
    return True


# --------------------------------------------------------------------------
# 数える
# --------------------------------------------------------------------------

def _classified(account):
    """`sent/` の記録を (種類, 時刻, blog_id, 鍵) の list に。種類は `post`・`retract`・`bookmark`。

    中身は `_action_rows`（動作つき）と同じ（段 2）。"""
    return [(what, at, blog, key) for what, _action, at, blog, key in _action_rows(account)]


def _action_rows(account):
    """`sent/` の記録を (種類, 動作, 時刻, blog_id, 鍵) の list に（段 2・分の窓は行の `actions` で数える）。

    動作: SNS の記録（`kind` 無し）は `post`（投稿と返信を分けない）・`retract`。はてなは `entry_create`・
    `entry_update`（足された `retracted_at` は `entry_delete`）・`entry_delete`・`bookmark_add`（足された
    `retracted_at` は `bookmark_delete`・打ち直しの前の試みは `bookmark_add`）・`bookmark_delete`。知らない
    `kind` は `post`／`retract`（`assess.EVENT_ALIASES` で記事の行にも数える・数え漏らさない）。

    鍵（4.0 S5 §6.2）は burst の窓で「1 件」と数える単位: はてなの `entry_*` の行は `("delivery", 配信 id)`
    （`delivery` の無い古い行は `("post", post_id)`・`("retract", post_id)`）、SNS の行は
    `("post", post_id)` と `("retract", post_id)`（公開と削除は別の件・今と同じ数）、ブックマークは
    試みごとに別の鍵（S4 の数え方のまま）。日の数えは鍵を見ない（操作 1 回を 1 件）。

    `kind` の無い行（SNS）は今までどおり `sent_at` を公開・`retracted_at` を削除に数える。
    はてなの行（設計 4.0 S1 §4.2）: `entry_create`／`entry_update` は公開（足された
    `retracted_at` は削除）・`entry_delete` は削除・`bookmark_*` はブックマーク（足された
    `retracted_at` も）。知らない `kind` は公開として数える（数え漏らす側に倒さない）。
    """
    out = []
    for row in sent_mod.records(accounts.state_dir_for(account)):
        kind = row.get("kind")
        if kind == "bookmark_add":
            act_first, act_second = "bookmark_add", "bookmark_delete"
        elif kind in ("bookmark_delete", "entry_delete"):
            act_first = act_second = kind
        elif kind in ("entry_create", "entry_update"):
            act_first, act_second = kind, "entry_delete"
        else:
            act_first, act_second = "post", "retract"
        blog = row.get("blog_id")
        post_id = row.get("post_id")
        sent_at = jst.parse(row.get("sent_at"))
        retracted_at = jst.parse(row.get("retracted_at"))
        if kind in ("bookmark_add", "bookmark_delete"):
            first, second = "bookmark", "bookmark"
        elif kind == "entry_delete":
            first, second = "retract", "retract"
        else:
            first, second = "post", "retract"
        delivery = row.get("delivery") if isinstance(kind, str) and kind.startswith("entry_") else None
        if isinstance(delivery, str) and delivery:
            key_first = key_second = ("delivery", delivery)
        elif first == "bookmark":
            key_first, key_second = ("bm", post_id, "sent"), ("bm", post_id, "retracted")
        else:
            key_first, key_second = (first, post_id), (second, post_id)
        if sent_at is not None:
            out.append((first, act_first, sent_at, blog, key_first))
        if retracted_at is not None:
            out.append((second, act_second, retracted_at, blog, key_second))
        if first == "bookmark":
            # 4.0 S4 §7: `not_found` のあと打ち直した行は、前の試みを `attempts[]` に持つ（数え続ける）。
            for index, attempt in enumerate(row.get("attempts") or []):
                at = jst.parse(attempt.get("sent_at")) if isinstance(attempt, dict) else None
                if at is not None:
                    out.append(("bookmark", "bookmark_add", at, blog, ("bm", post_id, "attempt", index)))
    return out


def _events_of(account) -> list:
    """分の窓の材料（口座）: (動作, 時刻, 鍵)。"""
    return [(action, at, key) for _what, action, at, _blog, key in _action_rows(account)]


def principal_events(principal_name) -> list:
    """分の窓の材料（主体の全口座）: (動作, 時刻, 鍵)。鍵は口座名つき（同じ配信の鍵だけは口座をまたいで 1 件）。"""
    from . import principals
    out = []
    for name, _raw in principals.accounts_of(principal_name):
        for _what, action, at, _blog, key in _action_rows(name):
            out.append((action, at, (name,) + key if key[0] != "delivery" else key))
    return out


def _recent_keys(rows, window, now) -> set:
    """窓 `(window, now]` の中の鍵の集合（burst の数 = この集合の大きさ・4.0 S5 §6.2）。"""
    return {key for _what, at, _blog, key in rows if window < at <= now}


def _events(account):
    """`sent/` の記録から (公開の時刻の list, 削除の時刻の list)。読めない時刻は数えない。

    ブックマーク（はてな）は公開の枠に入れない（`_classified`）。SNS の口座の数は変わらない。
    """
    posts, retracts = [], []
    for what, at, _blog, _key in _classified(account):
        if what == "post":
            posts.append(at)
        elif what == "retract":
            retracts.append(at)
    return posts, retracts


def summary(account, cfg, *, now=None) -> dict:
    """数えた結果（読むだけ・`/activity` や答えに添える用）。"""
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    limits = accounts.guard_limits(cfg)
    rows = [row for row in _classified(account) if row[0] in ("post", "retract")]
    posts = [at for what, at, _b, _k in rows if what == "post"]
    retracts = [at for what, at, _b, _k in rows if what == "retract"]
    today = now.date()
    window = now - datetime.timedelta(minutes=limits["burst"]["minutes"])
    keys = _recent_keys(rows, window, now)
    return {
        "posts_today": sum(1 for at in posts if at.date() == today),
        "retracts_today": sum(1 for at in retracts if at.date() == today),
        # 4.0 S5 §6: 窓の中の鍵の数（SNS は今と同じ・はてなは同じ配信を 1 件）。
        "recent": len(keys),
        "recent_keys": keys,
        "last_post_at": max(posts) if posts else None,
        "limits": limits,
        "posts": posts,
    }


def stop_place_usable(account) -> bool:
    """止めの印（`guard/stopped.json`）を置ける置き場か。THTH_ROOT の外・モードの緩い置き場は False。

    運営者の CLI（VM で打つ `thth send`・手元の Mac）はこういう置き場でも動いてきたので、
    そこでは数だけ守らせ、止めの印は置かない（`check(cli=True)`）。
    """
    try:
        state = Path(accounts.state_dir_for(account))
        if not state.exists():
            return True  # 初めての口座（置き場は作るときに 0700）
        with server_files.directory(state):
            pass
        return True
    except (server_files.UnsafeFile, OSError, ValueError):
        return False


def burst_slot(account, cfg, *, now=None, pending=()) -> datetime.datetime:
    """burst の窓が空く最初の時刻（3.25.0・返信の順番待ち）。

    材料は `sent/` の公開・削除と、すでに順番待ちに並んでいる返信の `publish_at`（`pending`）。
    候補は「今」と「各出来事 + `burst.minutes` 分 + 1 分」。並んでいるものより後ろで、その時刻の
    窓（`summary` と同じ `(t - 分, t]`）に入る数 + 1 が `burst.count` 以下になる最初の時刻を返す。
    1 件目は「窓の中で最も古い出来事 + 分 + 1 分」になり、続く返信はその後ろへずれる。
    """
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    limits = accounts.guard_limits(cfg)
    span = datetime.timedelta(minutes=limits["burst"]["minutes"])
    gap = datetime.timedelta(minutes=1)
    posts, retracts = _events(account)
    pending = sorted(jst.to_jst(at) for at in pending)
    events = posts + retracts + pending
    after = pending[-1] if pending else None
    candidates = {now} | {at + span + gap for at in events}
    if after is not None:
        candidates.add(after + gap)
    for t in sorted(candidates):
        if t < now or (after is not None and t <= after):
            continue
        if sum(1 for at in events if t - span < at <= t) + 1 <= limits["burst"]["count"]:
            return t
    # 届かない（候補の最後は窓が空なので来ない）。念のため最後の出来事の窓の外。
    return max(events + [now]) + span + gap


def check(account, cfg, kind, *, now=None, actor=None, via=None, credential=None,
          publish_at=None, scheduled_same_day=0, reply=False, cli=False,
          principal_scheduled_same_day=None, bookmark_url=None, pending_replies=None,
          bookmark_target=None, bookmark_once=True, delivery=None) -> None:
    """依頼が安全装置に掛かれば `GuardRefused` を上げる。掛からなければ何もしない。

    道（設計 上限と連投の柵 §5.1・段 1）: **止めの印 → 行の表（安全装置と見立て）→ 結論を当てる**。
    行は `limits_policy.rules_for` が表の順に返し、`assess.evaluate` が最初に当たった行を結論にする
    （表の順は 4.0.1 の `if` の順そのもの）。断りには `assessment`（§7.1）を添える。

    `delivery`（はてなの配信 id・4.0 S5 §6）: 同じ配信への操作が窓の中に既にあれば、この依頼は burst の
    数を増やさない（同じ配信は窓の中で 1 件）。

    `kind`: `publish`（今すぐ出す）・`retract`（消す）・`schedule`（queue に「出してよい」と
    刻む。`publish_at` の日の公開数に、その日にすでに刻んだ件数 `scheduled_same_day` を足して見る）。
    burst を超える依頼は**口座を止めてから**断る。

    ブックマーク（4.0 S4 §5.3・§6.5）: `bookmark_target`（`own`／`external`）があれば自分の記事の枠
    （`daily_max_self_bookmarks`）を、`bookmark_url` のホストで同じホストの枠（`bookmark_same_host_daily`）を
    見る。`bookmark_once=False` は「1 URL に 1 回」をここで見ない（`hatena_bookmark.execute_locked` が owner の
    ロックの中で柵 1 として見る）。

    はてなの口座（`media: hatenablog`）は `_check_hatena`（主体 → 口座 → 媒体の断りの順に止めを
    見て、口座・ブログ・主体の 3 段で数える）。`kind` は `entry_create`・`entry_update`・
    `entry_delete`・`bookmark`（`bookmark_url` が要る）・`bookmark_delete`・`schedule`
    （`principal_scheduled_same_day` を省くと主体の全口座の queue から数える）。

    `reply`（返信）には最短間隔 `min_interval_hours` を掛けない——返信は会話なので
    （段 1〜2 の裁定 (a)）。1 日の上限と burst には数える。

    **返信が burst を超えるときは口座を止めない**（3.25.0・kopicha 10/16）。`burst_limit`（詳細
    `burst`・`next_at` は窓が空く時刻 `burst_slot`）で断り、呼び手がその 1 件を queue の順番待ちに
    並べる（timer に載っていない口座ではそのまま断りになる）。`pending_replies`（すでに並んでいる
    返信の `publish_at`）がまだ先にあれば、窓が空いていても後ろに並ばせる（順を守る）。
    止めるのは投稿（返信でないもの）の連投だけ。
    """
    if kind not in KINDS:
        raise ValueError("invalid_guard_kind")
    from . import principals
    if principals.uses_principal(cfg):
        # はてなの口座（設計 4.0 S1 §4.1・§4.2）。SNS の口座はこの先の今までの道を通る。
        return _check_hatena(account, cfg, {"publish": "entry_create", "retract": "entry_delete"}.get(kind, kind),
                             now=now, actor=actor, via=via, credential=credential, publish_at=publish_at,
                             scheduled_same_day=scheduled_same_day,
                             principal_scheduled_same_day=principal_scheduled_same_day,
                             bookmark_url=bookmark_url, cli=cli, bookmark_target=bookmark_target,
                             bookmark_once=bookmark_once, delivery=delivery)
    if kind in HATENA_KINDS:
        raise ValueError("invalid_guard_kind")
    # 1. 止めの印。`cli`（運営者の CLI・10/15）: 止めの印を置けない置き場なら、印は読まず・置かず、数だけ守らせる。
    can_stop = not cli or stop_place_usable(account)
    halted = stopped(account) if can_stop else None
    if halted is not None:
        raise _stopped_refusal("account", halted.get("reason"))
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    counts = summary(account, cfg, now=now)
    action = {"publish": "reply" if reply else "post", "retract": "retract", "schedule": "schedule"}[kind]
    request = assess.Request(account=account, cfg=cfg, action=action, now=now, family="sns",
                             publish_at=publish_at, scheduled_same_day=scheduled_same_day,
                             pending_replies=tuple(pending_replies or ()))
    materials = assess.Materials(
        loaders={"counts": counts, "events": lambda: _events_of(account)},
        fns={"burst_slot": lambda ahead: burst_slot(account, cfg, now=now, pending=ahead)})
    # 2〜3. 行の表（安全装置と見立て）を表の順に当てる。
    result = assess.evaluate(_rules_for("sns", action, cfg=cfg), request, materials)
    _enforce(result, account=account, principal=None, can_stop=can_stop, now=now, actor=actor, via=via,
             credential=credential)


# --------------------------------------------------------------------------
# はてなの口座: 主体・口座・ブログの 3 段（設計 4.0 S1 §4.1・§4.2）
# --------------------------------------------------------------------------

def principal_summary(principal_name, principal_cfg, *, now=None) -> dict:
    """主体を使う全口座の `sent/` を横断して数えた結果（読むだけ）。

    `posts`・`retracts`・`bookmarks` は (時刻, blog_id) の list。blog_id は記録に無ければ
    その口座の台帳の値。主体には sent を置かない——数は口座の `sent/` から毎回数える。
    """
    from . import principals
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    out = {"posts": [], "retracts": [], "bookmarks": []}
    rows = []
    for name, raw in principals.accounts_of(principal_name):
        default_blog = str(raw.get("blog_id") or "").strip().lower()
        for what, at, blog, key in _classified(name):
            out[what + "s"].append((at, blog or default_blog))
            rows.append((what, at, blog, (name,) + key if key[0] != "delivery" else key))
    limits = principals.limits(principal_cfg)
    today = now.date()
    window = now - datetime.timedelta(minutes=limits["burst"]["minutes"])
    entry_rows = [row for row in rows if row[0] in ("post", "retract")]
    bm_window = now - datetime.timedelta(minutes=limits["bookmark_burst"]["minutes"])
    out.update({
        "limits": limits,
        "posts_today": sum(1 for at, _ in out["posts"] if at.date() == today),
        "retracts_today": sum(1 for at, _ in out["retracts"] if at.date() == today),
        "bookmarks_today": sum(1 for at, _ in out["bookmarks"] if at.date() == today),
        # burst は entry（公開・更新・削除）だけ（4.0.1・ブックマークは別の窓）。同じ配信は 1 件（4.0 S5 §6）。
        "recent": len(_recent_keys(entry_rows, window, now)),
        "recent_keys": _recent_keys(entry_rows, window, now),
        # 4.0.1: ブックマークの連投の窓（`bookmark_burst`・付ける・消すの合計・試みごとに 1 件）。
        "bookmark_recent_times": sorted(at for at, _ in out["bookmarks"] if bm_window < at <= now),
    })
    out["bookmark_recent"] = len(out["bookmark_recent_times"])
    return out


def bookmark_burst_slot(whole, *, now) -> datetime.datetime:
    """ブックマークの窓（`bookmark_burst`）に 1 件入る余地ができる最初の時刻（4.0.1）。

    窓は `(t - 分, t]`。窓の中の古い順に、`件数 + 1 - count` 番目が窓を出る時刻（秒に切り上げ）。
    """
    limits = whole["limits"]["bookmark_burst"]
    times = whole["bookmark_recent_times"]
    leave = len(times) + 1 - limits["count"]
    if leave <= 0:
        return jst.to_jst(now)
    at = jst.to_jst(times[leave - 1]) + datetime.timedelta(minutes=limits["minutes"])
    if at.microsecond:
        at = at.replace(microsecond=0) + datetime.timedelta(seconds=1)
    return at


def _principal_bookmarked(principal_name, url) -> bool:
    """主体の全口座の `sent/` に、この URL のブックマークの記録があるか（削除済みでも）。"""
    return owner_bookmarked(principal_name, url, owner_wide=False) is not None


def owner_bookmarked(principal_name, url, *, owner_wide=True) -> str | None:
    """柵 1（設計 4.0 S4 §4.2）: 同じ owner の全主体の全口座の `sent/` に、同じ正規化 URL の `bm:` の行が
    あれば、その詳細（`same_principal`／`other_principal:<主体名>`）。無ければ None。

    削除済み（`retracted_at`）・結果不明（`result: unknown`）の行も「付いている」に数える。照合して
    `not_found` になった行だけは無視する（打ち直せる・§7）。`owner_wide=False` は主体の中だけ（S1 の形）。
    """
    from . import principals
    post_id = sent_mod.bookmark_post_id(url)
    group = principals.siblings(principal_name) if owner_wide else [(principal_name, None)]
    for sibling, _cfg in group:
        for name, _raw in principals.accounts_of(sibling):
            row = sent_mod.read(accounts.state_dir_for(name), post_id)
            if row is None or row.get("result") == "not_found":
                continue
            return "same_principal" if sibling == principal_name else f"other_principal:{sibling}"
    return None


def principal_bookmark_rows(principal_name) -> list:
    """主体の全口座の `sent/` のうち `kind: bookmark_add` の行（自分の記事・同じホストの枠を数える材料）。"""
    from . import principals
    out = []
    for name, _raw in principals.accounts_of(principal_name):
        out += [row for row in sent_mod.records(accounts.state_dir_for(name)) if row.get("kind") == "bookmark_add"]
    return out


def _bookmark_host(row) -> str | None:
    from .hatena_bookmark import canonical_url, host_of
    try:
        return host_of(row.get("url_canonical") or canonical_url(row.get("url") or ""))
    except ValueError:
        return None


def bookmark_counts(principal_name, *, url=None, now=None) -> dict:
    """今日（JST）の主体のブックマークのうち、自分の記事（`target: own`）の数と、`url` と同じホストの数。"""
    from .hatena_bookmark import canonical_url, host_of
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    today = now.date()
    host = None
    if url:
        host = host_of(canonical_url(url))
    own = same = 0
    for row in principal_bookmark_rows(principal_name):
        # `not_found` のあと打ち直した行は、前の試み（`attempts[]`）も同じ target・ホストで数える。
        times = [row.get("sent_at")] + [a.get("sent_at") for a in (row.get("attempts") or [])
                                         if isinstance(a, dict)]
        n = sum(1 for raw in times if (at := jst.parse(raw)) is not None and jst.to_jst(at).date() == today)
        if not n:
            continue
        if row.get("target") == "own":
            own += n
        if host is not None and _bookmark_host(row) == host:
            same += n
    return {"own_today": own, "same_host_today": same, "host": host}


def _stopped_refusal(scope, reason) -> "GuardRefused":
    code = "principal_stopped" if scope == "principal" else "account_stopped"
    return GuardRefused(code, reason=reason, assessment=assess.stopped_assessment(scope, reason))


def _enforce(result, *, account, principal, can_stop, now, actor, via, credential) -> None:
    """見立ての結論を当てる（印を置く・断る）。`pass` は何もしない。

    - `stopped`（安全装置 `stop`）: 単位（口座／主体）を止めてから `account_stopped`／`principal_stopped`
      （詳細 `burst`）。止めの印を置けない置き場の CLI は止めず `burst_limit`（詳細 `burst`・時刻なし・10/15）。
    - `stopped`（`halted`・媒体の断りの印）: 印は置き済み。符丁だけ（詳細の語は添えない）。
    - `refuse`・`wait`: 行の符丁・詳細・`next_at` で断る（`wait` は呼び手が順番待ちに並べる）。
    """
    if result["outcome"] in ("pass", "advise"):
        return
    rule = limits_policy.rule(result["rule"])
    if rule["on_exceed"] == "stop":
        if not can_stop:
            downgraded = dict(result, outcome="refuse", code="burst_limit", detail="burst", next_at=None)
            raise GuardRefused("burst_limit", reason="burst", assessment=downgraded)
        if result["scope"] == "principal":
            from . import principals
            principals.stop(principal, "burst", now=now, actor=actor, via=via, credential=credential)
        else:
            stop(account, "burst", now=now, actor=actor, via=via, credential=credential)
        raise GuardRefused(result["code"], reason="burst", assessment=result)
    raise GuardRefused(result["code"], reason=result["detail"], next_at=result["next_at"], assessment=result)


def _check_hatena(account, cfg, kind, *, now, actor, via, credential, publish_at,
                  scheduled_same_day, principal_scheduled_same_day, bookmark_url, cli,
                  bookmark_target=None, bookmark_once=True, delivery=None) -> None:
    """はてなの口座の安全装置。止め: 主体 → 口座 → 媒体の断り。数え: 口座 → ブログ → 主体。

    **主体の台帳はここで読み直す**（ロックの中で呼ばれる——承認のあとに主体の台帳から
    ブログやレーンを外せば、ここで `blog_not_allowed_for_lane` で止まる・§5.2 の 6）。
    **主体の数え直しもロックの中**（§4.3: 2 レーンの同時の公開で burst をすり抜けない）。

    当てる行は `limits_policy` の `hatenablog`（記事・予約）／`hatenabookmark`（ブックマーク）の表の順。
    """
    from . import principals, publish_hold
    if kind == "bookmark" and not (isinstance(bookmark_url, str) and bookmark_url.strip()):
        raise ValueError("bookmark_url_required")
    name = cfg.get("principal")
    try:
        principal_cfg = principals.check_account(cfg, account)
    except principals.PrincipalError as exc:
        raise GuardRefused(exc.code if exc.code in PRINCIPAL_REFUSALS else "principal_unavailable") from None
    can_stop = not cli or (stop_place_usable(account) and principals.stop_place_usable(name))
    # 1. 主体の止め（すべてのブログの公開・更新・削除とブックマーク）
    halted = principals.stopped(name) if can_stop else None
    if halted is not None:
        raise _stopped_refusal("principal", halted.get("reason"))
    # 2. 口座の止め
    halted = stopped(account) if can_stop else None
    if halted is not None:
        raise _stopped_refusal("account", halted.get("reason"))
    action = {"bookmark": "bookmark_add"}.get(kind, kind)
    family = "hatenabookmark" if action.startswith("bookmark") else "hatenablog"
    blog_id = str(cfg.get("blog_id") or "").strip().lower()
    blog = principals.blog_entry(principal_cfg, blog_id) or {}
    rules = _rules_for(family, action, cfg=cfg, principal_cfg=principal_cfg, blog=blog,
                                    media="hatenabookmark" if family == "hatenabookmark" else "hatenablog")
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    request = assess.Request(account=account, cfg=cfg, action=action, now=now, family=family,
                             publish_at=publish_at, scheduled_same_day=scheduled_same_day, principal=name,
                             principal_scheduled_same_day=principal_scheduled_same_day, blog_id=blog_id,
                             bookmark_url=bookmark_url, bookmark_target=bookmark_target,
                             bookmark_once=bookmark_once, delivery=delivery)
    # 3. 媒体の断りで止めた印（詳細の語は添えない・`server_writes.direct_send` と同じ）
    halt = assess.Materials(loaders={"publish_hold": lambda: publish_hold.held(account)})
    _enforce(assess.evaluate([r for r in rules if r["phase"] == "halt"], request, halt),
             account=account, principal=name, can_stop=can_stop, now=now, actor=actor, via=via,
             credential=credential)
    counts = summary(account, cfg, now=now)
    whole = principal_summary(name, principal_cfg, now=now)
    if family == "hatenabookmark":
        # レーンの許し（上限でなく持ち主の決め・quiet_hours・口座の枠には掛けない・§7.3）
        if cfg.get("project") not in ((principal_cfg.get("bookmark") or {}).get("lanes") or []):
            raise GuardRefused("bookmark_not_allowed_for_lane")
    materials = assess.Materials(
        loaders={"counts": counts, "whole": whole,
                 "events": lambda: _events_of(account),
                 "principal_events": lambda: principal_events(name),
                 "marks": lambda: bookmark_counts(name, url=bookmark_url, now=now),
                 "owner_bookmarked": lambda: owner_bookmarked(name, bookmark_url)},
        fns={"principal_scheduled_same_day": lambda day: principals.scheduled_same_day(name, day)})
    # 4〜5. 行の表（ブックマークの止め線 → 日の上限 → ブックマークの枠 → 記事の burst（口座 → 主体）→
    # 1 URL に 1 回 → 共通の最低線）。
    result = assess.evaluate([r for r in rules if r["phase"] != "halt"], request, materials)
    _enforce(result, account=account, principal=name, can_stop=can_stop, now=now, actor=actor, via=via,
             credential=credential)


def reactions_today(account, *, now=None) -> int:
    """今日（JST）の反応と取り消しの数（`state/<account>/reactions/` から）。"""
    from . import reactions as reactions_mod
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    today = now.date()
    return sum(1 for at in reactions_mod.events(accounts.state_dir_for(account)) if at.date() == today)


def check_reaction(account, cfg, kind, *, undo=False, now=None) -> None:
    """反応の依頼が安全装置に掛かれば `GuardRefused`（設計 3.15.0 §2.5）。

    - 止まった口座（`account_stopped`）は反応も断る。
    - 夜間（`quiet_hours`）は**再投稿にだけ**（フォロワーのタイムラインに出るため）。
      いいねと取り消しには掛けない。
    - `daily_max_reactions`（既定 50）: 反応と取り消しの合計。超えたら `reaction_limit`。
    - 公開の枠（`daily_max_posts`・`min_interval_hours`・burst）には数えない。

    行は `limits_policy` の `reaction`（`reaction.quiet` → `reaction.daily`）。
    """
    halted = stopped(account)
    if halted is not None:
        raise _stopped_refusal("account", halted.get("reason"))
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    action = "undo" if undo else kind
    request = assess.Request(account=account, cfg=cfg, action=action, now=now, family="reaction")
    materials = assess.Materials(loaders={"reactions_today": lambda: reactions_today(account, now=now)})
    result = assess.evaluate(_rules_for("reaction", action, cfg=cfg), request, materials)
    _enforce(result, account=account, principal=None, can_stop=True, now=now, actor=None, via=None,
             credential=None)
