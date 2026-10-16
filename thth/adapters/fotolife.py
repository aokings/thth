"""はてなフォトライフの AtomPub の口（設計 4.0 S3 §6）。

`base.Adapter` は継がない・`adapters.REGISTRY` にも載せない（`hatenablog` と同じ理由）。
`hatenablog.HatenaBlogAdapter` とは別のクラス（入口のホストと XML の名前空間が違う）。認証の材料と
署名した 1 回の HTTP は `hatena_common` を共有する。

- URL は `https://f.hatena.ne.jp` 固定（`BASE`）。公式の例の `http://` は使わない（平文は https に
  302 で飛ばされ、`httpsafe` は平文を断る）。`/atom/post`・`/atom/edit/<id>`・`/atom/feed` だけ。
- 口は 3 つ（`upload`・`get_image`・`recent`）と doctor の `probe`。**DELETE・PUT は作らない**
  （4.0 では消す口を作らない・§6.1・§8）。
- 送る XML は公式の例のまま Atom 0.3（`http://purl.org/atom/ns#`）を文字列で組む（ET の既定の
  名前空間の登録が Atom 1.0 とぶつからないように）。
- 応答は名前空間を決め打ちせず local name で読む（公式の例の閉じタグの綴りの誤り・0.3／1.0 の
  どちらで返るか分からないことへの備え・§6.3）。
- 断り（4xx）は「出ていない」、5xx・時間切れ・読めない応答は「分からない」（§6.4 の表）。
  **再試行しない・sleep しない。**
"""
from __future__ import annotations

import base64
import dataclasses
import http.client
import re
import urllib.error
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from .. import httpsafe
from . import hatena_common

BASE = "https://f.hatena.ne.jp"
ATOM03 = "http://purl.org/atom/ns#"
DC = "http://purl.org/dc/elements/1.1/"
GENERATOR = "thth"
READ_TIMEOUT = 30
UPLOAD_TIMEOUT = 120
FOTOLIFE_ID_RE = re.compile(r"[0-9]{1,20}")
# 送る `Content-Type`（推測・公式に記載なし。実機の §15 の 14 で確かめる）。
CONTENT_TYPE = "application/atom+xml; charset=utf-8"


class ImageMissing(Exception):
    """`get_image` が 404（人がフォトライフの画面で消した）。"""


class CheckFailed(Exception):
    """確かめの GET（`get_image`・`recent`）が失敗した（符丁は `str(exc)`）。在るかもしれない。"""


@dataclasses.dataclass
class UploadResult:
    """`upload` の 1 回の答え。`ok` が偽で `ambiguous` が真なら「上がったか分からない」。"""

    ok: bool
    ambiguous: bool = False
    fotolife_id: str | None = None
    syntax: str | None = None
    image_url: str | None = None
    edit_uri: str | None = None
    error: str | None = None
    status: int | None = None


# --------------------------------------------------------------------------
# XML
# --------------------------------------------------------------------------

def _has_control(value: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 or c in ("\x1e", "\x1f") for c in value)


def post_xml(*, data: bytes, content_type: str, title: str, folder: str) -> bytes:
    """送る XML（§6.2）。題・フォルダに制御文字があれば `ValueError("fotolife_title_invalid")`。"""
    for value in (title, folder, content_type):
        if not isinstance(value, str) or not value or _has_control(value):
            raise ValueError("fotolife_title_invalid")
    encoded = base64.b64encode(data).decode("ascii")
    text = ('<?xml version="1.0" encoding="utf-8"?>\n'
            f'<entry xmlns="{ATOM03}" xmlns:dc="{DC}">'
            f"<title>{escape(title)}</title>"
            f'<content mode="base64" type="{escape(content_type, {chr(34): "&quot;"})}">{encoded}</content>'
            f"<dc:subject>{escape(folder)}</dc:subject>"
            f"<generator>{GENERATOR}</generator>"
            "</entry>")
    return text.encode("utf-8")


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(node, name):
    for child in list(node):
        if _local(child.tag) == name:
            return child
    return None


def _children(node, name):
    return [child for child in list(node) if _local(child.tag) == name]


def _text(node):
    return node.text.strip() if node is not None and node.text is not None and node.text.strip() else None


def _parse_root(raw, expected):
    if not isinstance(raw, (bytes, bytearray)):
        raise hatena_common.ResponseInvalid("response_invalid")
    lowered = bytes(raw).lower()
    if b"<!doctype" in lowered[:4096] or b"<!entity" in lowered:
        raise hatena_common.ResponseInvalid("response_invalid")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise hatena_common.ResponseInvalid("response_invalid") from None
    if _local(root.tag) != expected:
        raise hatena_common.ResponseInvalid("response_invalid")
    return root


def _tail_id(url) -> str | None:
    if not isinstance(url, str):
        return None
    match = re.search(r"/atom/edit/([0-9]{1,20})/?$", url.strip())
    return match.group(1) if match else None


def _photo_fields(node, *, location=None) -> dict:
    links = _children(node, "link")
    edit = next((l.get("href") for l in links if l.get("rel") == "service.edit"), None)
    alternate = next((l.get("href") for l in links if l.get("rel") == "alternate"), None)
    fotolife_id = _tail_id(edit) or _tail_id(location)
    return {"id": _text(_child(node, "id")), "fotolife_id": fotolife_id,
            "edit_uri": edit or (location if _tail_id(location) else None),
            "syntax": _text(_child(node, "syntax")), "image_url": _text(_child(node, "imageurl")),
            "page_url": alternate, "title": _text(_child(node, "title"))}


def parse_photo(raw: bytes, *, location: str | None = None) -> dict:
    """1 枚の応答（§6.3）。`fotolife_id`（`service.edit` の末尾か `Location` の末尾）と `syntax` が
    無ければ・DOCTYPE があれば・読めなければ `ResponseInvalid("response_invalid")`。"""
    fields = _photo_fields(_parse_root(raw, "entry"), location=location)
    if not fields["fotolife_id"] or not fields["syntax"]:
        raise hatena_common.ResponseInvalid("response_invalid")
    return fields


def parse_feed(raw: bytes) -> dict:
    """一覧（`/atom/feed`）。`entries`（読めた写真）と `next`（次の頁の URL・無ければ None）。"""
    root = _parse_root(raw, "feed")
    nxt = next((l.get("href") for l in _children(root, "link") if l.get("rel") == "next"), None)
    return {"entries": [_photo_fields(e) for e in _children(root, "entry")], "next": nxt}


def syntax_user(syntax) -> str | None:
    """`f:id:<user>:<ID'>:image` の `<user>`（形が違えば None）。"""
    parts = str(syntax or "").split(":")
    if len(parts) < 4 or parts[0] != "f" or parts[1] != "id" or not parts[2] or not parts[3]:
        return None
    return parts[2]


# --------------------------------------------------------------------------
# adapter
# --------------------------------------------------------------------------

def factory(account_cfg, principal_cfg):
    """`hatena_fotolife.make_adapter` の既定。作れなければ `AdapterUnavailable(code)`（出ない側）。"""
    token, consumer = hatena_common.load_credentials(account_cfg, principal_cfg)
    return FotolifeAdapter(account_cfg, principal_cfg, token=token, consumer=consumer)


class FotolifeAdapter:
    """1 つの主体（はてな ID）のフォトライフへの口。"""

    def __init__(self, account_cfg, principal_cfg, *, token, consumer):
        self.hatena_id = principal_cfg["hatena_id"]
        self.contact = principal_cfg.get("user_agent_contact") or hatena_common.DEFAULT_CONTACT
        timeout = (account_cfg or {}).get("timeout")
        self.timeout = timeout if isinstance(timeout, (int, float)) and timeout > 0 else 300
        self._consumer = consumer
        self._token = token

    # ----- URL ---------------------------------------------------------------

    @property
    def post_url(self) -> str:
        return f"{BASE}/atom/post"

    @property
    def feed_url(self) -> str:
        return f"{BASE}/atom/feed"

    def edit_url(self, fotolife_id) -> str:
        if not isinstance(fotolife_id, str) or not FOTOLIFE_ID_RE.fullmatch(fotolife_id):
            raise ValueError("fotolife_id_invalid")
        return f"{BASE}/atom/edit/{fotolife_id}"

    # ----- HTTP ---------------------------------------------------------------

    def _scrub(self, text) -> str:
        return hatena_common.scrub(text, self._token.get("oauth_token_secret"),
                                   self._consumer.get("consumer_secret"), self._token.get("oauth_token"),
                                   self._consumer.get("consumer_key"))

    def _request(self, method, url, *, body=None, timeout):
        headers = {"Accept": "application/atom+xml, application/xml",
                   "User-Agent": hatena_common.user_agent(self.contact)}
        if body is not None:
            headers["Content-Type"] = CONTENT_TYPE
        return hatena_common.request_signed(method, url, headers=headers, body=body, timeout=timeout,
                                            consumer=self._consumer, token=self._token)

    @property
    def upload_timeout(self):
        return min(self.timeout, UPLOAD_TIMEOUT)

    @property
    def read_timeout(self):
        return min(self.timeout, READ_TIMEOUT)

    @staticmethod
    def _http_error(code) -> tuple:
        """`(error, ambiguous)`（§6.4 の表）。"""
        if code in (400, 415):
            return f"fotolife_rejected_{code}", False
        if code in (401, 403):
            return "hatena_auth_rejected", False
        if code == 413:
            return "fotolife_too_large", False
        if code == 429:
            return "provider_rate_limited", False
        if code >= 500:
            return f"fotolife_http_{code}", True
        return f"fotolife_http_{code}", False

    # ----- 口 -----------------------------------------------------------------

    def upload(self, *, data: bytes, content_type: str, title: str, folder: str) -> UploadResult:
        """POST `/atom/post` を 1 回（§6.2〜§6.4）。`syntax` の user が `hatena_id` と違えば
        `fotolife_user_mismatch`（出ていない扱い）。"""
        try:
            xml = post_xml(data=data, content_type=content_type, title=title, folder=folder)
        except ValueError as exc:
            return UploadResult(ok=False, ambiguous=False, error=str(exc))
        try:
            status, headers, raw = self._request("POST", self.post_url, body=xml, timeout=self.upload_timeout)
        except httpsafe.RedirectBlocked:
            return UploadResult(ok=False, ambiguous=True, error="redirect_blocked")
        except urllib.error.HTTPError as exc:
            error, ambiguous = self._http_error(exc.code)
            return UploadResult(ok=False, ambiguous=ambiguous, error=error, status=exc.code)
        except httpsafe.EndpointRejected:
            return UploadResult(ok=False, ambiguous=False, error="endpoint_rejected")
        except hatena_common.ResponseTooLarge:
            return UploadResult(ok=False, ambiguous=True, error="response_too_large")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
            return UploadResult(ok=False, ambiguous=True, error="fotolife_transport_failed")
        if status != 201:
            return UploadResult(ok=False, ambiguous=True, error=f"fotolife_http_{status}", status=status)
        location = next((v for k, v in (headers or {}).items() if str(k).lower() == "location"), None)
        try:
            fields = parse_photo(raw, location=location)
        except hatena_common.ResponseInvalid:
            return UploadResult(ok=False, ambiguous=True, error="response_invalid", status=status)
        if syntax_user(fields["syntax"]) != self.hatena_id:
            return UploadResult(ok=False, ambiguous=False, error="fotolife_user_mismatch", status=status)
        return UploadResult(ok=True, fotolife_id=fields["fotolife_id"], syntax=fields["syntax"],
                            image_url=fields["image_url"], edit_uri=self.edit_url(fields["fotolife_id"]),
                            status=status)

    def get_image(self, *, fotolife_id: str, edit_uri: str | None = None) -> dict:
        """GET `/atom/edit/<id>`（§4.3）。200 なら写真の欄。404 → `ImageMissing`、ほか → `CheckFailed`。

        台帳の `edit_uri` は組んだ URL と比べるだけ（違えば `CheckFailed("edit_uri_mismatch")`）。
        """
        try:
            url = self.edit_url(fotolife_id)
        except ValueError as exc:
            raise CheckFailed(str(exc)) from None
        if edit_uri and edit_uri != url:
            raise CheckFailed("edit_uri_mismatch")
        try:
            status, _headers, raw = self._request("GET", url, timeout=self.read_timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ImageMissing("fotolife_image_missing") from None
            raise CheckFailed(self._http_error(exc.code)[0]) from None
        except (httpsafe.RedirectBlocked, httpsafe.EndpointRejected, hatena_common.ResponseTooLarge,
                urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
            raise CheckFailed(self._scrub(exc.__class__.__name__)) from None
        if status != 200:
            raise CheckFailed(f"fotolife_http_{status}")
        try:
            fields = _photo_fields(_parse_root(raw, "entry"))
        except hatena_common.ResponseInvalid:
            raise CheckFailed("response_invalid") from None
        return fields

    def recent(self, *, pages: int = 1) -> list:
        """`/atom/feed` を `pages` 頁まで読む（§7.2 の照合）。失敗は `CheckFailed`。"""
        out, url = [], self.feed_url
        for _ in range(max(1, int(pages))):
            try:
                status, _headers, raw = self._request("GET", url, timeout=self.read_timeout)
            except urllib.error.HTTPError as exc:
                raise CheckFailed(self._http_error(exc.code)[0]) from None
            except (httpsafe.RedirectBlocked, httpsafe.EndpointRejected, hatena_common.ResponseTooLarge,
                    urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
                raise CheckFailed(self._scrub(exc.__class__.__name__)) from None
            if status != 200:
                raise CheckFailed(f"fotolife_http_{status}")
            try:
                feed = parse_feed(raw)
            except hatena_common.ResponseInvalid:
                raise CheckFailed("response_invalid") from None
            out += feed["entries"]
            nxt = feed["next"]
            if not nxt or not str(nxt).startswith(BASE + "/"):
                break
            url = nxt
        return out

    def probe(self) -> dict:
        """一覧を 1 回読む（doctor・実機の §15 の 14）。例外は上げない。`{"ok", "status", "entries", "error"}`。"""
        try:
            status, _headers, raw = self._request("GET", self.feed_url, timeout=self.read_timeout)
            if status != 200:
                return {"ok": False, "status": status, "entries": None, "error": f"fotolife_http_{status}"}
            feed = parse_feed(raw)
        except urllib.error.HTTPError as exc:
            return {"ok": False, "status": exc.code, "entries": None, "error": self._http_error(exc.code)[0]}
        except hatena_common.ResponseInvalid:
            return {"ok": False, "status": 200, "entries": None, "error": "response_invalid"}
        except (httpsafe.RedirectBlocked, httpsafe.EndpointRejected, hatena_common.ResponseTooLarge,
                urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
            return {"ok": False, "status": None, "entries": None, "error": self._scrub(exc.__class__.__name__)}
        return {"ok": True, "status": 200, "entries": len(feed["entries"]), "error": None}
