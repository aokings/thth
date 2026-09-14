"""秘密の伏字化（発注 §0-3・設計 §3.6）。

ログ・runs・例外文・ping 本文はすべてここを通す。伏字にするのは:
  - `access_token=...` / `"access_token": "..."` の値
  - `client_secret=...` / `"client_secret": "..."` の値
  - `code=...` / `"code": "..."`（`thth auth` の認可コード。T2a で追加・masaru の指示）
  - `Authorization: ...`（ヘッダ値。行の残り全部）
  - `hc-ping.com/` 以降のパス（check の UUID が漏れないように）

watchtower/bin/_redact.py・watchtower/watchtower/fetch.py の redact() を写したが、
THTH の伏字対象（access_token=・client_secret=・Authorization:・hc-ping.com/）は
watchtower のクエリパラメータ全般の伏字と違うので、正規表現は THTH 用に書き直した。
"""
from __future__ import annotations

import re

# key=value 形式（クエリ文字列・URL）と "key": "value" 形式（JSON）の両方を拾う。
# 値は空白・&・"・'・,・} のいずれかで終わるとみなす。
_KV_PATTERNS = [
    re.compile(r'(access_token"?\s*[:=]\s*"?)([^\s&"\',}]+)', re.IGNORECASE),
    re.compile(r'(client_secret"?\s*[:=]\s*"?)([^\s&"\',}]+)', re.IGNORECASE),
    # \b で単語境界を要求する（"unicode" 等の途中に "code" が現れても拾わない）。
    re.compile(r'(\bcode"?\s*[:=]\s*"?)([^\s&"\',}]+)', re.IGNORECASE),
    # **v2 で増えた媒体の秘密**（セキュリティ監査 2026-09-14・P3-1）。
    # Bluesky は `access_token` を使わない——`app_password`・`accessJwt`・
    # `refreshJwt` がその位置にいる。`redact()` を通しても**綴りを知らないので
    # 素通り**していた（`thth doctor --json` の生の応答・`runs` の ndjson）。
    # 値そのものを消す `bluesky.scrub()` は境界の中だけなので、ここにも足す。
    re.compile(r'(app_password"?\s*[:=]\s*"?)([^\s&"\',}]+)', re.IGNORECASE),
    re.compile(r'(accessJwt"?\s*[:=]\s*"?)([^\s&"\',}]+)', re.IGNORECASE),
    re.compile(r'(refreshJwt"?\s*[:=]\s*"?)([^\s&"\',}]+)', re.IGNORECASE),
]
# Authorization ヘッダは値に空白を含む（"Bearer xxx"）ので行末までを伏字にする。
_AUTH_RE = re.compile(r'(Authorization:\s*)(.+)', re.IGNORECASE)
# healthchecks.io の ping URL は check の UUID がそのまま識別子になるので、
# ホスト名の直後から伏字にする。
_HC_PING_RE = re.compile(r'(hc-ping\.com/)([^\s"\'<>]*)')


def redact(text: str | None) -> str | None:
    """`text` 中の秘密らしき値を伏字にして返す。None・空文字はそのまま返す。"""
    if not text:
        return text
    for pat in _KV_PATTERNS:
        text = pat.sub(lambda m: m.group(1) + "***", text)
    text = _AUTH_RE.sub(lambda m: m.group(1) + "***", text)
    text = _HC_PING_RE.sub(lambda m: m.group(1) + "***", text)
    return text
