"""はてなブックマークの承認（設計 4.0 S1 §7）。**送信は S4**——S1 は 2 段の承認と受け口まで。

- 対象は**自分の記事だけ**: その口座の配信の台帳に `published_url` がある配信（`bookmark_target_not_own`）。
- 承認の対象は `thth-hatena-bookmark-1`（`approval.compute_hatena_bookmark_sha`）。digest はその先頭 12 桁。
- 2 段目はロックの中（repo → principal → account）で安全装置（主体の止め → 口座の止め →
  `daily_max_bookmarks` → burst → `bookmark_once`）を通してから adapter を呼ぶ。adapter
  （S4）が無ければ `unsupported_medium` で止まる（打たない側に倒す）。

手元の口（`thth bookmark add`）と遠くの道（`bookmark_request`）が同じ関数を通る。
"""
from __future__ import annotations

from . import accounts as accounts_mod
from . import approval as approval_mod
from . import deliveries as deliveries_mod
from . import jst

# S4 が差し込む（`callable(account_cfg, principal_cfg) -> adapter`・`adapter.add_bookmark(url=, comment=, tags=)`）。
ADAPTER_FACTORY = None


class BookmarkError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def parse_tags(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
        return list(raw)
    raise BookmarkError("invalid_request")


def prepare(account_name: str, cfg: dict, *, delivery: str, comment: str, tags=None, op: str = "add") -> dict:
    """1 段目の材料（何も書かない）。外れれば `BookmarkError`。"""
    from . import guard, principals
    if not principals.uses_principal(cfg):
        raise BookmarkError("unsupported_operation")
    if not deliveries_mod.is_delivery_id(delivery):
        raise BookmarkError("bookmark_target_not_own")
    try:
        record = deliveries_mod.read(account_name, delivery)
    except deliveries_mod.DeliveryError:
        raise BookmarkError("bookmark_target_not_own") from None
    url = record.get("published_url")
    if record.get("state") != "published" or not isinstance(url, str) or not url:
        raise BookmarkError("bookmark_target_not_own")
    tag_list = parse_tags(tags)
    try:
        components = approval_mod.compute_hatena_bookmark_components(
            account=account_name, principal=cfg["principal"], op=op, url=url, delivery=delivery,
            comment=comment, tags=tag_list)
        sha = approval_mod.compute_hatena_bookmark_sha(
            account=account_name, principal=cfg["principal"], op=op, url=url, delivery=delivery,
            comment=comment, tags=tag_list)
    except ValueError as exc:
        raise BookmarkError(str(exc) if str(exc) in ("comment_too_long", "comment_required", "too_many_tags")
                            else "invalid_request") from None
    remaining = None
    try:
        principal_cfg = principals.load_principal(cfg["principal"])
        whole = guard.principal_summary(cfg["principal"], principal_cfg)
        remaining = max(0, whole["limits"]["daily_max_bookmarks"] - whole["bookmarks_today"])
    except Exception:  # noqa: BLE001 — 残りの枠は見せるためだけ
        remaining = None
    return {"account": account_name, "principal": cfg["principal"], "op": op, "delivery": delivery,
            "url": url, "article": record.get("article"), "blog_id": record.get("blog_id"),
            "comment": components["comment"], "tags": sorted(tag_list), "approved_sha": sha,
            "digest": sha[:approval_mod.APPROVE_DIGEST_LENGTH], "remaining_today": remaining}


def preview(p: dict) -> dict:
    return {key: p[key] for key in ("account", "principal", "op", "delivery", "url", "article", "blog_id",
                                    "comment", "tags", "remaining_today")}


def lines(p: dict) -> list:
    tags = "・".join(p["tags"]) if p["tags"] else "（なし）"
    left = "不明" if p["remaining_today"] is None else str(p["remaining_today"])
    return [f"ブックマーク  {p['account']}（主体 {p['principal']}）",
            f"URL: {p['url']}",
            f"記事: {p['article']}（{p['blog_id']}・{p['delivery']}）",
            f"コメント: {p['comment']}",
            f"タグ: {tags}",
            f"主体の今日の残り枠: {left}",
            f"digest: {p['digest']}"]


def execute_locked(account_name: str, cfg: dict, p: dict, *, adapter_factory=None, actor=None, via=None,
                   credential=None) -> dict:
    """2 段目（**ロックの中で呼ぶ**）。安全装置 → adapter（S4）→ 記録。断りは `GuardRefused`・`BookmarkError`。"""
    from . import guard, principals, sent as sent_mod
    guard.check(account_name, cfg, "bookmark" if p["op"] == "add" else "bookmark_delete",
                actor=actor, via=via, credential=credential, bookmark_url=p["url"])
    principal_cfg = principals.check_account(cfg, account_name)
    factory = adapter_factory or ADAPTER_FACTORY
    if factory is None:
        raise BookmarkError("unsupported_medium")
    adapter = factory(cfg, principal_cfg)
    result = adapter.add_bookmark(url=p["url"], comment=p["comment"], tags=p["tags"])
    if not getattr(result, "ok", False):
        raise BookmarkError("publication_failed")
    at = jst.iso()
    sent_mod.write(accounts_mod.state_dir_for(account_name), post_id=sent_mod.bookmark_post_id(p["url"]),
                   text=p["comment"], body_hash=approval_mod.compute_body_hash(p["comment"]), sent_at=at,
                   approved_fingerprint=p["approved_sha"], kind="bookmark_add", blog_id=p["blog_id"],
                   delivery=p["delivery"], url=p["url"])
    import hashlib
    deliveries_mod.advance(account_name, p["delivery"], state="published", at=at,
                           fields={"bookmark": {"state": "added", "at": at,
                                                "comment_sha256": hashlib.sha256(p["comment"].encode()).hexdigest(),
                                                "tags": p["tags"]}},
                           row={"bookmark": "added", "approved_sha": p["approved_sha"]})
    return {"account": account_name, "status": "bookmarked", "url": p["url"], "delivery": p["delivery"]}
