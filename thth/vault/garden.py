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
  1 枚 1 MB・UTF-8 だけ（BOM の FE FF・FF FE と NUL を断る）・`http-equiv` を含めない。monban の道具が黙って飛ばす
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
_ACTIVE_RAW = re.compile(r"<\s*/?\s*(?:" + "|".join(sorted(ACTIVE_TAGS)) + r")\b", re.IGNORECASE)
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
_SCHEME = re.compile(r"([a-z][a-z0-9+.-]*):")
_ACTIVE_SCHEMES = frozenset(("javascript", "vbscript", "livescript"))
_CSS_ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6})\s?|\\(.)", re.DOTALL)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE | re.DOTALL)
_ENTITY = re.compile(r"<!ENTITY|<!DOCTYPE[^>]*\[", re.IGNORECASE)


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


def _css_text(text: str) -> str:
    """CSS の注を外し、`\68` などの崩しを戻す（外の url を崩して隠す道を塞ぐ）。"""
    text = _CSS_COMMENT.sub(" ", text)

    def back(m):
        if m.group(1):
            try:
                return chr(int(m.group(1), 16))
            except (ValueError, OverflowError):
                return "?"
        return m.group(2)
    return _CSS_ESCAPE.sub(back, text)


_CSS_ONE_ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6})[ \t\n\r\f]?|\\(.)", re.DOTALL)
# 上の段（関数の中でない）の文字列を字として見せるだけの性質。ここの文字列は読みに行かない（監査 段 1 の 7）。
CSS_TEXT_PROPS = frozenset(("content", "quotes"))
_CSS_IDENT = re.compile(r"[\w-]")


def _css_escape(text: str, i: int):
    m = _CSS_ONE_ESCAPE.match(text, i)
    if m is None:
        return "", i + 1
    if m.group(1):
        code = int(m.group(1), 16)
        return ("\ufffd" if code == 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF else chr(code)), m.end()
    return ("" if m.group(2) == "\n" else m.group(2)), m.end()


def _css_string(text: str, i: int):
    quote, out, i = text[i], [], i + 1
    while i < len(text):
        ch = text[i]
        if ch == quote:
            return "".join(out), i + 1
        if ch == "\n":
            return "".join(out), i  # 閉じない文字列は行の終わりまで（CSS の読み方）
        if ch == "\\":
            got, i = _css_escape(text, i)
            out.append(got)
            continue
        out.append(ch)
        i += 1
    return "".join(out), i


def _css_values(text: str):
    """CSS を字句の順に読み、URL になりうる値を (場所, 値) で返す。場所は `url`（url(…) の中）・`func`（ほかの関数の
    中の文字列: image-set・src・var で渡す先など）・`top`（宣言の上の段の文字列）・`free`（content・quotes の上の段と、
    セレクタの中の文字列: 読みに行かない）。注・文字列・崩し（\\）を 1 回で読むので、文字列の中の `/*` に惑わされない。"""
    out, pending, stack, ident, prop_buf = [], [], [], [], []
    prop, paren_seen, i, n = None, False, 0, len(text)

    def flush(prelude):
        where = "free" if prelude or (prop in CSS_TEXT_PROPS) else "top"
        out.extend((where, v) for v in pending)
        pending.clear()

    while i < n:
        ch = text[i]
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i, ident = (n if end < 0 else end + 2), []
            continue
        if ch == "\\":
            got, i = _css_escape(text, i)
            ident.append(got)
            prop_buf.append(got)
            continue
        if ch in "\"'":
            value, i = _css_string(text, i)
            ident = []
            if stack:
                out.append(("url" if stack[-1] == "url" else "func", value))
            elif paren_seen:
                out.append(("func", value))  # 崩した ) で関数を閉じたように見せても、( の後ろはいつも関数の中と見る
            else:
                pending.append(value)
            continue
        if ch == "(":
            name, ident, paren_seen = "".join(ident).lower(), [], True
            i += 1
            if name == "url":
                j = i
                while j < n and text[j] in " \t\n\r\f":
                    j += 1
                if j >= n or text[j] not in "\"'":
                    value = []
                    while j < n and text[j] != ")":
                        if text[j] == "\\":
                            got, j = _css_escape(text, j)
                            value.append(got)
                        else:
                            value.append(text[j])
                            j += 1
                    out.append(("url", "".join(value).strip()))
                    i = j + 1
                    continue
            stack.append(name)
            continue
        if ch == ")":
            if stack:
                stack.pop()
        elif ch in ";{}" and not stack:
            flush(ch == "{")
            prop, paren_seen, prop_buf = None, False, []
            i, ident = i + 1, []
            continue
        elif ch == ":" and not stack and prop is None:
            prop = "".join(prop_buf).strip().lower()
        if _CSS_IDENT.match(ch):
            ident.append(ch)
        else:
            ident = []
        prop_buf.append(ch)
        i += 1
    flush(False)
    return out


def css_problem(text: str):
    """CSS の外の資源（url(…)・@import・関数の中と宣言の上の段の文字列の外の URL）。断る理由の語か None。
    content と quotes の上の段の文字列（`content:"Note: "`）とセレクタの中の文字列は読みに行かないので見ない。"""
    flat = _css_text(text)
    if re.search(r"@import", flat, re.IGNORECASE):
        return "@import"
    for _, value in _CSS_URL.findall(flat):  # 注を外した姿でも見る（url( の中の注・安全な側）
        if url_kind(value) in ("external", "active"):
            return "url"
    for where, value in _css_values(text):
        if where != "free" and url_kind(value) in ("external", "active"):
            return "url"
    return None


class _Reader(HTMLParser):
    """タグと属性を標準の html.parser で読む（正規表現の柵の抜け道を塞ぐ・監査 段 1 の 4）。"""

    def __init__(self):
        # 本文の文字参照は戻さない（&lt; を < と読んで壊れたタグと取り違えない）。属性の値は html.parser がいつも戻す。
        super().__init__(convert_charrefs=False)
        self.problem = None
        self.guides = 0

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
        if re.search(r"<!\[(?!CDATA\[)", text) or re.search(r"<!\[CDATA\[(?![\s\S]*\]\]>)", text):
            return text, "cdata"
        # XML の CDATA の中身は字だが、ここでは印だけを外して中身もタグとして読む（読み手より多く見る側に倒す・
        # 版で CDATA の読み方が違う html.parser に印を見せない）。
        text = text.replace("<![CDATA[", " " * 9).replace("]]>", "   ")
    # <title> の中身は、ブラウザと Python 3.13 からの html.parser は字として、3.12 までは タグとして読む。タグに見える
    # 字を断って、版で結果が割れないようにする。
    for m in re.finditer(r"<title\b[^>]*>", text, re.IGNORECASE):
        end = re.compile(r"</title", re.IGNORECASE).search(text, m.end())
        if end is None or _TAGLIKE.search(text, m.end(), end.start()):
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
    if re.search(r"http-equiv", text, re.IGNORECASE):
        return "garden_text_refused", _detail(path, "http-equiv")
    if _ENTITY.search(text):
        return "garden_markup_refused", _detail(path, "entity")
    if _ACTIVE_RAW.search(text):  # 読み手が見落としても字面で止める（本文の < は build が escape する）
        return "garden_active_refused", _detail(path, "script or form")
    feed, why = _parser_text(text, kind)
    if why:
        return "garden_markup_refused", _detail(path, why)
    reader = _Reader()
    reader.feed(feed)
    if reader.problem is None and "<" in reader.rawdata:
        # 閉じないタグ・引用符の合わないタグ（終わりの `R&D` のような字だけの残りは本文として通す・監査 段 1 の 7）
        reader.problem = ("garden_markup_refused", "broken tag")
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
