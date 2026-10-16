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
  `burst.count` を超える依頼だった → 口座を止めて断る
- `burst_limit`（詳細 `burst`）: 口座を**止めずに**この 1 件だけ断る。**返信**が burst を超えたとき
  （3.25.0・`next_at` は窓が空く時刻・呼び手は queue の順番待ちに並べる）と、止めの印を置けない
  置き場の運営者の CLI（10/15）
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

from . import accounts, jst, sent as sent_mod, server_files
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
                       "burst_limit"))
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
    """安全装置の断り。`reason` は静的な詳細、`next_at` は次に出せる時刻（無ければ None）。"""

    def __init__(self, code, *, reason=None, next_at=None):
        super().__init__(code)
        self.reason = reason
        self.next_at = next_at


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
    """`sent/` の記録を (種類, 時刻, blog_id) の list に。種類は `post`・`retract`・`bookmark`。

    `kind` の無い行（SNS）は今までどおり `sent_at` を公開・`retracted_at` を削除に数える。
    はてなの行（設計 4.0 S1 §4.2）: `entry_create`／`entry_update` は公開（足された
    `retracted_at` は削除）・`entry_delete` は削除・`bookmark_*` はブックマーク（足された
    `retracted_at` も）。知らない `kind` は公開として数える（数え漏らす側に倒さない）。
    """
    out = []
    for row in sent_mod.records(accounts.state_dir_for(account)):
        kind = row.get("kind")
        blog = row.get("blog_id")
        sent_at = jst.parse(row.get("sent_at"))
        retracted_at = jst.parse(row.get("retracted_at"))
        if kind in ("bookmark_add", "bookmark_delete"):
            first, second = "bookmark", "bookmark"
        elif kind == "entry_delete":
            first, second = "retract", "retract"
        else:
            first, second = "post", "retract"
        if sent_at is not None:
            out.append((first, sent_at, blog))
        if retracted_at is not None:
            out.append((second, retracted_at, blog))
        if first == "bookmark":
            # 4.0 S4 §7: `not_found` のあと打ち直した行は、前の試みを `attempts[]` に持つ（数え続ける）。
            for attempt in row.get("attempts") or []:
                at = jst.parse(attempt.get("sent_at")) if isinstance(attempt, dict) else None
                if at is not None:
                    out.append(("bookmark", at, blog))
    return out


def _events(account):
    """`sent/` の記録から (公開の時刻の list, 削除の時刻の list)。読めない時刻は数えない。

    ブックマーク（はてな）は公開の枠に入れない（`_classified`）。SNS の口座の数は変わらない。
    """
    posts, retracts = [], []
    for what, at, _blog in _classified(account):
        if what == "post":
            posts.append(at)
        elif what == "retract":
            retracts.append(at)
    return posts, retracts


def _next_midnight(now):
    day = jst.to_jst(now).date() + datetime.timedelta(days=1)
    return datetime.datetime.combine(day, datetime.time(0, 0), tzinfo=jst.JST)


def _quiet_end(now, quiet_hours):
    hour, minute = (int(x) for x in quiet_hours[1].split(":"))
    now = jst.to_jst(now)
    end = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return end if end > now else end + datetime.timedelta(days=1)


def summary(account, cfg, *, now=None) -> dict:
    """数えた結果（読むだけ・`/activity` や答えに添える用）。"""
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    limits = accounts.guard_limits(cfg)
    posts, retracts = _events(account)
    today = now.date()
    window = now - datetime.timedelta(minutes=limits["burst"]["minutes"])
    return {
        "posts_today": sum(1 for at in posts if at.date() == today),
        "retracts_today": sum(1 for at in retracts if at.date() == today),
        "recent": sum(1 for at in posts + retracts if window < at <= now),
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
          bookmark_target=None, bookmark_once=True) -> None:
    """依頼が安全装置に掛かれば `GuardRefused` を上げる。掛からなければ何もしない。

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
                             bookmark_once=bookmark_once)
    if kind in HATENA_KINDS:
        raise ValueError("invalid_guard_kind")
    # `cli`（運営者の CLI・10/15）: 止めの印を置けない置き場なら、印は読まず・置かず、数だけ守らせる。
    can_stop = not cli or stop_place_usable(account)
    halted = stopped(account) if can_stop else None
    if halted is not None:
        raise GuardRefused("account_stopped", reason=halted.get("reason"))
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    counts = summary(account, cfg, now=now)
    limits = counts["limits"]
    if kind == "publish":
        quiet = cfg.get("quiet_hours")
        from .select import in_quiet_hours
        if quiet and in_quiet_hours(now, quiet):
            raise GuardRefused("quiet_hours", next_at=jst.iso(_quiet_end(now, quiet)))
        hours = cfg.get("min_interval_hours")
        last = counts["last_post_at"]
        if (not reply and isinstance(hours, (int, float)) and not isinstance(hours, bool)
                and hours > 0 and last is not None):
            ready = last + datetime.timedelta(hours=hours)
            if now < ready:
                raise GuardRefused("too_soon", next_at=jst.iso(ready))
        if counts["posts_today"] >= limits["daily_max_posts"]:
            raise GuardRefused("daily_limit", next_at=jst.iso(_next_midnight(now)))
    elif kind == "retract":
        if counts["retracts_today"] >= limits["daily_max_retracts"]:
            raise GuardRefused("retract_limit", next_at=jst.iso(_next_midnight(now)))
    else:
        day = jst.to_jst(publish_at).date() if publish_at is not None else now.date()
        published = sum(1 for at in counts["posts"] if at.date() == day)
        if published + scheduled_same_day >= limits["daily_max_posts"]:
            nxt = datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(0, 0), tzinfo=jst.JST)
            raise GuardRefused("daily_limit", next_at=jst.iso(nxt))
        return
    # 急な連投（公開と削除の合計）。返信は止めず、順番待ちの時刻を添えて断る（3.25.0）。
    if kind == "publish" and reply:
        ahead = [jst.to_jst(at) for at in (pending_replies or ()) if jst.to_jst(at) > now]
        if ahead or counts["recent"] + 1 > limits["burst"]["count"]:
            slot = burst_slot(account, cfg, now=now, pending=ahead)
            raise GuardRefused("burst_limit", reason="burst", next_at=jst.iso(slot))
        return
    # 投稿（返信でないもの）。この依頼を足すと超えるなら止める。
    if counts["recent"] + 1 > limits["burst"]["count"]:
        if not can_stop:
            # 印を置けないので口座は止めず、この 1 件を断る（数え直せば次の窓で出せる）。
            raise GuardRefused("burst_limit", reason="burst")
        stop(account, "burst", now=now, actor=actor, via=via, credential=credential)
        raise GuardRefused("account_stopped", reason="burst")


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
    for name, raw in principals.accounts_of(principal_name):
        default_blog = str(raw.get("blog_id") or "").strip().lower()
        for what, at, blog in _classified(name):
            out[what + "s"].append((at, blog or default_blog))
    limits = principals.limits(principal_cfg)
    today = now.date()
    window = now - datetime.timedelta(minutes=limits["burst"]["minutes"])
    out.update({
        "limits": limits,
        "posts_today": sum(1 for at, _ in out["posts"] if at.date() == today),
        "retracts_today": sum(1 for at, _ in out["retracts"] if at.date() == today),
        "bookmarks_today": sum(1 for at, _ in out["bookmarks"] if at.date() == today),
        # burst は entry（公開・削除）とブックマークの合計（§4.2）
        "recent": sum(1 for at, _ in out["posts"] + out["retracts"] + out["bookmarks"] if window < at <= now),
    })
    return out


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
        at = jst.parse(row.get("sent_at"))
        if at is None or jst.to_jst(at).date() != today:
            continue
        if row.get("target") == "own":
            own += 1
        if host is not None and _bookmark_host(row) == host:
            same += 1
    return {"own_today": own, "same_host_today": same, "host": host}


def _check_hatena(account, cfg, kind, *, now, actor, via, credential, publish_at,
                  scheduled_same_day, principal_scheduled_same_day, bookmark_url, cli,
                  bookmark_target=None, bookmark_once=True) -> None:
    """はてなの口座の安全装置。止め: 主体 → 口座 → 媒体の断り。数え: 口座 → ブログ → 主体。

    **主体の台帳はここで読み直す**（ロックの中で呼ばれる——承認のあとに主体の台帳から
    ブログやレーンを外せば、ここで `blog_not_allowed_for_lane` で止まる・§5.2 の 6）。
    **主体の数え直しもロックの中**（§4.3: 2 レーンの同時の公開で burst をすり抜けない）。
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
        raise GuardRefused("principal_stopped", reason=halted.get("reason"))
    # 2. 口座の止め
    halted = stopped(account) if can_stop else None
    if halted is not None:
        raise GuardRefused("account_stopped", reason=halted.get("reason"))
    # 3. 媒体の断りで止めた印（詳細の語は添えない・`server_writes.direct_send` と同じ）
    if publish_hold.held(account) is not None:
        raise GuardRefused("account_stopped")
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    counts = summary(account, cfg, now=now)
    limits = counts["limits"]
    whole = principal_summary(name, principal_cfg, now=now)
    plimits = whole["limits"]
    blog_id = str(cfg.get("blog_id") or "").strip().lower()
    blog = principals.blog_entry(principal_cfg, blog_id) or {}
    today = now.date()
    midnight = jst.iso(_next_midnight(now))

    def blog_today(rows):
        return sum(1 for at, b in rows if b == blog_id and at.date() == today)

    if kind in ("entry_create", "entry_update"):
        quiet = cfg.get("quiet_hours")
        from .select import in_quiet_hours
        if quiet and in_quiet_hours(now, quiet):
            raise GuardRefused("quiet_hours", next_at=jst.iso(_quiet_end(now, quiet)))
        hours = cfg.get("min_interval_hours")
        last = counts["last_post_at"]
        if (isinstance(hours, (int, float)) and not isinstance(hours, bool)
                and hours > 0 and last is not None):
            ready = last + datetime.timedelta(hours=hours)
            if now < ready:
                raise GuardRefused("too_soon", next_at=jst.iso(ready))
        if counts["posts_today"] >= limits["daily_max_posts"]:
            raise GuardRefused("daily_limit", next_at=midnight)
        if "daily_max_posts" in blog and blog_today(whole["posts"]) >= blog["daily_max_posts"]:
            raise GuardRefused("blog_daily_limit", next_at=midnight)
        if whole["posts_today"] >= plimits["daily_max_posts"]:
            raise GuardRefused("principal_daily_limit", next_at=midnight)
    elif kind == "entry_delete":
        if counts["retracts_today"] >= limits["daily_max_retracts"]:
            raise GuardRefused("retract_limit", next_at=midnight)
        if "daily_max_retracts" in blog and blog_today(whole["retracts"]) >= blog["daily_max_retracts"]:
            raise GuardRefused("blog_retract_limit", next_at=midnight)
        if whole["retracts_today"] >= plimits["daily_max_retracts"]:
            raise GuardRefused("principal_retract_limit", next_at=midnight)
    elif kind == "schedule":
        day = jst.to_jst(publish_at).date() if publish_at is not None else today
        nxt = jst.iso(datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(0, 0),
                                                tzinfo=jst.JST))
        published = sum(1 for at in counts["posts"] if at.date() == day)
        if published + scheduled_same_day >= limits["daily_max_posts"]:
            raise GuardRefused("daily_limit", next_at=nxt)
        if principal_scheduled_same_day is None:
            principal_scheduled_same_day = principals.scheduled_same_day(name, day)
        principal_published = sum(1 for at, _ in whole["posts"] if at.date() == day)
        if principal_published + principal_scheduled_same_day >= plimits["daily_max_posts"]:
            raise GuardRefused("principal_daily_limit", next_at=nxt)
        return
    else:  # bookmark・bookmark_delete（quiet_hours・口座の枠には掛けない・§7.3）
        if cfg.get("project") not in ((principal_cfg.get("bookmark") or {}).get("lanes") or []):
            raise GuardRefused("bookmark_not_allowed_for_lane")
        if whole["bookmarks_today"] >= plimits["daily_max_bookmarks"]:
            raise GuardRefused("bookmark_limit", next_at=midnight)
        if kind == "bookmark":
            # 4.0 S4 §5.3: 自分の記事は 1 日 `daily_max_self_bookmarks`・同じホストは 1 日 `bookmark_same_host_daily`。
            marks = bookmark_counts(name, url=bookmark_url, now=now)
            if bookmark_target == "own" and marks["own_today"] >= plimits["daily_max_self_bookmarks"]:
                raise GuardRefused("self_bookmark_limit", next_at=midnight)
            if marks["same_host_today"] >= plimits["bookmark_same_host_daily"]:
                raise GuardRefused("bookmark_host_limit", next_at=midnight)
    # 急な連投。口座（entry だけ・今までの数え方）→ 主体（entry とブックマークの合計）。
    if kind.startswith("entry_") and counts["recent"] + 1 > limits["burst"]["count"]:
        if not can_stop:
            raise GuardRefused("burst_limit", reason="burst")
        stop(account, "burst", now=now, actor=actor, via=via, credential=credential)
        raise GuardRefused("account_stopped", reason="burst")
    if whole["recent"] + 1 > plimits["burst"]["count"]:
        if not can_stop:
            raise GuardRefused("burst_limit", reason="burst")
        principals.stop(name, "burst", now=now, actor=actor, via=via, credential=credential)
        raise GuardRefused("principal_stopped", reason="burst")
    # 1 URL に 1 回だけ（削除したあとの 2 回目も断る・§4.2・§7.3 の順）。4.0 S4 §4.2: 同じ owner の
    # 全主体をまたぐ（柵 1）。`bookmark_once=False` なら呼び手が owner のロックの中で見る。
    if kind == "bookmark" and bookmark_once:
        detail = owner_bookmarked(name, bookmark_url)
        if detail is not None:
            raise GuardRefused("bookmark_once", reason=detail)


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
    """
    halted = stopped(account)
    if halted is not None:
        raise GuardRefused("account_stopped", reason=halted.get("reason"))
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    if kind == "repost" and not undo:
        quiet = cfg.get("quiet_hours")
        from .select import in_quiet_hours
        if quiet and in_quiet_hours(now, quiet):
            raise GuardRefused("quiet_hours", next_at=jst.iso(_quiet_end(now, quiet)))
    limit = accounts.guard_limits(cfg)["daily_max_reactions"]
    if reactions_today(account, now=now) >= limit:
        raise GuardRefused("reaction_limit", next_at=jst.iso(_next_midnight(now)))
