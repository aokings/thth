"""はてなブログの AtomPub の口（設計 4.0 S2 §3）。

`base.Adapter` は**継がない**（口が `publish(Post)` ではなく記事の作成・更新・削除）。
`adapters.REGISTRY` にも載せない——core は `media == "hatenablog"` で `hatena_publish` に
分岐し、`hatena_publish` がここの `factory` を使う。

- URL は主体の `hatena_id` と口座の `blog_id` **だけ**から組む
  （`https://blog.hatena.ne.jp/<hatena_id>/<blog_id>/atom/entry[/<数字>]`）。台帳の `edit_uri` は
  使わない。組んだ URL と違えば `edit_uri_mismatch` で送らない。
- 認証は OAuth 1.0a（`thth/oauth1.py`）。`wsse` は `auth_unsupported` で断る。
- HTTP は `httpsafe.urlopen`（同一 origin のリダイレクトだけ・平文不可・proxy 不可・leave_gate）。
  **再試行しない・sleep しない。** 書き込みは 1 回。
- 断り（4xx）は「出ていない」、5xx・時間切れ・読めない応答は「分からない」（§3.5 の表）。
- 例外の文は `redact` を通してから返す。秘密の値はどこにも出さない。
"""
from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .. import httpsafe
from .. import redact as redact_mod

ATOM = "http://www.w3.org/2005/Atom"
APP = "http://www.w3.org/2007/app"
HATENA = "http://www.hatena.ne.jp/info/xmlns#"
HATENABLOG = "http://www.hatena.ne.jp/info/xmlns#hatenablog"
BASE = "https://blog.hatena.ne.jp"

TOKEN_SCHEMA = "thth.hatena-oauth/1"
CONSUMER_SCHEMA = "thth.hatena-consumer/1"
MARKDOWN = "text/x-markdown"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
READ_TIMEOUT = 30
WRITE_TIMEOUT = 60
DEFAULT_CONTACT = "https://thth.me/"

# 送る XML は公式の例と同じく Atom を既定の名前空間・`app:` を接頭辞にする（ET の登録は process 全体）。
ET.register_namespace("", ATOM)
ET.register_namespace("app", APP)


class ResponseTooLarge(Exception):
    """応答が上限（4 MiB）を超えた。"""


class ResponseInvalid(Exception):
    """応答の XML が読めない・形が違う。"""


def _q(tag, ns=ATOM):
    return "{" + ns + "}" + tag


# --------------------------------------------------------------------------
# XML
# --------------------------------------------------------------------------

def entry_xml(*, title, body, categories, author) -> bytes:
    """送る XML（§3.3）。`updated`・`app:preview`・`hatenablog:scheduled`・`custom-url` は作らない。"""
    root = ET.Element(_q("entry"))
    ET.SubElement(root, _q("title")).text = title or ""
    ET.SubElement(ET.SubElement(root, _q("author")), _q("name")).text = author
    content = ET.SubElement(root, _q("content"), {"type": "text/plain"})
    content.text = body or ""
    for term in categories or []:
        ET.SubElement(root, _q("category"), {"term": str(term)})
    control = ET.SubElement(root, _q("control", APP))
    ET.SubElement(control, _q("draft", APP)).text = "no"
    text = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="utf-8"?>\n' + text).encode("utf-8")


def _parse_root(raw: bytes, expected: str):
    if not isinstance(raw, (bytes, bytearray)):
        raise ResponseInvalid("response_invalid")
    if b"<!doctype" in bytes(raw[:4096]).lower() or b"<!entity" in bytes(raw).lower():
        raise ResponseInvalid("response_invalid")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise ResponseInvalid("response_invalid") from None
    if root.tag != _q(expected):
        raise ResponseInvalid("response_invalid")
    return root


def _text(node):
    return node.text if node is not None and node.text is not None else None


def _entry_fields(root) -> dict:
    links = root.findall(_q("link"))
    edit = next((l.get("href") for l in links if l.get("rel") == "edit"), None)
    alternates = [l for l in links if l.get("rel") == "alternate"]
    html = next((l.get("href") for l in alternates if l.get("type") == "text/html"), None)
    published_url = html or (alternates[0].get("href") if alternates else None)
    content = root.find(_q("content"))
    control = root.find(_q("control", APP))
    draft = _text(control.find(_q("draft", APP))) if control is not None else None
    return {
        "entry_id": (_text(root.find(_q("id"))) or "").strip() or None,
        "edit_uri": edit,
        "published_url": published_url,
        "content": (content.text or "") if content is not None else "",
        "content_type": content.get("type") if content is not None else None,
        "draft": draft.strip() if isinstance(draft, str) else None,
        "title": _text(root.find(_q("title"))),
        "categories": [c.get("term") for c in root.findall(_q("category")) if c.get("term") is not None],
        "edited": _text(root.find(_q("edited", APP))),
        "published": _text(root.find(_q("published"))),
    }


def parse_entry(raw: bytes) -> dict:
    """1 本の記事の応答（§3.4）。読めない・根が entry でない・DOCTYPE あり → `ResponseInvalid`。"""
    return _entry_fields(_parse_root(raw, "entry"))


def parse_feed(raw: bytes) -> dict:
    """一覧の応答。`entries`（先頭が最新）と `next`（次の頁の URL）。"""
    root = _parse_root(raw, "feed")
    nxt = next((l.get("href") for l in root.findall(_q("link")) if l.get("rel") == "next"), None)
    return {"entries": [_entry_fields(e) for e in root.findall(_q("entry"))], "next": nxt}


# --------------------------------------------------------------------------
# 秘密のファイル（§2.3）
# --------------------------------------------------------------------------

def _unavailable(code):
    from ..hatena_publish import AdapterUnavailable
    return AdapterUnavailable(code)


def _good(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not any(
        ord(c) < 32 or ord(c) == 127 or c.isspace() for c in value)


def read_consumer(path) -> dict:
    """consumer のファイルを読む。無い → `consumer_missing`・形が違う → `consumer_invalid`。"""
    if not isinstance(path, str) or not path or not os.path.exists(path):
        raise _unavailable("consumer_missing")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise _unavailable("consumer_invalid") from None
    if (not isinstance(data, dict) or data.get("schema") != CONSUMER_SCHEMA
            or not _good(data.get("consumer_key")) or not _good(data.get("consumer_secret"))):
        raise _unavailable("consumer_invalid")
    return data


def check_token(token, hatena_id) -> dict:
    if (not isinstance(token, dict) or token.get("schema") != TOKEN_SCHEMA
            or not _good(token.get("oauth_token")) or not _good(token.get("oauth_token_secret"))
            or token.get("hatena_id") != hatena_id):
        raise _unavailable("token_invalid")
    return token


def factory(account_cfg, principal_cfg):
    """`hatena_publish.make_adapter` の既定。作れなければ `AdapterUnavailable(code)`（出ない側）。"""
    from .. import accounts
    if (principal_cfg or {}).get("auth", "oauth") != "oauth":
        raise _unavailable("auth_unsupported")
    try:
        token = accounts.load_token(account_cfg)
    except (ValueError, OSError):
        # 読めない（権限・壊れたファイル・ディレクトリ）も「使えない」。timer の run を落とさない。
        raise _unavailable("token_invalid") from None
    if token is None:
        raise _unavailable("token_missing")
    check_token(token, principal_cfg.get("hatena_id"))
    consumer = read_consumer(principal_cfg.get("consumer"))
    return HatenaBlogAdapter(account_cfg, principal_cfg, token=token, consumer=consumer)


# --------------------------------------------------------------------------
# adapter
# --------------------------------------------------------------------------

def _user_agent(contact) -> str:
    from .. import __version__
    return f"thth/{__version__} (+{contact or DEFAULT_CONTACT})"


class HatenaBlogAdapter:
    """1 つのブログ（主体の hatena_id × 口座の blog_id）への口。"""

    def __init__(self, account_cfg, principal_cfg, *, token, consumer):
        self.hatena_id = principal_cfg["hatena_id"]
        self.blog_id = str(account_cfg["blog_id"]).strip().lower()
        self.contact = principal_cfg.get("user_agent_contact") or DEFAULT_CONTACT
        timeout = account_cfg.get("timeout")
        self.timeout = timeout if isinstance(timeout, (int, float)) and timeout > 0 else 300
        self._consumer_key = consumer["consumer_key"]
        self._consumer_secret = consumer["consumer_secret"]
        self._token = token["oauth_token"]
        self._token_secret = token["oauth_token_secret"]
        for value in (self._consumer_key, self._consumer_secret, self._token, self._token_secret):
            redact_mod.register_secret(value)

    # ----- URL ---------------------------------------------------------------

    @property
    def collection_url(self) -> str:
        return (f"{BASE}/{urllib.parse.quote(self.hatena_id, safe='')}/"
                f"{urllib.parse.quote(self.blog_id, safe='')}/atom/entry")

    def member_url(self, entry_id) -> str:
        """`entry_id` の末尾の数字（`tag:…-<数字>`）から個別の URL を組む。"""
        tail = str(entry_id or "").rsplit("-", 1)[-1]
        if not str(entry_id or "").startswith("tag:") or not tail.isdigit() or not tail.isascii():
            raise ValueError("entry_id_invalid")
        return f"{self.collection_url}/{tail}"

    def _member(self, entry_id, edit_uri):
        """組んだ URL と台帳の `edit_uri`（あれば）が同じか。違えば `edit_uri_mismatch`。"""
        url = self.member_url(entry_id)
        if edit_uri and edit_uri != url:
            raise ValueError("edit_uri_mismatch")
        return url

    # ----- HTTP ---------------------------------------------------------------

    def _scrub(self, text) -> str:
        out = str(text)
        for value in (self._token_secret, self._consumer_secret, self._token, self._consumer_key):
            if value:
                out = out.replace(value, "***")
        return redact_mod.redact(out) or ""

    def _request(self, method, url, *, body=None):
        """1 回だけ投げて `(status, headers, bytes)`。urllib の例外はそのまま上げる。"""
        from .. import oauth1
        headers = {
            "Authorization": oauth1.authorization_header(
                consumer_key=self._consumer_key, consumer_secret=self._consumer_secret,
                token=self._token, token_secret=self._token_secret, method=method, url=url),
            "Accept": "application/atom+xml",
            "User-Agent": _user_agent(self.contact),
        }
        if body is not None:
            headers["Content-Type"] = "application/atom+xml; charset=utf-8"
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        limit = READ_TIMEOUT if method == "GET" else WRITE_TIMEOUT
        with httpsafe.urlopen(req, timeout=min(self.timeout, limit)) as response:
            status = response.status
            response_headers = dict(response.headers.items()) if response.headers is not None else {}
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ResponseTooLarge("response_too_large")
        return status, response_headers, raw

    def _write(self, method, url, *, body=None, op):
        """書き込みを 1 回。`(status, raw)` か、失敗なら `EntryResult`（§3.5 の表）。"""
        from ..hatena_publish import EntryResult
        try:
            status, _headers, raw = self._request(method, url, body=body)
        except httpsafe.RedirectBlocked:
            # サーバが 3xx を返したあと＝要求は届いている。書き込みは「出たか分からない」
            # （inflight を残す・出し直さない）。接続の前の断り（EndpointRejected）とは分ける。
            return EntryResult(ok=False, ambiguous=True, error="redirect_blocked")
        except urllib.error.HTTPError as exc:
            return EntryResult(ok=False, ambiguous=exc.code >= 500, error=self._http_error(exc.code, op))
        except httpsafe.EndpointRejected:
            return EntryResult(ok=False, ambiguous=False, error="endpoint_rejected")
        except ResponseTooLarge:
            return EntryResult(ok=False, ambiguous=True, error="response_too_large")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
            return EntryResult(ok=False, ambiguous=True, error="hatena_transport_failed")
        return status, raw

    @staticmethod
    def _http_error(code, op) -> str:
        if code in (401, 403):
            return "hatena_auth_rejected"
        if code == 404 and op in ("update", "delete", "get"):
            return "hatena_entry_missing"
        if code == 429:
            return "provider_rate_limited"
        return f"hatena_http_{code}"

    # ----- 口 -----------------------------------------------------------------

    def get_entry(self, *, entry_id, edit_uri) -> str:
        """今の本文（`content` の文字列）。404 は `EntryMissing`、ほかの失敗は例外（`readback_failed`）。"""
        return self.read_entry(entry_id=entry_id, edit_uri=edit_uri)["content"]

    def read_entry(self, *, entry_id, edit_uri) -> dict:
        """1 本の記事を GET で読む（`parse_entry` の欄）。失敗は `get_entry` と同じ例外。"""
        from ..hatena_publish import EntryMissing, EntryRefused
        try:
            url = self._member(entry_id, edit_uri)
        except ValueError as exc:
            raise EntryRefused(str(exc)) from None
        try:
            status, _headers, raw = self._request("GET", url)
        except httpsafe.RedirectBlocked:
            raise RuntimeError("endpoint_rejected") from None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise EntryMissing("hatena_entry_missing") from None
            raise RuntimeError(self._http_error(exc.code, "get")) from None
        if status != 200:
            raise RuntimeError(f"hatena_http_{status}")
        return parse_entry(raw)

    def _accepted(self, status, raw, *, expect, op, entry_id=None):
        from ..hatena_publish import EntryResult
        from .. import postid
        if status != expect:
            return EntryResult(ok=False, ambiguous=True, error=f"hatena_http_{status}")
        try:
            fields = parse_entry(raw)
        except ResponseInvalid:
            return EntryResult(ok=False, ambiguous=True, error="response_invalid")
        got = fields["entry_id"]
        if (not got or not postid.is_usable(got) or not fields["edit_uri"]
                or (op == "create" and not fields["published_url"])
                or (entry_id is not None and got != entry_id)):
            return EntryResult(ok=False, ambiguous=True, error="response_invalid", entry_id=got)
        if fields["draft"] == "yes":
            return EntryResult(ok=False, ambiguous=True, error="hatena_draft_unexpected", entry_id=got,
                               edit_uri=fields["edit_uri"], published_url=fields["published_url"])
        return EntryResult(ok=True, entry_id=got, edit_uri=fields["edit_uri"],
                           published_url=fields["published_url"], readback_body=fields["content"],
                           content_type=fields["content_type"])

    def create_entry(self, *, title, body, categories, images):
        from ..hatena_publish import EntryResult
        if images:
            return EntryResult(ok=False, ambiguous=False, error="images_unsupported")
        xml = entry_xml(title=title, body=body, categories=categories, author=self.hatena_id)
        out = self._write("POST", self.collection_url, body=xml, op="create")
        if isinstance(out, EntryResult):
            return out
        return self._accepted(*out, expect=201, op="create")

    def update_entry(self, *, entry_id, edit_uri, title, body, categories, images):
        from ..hatena_publish import EntryResult
        if images:
            return EntryResult(ok=False, ambiguous=False, error="images_unsupported")
        try:
            url = self._member(entry_id, edit_uri)
        except ValueError as exc:
            return EntryResult(ok=False, ambiguous=False, error=str(exc))
        xml = entry_xml(title=title, body=body, categories=categories, author=self.hatena_id)
        out = self._write("PUT", url, body=xml, op="update")
        if isinstance(out, EntryResult):
            return out
        return self._accepted(*out, expect=200, op="update", entry_id=entry_id)

    def delete_entry(self, *, entry_id, edit_uri):
        from ..hatena_publish import EntryResult
        try:
            url = self._member(entry_id, edit_uri)
        except ValueError as exc:
            return EntryResult(ok=False, ambiguous=False, error=str(exc))
        out = self._write("DELETE", url, op="delete")
        if isinstance(out, EntryResult):
            return out
        status, _raw = out
        if status != 200:
            return EntryResult(ok=False, ambiguous=True, error=f"hatena_http_{status}")
        return EntryResult(ok=True, entry_id=entry_id)

    def _list(self):
        status, _headers, raw = self._request("GET", self.collection_url)
        if status != 200:
            raise RuntimeError(f"hatena_http_{status}")
        return parse_feed(raw)

    def blog_syntax(self) -> str | None:
        """最新の記事の `content` の `type`（記事が無ければ None）。失敗は例外（§5 の 2）。"""
        entries = self._list()["entries"]
        return entries[0]["content_type"] if entries else None

    def probe(self) -> dict:
        """一覧を 1 回読む（doctor・`principal auth` の段 7）。例外は上げない。

        `{"ok", "status", "entries", "syntax", "error"}`。`entries` は 1 頁目の件数（7 件まで）。
        """
        try:
            feed = self._list()
        except urllib.error.HTTPError as exc:
            return {"ok": False, "status": exc.code, "entries": None, "syntax": None,
                    "error": self._http_error(exc.code, "probe")}
        except ResponseInvalid:
            return {"ok": False, "status": 200, "entries": None, "syntax": None, "error": "response_invalid"}
        except ResponseTooLarge:
            return {"ok": False, "status": None, "entries": None, "syntax": None, "error": "response_too_large"}
        except (httpsafe.EndpointRejected, urllib.error.URLError, TimeoutError, OSError, ValueError,
                RuntimeError, http.client.HTTPException) as exc:
            return {"ok": False, "status": None, "entries": None, "syntax": None,
                    "error": self._scrub(exc.__class__.__name__)}
        entries = feed["entries"]
        return {"ok": True, "status": 200, "entries": len(entries),
                "syntax": entries[0]["content_type"] if entries else None, "error": None}
