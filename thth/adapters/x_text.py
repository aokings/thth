"""X の本文の長さ（twitter-text v3 の weighted length・3.20.0）。

X のアプリが出す「284/280」と同じ数え方を、依存を足さずに写したもの。出典は
twitter/twitter-text@30e2430（`config/v3.json`・`js/src/parseTweet.js`・
`js/src/extractUrlsWithIndices.js`・`js/src/regexp/*`）と、その中で絵文字を
拾っている twitter/twemoji-parser@f8312f3（v14.0.0・`src/lib/regex.js`）。
TLD の一覧と絵文字の正規表現は `_x_text_data.py`（`tools/generate_x_text.py` が
固定した版から作る）。試験は conformance の `validate.yml` から写した
`tests/fixtures/twitter_text_v3_weighted.json`。

数え方（v3）:
- NFC に正規化してから、**UTF-16 の単位で**左から見る（JavaScript と同じ）。
- URL（twitter-text の抜き出しの規則に合うもの）は長さによらず 23。
- 絵文字（twemoji-parser が 1 つと見る並び。ZWJ・異体字セレクタ・肌色を含む）は 2。
- それ以外の 1 字は、U+0000–U+10FF・U+2000–U+200D・U+2010–U+201F・
  U+2032–U+2037 が 1、ほかは 2（CJK・全角は 2）。
- 上限は 280。

**合わせきれていない所**（正直に）: X のサーバが今どの版の TLD 一覧・絵文字表で
数えているかは公開されていない。ここは上の固定した版（TLD は 2021-10、絵文字は
twemoji-parser 14.0.0＝Unicode 14 まで）に合わせている。それより新しい TLD の
裸のドメインや、新しい絵文字の ZWJ の並びは、X と 1〜数字ずれることがある。
大文字小文字を無視した照合は Python の `re.IGNORECASE` で、JavaScript の `i` と
特殊な字（ケルビン記号など）で違いうる。
"""
from __future__ import annotations

import array
import re
import unicodedata

from . import _x_text_data as data

# config/v3.json（twitter-text@30e2430）
MAX_WEIGHTED_LENGTH = 280
SCALE = 100
DEFAULT_WEIGHT = 200
TRANSFORMED_URL_LENGTH = 23
RANGES = ((0x0000, 0x10FF, 100), (0x2000, 0x200D, 100),
          (0x2010, 0x201F, 100), (0x2032, 0x2037, 100))

# extractUrlsWithIndices.js
_DEFAULT_PROTOCOL = 'https://'
_MAX_URL_LENGTH = 4096
_MAX_TCO_SLUG_LENGTH = 40
_MAX_DOMAIN_LABEL_LENGTH = 63

# js/src/regexp/*.js を、regexSupplant と同じく**文字列のまま**差し込んで組む。
_LATIN_ACCENT = (r'\xC0-\xD6\xD8-\xF6\xF8-\xFFĀ-ɏɓɔɖɗ'
                 r'əɛɣɨɯɲʉʋʻ̀-ͯ'
                 r'Ḁ-ỿ')
_CYRILLIC = r'Ѐ-ӿ'
_SPACES = r'\x09-\x0D\x20\x85\xA0 ᠎ -     　'
_INVALID = r'￾﻿￿'
_DIRECTIONAL = r'‪-‮؜‎‏⁦⁧⁨⁩'
_PUNCT = r"\!'#%&'\(\)*\+,\\\-\.\/:;<=>\?@\[\]\^_{|}~\$"
_DOMAIN_CHARS = '[^' + _PUNCT + _SPACES + _INVALID + _DIRECTIONAL + ']'
_SUBDOMAIN = f'(?:(?:{_DOMAIN_CHARS}(?:[_-]|{_DOMAIN_CHARS})*)?{_DOMAIN_CHARS}\\.)'
_DOMAIN_NAME = f'(?:(?:{_DOMAIN_CHARS}(?:-|{_DOMAIN_CHARS})*)?{_DOMAIN_CHARS}\\.)'


def _utf16(text: str) -> str:
    """`text` を UTF-16 の単位 1 つ＝1 字の str にする（サロゲートは別々の字）。"""
    units = array.array('H')
    units.frombytes(text.encode('utf-16-le', 'surrogatepass'))
    return ''.join(map(chr, units))


def _from_utf16(units: str) -> str:
    return units.encode('utf-16-le', 'surrogatepass').decode('utf-16-le', 'surrogatepass')


def _tld(names) -> str:
    return ('(?:(?:' + '|'.join(re.escape(_utf16(name)) for name in names)
            + ')(?=[^0-9a-zA-Z@+-]|$))')


_GTLD = _tld(data.GTLD)
_CCTLD = _tld(data.CCTLD)
_PUNYCODE = r'(?:xn--[\-0-9a-z]+)'
_DOMAIN = f'(?:{_SUBDOMAIN}*{_DOMAIN_NAME}(?:{_GTLD}|{_CCTLD}|{_PUNYCODE}))'
_PRECEDING = f'(?:[^A-Za-z0-9@＠$#＃{_INVALID}]|[{_DIRECTIONAL}]|^)'
_PATH_CHARS = (f"[a-z{_CYRILLIC}0-9!\\*';:=\\+,\\.\\$\\/%#\\[\\]\\-\\u2013_~@\\|&"
               f"{_LATIN_ACCENT}]")
_BALANCED = (f'\\((?:{_PATH_CHARS}+|(?:{_PATH_CHARS}*\\({_PATH_CHARS}+\\)'
             f'{_PATH_CHARS}*))\\)')
_PATH_ENDING = f'[\\+\\-a-z{_CYRILLIC}0-9=_#\\/{_LATIN_ACCENT}]|(?:{_BALANCED})'
# 括弧の付け方まで原文どおり（`_PATH_ENDING` の `|` は外の (?: …) に効く）。
_PATH = (f'(?:(?:{_PATH_CHARS}*(?:{_BALANCED}{_PATH_CHARS}*)*{_PATH_ENDING})'
         f'|(?:@{_PATH_CHARS}+/))')
_QUERY = r"[a-z0-9!?\*'@\(\);:&=\+\$\/%#\[\]\-_\.,~|]"
_QUERY_END = r'[a-z0-9\-_&=#\/]'

_EXTRACT_URL = re.compile(
    f'(({_PRECEDING})((https?:\\/\\/)?({_DOMAIN})(?::([0-9]+))?'
    f'(\\/{_PATH}*)?(\\?{_QUERY}*{_QUERY_END})?))', re.IGNORECASE)
_ASCII_DOMAIN = re.compile(
    f'(?:(?:[\\-a-z0-9{_LATIN_ACCENT}]+)\\.)+(?:{_GTLD}|{_CCTLD}|{_PUNYCODE})',
    re.IGNORECASE)
_TCO = re.compile(
    f'^https?:\\/\\/t\\.co\\/([a-z0-9]+)(?:\\?{_QUERY}*{_QUERY_END})?', re.IGNORECASE)
_INVALID_NO_PROTOCOL_PRECEDING = re.compile(r'[-_./]\Z')
_LABEL_SEPARATORS = re.compile('[\x2E。．｡]')
_EMOJI = re.compile(''.join(data.EMOJI_PATTERN))


def _punycode_length(label16: str) -> int:
    """punycode.js の `toASCII(label)` の長さ（lib/idna.js が 1〜63 を求める）。"""
    parts = []
    for part in _LABEL_SEPARATORS.split(_from_utf16(label16)):
        if any(ord(ch) > 0x7E for ch in part):
            parts.append('xn--' + part.encode('punycode').decode('ascii'))
        else:
            parts.append(part)
    return len('.'.join(parts))


def _valid_url(url: str, protocol: str, domain: str) -> bool:
    """extractUrlsWithIndices.js の `isValidUrl`（＋ lib/idna.js の `toAscii`）。"""
    if domain[:4] == 'xn--' and not _ASCII_DOMAIN.search(domain):
        return False
    for label in domain.split('.'):
        if not 1 <= _punycode_length(label) <= _MAX_DOMAIN_LABEL_LENGTH:
            return False
    return len(protocol) + len(url) <= _MAX_URL_LENGTH


def extract_urls(units: str) -> list:
    """`[(url, start, end), …]`（UTF-16 の位置）。extractUrlsWithIndices.js の写し
    （`extractUrlsWithoutProtocol: true`＝parseTweet の既定）。"""
    if '.' not in units:
        return []
    urls = []
    for match in _EXTRACT_URL.finditer(units):
        before = match.group(2) or ''
        url = match.group(3)
        protocol = match.group(4) or ''
        domain = match.group(5)
        path = match.group(7)
        end = match.end()
        start = end - len(url)
        if not _valid_url(url, protocol or _DEFAULT_PROTOCOL, domain):
            continue
        if not protocol:
            if _INVALID_NO_PROTOCOL_PRECEDING.search(before):
                continue
            last = None
            for ascii_match in _ASCII_DOMAIN.finditer(domain):
                last = [ascii_match.group(0), start + ascii_match.start(),
                        start + ascii_match.end()]
                urls.append(last)
            if last is None:
                continue
            if path:
                last[0] = url.replace(domain, last[0], 1)
                last[2] = end
        else:
            tco = _TCO.match(url)
            if tco:
                if tco.group(1) and len(tco.group(1)) > _MAX_TCO_SLUG_LENGTH:
                    continue
                url = tco.group(0)
                end = start + len(url)
            urls.append([url, start, end])
    return [tuple(row) for row in urls]


def _weight(unit: int) -> int:
    for start, end, weight in RANGES:
        if start <= unit <= end:
            return weight
    return DEFAULT_WEIGHT


def weighted_length(text: str) -> int:
    """X の重み付きの長さ（parseTweet.js の `weightedLength`）。上限は 280。"""
    units = _utf16(unicodedata.normalize('NFC', text or ''))
    urls = {start: url for url, start, _end in extract_urls(units)}
    emoji = {m.start(): m.group(0) for m in _EMOJI.finditer(units)}
    total = 0
    index = 0
    size = len(units)
    while index < size:
        if index in urls:
            total += TRANSFORMED_URL_LENGTH * SCALE
            index += len(urls[index])
            continue
        if index in emoji:
            total += DEFAULT_WEIGHT
            index += len(emoji[index])
            continue
        if (index < size - 1 and 0xD800 <= ord(units[index]) <= 0xDBFF
                and 0xDC00 <= ord(units[index + 1]) <= 0xDFFF):
            index += 1
        total += _weight(ord(units[index]))
        index += 1
    return total // SCALE
