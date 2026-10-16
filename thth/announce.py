"""SNS の告知を記事に結ぶ（設計 4.0 S4 §9）。

SNS（Threads・X・Bluesky・Mastodon）の口座の原稿の front matter に `announces: <配信 id>` を書くと、
その配信（**同じ project の**はてなの口座の配信の台帳）が `published` になるまで timer が待ち、
本文の `{article_url}` を台帳の `published_url` に置き換えて出す。

- 承認の指紋は置き換えの**前**の本文で取る（`announces` は値があるときだけ末尾に足す任意項目・
  `approval._OPTION_ORDER`）。既存の `approved_sha` は 1 つも変わらない。
- 文字数は `{article_url}` を `https://<blog_id>/entry/0000/00/00/000000` に置き換えて数える
  （`published_url` は公開まで分からない。X は URL を 23 字に数える既存の `count_for` がそのまま効く）。
- 置き換えるのは台帳の `published_url` だけ（台帳の URL は S1 の照合でホストが `blog_id` と同じ）。
"""
from __future__ import annotations

import dataclasses
import json
import os

from . import accounts as accounts_mod
from . import deliveries as deliveries_mod

PLACEHOLDER = "{article_url}"
PREFIX = "announces_unresolved"
WAITING_FOR = "waiting_for"
# lint の符丁（`lint._REASON_TABLE` に載る）。
LINT_CODES = ("invalid_announces", "announces_not_found", "announces_other_lane", "announces_not_allowed",
              "article_mismatch", "article_url_placeholder_twice", "article_url_without_announces")
_MESSAGES = {
    "invalid_announces": "announces は配信 id（dl_ と 16 進 12 桁）です",
    "announces_not_found": "announces の配信が同じ project のはてなの口座の配信の台帳にありません",
    "announces_other_lane": "announces の配信は別の project（別レーン）の記事です。結べません",
    "announces_not_allowed": "announces は SNS の口座の原稿にだけ書けます（はてなの口座には書けません）",
    "article_mismatch": "article が配信の台帳の記事の名と違います",
    "article_url_placeholder_twice": "本文の {article_url} は 1 つまでです",
    "article_url_without_announces": "{article_url} を置くなら announces: <配信 id> を書いてください",
}


class AnnounceError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def announces_of(fm) -> str | None:
    raw = (fm or {}).get("announces")
    if raw is None:
        return None
    return str(raw).strip() or None


def placeholder_url(blog_id) -> str:
    """文字数を数えるときの仮の URL（§9.2）。"""
    return f"https://{blog_id or 'example.hatenablog.com'}/entry/0000/00/00/000000"


def fill(text: str, url: str) -> str:
    """`{article_url}` を 1 つだけ置き換える（無ければそのまま）。"""
    return text.replace(PLACEHOLDER, url, 1) if isinstance(text, str) else text


def _raw_accounts():
    """`(口座名, 生の台帳)` を名前の順に（読めない台帳は飛ばす・`principals.accounts_of` と同じ考え）。"""
    d = accounts_mod.accounts_dir()
    for name in accounts_mod.list_account_names():
        if not accounts_mod.name_is_safe(name):
            continue
        try:
            with open(os.path.join(d, f"{name}.json"), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            yield name, data


def find_delivery(account_cfg: dict, delivery: str):
    """同じ project のはてなの口座から配信を探す。`(口座名, 台帳)`。

    無い → `announces_not_found`・別の project のはてなの口座にある → `announces_other_lane`・
    台帳が読めない → `target_unreadable`（`AnnounceError`）。
    """
    if not deliveries_mod.is_delivery_id(delivery):
        raise AnnounceError("invalid_announces")
    project = (account_cfg or {}).get("project")
    elsewhere = False
    unreadable = False
    for name, data in _raw_accounts():
        if data.get("media") != "hatenablog":
            continue
        try:
            record = deliveries_mod.read(name, delivery)
        except deliveries_mod.DeliveryError as exc:
            if str(exc) != "delivery_missing":
                unreadable = True
            continue
        if data.get("project") == project:
            return name, record
        elsewhere = True
    if elsewhere:
        raise AnnounceError("announces_other_lane")
    if unreadable:
        raise AnnounceError("target_unreadable")
    raise AnnounceError("announces_not_found")


def lint_errors(fm: dict, section, account_cfg: dict | None, *, hatena: bool) -> list:
    """告知の lint（§9.2）。1 行は `<符丁>: <文>`。"""
    out = []
    announces = announces_of(fm)
    count = section.count(PLACEHOLDER) if isinstance(section, str) else 0
    if hatena:
        if announces is not None:
            out.append(f"announces_not_allowed: {_MESSAGES['announces_not_allowed']}")
        return out
    if count > 1:
        out.append(f"article_url_placeholder_twice: {_MESSAGES['article_url_placeholder_twice']}")
    if announces is None:
        if count:
            out.append(f"article_url_without_announces: {_MESSAGES['article_url_without_announces']}")
        if (fm or {}).get("article") is not None and str(fm.get("article")).strip():
            out.append(f"article_url_without_announces: article は announces と一緒に書いてください")
        return out
    try:
        _name, record = find_delivery(account_cfg, announces)
    except AnnounceError as exc:
        code = exc.code if exc.code in LINT_CODES else "announces_not_found"
        return out + [f"{code}: {_MESSAGES[code]}（{announces!r}）"]
    article = (fm or {}).get("article")
    if article is not None and str(article).strip() and str(article).strip() != record.get("article"):
        out.append(f"article_mismatch: {_MESSAGES['article_mismatch']}（{record.get('article')!r}）")
    return out


def counted_text(section: str, fm: dict, account_cfg: dict | None, *, url: str | None = None) -> str:
    """文字数を数えるための本文（`{article_url}` を実際の URL か仮の URL に置き換える）。"""
    if not isinstance(section, str) or PLACEHOLDER not in section:
        return section
    if url is None:
        blog_id = None
        announces = announces_of(fm)
        if announces is not None:
            try:
                _name, record = find_delivery(account_cfg, announces)
                blog_id = record.get("blog_id")
            except AnnounceError:
                blog_id = None
        url = placeholder_url(blog_id)
    return fill(section, url)


@dataclasses.dataclass
class AnnounceResolution:
    delivery: str | None
    url: str | None
    article: str | None
    reason: str | None   # None なら解決・それ以外は待つ／断る理由（静的な符丁）


def unresolved(detail: str) -> str:
    return f"{PREFIX}: {detail}"


def waiting_target(reason) -> str | None:
    """理由が「記事の公開を待っている」なら配信 id。違えば None。"""
    prefix = unresolved(WAITING_FOR) + " "
    if isinstance(reason, str) and reason.startswith(prefix):
        return reason[len(prefix):]
    return None


def resolve(fm: dict, account_cfg: dict) -> AnnounceResolution | None:
    """`announces` を解決する（§9.4 の 1）。持たない原稿は None。

    - `published` で `published_url` あり → 解決
    - `wanted`・`inflight`・`unknown` → `announces_unresolved: waiting_for <配信 id>`（待つ・held に数えない）
    - `conflict`・`deleted`・`no_follow` → `announces_unresolved: target_<state>`（held）
    - 無い → `target_missing`・別レーン → `target_other_lane`・読めない → `target_unreadable`（held）
    """
    announces = announces_of(fm)
    if announces is None:
        return None
    try:
        _name, record = find_delivery(account_cfg, announces)
    except AnnounceError as exc:
        detail = {"announces_not_found": "target_missing", "announces_other_lane": "target_other_lane",
                  "invalid_announces": "target_invalid"}.get(exc.code, "target_unreadable")
        return AnnounceResolution(announces, None, None, unresolved(detail))
    except (accounts_mod.AccountError, OSError, ValueError):
        return AnnounceResolution(announces, None, None, unresolved("target_unreadable"))
    state = record.get("state")
    url = record.get("published_url")
    article = record.get("article")
    if state == "published" and isinstance(url, str) and url.startswith(("https://", "http://")):
        return AnnounceResolution(announces, url, article, None)
    if state in ("wanted", "inflight", "unknown", "published"):
        return AnnounceResolution(announces, None, article, unresolved(f"{WAITING_FOR} {announces}"))
    return AnnounceResolution(announces, None, article, unresolved(f"target_{state}"))
