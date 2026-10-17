"""はてなブックマークの承認と送信（設計 4.0 S1 §7・S4 §3〜§8）。

- 対象は 2 種: **自分の記事**（`--delivery`・その口座の配信の台帳で `published` の `published_url`）と、
  **参照先**（`--url`・配信の台帳に無い他人のページ）。同じ owner の**別の主体**の記事は断る
  （`bookmark_other_lane_article`）。自分の主体の記事を `--url` で指せば `use_delivery`。
- 承認の対象は `thth-hatena-bookmark-1`（`approval.compute_hatena_bookmark_sha`）。digest はその先頭 12 桁。
- 2 段目はロックの中（repo → principal → account）で安全装置を通し、adapter を作り、**owner のロック**
  （最後に取る・待たない）の中で 3 つの柵（台帳の照合・公開のエントリー情報・自分の主体の GET）を
  見てから 1 回だけ打つ。結果は口座の `sent/bm:<sha>.json` 1 つに書く（**配信の台帳には書かない**）。
- 結果不明（5xx・時間切れ・読めない応答・adapter の例外）は `result: unknown` の行を残し、打ち直さない。
  解くのは `reconcile`（GET で確かめる）。

手元の口（`thth bookmark add|delete|reconcile|list`）と遠くの道（`bookmark_request`）が同じ関数を通る。
"""
from __future__ import annotations

import hashlib
import re
import time
import urllib.parse

from . import accounts as accounts_mod
from . import approval as approval_mod
from . import deliveries as deliveries_mod
from . import jst

# 送信の口の差し替え（試験）。`callable(account_cfg, principal_cfg) -> adapter`。None なら本物
# （`adapters.hatenabookmark.factory`）を `execute_locked` が呼ぶ直前に解決する。
ADAPTER_FACTORY = None
# 柵 2（公開のエントリー情報）の差し替え（試験）。`callable(url) -> dict | None`。None なら adapter の
# `entry_info`（1 段目は adapter が無いのでモジュールの `entry_info`）。
ENTRY_INFO = None
# reconcile の間隔（公式の「繰り返すなら数秒空けて」・§7）。
RECONCILE_INTERVAL_SECONDS = 2
_CONTROL = re.compile(r"[\x00-\x20\x7f]")

# 断りの符丁（遠くの道の `SAFE_ERRORS` に足す・静的な語だけ）。
REASONS = frozenset((
    "invalid_url", "use_delivery", "bookmark_other_lane_article", "bookmark_precheck_failed",
    "bookmark_not_found", "invalid_op", "hatena_auth_rejected", "provider_rate_limited",
    "hatena_user_mismatch", "response_invalid", "hatena_transport_failed", "redirect_blocked",
    "response_too_large", "endpoint_rejected", "adapter_error",
    # 打ったあとに `sent/bm:` を書けなかった（ディスク・置き場）。
    "bookmark_record_failed",
    # adapter を作れない（`hatena_publish.AdapterUnavailable`）。
    "token_missing", "token_invalid", "consumer_missing", "consumer_invalid", "auth_unsupported"))


class BookmarkError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# --------------------------------------------------------------------------
# URL の正規化（§4.3）
# --------------------------------------------------------------------------

def canonical_url(url) -> str:
    """照合（柵 1・`post_id`）に使う URL。指紋の `url` は変えない（承認した文字列どおり）。

    scheme と host を小文字・既定のポート（80・443）を落とす・fragment を落とす・空の path は `/`。
    query は並べ替えない。`http`／`https` 以外・host 無し・制御文字や空白が残る → `ValueError("invalid_url")`。
    """
    if not isinstance(url, str):
        raise ValueError("invalid_url")
    text = url.strip()
    if not text or _CONTROL.search(text) or len(text) > 2048:
        raise ValueError("invalid_url")
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port
    except ValueError:
        raise ValueError("invalid_url") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("invalid_url")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("invalid_url")
    if parts.username is not None or parts.password is not None:
        raise ValueError("invalid_url")
    if ":" in host:
        host = f"[{host}]"
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    path = parts.path or "/"
    out = f"{scheme}://{host}{path}" + (f"?{parts.query}" if parts.query else "")
    if _CONTROL.search(out):
        raise ValueError("invalid_url")
    return out


def host_of(canonical) -> str:
    """正規化した URL のホスト（ポートは含める・同じホストの枠の数え方）。"""
    return urllib.parse.urlsplit(canonical).netloc


def parse_tags(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
        return list(raw)
    raise BookmarkError("invalid_request")


# --------------------------------------------------------------------------
# 1 段目（何も書かない）
# --------------------------------------------------------------------------

def _owner_deliveries(principal_name):
    """同じ owner の全主体の全口座の配信（`(主体名, 口座名, 台帳)`）。読めない台帳は飛ばす。"""
    from . import principals
    for sibling, _cfg in principals.siblings(principal_name):
        for name, _raw in principals.accounts_of(sibling):
            try:
                records, _broken = deliveries_mod.list_for(name)
            except (deliveries_mod.DeliveryError, OSError, ValueError):
                continue
            for record in records:
                yield sibling, name, record


def _match_delivery(principal_name, canonical):
    """`--url` を配信の台帳と照合する。`(主体名, 口座名, 台帳)` か None。"""
    for sibling, name, record in _owner_deliveries(principal_name):
        url = record.get("published_url")
        if not isinstance(url, str) or not url:
            continue
        try:
            if canonical_url(url) == canonical:
                return sibling, name, record
        except ValueError:
            continue
    return None


def _entry_info(url, adapter=None):
    """柵 2 の GET（差し替え → adapter → モジュール）。失敗は None。"""
    if ENTRY_INFO is not None:
        return ENTRY_INFO(url)
    if adapter is not None and hasattr(adapter, "entry_info"):
        return adapter.entry_info(url=url)
    from .adapters import hatenabookmark
    return hatenabookmark.entry_info(url)


def remote_others(principal_name, url, *, adapter=None) -> dict:
    """柵 2: 公開のエントリー情報に、同じ owner のどれかの主体の `hatena_id` があるか。

    `{"checked", "count", "exists", "hits": [{"principal", "hatena_id", "self"}]}`。GET が失敗したら
    `checked: False`（断らない・柵 1 と 3 がある）。**キャッシュなので「無い」は信じない**。
    """
    from . import principals
    info = _entry_info(url, adapter)
    if not isinstance(info, dict):
        return {"checked": False, "count": None, "exists": None, "hits": []}
    users = set(info.get("users") or [])
    hits = []
    for sibling, cfg in principals.siblings(principal_name):
        hatena_id = (cfg or {}).get("hatena_id")
        if hatena_id in users:
            hits.append({"principal": sibling, "hatena_id": hatena_id, "self": sibling == principal_name})
    return {"checked": True, "count": info.get("count"), "exists": info.get("exists"), "hits": hits,
            "at": jst.iso()}


def _ledger_row(principal_name, canonical):
    """`sent/bm:` の行（この主体の全口座から）。`(口座名, 行)` か None。"""
    from . import principals, sent as sent_mod
    post_id = sent_mod.bookmark_post_id(canonical)
    for name, _raw in principals.accounts_of(principal_name):
        row = sent_mod.read(accounts_mod.state_dir_for(name), post_id)
        if row is not None:
            return name, row
    return None


def prepare(account_name: str, cfg: dict, *, delivery: str | None = None, url: str | None = None,
            comment: str = "", tags=None, op: str = "add", check_remote: bool = True) -> dict:
    """1 段目の材料（何も書かない）。外れれば `BookmarkError`。

    `delivery` と `url` は**どちらか 1 つ**（§6.1）。`check_remote=False` は柵 2 の GET を打たない
    （2 段目の読み直し——柵 2 は owner のロックの中で 1 回だけ打つ）。
    """
    from . import guard, principals
    if not principals.uses_principal(cfg):
        raise BookmarkError("unsupported_operation")
    if op not in approval_mod.HATENA_BOOKMARK_OPS:
        raise BookmarkError("invalid_op")
    has_delivery = delivery is not None and str(delivery).strip() != ""
    has_url = url is not None and str(url).strip() != ""
    if has_delivery == has_url:
        raise BookmarkError("invalid_request")
    principal = cfg["principal"]
    article = blog_id = None
    if has_delivery:
        if not deliveries_mod.is_delivery_id(delivery):
            raise BookmarkError("bookmark_target_not_own")
        try:
            record = deliveries_mod.read(account_name, delivery)
        except deliveries_mod.DeliveryError:
            raise BookmarkError("bookmark_target_not_own") from None
        target_url = record.get("published_url")
        if not isinstance(target_url, str) or not target_url:
            raise BookmarkError("bookmark_target_not_own")
        if op == "add" and record.get("state") != "published":
            raise BookmarkError("bookmark_target_not_own")
        target = "own"
        article, blog_id = record.get("article"), record.get("blog_id")
        delivery_value = delivery
    else:
        target_url = str(url).strip()
        target = "external"
        delivery_value = ""
    try:
        canonical = canonical_url(target_url)
    except ValueError:
        raise BookmarkError("invalid_url") from None
    if target == "external":
        found = _match_delivery(principal, canonical)
        if found is not None:
            owner_principal, _acct, record = found
            if owner_principal == principal:
                raise BookmarkError("use_delivery")
            raise BookmarkError("bookmark_other_lane_article")
    tag_list = parse_tags(tags)
    try:
        components = approval_mod.compute_hatena_bookmark_components(
            account=account_name, principal=principal, op=op, url=target_url, delivery=delivery_value,
            comment=comment, tags=tag_list)
        sha = approval_mod.compute_hatena_bookmark_sha(
            account=account_name, principal=principal, op=op, url=target_url, delivery=delivery_value,
            comment=comment, tags=tag_list)
    except ValueError as exc:
        code = str(exc)
        raise BookmarkError(code if code in ("comment_too_long", "comment_required", "too_many_tags",
                                             "invalid_url") else "invalid_request") from None
    ledger = _ledger_row(principal, canonical)
    if op == "delete" and (ledger is None or ledger[1].get("retracted_at")
                           or ledger[1].get("result") not in ("confirmed", "unknown")):
        raise BookmarkError("bookmark_not_found")
    remaining = None
    try:
        principal_cfg = principals.load_principal(principal)
        whole = guard.principal_summary(principal, principal_cfg)
        limits = whole["limits"]
        marks = guard.bookmark_counts(principal, url=canonical)
        remaining = {"total": max(0, limits["daily_max_bookmarks"] - whole["bookmarks_today"]),
                     "own": max(0, limits["daily_max_self_bookmarks"] - marks["own_today"]),
                     "host": max(0, limits["bookmark_same_host_daily"] - marks["same_host_today"])}
    except Exception:  # noqa: BLE001 — 残りの枠は見せるためだけ
        remaining = None
    others = None
    once = None
    if op == "add":
        try:
            once = guard.owner_bookmarked(principal, canonical)
        except Exception:  # noqa: BLE001 — 表示のためだけ（2 段目で断る）
            once = None
        if check_remote:
            others = remote_others(principal, target_url)
    try:
        owner = principals.owner_of(principal)
    except Exception:  # noqa: BLE001
        owner = principal
    return {"account": account_name, "principal": principal, "owner": owner, "op": op,
            "delivery": delivery_value or None, "url": target_url, "url_canonical": canonical,
            "target": target, "article": article, "blog_id": blog_id,
            "comment": components["comment"], "tags": sorted(tag_list), "approved_sha": sha,
            "digest": sha[:approval_mod.APPROVE_DIGEST_LENGTH], "remaining_today": remaining,
            "others": others, "ledger": once}


def preview(p: dict) -> dict:
    keys = ("account", "principal", "owner", "op", "target", "delivery", "url", "article", "blog_id",
            "comment", "tags", "remaining_today", "others", "ledger")
    out = {key: p.get(key) for key in keys}
    # 1 段目の残り枠（全体）は S1 から数で返していた（遠くの道の受け手の互換）。
    rem = p.get("remaining_today")
    out["remaining_today"] = rem["total"] if isinstance(rem, dict) else rem
    out["remaining"] = rem if isinstance(rem, dict) else None
    return out


def _others_line(p) -> str:
    others = p.get("others")
    if others is None:
        return "はてな側: 確かめていません"
    if not others.get("checked"):
        return "はてな側: 確かめられなかった（公開のエントリー情報が読めない・2 段目は台帳と自分の主体で確かめる）"
    if others["hits"]:
        who = "・".join(f"{h['principal']}（{h['hatena_id']}）" for h in others["hits"])
        return f"はてな側: {who}が付けています → 断ります"
    when = jst.parse(others.get("at"))
    stamp = jst.to_jst(when).strftime("%m/%d %H:%M") if when else "今"
    if not others.get("exists"):
        return f"はてな側: はてなにまだエントリーが無いページ（{stamp} に確かめた・キャッシュ）"
    return (f"はてな側: ブックマーク {others.get('count')} 件・{p.get('owner')} の ID は付けていない"
            f"（{stamp} に確かめた・キャッシュ）")


def lines(p: dict) -> list:
    tags = "・".join(p["tags"]) if p["tags"] else "（なし）"
    rem = p.get("remaining_today")
    if isinstance(rem, dict):
        left = f"全体 {rem['total']}・自分の記事 {rem['own']}・このホスト {rem['host']}"
    else:
        left = "不明"
    head = "ブックマークを消す" if p["op"] == "delete" else "ブックマーク"
    out = [f"{head}  {p['account']}（主体 {p['principal']}・owner {p.get('owner')}）",
           f"URL: {p['url']}"]
    if p["target"] == "own":
        out.append(f"対象: 自分の記事: {p['article']}（{p['delivery']}）")
    else:
        out.append("対象: 参照先（配信の台帳に無い）")
    if p["op"] == "add":
        out.append(_others_line(p))
        if p.get("ledger"):
            out.append(f"THTH の台帳: 既に付けています（{p['ledger']}）→ 2 段目で断ります")
        out.append(f"コメント: {p['comment']}")
        if p["target"] == "own":
            out.append("  （自分の記事です。運営者であることが分かる文にしてください。例: 自分の記事。…）")
        out.append(f"タグ: {tags}")
    out += [f"主体の今日の残り枠: {left}", f"digest: {p['digest']}"]
    return out


# --------------------------------------------------------------------------
# 2 段目
# --------------------------------------------------------------------------

def _adapter(cfg, principal_cfg, adapter_factory):
    from . import hatena_publish
    factory = adapter_factory or ADAPTER_FACTORY
    if factory is None:
        from .adapters import hatenabookmark
        factory = hatenabookmark.factory
    try:
        return factory(cfg, principal_cfg)
    except hatena_publish.AdapterUnavailable as exc:
        raise BookmarkError(exc.code) from None


class _OwnerLock:
    """owner のロック（`$THTH_ROOT/owner_state/<owner>/bookmark.lock`・待たない・§4.4）。"""

    def __init__(self, owner):
        from . import lock as lock_mod, principals
        self.lock = lock_mod.AccountLock(principals.owner_bookmark_lock_path(owner))

    def __enter__(self):
        from . import guard, lock as lock_mod
        try:
            lock_mod.acquire(self.lock, 0)
        except lock_mod.LockBusy:
            raise guard.GuardRefused("bookmark_busy") from None
        return self

    def __exit__(self, *exc):
        self.lock.release()
        return False


def _remote_fields(bookmark) -> dict | None:
    if not isinstance(bookmark, dict):
        return None
    out = {}
    if isinstance(bookmark.get("permalink"), str):
        out["permalink"] = bookmark["permalink"]
    if isinstance(bookmark.get("created_epoch"), int) and not isinstance(bookmark.get("created_epoch"), bool):
        out["created_epoch"] = bookmark["created_epoch"]
    if isinstance(bookmark.get("user"), str):
        out["user"] = bookmark["user"]
    return out


def execute_locked(account_name: str, cfg: dict, p: dict, *, adapter_factory=None, actor=None, via=None,
                   credential=None, log=None) -> dict:
    """2 段目（**repo → principal → account のロックの中で呼ぶ**）。断りは `GuardRefused`・`BookmarkError`。

    順（§6.5）: 安全装置（`bookmark_once` は見ない）→ 主体の突き合わせ → adapter → **owner のロック**
    （柵 1 → 柵 2 → 柵 3 → 1 回だけ打つ → `sent/bm:` に書く）。削除は `delete_locked`。
    """
    if p["op"] == "delete":
        return delete_locked(account_name, cfg, p, adapter_factory=adapter_factory, actor=actor, via=via,
                             credential=credential)
    from . import guard, principals, redact as redact_mod, sent as sent_mod
    from .adapters.hatenabookmark import BookmarkHTTPError, BookmarkResult
    guard.check(account_name, cfg, "bookmark", actor=actor, via=via, credential=credential,
                bookmark_url=p["url_canonical"], bookmark_target=p["target"], bookmark_once=False)
    principal_cfg = principals.check_account(cfg, account_name)
    adapter = _adapter(cfg, principal_cfg, adapter_factory)
    principal = cfg["principal"]
    with _OwnerLock(principals.owner_of(principal, principal_cfg)):
        # 柵 1: 同じ owner の全主体の台帳
        detail = guard.owner_bookmarked(principal, p["url_canonical"])
        if detail is not None:
            raise guard.GuardRefused("bookmark_once", reason=detail)
        # 柵 2: 公開のエントリー情報（失敗なら通す）
        others = remote_others(principal, p["url"], adapter=adapter)
        if others["hits"]:
            hit = others["hits"][0]
            raise guard.GuardRefused("bookmark_once", reason=("self_remote" if hit["self"]
                                                              else f"other_principal_remote:{hit['hatena_id']}"))
        # 柵 3: この主体で既に付いていないか（POST は「あれば更新」なので上書きしない）
        try:
            existing = adapter.get_bookmark(url=p["url"])
        except BookmarkHTTPError:
            raise BookmarkError("bookmark_precheck_failed") from None
        except Exception:  # noqa: BLE001 — 確かめられないなら打たない（出ない側）
            raise BookmarkError("bookmark_precheck_failed") from None
        if existing is not None:
            raise guard.GuardRefused("bookmark_exists_remote")
        try:
            result = adapter.add_bookmark(url=p["url"], comment=p["comment"], tags=p["tags"])
        except Exception as exc:  # noqa: BLE001 — adapter の例外は結果不明（S1 監査 §15 の 2）
            result = BookmarkResult(ok=False, ambiguous=True,
                                    error="adapter_error:" + (redact_mod.redact(exc.__class__.__name__) or ""))
        if not getattr(result, "ok", False) and not getattr(result, "ambiguous", False):
            raise BookmarkError(getattr(result, "error", None) or "publication_failed")
        state = "confirmed" if result.ok else "unknown"
        at = jst.iso()
        post_id = sent_mod.bookmark_post_id(p["url_canonical"])
        remote = _remote_fields(getattr(result, "bookmark", None)) if state == "confirmed" else None
        try:
            sent_mod.write(accounts_mod.state_dir_for(account_name), post_id=post_id, text=p["comment"],
                           body_hash=approval_mod.compute_body_hash(p["comment"]), sent_at=at,
                           approved_fingerprint=p["approved_sha"], kind="bookmark_add", blog_id=p.get("blog_id"),
                           delivery=p.get("delivery"), url=p["url"], url_canonical=p["url_canonical"],
                           target=p["target"], article=p.get("article") if p["target"] == "own" else None,
                           tags=list(p["tags"]), result=state, remote=remote, retry_not_found=True)
        except (OSError, ValueError):
            # はてなには打った（か分からない）のに記録を書けない（ディスク・置き場）。traceback にせず静的な
            # 符丁で返す。打ち直しは柵 3（この主体の GET）が `bookmark_exists_remote` で止める。
            if log is not None:
                log(f"ブックマークの記録を書けませんでした（はてなには打っています）: {p['url']}。"
                    "置き場を直してから、はてなの画面で付いているかを確かめてください")
            raise BookmarkError("bookmark_record_failed") from None
    out = {"account": account_name, "status": "bookmarked" if state == "confirmed" else "unknown",
           "result": state, "url": p["url"], "delivery": p.get("delivery"), "target": p["target"],
           "post_id": post_id, "permalink": (remote or {}).get("permalink")}
    if state == "unknown":
        error = str(getattr(result, "error", "") or "")
        out["error"] = error.split(":", 1)[0] if error.startswith("adapter_error") else error
    return out


def delete_locked(account_name: str, cfg: dict, p: dict, *, adapter_factory=None, actor=None, via=None,
                  credential=None, reason=None) -> dict:
    """削除の 2 段目（§8・ロックの中）。204・404 で `mark_retracted`。5xx・時間切れは何も書かない。"""
    from . import guard, principals, sent as sent_mod
    guard.check(account_name, cfg, "bookmark_delete", actor=actor, via=via, credential=credential)
    principal_cfg = principals.check_account(cfg, account_name)
    found = _ledger_row(cfg["principal"], p["url_canonical"])
    if (found is None or found[1].get("retracted_at")
            or found[1].get("result") not in ("confirmed", "unknown")):
        raise BookmarkError("bookmark_not_found")
    holder, row = found
    adapter = _adapter(cfg, principal_cfg, adapter_factory)
    try:
        result = adapter.delete_bookmark(url=row.get("url") or p["url"])
    except Exception:  # noqa: BLE001 — 削除は冪等。何も書かず人がもう一度打つ
        raise BookmarkError("hatena_transport_failed") from None
    if not getattr(result, "ok", False):
        raise BookmarkError(getattr(result, "error", None) or "deletion_failed")
    missing = bool(getattr(result, "missing", False))
    why = "remote_missing" if missing else (reason or "bookmark_delete")
    origin = None
    if via in ("mcp", "cli", "http", "api"):
        origin = {"via": via, "credential": credential} if credential else {"via": via}
    sent_mod.mark_retracted(accounts_mod.state_dir_for(holder), row["post_id"], retracted_at=jst.iso(),
                            retracted_by=actor or "thth", retract_reason=why, origin=origin)
    return {"account": account_name, "status": "bookmark_deleted", "url": row.get("url"),
            "post_id": row["post_id"], "missing": missing, "retract_reason": why}


# --------------------------------------------------------------------------
# 照合・一覧（CLI だけ・§7・§11）
# --------------------------------------------------------------------------

def reconcile(account_name: str, cfg: dict, *, url=None, by=None, adapter_factory=None, sleep=None) -> dict:
    """`result: unknown` の行ごとに `get_bookmark` を 1 回（**口座のロックの中で呼ぶ**）。

    200 で `user` が主体の ID → `confirmed`（`remote` を埋める）。404 → `not_found`（枠には数えたまま・
    以後 `bookmark_once` はこの行を無視）。それ以外はそのまま（次回）。1 件ごとに 2 秒空ける。
    """
    from . import admin_log, principals, sent as sent_mod
    from .adapters.hatenabookmark import BookmarkHTTPError
    if not principals.uses_principal(cfg):
        raise BookmarkError("unsupported_operation")
    principal_cfg = principals.check_account(cfg, account_name)
    want = None
    if url is not None:
        try:
            want = canonical_url(url)
        except ValueError:
            raise BookmarkError("invalid_url") from None
    state_dir = accounts_mod.state_dir_for(account_name)
    rows = [row for row in sent_mod.records(state_dir)
            if row.get("kind") == "bookmark_add" and row.get("result") == "unknown"
            and (want is None or (row.get("url_canonical") or "") == want)]
    out = {"account": account_name, "checked": 0, "confirmed": 0, "not_found": 0, "unchanged": 0, "rows": []}
    if not rows:
        return out
    adapter = _adapter(cfg, principal_cfg, adapter_factory)
    wait = sleep if sleep is not None else time.sleep
    for index, row in enumerate(rows):
        if index:
            wait(RECONCILE_INTERVAL_SECONDS)
        out["checked"] += 1
        try:
            found = adapter.get_bookmark(url=row.get("url"))
        except (BookmarkHTTPError, Exception):  # noqa: BLE001 — 次回
            found = False
        if found is None:
            sent_mod.mark_bookmark_result(state_dir, row["post_id"], result="not_found", at=jst.iso())
            verdict = "not_found"
        elif isinstance(found, dict) and found.get("user") == principal_cfg["hatena_id"]:
            sent_mod.mark_bookmark_result(state_dir, row["post_id"], result="confirmed", at=jst.iso(),
                                          remote=_remote_fields(found))
            verdict = "confirmed"
        else:
            verdict = "unchanged"
        out[verdict] += 1
        out["rows"].append({"post_id": row["post_id"], "url": row.get("url"), "result": verdict})
    if out["confirmed"] or out["not_found"]:
        try:
            admin_log.append("bookmark_reconciled", account_name, cfg, by=by or "thth", via="cli",
                             diff={"confirmed": [0, out["confirmed"]], "not_found": [0, out["not_found"]]})
        except Exception:  # noqa: BLE001 — 正本は sent/。変更ログに残せなくても結果は変わらない
            pass
    return out


def list_rows(account_name: str) -> list:
    """口座の `sent/bm:` の一覧（URL・target・result・時刻・削除）。読むだけ。"""
    from . import sent as sent_mod
    out = []
    for row in sent_mod.records(accounts_mod.state_dir_for(account_name)):
        if row.get("kind") != "bookmark_add":
            continue
        out.append({"post_id": row["post_id"], "url": row.get("url"), "target": row.get("target"),
                    "delivery": row.get("delivery"), "article": row.get("article"),
                    "result": row.get("result"), "sent_at": row.get("sent_at"),
                    "retracted_at": row.get("retracted_at"), "permalink": (row.get("remote") or {}).get("permalink")})
    out.sort(key=lambda r: (r["sent_at"] or "", r["post_id"]))
    return out


def unknown_count(account_name: str) -> int:
    from . import sent as sent_mod
    return sum(1 for row in sent_mod.records(accounts_mod.state_dir_for(account_name))
               if row.get("kind") == "bookmark_add" and row.get("result") == "unknown")


def by_delivery(account_name: str) -> dict:
    """配信 id → その配信のブックマークの行（`thth deliveries` が読むときに添える・台帳には書かない）。"""
    out = {}
    for row in list_rows(account_name):
        if row.get("delivery"):
            out.setdefault(row["delivery"], row)
    return out


def event_id(post_id: str, what: str) -> str:
    """ブックマークの出来事の id（§3.3）。時刻を入れない（照合で `confirmed` になっても同じ id）。"""
    return "ev_" + hashlib.sha256(f"thth.bookmark/1|{post_id}|{what}".encode("utf-8")).hexdigest()[:24]
