"""見立ての予告（advise）——断らない・止めない・本文を直さない（設計「上限と連投の柵」§5.2・§7.3・段 4）。

信号は 4 つ（最初の版・決まった規則・確率や学習はしない）:

- `counterpart`（X の返信）: 原稿の `found_by` が `mention` でない返信は、相手が @ か引用で呼んでいない
  見込み。X は呼ばれていない返信を 403 `x_reply_not_engaged` で断る（`x.reply_requires_mention`）。
- `same_text`: 本文が、この口座の直近 30 日の公開（`sent/`）と同じ、または書き出し 20 字が同じ。
- `link_heavy`: 本文の URL が 3 つ以上、または同じ URL が 2 回以上。
- `engagement_bait`: 「いいねして」「フォローして」「拡散希望」などの決まった型。

根拠は媒体の特性の台帳（`thth/media_traits/`）の項目のうち、`signals` にその行（`post.same_text` など）を
挙げるもの。台帳に根拠の無い媒体・信号は `thth:<行>`（THTH の推測・確かさ low）と書く。
出すのは lint の `warning:` の 1 行と、1 段目の `advisories[]`（`assess.finding` と同じ形の `outcome: advise`）。
"""
from __future__ import annotations

import datetime
import re

from . import jst

SIGNAL_RULES = {"counterpart": "reply.counterpart", "same_text": "post.same_text",
                "link_heavy": "post.link_heavy", "engagement_bait": "post.engagement_bait"}
SAME_TEXT_DAYS = 30
SAME_HEAD_CHARS = 20
LINKS_MAX = 2
URL = re.compile(r"https?://[^\s<>\"'）」』】]+")
BAIT = re.compile(r"(いいね(して|を?お願い|ください)|フォロー(して|お願い|ください)|拡散(希望|して|お願い)|"
                  r"リポスト(して|お願い)|RT(して|お願い)|シェア(して|お願い)|"
                  r"\blike (this|if)\b|\bfollow (me|for)\b|\bplease (share|retweet|repost)\b)", re.I)
CONFIDENCE_JA = {"high": "高", "medium": "中", "low": "低"}
KIND_JA = {"spam_signal": "不自然とみなされる形", "policy": "規約", "observation": "THTH の実機",
           "api_limit": "API の上限", "culture": "使い方の慣習", "good_signal": "良い振る舞い", "penalty": "罰"}


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def trait_for(media, rule) -> dict:
    """その媒体で行（`post.same_text` など）を `signals` に挙げる台帳の項目（無ければ THTH の推測）。"""
    try:
        from . import media_traits
        doc = media_traits.load(media)
        found = [t for t in doc["traits"] if rule in (t.get("signals") or [])]
    except Exception:  # noqa: BLE001 — 台帳が読めなくても予告は推測として出す（断らない）
        found = []
    if found:
        best = sorted(found, key=lambda t: {"high": 0, "medium": 1, "low": 2}.get(t.get("confidence"), 3))[0]
        return {"id": best["id"], "kind": best.get("kind"), "confidence": best.get("confidence"),
                "statement": best.get("statement"), "evidence_type": (best.get("evidence") or {}).get("type")}
    return {"id": "thth:" + rule, "kind": None, "confidence": "low", "statement": None, "evidence_type": "inferred"}


def basis_text(trait) -> str:
    """根拠の 1 句（確かさが中以下なら「（中）」「（低）」・推測は「THTH の推測」）。"""
    conf = CONFIDENCE_JA.get(trait.get("confidence"), "?")
    if trait.get("evidence_type") == "inferred" or not trait.get("statement"):
        return f"THTH の推測（確かさ {conf}・{trait['id']}）"
    kind = KIND_JA.get(trait.get("kind"), trait.get("kind") or "")
    return f"{trait['statement']}（{kind}・確かさ {conf}・{trait['id']}）"


def _finding(media, signal, message, **extra) -> dict:
    rule = SIGNAL_RULES[signal]
    trait = trait_for(media, rule)
    out = {"outcome": "advise", "rule": rule, "signal": signal, "phase": "assess", "purpose": "platform",
           "scope": "account", "used": None, "count": None, "window": None, "next_at": None,
           "trait": trait, "origin": "default", "how_to_pass": "通します（断りません）。直すかは書き手が決めます",
           "code": None, "detail": None, "message": message}
    out.update(extra)
    return out


def counterpart(media, fm) -> dict | None:
    if media != "x" or not (fm.get("reply_to") or fm.get("reply_to_file")):
        return None
    if str(fm.get("found_by") or "") == "mention":
        return None
    return _finding(media, "counterpart",
                    "X の返信: 相手がこの口座を @ か引用で呼んだ投稿にしか返信できません（呼ばれていなければ 403 "
                    "x_reply_not_engaged）。呼ばれた返信なら found_by: mention と書くとこの予告は消えます")


def _recent_texts(account, now, days=SAME_TEXT_DAYS) -> list:
    if not account:
        return []
    from . import accounts, sent as sent_mod
    try:
        rows = sent_mod.records(accounts.state_dir_for(account))
    except Exception:  # noqa: BLE001
        return []
    since = now - datetime.timedelta(days=days)
    out = []
    for row in rows:
        at = jst.parse(row.get("sent_at"))
        if at is not None and at >= since and isinstance(row.get("text"), str) and not row.get("kind"):
            out.append(row["text"])
    return out


def same_text(media, text, *, account=None, now=None) -> dict | None:
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    body = _norm(text)
    if not body:
        return None
    for old in _recent_texts(account, now):
        old_norm = _norm(old)
        if old_norm == body:
            return _finding(media, "same_text", f"直近 {SAME_TEXT_DAYS} 日に同じ本文を公開しています", detail="same")
        if len(body) >= SAME_HEAD_CHARS and old_norm[:SAME_HEAD_CHARS] == body[:SAME_HEAD_CHARS]:
            return _finding(media, "same_text",
                            f"直近 {SAME_TEXT_DAYS} 日の公開と書き出し {SAME_HEAD_CHARS} 字が同じです", detail="head")
    return None


def link_heavy(media, text) -> dict | None:
    urls = URL.findall(str(text or ""))
    if len(urls) > LINKS_MAX:
        return _finding(media, "link_heavy", f"本文に URL が {len(urls)} つあります", used=len(urls),
                        count=LINKS_MAX)
    if len(set(urls)) < len(urls):
        return _finding(media, "link_heavy", "本文に同じ URL が 2 回以上あります", detail="repeated")
    return None


def engagement_bait(media, text) -> dict | None:
    hit = BAIT.search(str(text or ""))
    if hit is None:
        return None
    return _finding(media, "engagement_bait", f"「{hit.group(0)}」の型（反応を頼む言い回し）があります")


def advisories(media, text, *, fm=None, account=None, now=None) -> list:
    """本文 1 本の予告（無ければ空）。順は counterpart → same_text → link_heavy → engagement_bait。"""
    out = []
    for item in (counterpart(media, fm or {}), same_text(media, text, account=account, now=now),
                 link_heavy(media, text), engagement_bait(media, text)):
        if item is not None:
            out.append(item)
    return out


def warning_line(item) -> str:
    """lint の `warning:` の 1 行（§7.2 の advise の文）。"""
    return (f"warning: advise: {item['signal']}: 通します。{item['message']}。"
            f"この媒体では見られやすい形です——根拠: {basis_text(item['trait'])}")
