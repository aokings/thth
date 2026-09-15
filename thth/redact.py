"""秘密の伏字化（発注 §0-3・設計 §3.6）。

ログ・runs・例外文・ping 本文はすべてここを通す。伏字にするのは:
  - `access_token=...` / `"access_token": "..."` の値
  - `client_secret=...` / `"client_secret": "..."` の値
  - `code=...` / `"code": "..."`（`thth auth` の認可コード。T2a で追加・masaru の指示）
  - `Authorization: ...`（ヘッダ値。行の残り全部）
  - `hc-ping.com/` 以降のパス（check の UUID が漏れないように）
  - **登録された秘密の値そのもの**（セキュリティ監査 2026-09-16・P1-1）

watchtower/bin/_redact.py・watchtower/watchtower/fetch.py の redact() を写したが、
THTH の伏字対象（access_token=・client_secret=・Authorization:・hc-ping.com/）は
watchtower のクエリパラメータ全般の伏字と違うので、正規表現は THTH 用に書き直した。

**綴りではなく値を消す登録簿**（P1-1）: 上の正規表現は`access_token=` のような
**綴り**が要る。サーバが `rejected credential <値>` のように**キー名なしで値を
反射**すると、綴りが無いのでどの正規表現も当たらず、値がそのまま
`thth/adapters/threads.py::_read`・`thth/oauth.py::_error_message` の例外文を
抜けて `thth topics ... --search --json` の標準出力・`thth doctor` の出力に残る。
`register_secret()` で**プロセス内**に値を控えておくと、`redact()` は正規表現の
あとにその値そのものを（長い順に）`***` へ置換する。**登録簿はファイルにも
ログにも出さない**——メモリの中だけ。Bluesky の `scrub(text, *secrets)`
（`thth/adapters/bluesky.py`）と同じ発想だが、**呼び出しのたびに値を渡し直さず
に済むよう、プロセス内の 1 か所に登録する**形にした——秘密を持つ側
（ThreadsAdapter・oauth の各関数）が値を知った直後に 1 回登録すれば、以後は
`redact()` を通すだけでその値が消える。
"""
from __future__ import annotations

import re

# **プロセス内だけの登録簿。** ファイルにもログにも出さない。
_registered_secrets: list[str] = []
# **8 文字未満は登録しない**——短い値を登録すると、その文字列を含む普通の語
# （例えば "code" や日本語の助詞の並び）まで巻き込んで消してしまう。トークン・
# app secret・code のどれも実際にはもっと長い。
_MIN_SECRET_LENGTH = 8


def register_secret(value) -> None:
    """`value` を登録簿に控える。次回以降の `redact()` がその値を伏字にする。

    `None`・空文字・8 文字未満は**登録しない**（普通の語を巻き込まないため）。
    値そのものだけを持つ——キー名・呼び出し元は残さない。
    """
    if not isinstance(value, str):
        return
    if len(value) < _MIN_SECRET_LENGTH:
        return
    if value not in _registered_secrets:
        _registered_secrets.append(value)


def _clear_registered() -> None:
    """登録簿を空にする（**テスト専用**）。本番の経路からは呼ばない。"""
    _registered_secrets.clear()

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
    # **登録された値そのものを、綴りに関わらず消す**（P1-1）。長い値から先に
    # 置換する——短い値が長い値の部分文字列だと、短い方を先に消したときに
    # 長い方の一部だけが残って中途半端な伏字になりうる。
    for secret in sorted(_registered_secrets, key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, "***")
    return text
