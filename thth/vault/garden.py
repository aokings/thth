"""`thth garden`: 店の写しから静的な頁を作り、出す前に検査し、monban の庭に置く（段 1・**仮**・masaru の答えを待たずに
推しで作った・朝に覆せる）。

正本: monban の設計案 §14・§15.3・§15.6（庭の口と制限）、THTH の docs/原則_庭の来訪者と広告_2026-11-12.md（7 つ）。

作る頁（`build`）:

- `index.html`（店の頁 1 枚）・`items.html`（商品の一覧）・`style.css`。UTF-8・script なし・form なし・`http-equiv` なし・
  外の資源なし（画像も外の URL を読まない。BASE の画像を手元に取り込むのは段 2）。
- 載せるのは**公開で買える商品だけ**（`thth.shop.base.buyable`: 公開・販売中・在庫あり）。非公開の商品は写しに id しか無く、
  頁には何も出さない。商品の頁の URL は BASE の公式で確かめられていないので、買う道は店の URL（`--shop-url`）へのリンク。
- 無料版の「THTH の案内」の静的な枠を各頁に 1 つ（原則の 7。段 1 には有料版が無いので、いつも入れる）。枠の印は
  `data-thth-guide="free"`。

出す前の検査（`check_dir`・publish の前に必ず通す）:

- monban の Edge の制限（edge/src/vault.js の写し）: 種類・名前・2000 個・1 個 25 MB・全部で 100 MB・HTML/SVG/XML は
  1 枚 1 MB・UTF-8 だけ（BOM の FE FF・FF FE と NUL を断る）・制御文字（タブ・改行・CR を除く）と UTF-8 以外の
  `encoding=`・`charset=` の宣言を含めない（監査 段 1 の 10）・`http-equiv` を含めない。monban の道具が黙って飛ばす
  点で始まる名前と symlink は、THTH は飛ばさずに断る（出したつもりの物が出ないのを防ぐ）。
- THTH の柵（タグと属性は標準の html.parser で読む・文字参照を戻し、URL はブラウザの読み方に寄せてタブと改行を捨て
  バックスラッシュを `/` と読む）: script・form・iframe など・`on…=` の差し込み・`javascript:`、外の資源（`<a>` のリンクを除く、
  URL を持つ属性の外の先・CSS の外の `url(…)` と `@import`）、閉じないタグ・DTD の entity・inline の style と `<style>`
  （monban の CSP は `style-src 'self'` なので効かない）、案内の枠が 1 つでない HTML。ブラウザと html.parser の版で読みの
  割れる書き方（`<!-->`・`--!>` などの注・HTML の `<![`・`<title>` の中のタグに見える字・xmp などの古いタグ）と、XML・SVG の
  `<?xml-stylesheet` は、html.parser に渡す前に字面で断る（監査 段 1 の 6）。読みに行かない印（`<link rel="canonical">` など・
  `cite`・`itemtype`・CSS の `content` の文字列）は外の先でも通す（監査 段 1 の 7）。script と form も monban の CSP で
  止まるが、止まった頁は壊れて見えるので、出す前に前向きに断る。案内の枠の検査はうっかりを見つけるためのもので、
  原則の 7 を守る壁ではない（持ち主は monban-vault を直に呼べる・有料版が来たら Edge の側へ）。

二段確認（構想 §6・monban §14.2）: `publish` の一段目は「新しく出る・変わる・消える」ファイルの一覧と digest を見せる
だけで何も置かない。二段目（`--confirm <digest>`）で monban-vault garden publish を呼ぶ。止める（`stop`）は確認なし。
最後に置いた目録（道と SHA-256 だけ・公開の中身）は `$THTH_ROOT/garden_state/` に 1 枚だけ持つ（違いを見せるため）。
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
from html.parser import HTMLParser

from .. import accounts, secrets_fs
from . import tools, wire
from .sync import URL as VAULT_URL
from .wire import VaultRefused

# ------------------------------------------------------------------ monban の制限（edge/src/vault.js の写し）

TYPES = frozenset(("html", "css", "png", "jpg", "jpeg", "webp", "gif", "svg", "ico", "woff2", "txt", "xml"))
TEXT_KINDS = frozenset(("html", "svg", "xml"))
PATH = re.compile(r"(?:[A-Za-z0-9_~-][A-Za-z0-9._~-]{0,127}/){0,15}[A-Za-z0-9_~-][A-Za-z0-9._~-]{0,127}\Z")
FILES_MAX = 2000
FILE_MAX = 25 * 1024 * 1024
GARDEN_MAX = 100 * 1024 ** 2
HTML_MAX = 1024 * 1024
GARDEN_NAME = re.compile(r"[a-z0-9](?:[a-z0-9]|-(?!-)){0,30}\Z")
GARDEN_RESERVED = frozenset(("www", "login", "owner", "monban", "admin", "api", "mail", "oauth", "mcp", "thth",
                             "static", "assets"))

# ------------------------------------------------------------------ THTH の柵

GUIDE_MARK = 'data-thth-guide="free"'
GUIDE_ATTR = ("data-thth-guide", "free")
# 動く部品（script と form は monban の CSP で止まるが、止まった頁は壊れて見えるので出す前に断る）。
ACTIVE_TAGS = frozenset(("script", "form", "iframe", "frame", "frameset", "object", "embed", "applet", "base",
                         "portal", "input", "button", "textarea", "select", "foreignobject", "noscript", "template"))
# 前置きつき（XML の `<h:iframe`・`<h:script`）も字面で見る（読み手が見落としても止める・監査 段 1 の 13 の気づき）。
_ACTIVE_RAW = re.compile(r"<\s*/?\s*(?:[A-Za-z_][\w.-]*:)?(?:" + "|".join(sorted(ACTIVE_TAGS)) + r")\b", re.IGNORECASE)
# URL を持つ属性（ここに無い属性の値は URL として読まない: SVG の xmlns・version などを外の資源と見なさない）。
URL_ATTRS = frozenset(("src", "srcset", "imagesrcset", "href", "xlink:href", "poster", "data", "background",
                       "action", "formaction", "codebase", "manifest", "ping", "lowsrc", "dynsrc", "longdesc",
                       "archive", "icon", "cite", "itemtype"))
# リンク（読みに行かない・押した人だけが行く）は外へ向けてよい。`ping` は押すと外へ送るので外さない。
LINK_TAGS = frozenset(("a", "area"))
LINK_ATTRS = frozenset(("href", "xlink:href"))
# 読みに行かない印の属性（引用の出所・schema.org の型）。外の先は通し、javascript: などは断る（監査 段 1 の 7）。
NOFETCH_ATTRS = frozenset(("cite", "itemtype"))
# `<link href>` で、ブラウザが読みに行かない rel（rel の語が全部ここにあるときだけ外の先を通す）。stylesheet・icon・
# preload・prefetch・preconnect・dns-prefetch・modulepreload・manifest・next（先読みする端末がある）などは入れない。
NOFETCH_RELS = frozenset(("canonical", "alternate", "me", "author", "license", "help", "bookmark", "shortlink",
                          "privacy-policy", "terms-of-service"))
# 古い「中身を字として読む」タグ（ブラウザと html.parser の版で中身の読み方が違う）。
_OLD_RAW = re.compile(r"<\s*/?\s*(?:xmp|plaintext|noembed|noframes)\b", re.IGNORECASE)
# 注の終わりの読み違い（監査 段 1 の 6）: ブラウザは `<!-->`・`<!--->` ですぐ閉じ、`--!>` でも閉じる。Python 3.12 までの
# html.parser はそこを閉じずに読み進め、3.13 からはブラウザに寄せた。`-- >` は 3.12 までだけが閉じる。どれも断る。
_COMMENT_ODD = re.compile(r"<!---?>|--!>|--\s+>")
_XML_STYLESHEET = re.compile(r"<\?xml-stylesheet", re.IGNORECASE)
_TAGLIKE = re.compile(r"<[A-Za-z/!?]")
_TITLE_OPEN = re.compile(r"<title\b", re.IGNORECASE)
_TITLE_CLOSE = re.compile(r"</title", re.IGNORECASE)
_SCHEME = re.compile(r"([a-z][a-z0-9+.-]*):")
_ACTIVE_SCHEMES = frozenset(("javascript", "vbscript", "livescript"))
_CSS_ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6})\s?|\\(.)", re.DOTALL)
_CSS_URL_OPEN = re.compile(r"url\(", re.IGNORECASE)
_ENTITY = re.compile(r"<!ENTITY", re.IGNORECASE)  # と <!DOCTYPE の内部の部分（_doctype_subset・Edge の型と同じ読み）
_DOCTYPE = re.compile(r"<!DOCTYPE", re.IGNORECASE)
# monban の Edge（edge/src/vault.js の refusedText・1e85405）と同じ 2 つの型（監査 段 1 の 10）。JS の字のまま持ち、
# 試験で Edge の字と突き合わせる。制御文字（ESC は ISO-2022-JP の切り替えの始まり）と、UTF-8 以外の文字コードの宣言が
# あると、UTF-8 で読む検査と宣言に従うブラウザとで別の名前に見える。タブ・改行・CR は通す。
CONTROL_JS = r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]"
DECLARED_JS = r'''(?:encoding|charset)\s*=\s*(?!["']?utf-?8(?![a-z0-9_.:-]))'''
_JS_SPACE = r"[\t\n\x0b\x0c\r \xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]"  # JS の \s
_CONTROL = re.compile(CONTROL_JS)
# JS の i（u なし）は ASCII の字だけを大小で揃えるので re.ASCII。\s も JS の字の組に置き換える（Python の \s と少し違う）。
_DECLARED = re.compile(DECLARED_JS.replace(r"\s", _JS_SPACE), re.IGNORECASE | re.ASCII)


def _doctype_subset(text: str) -> bool:
    """Edge の `/<!DOCTYPE[^>]*\\[/i` と同じ答え（ある <!DOCTYPE の後ろで、次の > より先に [ がある）を線形で出す。
    正規表現のままだと、> の無い <!DOCTYPE が多いときに 2 乗で遅い（1 MB で 40 秒ほど）。"""
    gt = br = -2  # 前に探した次の > と [ の場所（-1 は後ろに無い）
    for m in _DOCTYPE.finditer(text):
        at = m.end()
        if br != -1 and br < at:
            br = text.find("[", at)
        if br == -1:
            return False
        if gt != -1 and gt < at:
            gt = text.find(">", at)
        if gt == -1 or br < gt:
            return True
    return False


def _safe_name(path: str) -> str:
    """断りの詳細に入れる道（字の形を落とさない: 日本語の名前もそのまま・制御文字と記号は ? に）。"""
    out = "".join(ch if (ch.isalnum() or ch in "._~-/ ") else "?" for ch in path)
    return out[:72] or "?"


def _detail(path: str, what: str) -> str:
    return f"{_safe_name(path)}: {what}"


def url_kind(value: str) -> str:
    """属性の値の URL を、ブラウザの読み方に寄せて分ける: `active`（javascript: など）・`external`（外の先）・
    `data`（data: の画像）・`local`（同じ庭の中）。文字参照は呼ぶ側（html.parser）が戻す。"""
    text = "".join(ch for ch in value if ch not in "\t\n\r")  # ブラウザは URL の中のタブと改行を捨てる
    text = text.strip("".join(chr(c) for c in range(0x21)) + "\x7f").replace("\\", "/")  # 先頭の制御文字と空白・\ は /
    lower = text.lower()
    if lower.startswith("//"):
        return "external"
    m = _SCHEME.match(lower)
    if m:
        scheme = m.group(1)
        if scheme in _ACTIVE_SCHEMES:
            return "active"
        if scheme == "data":
            return "data" if lower.startswith("data:image/") and not lower.startswith("data:image/svg") else "active"
        return "external"  # http・https・mailto ほか、名前の付いた先はどれも庭の外
    return "local"


def _css_newlines(text: str) -> str:
    """CSS の決まりの前処理（CSS Syntax §3.3）: 改行は `\\n`・`\\r\\n`・`\\r`・`\\f` の 4 つなので `\\n` 1 つに直し、NUL は
    U+FFFD に。読み手が `\\n` しか知らないと、閉じない文字列の終わりと `\\` + CRLF の行継ぎで、文字列の内と外を
    ブラウザと逆に読む（監査 段 1 の 8）。"""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n").replace("\x00", "�")


def _strip_css_comments(text: str) -> str:
    """注を外す（文字列は見ない・安全な側）。閉じない注は外さずに残す（前の正規表現と同じ・多く見る側）。正規表現で
    なく find で読むので、閉じない `/*` が多くても線形。"""
    out, i = [], 0
    while True:
        start = text.find("/*", i)
        end = -1 if start < 0 else text.find("*/", start + 2)
        if end < 0:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:start])
        out.append(" ")
        i = end + 2


def _css_text(text: str) -> str:
    """CSS の注を外し、`\68` などの崩しを戻す（外の url を崩して隠す道を塞ぐ）。"""
    text = _strip_css_comments(text)

    def back(m):
        if m.group(1):
            try:
                return chr(int(m.group(1), 16))
            except (ValueError, OverflowError):
                return "?"
        return m.group(2)
    return _CSS_ESCAPE.sub(back, text)


_CSS_ONE_ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6})[ \t\n]?|\\(.)", re.DOTALL)
# 上の段（関数の中でない）の文字列を字として見せるだけの性質。ここの文字列は読みに行かない（監査 段 1 の 7）。
CSS_TEXT_PROPS = frozenset(("content", "quotes"))
# CSS Syntax Level 3 §4（改行は呼ぶ側が \n 1 つに直し、NUL は U+FFFD にしてある）。ident の字は英数字・`-`・`_` と
# U+0080 以上のすべての字（Python の \w に入らない ©・全角空白・U+00A0・絵文字も）。空白は \n・\t・空白だけ。
_CSS_NAME_RUN = re.compile("[A-Za-z0-9_\u0080-\U0010ffff-]+")
_CSS_WS_RUN = re.compile(r"[ \t\n]+")
_CSS_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_CSS_STRING_RUN = {q: re.compile(r"[^\\\n" + q + "]+") for q in "\"'"}
_CSS_URL_RUN = re.compile(r"[^)\\\"'( \t\n\x00-\x08\x0b\x0e-\x1f\x7f]+")
_CSS_BAD_URL_REST = re.compile(r"(?:[^)\\]+|\\[^\n]|\\)*")
_CSS_PUNCT = frozenset("()[]{},:;")


def _css_escape(text: str, i: int):
    """`\\` の崩しを 1 つ読む（i は `\\` の場所・呼ぶ側が `\\` + 改行でないことを確かめてある）。"""
    m = _CSS_ONE_ESCAPE.match(text, i)
    if m is None:
        return "�", i + 1  # 終わりの `\`（CSS の決まり）
    if m.group(1):
        code = int(m.group(1), 16)
        return ("�" if code == 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF else chr(code)), m.end()
    return m.group(2), m.end()


def _css_valid_escape(text: str, i: int) -> bool:
    return text.startswith("\\", i) and not text.startswith("\\\n", i)


def _css_name_start(text: str, i: int) -> bool:
    """ここから ident が始まるか（CSS Syntax §4.3.9）。"""
    ch = text[i:i + 1]
    if ch == "-":
        nxt = text[i + 1:i + 2]
        return nxt == "-" or (nxt != "" and (nxt.isascii() is False or nxt.isalpha() or nxt == "_")) or \
            _css_valid_escape(text, i + 1)
    if ch == "\\":
        return _css_valid_escape(text, i)
    return ch != "" and (not ch.isascii() or ch.isalpha() or ch == "_")


def _css_name(text: str, i: int):
    """ident の字の並びを読む（崩しを戻す・§4.3.11）。"""
    out, n = [], len(text)
    while i < n:
        m = _CSS_NAME_RUN.match(text, i)
        if m:
            out.append(m.group())
            i = m.end()
        elif _css_valid_escape(text, i):
            got, i = _css_escape(text, i)
            out.append(got)
        else:
            break
    return "".join(out), i


def _css_string(text: str, i: int):
    """文字列を読む。閉じない文字列は行の終わりまで（改行は食べない）。文字列の中の `\\` + 改行は行継ぎ（何も無い）。"""
    quote, out, i, n = text[i], [], i + 1, len(text)
    run = _CSS_STRING_RUN[quote]
    while i < n:
        m = run.match(text, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        ch = text[i]
        if ch == quote:
            return "".join(out), i + 1
        if ch == "\n":
            return "".join(out), i
        if text.startswith("\\\n", i):
            i += 2
            continue
        got, i = _css_escape(text, i)
        out.append(got)
    return "".join(out), i


def _css_url(text: str, i: int):
    """`url(` の引用符なしの値（§4.3.6）。i は `(` の後ろ。返すのは (種類, 値, 次の場所)。"""
    n = len(text)
    m = _CSS_WS_RUN.match(text, i)
    i, out = (m.end() if m else i), []
    while i < n:
        m = _CSS_URL_RUN.match(text, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        ch = text[i]
        if ch == ")":
            return "url", "".join(out), i + 1
        if ch in " \t\n":
            i = _CSS_WS_RUN.match(text, i).end()
            if i >= n or text[i] == ")":
                return "url", "".join(out), i + 1
        elif ch == "\\" and _css_valid_escape(text, i):
            got, i = _css_escape(text, i)
            out.append(got)
            continue
        # 壊れた url（引用符・`(`・制御文字・空白のあとの字・行継ぎ）: `)` まで読み捨てる。ブラウザは宣言ごと捨てるが、
        # 値は見る（安全な側）。
        rest = _CSS_BAD_URL_REST.match(text, i)
        out.append(rest.group())
        i = rest.end()
        return "bad-url", "".join(out), i + 1
    return "url", "".join(out), i


def _css_tokens(text: str):
    """CSS を字句に切る（CSS Syntax Level 3 §4.3）。(種類, 値) を順に返す。種類は ws・str（閉じない文字列も）・url
    （bad-url も）・ident・func（値は名前）・at・hash・num（数・dimension・percentage）・cdo・cdc・delim と、`()[]{},:;`
    はその字。注は字句を作らない。`url(` かどうかは ident の後ろでだけ決める（`#url(`・`©url(`・`<!--url(`・文字列の外の
    `\\` + 改行を、ブラウザと同じに読む・監査 段 1 の 12）。"""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if ch in " \t\n":
            i = _CSS_WS_RUN.match(text, i).end()
            yield "ws", " "
            continue
        if ch in "\"'":
            value, i = _css_string(text, i)
            yield "str", value
            continue
        if ch in _CSS_PUNCT:
            i += 1
            yield ch, ch
            continue
        if ch == "#" and (_CSS_NAME_RUN.match(text, i + 1) or _css_valid_escape(text, i + 1)):
            name, i = _css_name(text, i + 1)
            yield "hash", name
            continue
        if ch == "@" and _css_name_start(text, i + 1):
            name, i = _css_name(text, i + 1)
            yield "at", name
            continue
        if ch == "<" and text.startswith("<!--", i):
            i += 4
            yield "cdo", "<!--"
            continue
        if ch in "+-.0123456789":
            m = _CSS_NUMBER.match(text, i)
            if m:
                i = m.end()
                if _css_name_start(text, i):
                    unit, i = _css_name(text, i)
                    yield "num", m.group() + unit
                elif text.startswith("%", i):
                    i += 1
                    yield "num", m.group() + "%"
                else:
                    yield "num", m.group()
                continue
            if text.startswith("-->", i):
                i += 3
                yield "cdc", "-->"
                continue
        if _css_name_start(text, i):
            name, i = _css_name(text, i)
            if text.startswith("(", i):
                i += 1
                if name.lower() == "url":
                    m = _CSS_WS_RUN.match(text, i)
                    j = m.end() if m else i
                    if j >= n or text[j] not in "\"'":
                        _, value, i = _css_url(text, i)
                        yield "url", value
                        continue
                yield "func", name
                continue
            yield "ident", name
            continue
        i += 1
        yield "delim", ch


def _css_values(text: str):
    """CSS を字句（`_css_tokens`）の順に読み、URL になりうる値を (場所, 値) で返す。場所は `url`（url(…) の中）・`func`
    （ほかの関数の中の文字列: image-set・src・var で渡す先など）・`top`（宣言の上の段の文字列と、@ の規則の前置きの
    文字列）・`free`（content・quotes の上の段と、セレクタの中の文字列: 読みに行かない）・`import`（@import の規則・値は
    空）。`( )` と `[ ]` の塊は入れ子を数え、塊の中の `;{}` で規則が終わったと見ない（`@import "外" [{}];` を読み違えない・
    監査 段 1 の 9）。改行は呼ぶ側が `\\n` 1 つに直す。"""
    out, pending, stack, parens, prop_buf = [], [], [], [], []
    prop, paren_seen, at_rule = None, False, False

    def flush(prelude):
        where = "free" if (prelude and not at_rule) or (prop in CSS_TEXT_PROPS) else "top"
        out.extend((where, v) for v in pending)
        pending.clear()

    for kind, value in _css_tokens(text):
        if kind == "str":
            if parens:
                out.append(("url" if parens[-1] == "url" else "func", value))
            elif paren_seen:
                out.append(("func", value))  # 崩した ) で関数を閉じたように見せても、( の後ろはいつも関数の中と見る
            else:
                pending.append(value)  # セレクタの [title="…"] の中も、前置きか宣言かは塊の外で決まる
            continue
        if kind == "url":
            paren_seen = True
            out.append(("url", value.strip()))
            continue
        if kind == "at":
            # @ の規則（崩しも戻した名前）。@import はどこにあっても断る。前置きの文字列は読みに行かない物と見ない。
            name = value.lower()
            if name == "import":
                out.append(("import", ""))
            if not stack:
                at_rule = True
            prop_buf.append("@" + name)
            continue
        if kind in ("func", "("):
            paren_seen = True
            stack.append("(")
            parens.append(value.lower() if kind == "func" else "")
            continue
        if kind == "[":
            stack.append("[")
        elif kind == "]":
            if stack and stack[-1] == "[":
                stack.pop()
        elif kind == ")":
            if stack and stack[-1] == "(":  # [ ] の中の ) は塊を閉じない（CSS の読み方）
                stack.pop()
                parens.pop()
        elif kind in (";", "{", "}") and not stack:
            flush(kind == "{")
            prop, paren_seen, at_rule, prop_buf = None, False, False, []
            continue
        elif kind == ":" and not stack and prop is None:
            prop = "".join(prop_buf).strip().lower()
        prop_buf.append(("#" + value) if kind == "hash" else value)
    flush(False)
    return out


def _css_url_heads(flat: str):
    """注を外した姿の `url(` の値の頭。url_kind が見るのは頭（`//` と scheme）だけなので、値は次の `)` か次の `url(` の
    手前までで切る（`url(` の `(` は scheme に入らないので、切っても分け方は変わらない）。前の正規表現は閉じない
    `url(` が多いと 2 乗で遅かった（200 KB で 66 秒・監査 8b61eee の気づき）。これは線形。"""
    starts = [m.end() for m in _CSS_URL_OPEN.finditer(flat)]
    for k, start in enumerate(starts):
        stop = starts[k + 1] - 4 if k + 1 < len(starts) else len(flat)
        j = start
        while j < stop and flat[j].isspace():
            j += 1
        if j < stop and flat[j] in "\"'":
            j += 1
        end = flat.find(")", j, stop)
        yield flat[j:stop if end < 0 else end]


def css_problem(text: str):
    """CSS の外の資源（url(…)・@import・関数の中と宣言の上の段の文字列の外の URL）。断る理由の語か None。
    content と quotes の上の段の文字列（`content:"Note: "`）とセレクタの中の文字列は読みに行かないので見ない。"""
    text = _css_newlines(text)
    flat = _css_text(text)
    if re.search(r"@import", flat, re.IGNORECASE):
        return "@import"
    values = _css_values(text)
    if any(where == "import" for where, _ in values):
        return "@import"
    for value in _css_url_heads(flat):  # 注を外した姿でも見る（url( の中の注・閉じない url(・安全な側）
        if url_kind(value) in ("external", "active"):
            return "url"
    for where, value in values:
        if where != "free" and url_kind(value) in ("external", "active"):
            return "url"
    return None


class _Reader(HTMLParser):
    """タグと属性を標準の html.parser で読む（正規表現の柵の抜け道を塞ぐ・監査 段 1 の 4）。"""

    def __init__(self, kind: str = "html"):
        # 本文の文字参照は戻さない（&lt; を < と読んで壊れたタグと取り違えない）。属性の値は html.parser がいつも戻す。
        super().__init__(convert_charrefs=False)
        self.problem = None
        self.guides = 0
        self.xml = kind != "html"

    def _set(self, code, what):
        if self.problem is None:
            self.problem = (code, what)

    def handle_starttag(self, tag, attrs):
        self._tag(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._tag(tag, attrs)

    def _tag(self, tag, attrs):
        tag = tag.lower().split(":")[-1]
        if _TAGLIKE.search((self.get_starttag_text() or "")[1:]):
            # 引用符の合わない属性が次のタグを呑んだ（alt="a < b" のような字の < は通す・監査 段 1 の 7）
            return self._set("garden_markup_refused", "broken tag")
        if tag in ACTIVE_TAGS:
            return self._set("garden_active_refused", tag)
        if tag == "style":
            return self._set("garden_markup_refused", "style")
        names = {name.lower() for name, _ in attrs}
        rels = set()
        for name, value in attrs:
            if name.lower() == "rel":
                rels |= set((value or "").lower().split())
        for name, value in attrs:
            name = name.lower()
            value = value or ""
            if name.startswith("on"):
                return self._set("garden_active_refused", "on-event")
            if name == "style":
                return self._set("garden_markup_refused", "style")
            if self.xml and ":" in name and not name.startswith("xmlns:"):
                # SVG・XML は前置きの名前を替えられる（xmlns:z="…xlink" と z:href・監査 段 1 の 11）。前置きに依らず
                # 後ろの名前で引く（xlink 以外の名前空間の href も、外の先なら断る側に倒す）。xmlns:… は名前空間の宣言。
                name = "xlink:href" if name.split(":")[-1] == "href" else name.split(":")[-1]
            if name not in URL_ATTRS:
                continue
            parts = [p.strip().split(" ")[0] for p in value.split(",")] if name.endswith("srcset") else [value]
            for part in parts:
                kind = url_kind(part)
                if kind == "active":
                    return self._set("garden_active_refused", "javascript")
                if kind != "external" or name in NOFETCH_ATTRS or (tag in LINK_TAGS and name in LINK_ATTRS):
                    continue
                if tag == "link" and name == "href" and rels and rels <= NOFETCH_RELS:
                    continue  # canonical・alternate など、読みに行かない link
                return self._set("garden_external_refused", f"{tag} {name}"[:24])
        if (GUIDE_ATTR in [(n.lower(), v) for n, v in attrs]) and tag == "aside" and "hidden" not in names:
            self.guides += 1

    def handle_data(self, data):
        if _TAGLIKE.search(data):
            self._set("garden_markup_refused", "broken tag")


def _parser_text(text: str, kind: str):
    """html.parser に渡す前に、ブラウザ（と XML の読み手）と html.parser の版で読みの割れる書き方を字面で断る
    （監査 段 1 の 6）。返すのは (html.parser に渡す字, 断る理由の語か None)。どれも字面の検査なので、属性の値の中に
    書いてあっても断る（安全な側の誤り）。"""
    if _COMMENT_ODD.search(text):
        return text, "comment"
    if _OLD_RAW.search(text):
        return text, "old raw tag"
    if kind == "html":
        if "<![" in text:  # HTML の <![CDATA[ はブラウザでは最初の > で閉じる注・html.parser は ]]> まで読む
            return text, "cdata"
    else:
        if kind in ("xml", "svg") and _XML_STYLESHEET.search(text):
            return text, "xml-stylesheet"  # XSLT で頁を組み直す道を塞ぐ（手堅い側）
        # 閉じない CDATA があるのは、最後の <![CDATA[ の後ろに ]]> が無いときだけ（前の先読みの型は 2 乗で遅かった）。
        last = text.rfind("<![CDATA[")
        if re.search(r"<!\[(?!CDATA\[)", text) or (last >= 0 and text.find("]]>", last + 9) < 0):
            return text, "cdata"
        # CDATA の中の `<`（`<?`・`<!--`・`<!`・`</` など）は、XML の読み手には字でも、印を外して読む html.parser には
        # PI・注・宣言・終わりのタグの始まりになり、次の本物のタグを呑む（監査 段 1 の 13）。どの印から次の `]]>` までに
        # `<` があっても断る（本物の CDATA は必ずどれかの区間に収まるので、注や属性の中の偽の印でも抜けない）。次の `]]>`
        # と次の `<` の探しは前の答えを使い回して線形に。
        end = lt = -2
        for m in re.finditer(r"<!\[CDATA\[", text):
            start = m.end()
            if end != -1 and end < start:
                end = text.find("]]>", start)
            if lt != -1 and lt < start:
                lt = text.find("<", start)
            if lt != -1 and (end == -1 or lt < end):
                return text, "cdata lt"
        # XML の CDATA の中身は字だが、ここでは印だけを外して中身もタグとして読む（読み手より多く見る側に倒す・
        # 版で CDATA の読み方が違う html.parser に印を見せない）。
        text = text.replace("<![CDATA[", " " * 9).replace("]]>", "   ")
    # <title> の中身は、ブラウザと Python 3.13 からの html.parser は字として、3.12 までは タグとして読む。タグに見える
    # 字を断って、版で結果が割れないようにする。
    # 字の探しは前の答えを使い回して線形に（`<title\b[^>]*>` の型は > の無い <title が多いと 2 乗で遅かった）。
    gt = close = -2
    for m in _TITLE_OPEN.finditer(text):
        if gt != -1 and gt < m.end():
            gt = text.find(">", m.end())
        if gt == -1:
            break  # 後ろに > が無い <title はタグにならない（前の型と同じ）
        start = gt + 1
        if close != -1 and close < start:
            found = _TITLE_CLOSE.search(text, start)
            close = found.start() if found else -1
        if close == -1 or _TAGLIKE.search(text, start, close):
            return text, "title"
    return text, None


def _text_problem(path: str, data: bytes, kind: str):
    """HTML・SVG・XML の検査。断る (符丁, 詳細) か None。"""
    if len(data) > HTML_MAX:
        return "garden_size_refused", _detail(path, "1 MB")
    if data[:2] in (b"\xfe\xff", b"\xff\xfe") or b"\x00" in data:
        return "garden_text_refused", _detail(path, "utf-8")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "garden_text_refused", _detail(path, "utf-8")
    # monban の Edge と同じ順と同じ型（監査 段 1 の 10・monban 1e85405）。
    if _CONTROL.search(text):
        return "garden_text_refused", _detail(path, "control")
    if _DECLARED.search(text):
        return "garden_text_refused", _detail(path, "encoding")
    if re.search(r"http-equiv", text, re.IGNORECASE):
        return "garden_text_refused", _detail(path, "http-equiv")
    if _ENTITY.search(text) or _doctype_subset(text):
        return "garden_markup_refused", _detail(path, "entity")
    if _ACTIVE_RAW.search(text):  # 読み手が見落としても字面で止める（本文の < は build が escape する）
        return "garden_active_refused", _detail(path, "script or form")
    feed, why = _parser_text(text, kind)
    if why:
        return "garden_markup_refused", _detail(path, why)
    reader = _Reader(kind)
    reader.feed(feed)
    if reader.problem is None and "<" in reader.rawdata:
        # 閉じないタグ・引用符の合わないタグ（終わりの `R&D` のような字だけの残りは本文として通す・監査 段 1 の 7）
        reader.problem = ("garden_markup_refused", "broken tag")
    if "<" not in reader.rawdata:
        # 残りが字だけのときだけ close で流す。残りに < があると close は 1 字ずつ読み直して 2 乗で遅い（閉じない
        # <a が 7 万字で 1 分）。そのときは上で断っているので、読み終えなくてよい。
        reader.close()
    if reader.problem:
        return reader.problem[0], _detail(path, reader.problem[1])
    # 案内の枠: うっかり落とすのを見つける検査（`<aside data-thth-guide="free">` が 1 つ・hidden なし）。持ち主は
    # monban-vault を直に呼べるので、原則の 7 を守る壁にはならない。有料版が来たら、守る場所を Edge の側（配るときに
    # 差し込む・権限で分ける）へ移す（監査 段 1 の 3・朝に覆せる）。
    if kind == "html" and (reader.guides != 1 or text.count(GUIDE_MARK) != 1):
        return "garden_guide_missing", _safe_name(path)
    return None


def _css_problem(path: str, data: bytes):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "garden_text_refused", _detail(path, "utf-8")
    why = css_problem(text)
    if why:
        return "garden_external_refused", _detail(path, f"css {why}")
    return None


def check_dir(directory: str) -> dict:
    """出す前の検査。通れば `{"files": {"/道": {"sha256", "size"}}, "bytes": 全部}`。外れたら最初の 1 つで断る。"""
    if not isinstance(directory, str) or not os.path.isdir(directory) or os.path.islink(directory):
        raise VaultRefused("garden_dir_missing")
    root = os.path.realpath(directory)
    files, total = {}, 0
    for base, dirs, names in os.walk(root):
        rel_base = os.path.relpath(base, root)
        for name in sorted(dirs):
            rel = name if rel_base == "." else f"{rel_base}/{name}"
            if name.startswith(".") or os.path.islink(os.path.join(base, name)):
                raise VaultRefused("garden_kind_refused", _detail(rel, "dot or link"))
        dirs.sort()
        for name in sorted(names):
            full = os.path.join(base, name)
            rel = (name if rel_base == "." else f"{rel_base}/{name}").replace(os.sep, "/")
            if name.startswith(".") or os.path.islink(full) or not os.path.isfile(full):
                raise VaultRefused("garden_kind_refused", _detail(rel, "dot or link"))
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in TYPES:
                raise VaultRefused("garden_kind_refused", _detail(rel, "kind"))
            if not PATH.fullmatch(rel) or any(part in (".", "..") for part in rel.split("/")):
                raise VaultRefused("garden_kind_refused", _detail(rel, "name"))
            if len(files) >= FILES_MAX:
                raise VaultRefused("garden_size_refused", f"{FILES_MAX} files")
            size = os.path.getsize(full)
            if size > FILE_MAX:
                raise VaultRefused("garden_size_refused", _detail(rel, "25 MB"))
            with open(full, "rb") as stream:
                data = stream.read()
            problem = _text_problem(rel, data, ext) if ext in TEXT_KINDS else (
                _css_problem(rel, data) if ext == "css" else None)
            if problem:
                raise VaultRefused(*problem)
            total += len(data)
            if total > GARDEN_MAX:
                raise VaultRefused("garden_size_refused", "100 MB")
            files["/" + rel] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    if not files:
        raise VaultRefused("garden_empty")
    return {"files": files, "bytes": total}


# ------------------------------------------------------------------ 庭の URL と置いた目録

def check_url(url) -> str:
    m = VAULT_URL.fullmatch(url) if isinstance(url, str) else None
    if not m or not GARDEN_NAME.fullmatch(m.group(3)) or m.group(3) in GARDEN_RESERVED:
        raise VaultRefused("garden_args_invalid", "url")
    return url.rstrip("/")


def state_path(url: str) -> str:
    m = VAULT_URL.fullmatch(url)
    name = f"{m.group(1)}--{m.group(2)}--{m.group(3)}"
    return os.path.join(accounts.thth_root(), "garden_state", f"{name}.json")


def last_published(url: str) -> dict:
    try:
        with open(state_path(url), encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, ValueError):
        return {}
    files = value.get("files") if isinstance(value, dict) else None
    return files if isinstance(files, dict) else {}


def changes(files: dict, last: dict) -> dict:
    new = sorted(p for p in files if p not in last)
    changed = sorted(p for p in files if p in last and last[p] != files[p]["sha256"])
    removed = sorted(p for p in last if p not in files)
    return {"new": new, "changed": changed, "removed": removed, "first": not last}


def digest(url: str, files: dict) -> str:
    joined = "\x1f".join(["thth_garden_publish/1", url] + [f"{p}={files[p]['sha256']}" for p in sorted(files)])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


MONBAN_TO_GARDEN = {
    "garden_token_missing": "garden_not_ready",
    "garden_missing": "garden_not_ready",
    "garden_token_required": "garden_not_ready",
    "stale_head": "garden_busy",
    "too_many_pending": "garden_busy",
    "rate_limited": "garden_busy",
    "garden_full": "garden_full",
    "invalid_key": "garden_login_required",
    "not_logged_in": "garden_login_required",
    "login_expired": "garden_login_required",
    "remote_unavailable": "garden_unavailable",
    "invalid_vault_url": "garden_args_invalid",
}


def _monban_refusal(rc: int, out: str, err: str, step: str) -> VaultRefused:
    got = wire.monban_json(out)
    code = got.get("error") or wire.monban_code(err)
    if code in MONBAN_TO_GARDEN:
        return VaultRefused(MONBAN_TO_GARDEN[code], code)
    return VaultRefused("garden_failed", f"{step} {code}" if code else step)


def plan(directory: str, url: str) -> dict:
    url = check_url(url)
    found = check_dir(directory)
    return {"url": url, "directory": os.path.realpath(directory), "files": found["files"], "bytes": found["bytes"],
            "changes": changes(found["files"], last_published(url)), "digest": digest(url, found["files"])}


def publish(found: dict) -> dict:
    """二段目: monban-vault garden publish を呼ぶ（`found` は直前に作り直した `plan`）。"""
    exe = tools.need(tools.MONBAN_VAULT, "garden")
    rc, out, err = tools.run([exe, "garden", "publish", found["url"], found["directory"], "--json"], family="garden")
    if rc != 0:
        raise _monban_refusal(rc, out, err, "publish")
    got = wire.monban_json(out)
    record = {"schema": "thth.garden_published/1", "url": found["url"], "head": got.get("head"),
              "files": {p: v["sha256"] for p, v in found["files"].items()}}
    secrets_fs.atomic_write_json(state_path(found["url"]), record, mode=0o600)
    return {"head": got.get("head"), "files": got.get("files", len(found["files"])),
            "bytes": got.get("bytes", found["bytes"])}


def simple(url: str, verb: str) -> dict:
    """`stop`（確認なし・すぐ）と `status`（読むだけ）。"""
    url = check_url(url)
    exe = tools.need(tools.MONBAN_VAULT, "garden")
    rc, out, err = tools.run([exe, "garden", verb, url, "--json"], family="garden")
    if rc != 0:
        raise _monban_refusal(rc, out, err, verb)
    got = wire.monban_json(out)
    done = {"url": url, **{k: v for k, v in got.items() if k != "error"}}
    if verb == "status":
        # Edge は止めを 2 つ持つ（持ち主の止め・運営者の止め・edge/src/vault.js）。どちらかが立てば配っていない。
        done["owner_stopped"] = bool(got.get("owner_stopped"))
        done["operator_stopped"] = bool(got.get("operator_stopped"))
        done["serving"] = not (done["owner_stopped"] or done["operator_stopped"] or got.get("stopped"))
    return done


def state_words(done: dict) -> str:
    if done.get("operator_stopped"):
        return "止まっています（運営者が止めました・戻すのは運営者）"
    if done.get("owner_stopped") or done.get("stopped"):
        return "止まっています（自分で止めました・monban-vault garden resume で戻せます）"
    return "配っています"


# ------------------------------------------------------------------ 店の写しから頁を作る

OUTPUT_FILES = ("index.html", "items.html", "style.css")
TITLE_MAX = 60
_SHOP_URL = re.compile(r"https://[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~%/-]*)?\Z")
GUIDE = ('<aside class="thth-guide" ' + GUIDE_MARK + '><p>THTH の案内: この頁は '
         '<a href="https://thth.me/" rel="noopener">THTH</a> の無料版で作りました。</p></aside>')
STYLE = """body{margin:0 auto;max-width:42rem;padding:1.5rem 1rem;font-family:system-ui,sans-serif;line-height:1.8;color:#222}
h1{font-size:1.5rem;line-height:1.4}
ul.items{list-style:none;padding:0}
ul.items li{border-bottom:1px solid #ddd;padding:.75rem 0}
.price{font-variant-numeric:tabular-nums}
.thth-guide{margin-top:2rem;padding:.75rem 1rem;border:1px solid #ccc;border-radius:.5rem;font-size:.875rem;color:#555}
footer{margin-top:1.5rem;font-size:.75rem;color:#777}
"""


def _t(value) -> str:
    """本文に入れる文。escape したうえで、Edge が文の中でも断る `http-equiv` の字面を崩す（見た目は同じ）。"""
    out = html.escape(str(value), quote=True)
    return re.sub(r"(?i)(http)-(equiv)", r"\1&#45;\2", out)


def _page(title: str, body: str) -> str:
    return ("<!DOCTYPE html>\n<html lang=\"ja\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{_t(title)}</title>\n<link rel=\"stylesheet\" href=\"style.css\">\n</head>\n<body>\n"
            f"{body}\n{GUIDE}\n</body>\n</html>\n")


def _price(row: dict) -> str:
    price = row.get("price")
    return f"{price:,} 円" if isinstance(price, int) and not isinstance(price, bool) else "価格は店の頁で"


def render(title: str, items: list, shop_url: str | None, read_at: str | None) -> dict:
    """`{名前: 文}`。items は公開で買える商品だけ（呼ぶ側で選ぶ）。"""
    buy = (f'<p><a href="{_t(shop_url)}" rel="noopener">お店の頁へ（買うのはこちら）</a></p>' if shop_url else "")
    when = f"<footer><p>商品の様子は {_t(read_at)} に読んだ写しから作りました。</p></footer>" if read_at else ""
    index = _page(title, f"<header><h1>{_t(title)}</h1></header>\n<main>\n"
                         f"<p>今買える商品を {len(items)} 点載せています。</p>\n"
                         f'<p><a href="items.html">商品の一覧</a></p>\n{buy}\n</main>\n{when}')
    rows = "\n".join(f'<li><span class="name">{_t(row.get("name") or "")}</span> '
                     f'<span class="price">{_t(_price(row))}</span></li>' for row in items)
    listing = (f'<ul class="items">\n{rows}\n</ul>' if items else "<p>今買える商品はありません。</p>")
    page = _page(f"商品の一覧 - {title}", f"<header><h1>商品の一覧</h1><p><a href=\"index.html\">{_t(title)}</a></p>"
                                       f"</header>\n<main>\n{listing}\n{buy}\n</main>\n{when}")
    return {"index.html": index, "items.html": page, "style.css": STYLE}


def build(store: str, out: str, *, title: str | None = None, shop_url: str | None = None) -> dict:
    """店の写し（`thth shop diff` が書く 1 枚）から頁を作る。写しを読むだけ（店にも写しにも書かない）。"""
    from ..shop import base, ledger
    from ..shop.wire import StoreRefused
    try:
        ledger.check_name(store)
        snapshot = ledger.read_snapshot(store)
    except StoreRefused as exc:
        raise VaultRefused("garden_snapshot_missing" if exc.code == "store_ledger_invalid" else "garden_args_invalid",
                           "store") from None
    if snapshot is None:
        raise VaultRefused("garden_snapshot_missing", store)
    if shop_url is not None and (len(shop_url) > 300 or not _SHOP_URL.fullmatch(shop_url)):
        raise VaultRefused("garden_args_invalid", "shop-url")
    title = base.clean_text(title if title is not None else store, TITLE_MAX)
    if not title:
        raise VaultRefused("garden_args_invalid", "title")
    rows = [row for row in snapshot["items"].values() if isinstance(row, dict) and base.buyable(row)]
    rows.sort(key=lambda row: (str(row.get("name") or ""), str(row.get("id"))))
    if os.path.lexists(out) and (not os.path.isdir(out) or os.path.islink(out)
                                 or set(os.listdir(out)) - set(OUTPUT_FILES)
                                 or any(os.path.islink(os.path.join(out, n)) for n in OUTPUT_FILES)):
        raise VaultRefused("garden_out_not_empty")
    os.makedirs(out, mode=0o755, exist_ok=True)
    read_at = snapshot.get("read_at") if isinstance(snapshot.get("read_at"), str) else None
    for name, text in render(title, rows, shop_url, read_at and base.clean_text(read_at, 40)).items():
        with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    found = check_dir(out)  # 作った物も出す前の検査を通す（柵）
    return {"ok": True, "out": os.path.realpath(out), "items": len(rows),
            "not_listed": len(snapshot["items"]) - len(rows), "files": sorted(found["files"]), "bytes": found["bytes"]}
