"""open に出すときに**他人の情報を落とす**（設計 3.4.0 §3・C3／C4 の規律）。

他の持ち主が読むのは、ここで作った写し（`open_copy`・返信の `open_text`・観測の
`open_columns`）だけ。原本は置いた持ち主と管理者にしか見えない。

落とすもの:

  1. 書いた持ち主の返信の台帳にある**他人の返信の本文**（8 字以上の一致）
  2. 同じ台帳の**他人の username**（`@` の有無・大小を問わない）
  3. 本文の `@名前`（書いた持ち主の handle 以外は全部）。ただし書いた本人の文に
     `@名前` があれば、写しを作る前に `third_party_handle` で断る（設計 §9-7）
  4. `author_key` の形（16 進 16 桁）
  5. SNS の投稿の URL（書いた持ち主の投稿の permalink 以外）

台帳が読めなければ写しを作らない（`redaction_unavailable`）——落とせたかどうか
分からないものを他の持ち主に見せない。自分の投稿は先頭 60 字と permalink まで
（`plaza_observe.open_column`）。
"""
from __future__ import annotations

import re

from . import accounts

OTHER_TEXT = "［他の人の投稿を伏せました］"
OTHER_NAME = "［他の人］"
OTHER_KEY = "［仮名を伏せました］"
OTHER_URL = "［他の人の投稿の URL を伏せました］"
# 他人の本文として当てる断片の最短の長さ（短い語はありふれていて誤って消す）。
FRAGMENT_MIN = 8
USERNAME_MIN = 2
SNS_HOSTS = ("threads.net", "threads.com", "bsky.app", "x.com", "twitter.com",
             "mobile.twitter.com", "www.threads.net", "www.threads.com")

_MENTION = re.compile(r"(?<![\w@.])@([A-Za-z0-9_][A-Za-z0-9_.\-]*[A-Za-z0-9_]|[A-Za-z0-9_])"
                      r"(?:@[A-Za-z0-9][A-Za-z0-9.\-]*[A-Za-z0-9])?")
_AUTHOR_KEY = re.compile(r"(?<![0-9A-Fa-f])[0-9a-f]{16}(?![0-9A-Fa-f])")
_URL = re.compile(r"https?://[^\s）)」』>\]]+")
_SPLIT = re.compile(r"[\n。．.!?！？]+")


def _handle(value):
    if not isinstance(value, str):
        return None
    value = value.strip().lstrip("@").strip().lower()
    return value or None


class Info:
    """書いた持ち主の台帳から集めた「他人の情報」と、書いた持ち主自身の handle。"""

    def __init__(self, usernames=(), texts=(), own_handles=(), own_permalinks=()):
        self.usernames = {name for name in (_handle(u) for u in usernames)
                          if name and len(name) >= USERNAME_MIN}
        self.own_handles = {name for name in (_handle(h) for h in own_handles) if name}
        self.usernames -= self.own_handles
        fragments = set()
        for text in texts:
            if not isinstance(text, str):
                continue
            whole = " ".join(text.split())
            if len(whole) >= FRAGMENT_MIN:
                fragments.add(whole)
            for part in _SPLIT.split(text):
                part = " ".join(part.split())
                if len(part) >= FRAGMENT_MIN:
                    fragments.add(part)
        # 長いものから（短い断片で先に削ると長い一致が残らない）。
        self.fragments = sorted(fragments, key=len, reverse=True)
        self.own_permalinks = {p for p in own_permalinks if isinstance(p, str)}


def collect(names):
    """account 群（書いた持ち主の全 account）の台帳から `Info` を作る。

    返信の台帳（`replies.load`）の `own` が True でない行の username と本文が
    「他人の情報」。読めなければ `PlazaError("redaction_unavailable")`。
    """
    from . import plaza, replies
    usernames, texts, handles = [], [], []
    try:
        for name in sorted(names):
            cfg = accounts.load_account(name)
            handles.append(cfg.get("handle"))
            loaded = replies.load(name, allowed_names=tuple(names))
            for row in loaded["replies"]:
                if row.get("own") is True:
                    continue
                usernames.append(row.get("username"))
                texts.append(row.get("text"))
            if loaded.get("broken"):
                # 壊れた台帳の中身は読めていない＝落とせたか分からない。
                raise plaza.PlazaError("redaction_unavailable")
    except plaza.PlazaError:
        raise
    except (accounts.AccountError, OSError, ValueError, TypeError, KeyError):
        raise plaza.PlazaError("redaction_unavailable") from None
    return Info(usernames, texts, handles)


def third_party_handles(text, info):
    """文の中の `@名前`（書いた持ち主の handle 以外）の数（設計 §9-7）。"""
    if not isinstance(text, str) or not text:
        return 0
    return sum(1 for match in _MENTION.finditer(text)
               if match.group(1).lower() not in info.own_handles
               and match.group(0)[1:].lower() not in info.own_handles)


def refuse_handles(values, info):
    """open に出す文に他の人の `@名前` があれば断る（`third_party_handle`）。

    落とす処理（`sanitize`）は台帳にある名前と `@` の形を伏せるが、`@名前` は書いた
    側が消すほうが確か（伏せた痕だけが残ると、何が書いてあったか読み手が推し量る）。
    open に出すときだけ断り、project の範囲なら置ける。
    """
    from . import plaza
    if any(third_party_handles(value, info) for value in values):
        raise plaza.PlazaError("third_party_handle")


def sanitize(text, info):
    """他人の情報を落とした写しと、落とした数。"""
    if not isinstance(text, str) or not text:
        return text, 0
    masked = 0
    for fragment in info.fragments:
        if fragment in text:
            masked += text.count(fragment)
            text = text.replace(fragment, OTHER_TEXT)
    for name in sorted(info.usernames, key=len, reverse=True):
        pattern = re.compile(r"(?<![\w.])@?" + re.escape(name) + r"(?![\w])", re.IGNORECASE)
        text, n = pattern.subn(OTHER_NAME, text)
        masked += n

    def mention(match):
        nonlocal masked
        if match.group(1).lower() in info.own_handles or match.group(0)[1:].lower() in info.own_handles:
            return match.group(0)
        masked += 1
        return "@" + OTHER_NAME

    text = _MENTION.sub(mention, text)
    text, n = _AUTHOR_KEY.subn(OTHER_KEY, text)
    masked += n

    def url(match):
        nonlocal masked
        value = match.group(0)
        host = value.split("://", 1)[1].split("/", 1)[0].lower()
        path = value.split("://", 1)[1][len(host):]
        if value in info.own_permalinks:
            return value
        if host in SNS_HOSTS or host.endswith((".threads.net", ".bsky.app")) or "/@" in path:
            masked += 1
            return OTHER_URL
        return value

    text = _URL.sub(url, text)
    return text, masked


def _owner_names(record_or_account, project, trusted_accounts):
    from . import plaza
    return set(plaza._owner_accounts(record_or_account, project, trusted_accounts))


def _info_for(account, project, trusted_accounts, permalinks=()):
    names = _owner_names(account, project, trusted_accounts)
    if not names:
        from . import plaza
        raise plaza.PlazaError("redaction_unavailable")
    info = collect(names)
    info.own_permalinks |= set(permalinks)
    return info


def _permalinks(record):
    found = set()
    for observation in record.get("observations") or []:
        for entry in observation.get("columns") or []:
            for row in entry.get("posts") or []:
                if row.get("permalink"):
                    found.add(row["permalink"])
    return found


def attach_open_copy(record, *, trusted_accounts=None):
    """open に出す 1 件の写しを作る（題・本文・仮説・変えたこと・判定の理由・観測・
    書いた持ち主の返信）。他の持ち主が書いた返信は、書いた時点の写しをそのまま使う。"""
    from . import plaza_observe
    info = _info_for(record["account"], record.get("project"), trusted_accounts,
                     _permalinks(record))
    own_replies = [row for row in record.get("replies") or []
                   if row.get("project") == record.get("project") and (
                       record.get("project") is not None or row.get("account") == record.get("account"))]
    refuse_handles([record["title"], record["body"], record.get("hypothesis"), record.get("change"),
                    record.get("scope_note"), record.get("how"),
                    (record.get("verdict") or {}).get("reason")]
                   + [row["text"] for row in own_replies], info)
    total = 0

    def clean(value):
        nonlocal total
        if value is None:
            return None
        value, n = sanitize(value, info)
        total += n
        return value

    copy = {"title": clean(record["title"]), "body": clean(record["body"]),
            "hypothesis": clean(record.get("hypothesis")), "change": clean(record.get("change")),
            "scope_note": clean(record.get("scope_note")), "how": clean(record.get("how")),
            "verdict_reason": clean((record.get("verdict") or {}).get("reason"))}
    for observation in record.get("observations") or []:
        observation["open_columns"] = [plaza_observe.open_column(entry, lambda v: sanitize(v, info))
                                       for entry in observation.get("columns") or []]
    for row in own_replies:
        row["open_text"] = clean(row["text"])
    numbers = record.get("tool_numbers")
    if numbers:
        # 道具の数字（3.8.0 §C）: 集計の数字と期間はそのまま・言えないことの文は落とした写し。
        # account 名（target）と出し直しの命令は他の持ち主に見せない（how は写しで見える）。
        copy["tool_numbers"] = {"source": numbers.get("source"), "medium": numbers.get("medium"),
                                "at": numbers.get("at"), "period": numbers.get("period"),
                                "numbers": numbers.get("numbers"),
                                "cannot_say": [clean(line) for line in numbers.get("cannot_say") or []],
                                "observed": numbers.get("observed")}
    copy["masked"] = total
    record["open_copy"] = copy
    return record


def open_text(text, account, project, *, trusted_accounts=None):
    """open の 1 件への返信の写し（書いた持ち主の台帳で落とす）。"""
    info = _info_for(account, project, trusted_accounts)
    refuse_handles([text], info)
    return sanitize(text, info)[0]
