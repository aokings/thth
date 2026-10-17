"""はてなスターを読む口（設計 4.0 S6 §3.2・§9 の 2）。**認証なし・読むだけ・1 回だけ。**

公式の頁（スター取得 API）の入口は `http://s.hatena.com/entry.json?uri=` で、THTH は平文の http を使わない
（`httpsafe`）。同じ会社のカウント API が `https://s.hatena.ne.jp/blog.json` で https に答えるので、
`https://s.hatena.ne.jp/entry.json` も通ると**推測**して置く（実機の 44 で確かめる。通らなければ
`SUPPORTED = False` にして `not_supported` を言う・平文には倒さない）。

- `BASE` が `https://` でなければ import の時点で `ValueError`（試験は import のあとに 127.0.0.1 へ差し替える）。
- `uri` は複数をまとめて 1 回（呼び手が 50 件ずつに分ける）。
- 返りの `stars[]` の `quote`（引用の文）は**落とす**（第三者の引用で、数には要らない・§3.2）。
- `stars[]` に数（整数）が混ざるのは、並べきれないスターを畳んだもの（名前が無い）。`unnamed` として数だけ返す。
"""
from __future__ import annotations

import urllib.parse

from . import hatena_common

BASE = "https://s.hatena.ne.jp"
if not BASE.startswith("https://"):
    raise ValueError("hatenastar_base_not_https")
# https の入口で読めることを実機で確かめたか（44）。False の間は読みに行かず `not_supported`。
SUPPORTED = True
BATCH = 50
READ_TIMEOUT = 30


def entry_url(urls) -> str:
    return f"{BASE}/entry.json?" + urllib.parse.urlencode([("uri", u) for u in urls])


def _star_rows(stars, color) -> list:
    out = []
    for star in stars if isinstance(stars, list) else []:
        if isinstance(star, int) and not isinstance(star, bool):
            if star > 0:
                out.append({"name": None, "count": star, "color": color})
            continue
        if not isinstance(star, dict) or not isinstance(star.get("name"), str):
            continue
        count = star.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            count = 1
        out.append({"name": star["name"], "count": count, "color": color})
    return out


def entry_stars(urls, *, contact=None, timeout=READ_TIMEOUT) -> dict:
    """`{url: [{"name", "count", "color"}]}`（`quote` は落とす）。答えに無い URL は空（スターが無い）。

    ただし答えに `entries` があるのに、要求した URI が 1 つも一致しなければ `response_invalid`（URI の形が
    違う答えを「スター 0」と読まない・監査 中-6）。
    """
    urls = list(urls)
    if not urls or len(urls) > BATCH:
        raise ValueError("star_batch_invalid")
    data = hatena_common.public_json(entry_url(urls), contact=contact, timeout=timeout)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise hatena_common.PublicHTTPError("response_invalid")
    entries = data.get("entries", [])
    if not isinstance(entries, list):
        raise hatena_common.PublicHTTPError("response_invalid")
    out = {u: [] for u in urls}
    if entries and not any(isinstance(e, dict) and e.get("uri") in out for e in entries):
        raise hatena_common.PublicHTTPError("response_invalid")
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("uri") not in out:
            continue
        rows = _star_rows(entry.get("stars"), "yellow")
        for colored in entry.get("colored_stars") or []:
            if isinstance(colored, dict) and isinstance(colored.get("color"), str):
                rows += _star_rows(colored.get("stars"), colored["color"])
        out[entry["uri"]] = rows
    return out
