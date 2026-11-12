"""WordPress（自分で置く型）へ記事を出す口（5.16.0・設計 ショップとWordPressの口 §4）。

はてなブログの adapter（`hatenablog.py`）と同じ口を持ち、配信の道（`hatena_publish`）は媒体名で分岐しない:

    adapter.get_entry(*, entry_id, edit_uri) -> str
    adapter.read_entry(*, entry_id, edit_uri) -> dict
    adapter.create_entry(*, title, body, categories, images, send_as="publish") -> EntryResult
    adapter.update_entry(*, entry_id, edit_uri, title, body, categories, images) -> EntryResult
    adapter.whoami() -> dict

- 入口は REST `<site>/wp-json/wp/v2/posts`。`<site>` は主体のブログの行の `site_url`（無ければ
  `https://<blog_id>`）**だけ**から組む（原稿にも依頼にも URL を書く欄は無い）。平文の HTTP は `httpsafe` の
  柵（`THTH_TEST_ALLOW_HTTP=1` かつ loopback）の内だけ（ここで柵は緩めない）。
- 認証は**アプリケーションパスワード**（WordPress 5.6 から標準・Basic）。鍵は主体の `token` のファイル
  （0600・`thth principal auth <主体> --by <名前>` が標準入力か隠した入力で受け取る）。引数では受けない。
- 本文は承認の前に `thth/wp_html.py` で HTML にしてある（変換したあとの sha が指紋に入る）。ここは送るだけ。
- 読み戻しは `context=edit` の `content.raw`（`rendered` は WordPress が加工する）。
- カテゴリは原稿の `categories:` の名前。`#` で始まる名前は**タグ**。どちらも WordPress の側に**ある名前だけ**
  使う（無ければ送らずに断る `wordpress_term_unknown`。サイトの用語を勝手に増やさない）。
- この版は 作る・直す・下書きと公開 まで。予約（`status=future`）・消す・アイキャッチ・WordPress.com（OAuth2）は
  後の版（原稿の lint が `send_as_unsupported`・`op_unsupported_for_media` で先に断る）。
"""
from __future__ import annotations

import base64
import html
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

from .. import httpsafe
from .. import redact as redact_mod

TOKEN_SCHEMA = "thth.wordpress-app-password/1"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
READ_TIMEOUT = 30
WRITE_TIMEOUT = 60
DEFAULT_CONTACT = "https://thth.me/"
REST_PATH = "/wp-json/wp/v2"
TAG_PREFIX = "#"
SEND_AS = ("publish", "draft")
# 鍵の利用者の役割のうち、強すぎるもの（断らずに、編集者の利用者を作ってその鍵にするよう勧める）。
STRONG_ROLES = ("administrator",)

# WordPress の誤りの `code` → THTH の符丁（設計の直し 8: 分けて返す）。
_AUTH_DROPPED = ("rest_not_logged_in",)                       # 鍵を送ったのに「ログインしていない」
_AUTH_WRONG = ("incorrect_password", "invalid_username", "invalid_email", "rest_authentication_error")
_APP_PASSWORDS_OFF = ("application_passwords_disabled", "application_passwords_disabled_for_user")
_NO_PERMISSION = ("rest_cannot_create", "rest_cannot_edit", "rest_cannot_publish", "rest_forbidden",
                  "rest_cannot_assign_term", "rest_cannot_edit_others", "rest_cannot_view")
_REST_CLOSED = ("rest_no_route", "rest_disabled", "rest_api_disabled")
# セキュリティの部品が REST を閉じるときによく返す code（WordPress 本体の code ではない）。
_SECURITY = ("rest_cannot_access", "rest_login_required", "wordfence_blocked", "itsec_rest_api_restricted")


# この口が返す断りの符丁（断りの目録 `refusal_catalog_part_wp` に 3 つの文がある）。主体の台帳の `site_url_invalid`
# も WordPress だけの断りなので並べる。
WP_REFUSALS = frozenset((
    "wordpress_rest_unavailable", "wordpress_auth_header_dropped", "wordpress_auth_rejected",
    "wordpress_app_passwords_disabled", "wordpress_permission_missing", "wordpress_blocked_by_security",
    "wordpress_https_required", "wordpress_term_unknown", "wordpress_term_lookup_failed",
    "wordpress_status_unexpected", "wordpress_transport_failed", "wp_user_mismatch", "site_url_invalid",
))


class ResponseTooLarge(Exception):
    pass


class WordPressError(RuntimeError):
    """読む口の失敗（`str(exc)` は静的な符丁）。"""


def _unavailable(code):
    from ..hatena_publish import AdapterUnavailable
    return AdapterUnavailable(code)


def good_password(value) -> bool:
    """アプリケーションパスワードの形（英数 24 字を 4 字ずつ空白で区切った形・空白は無くてもよい）。"""
    if not isinstance(value, str):
        return False
    compact = value.replace(" ", "")
    return 16 <= len(compact) <= 64 and compact.isascii() and compact.isalnum()


def check_token(token, wp_user) -> dict:
    """鍵のファイルの形（`schema`・`wp_user`・`application_password`）。外れれば `token_invalid`。"""
    if (not isinstance(token, dict) or token.get("schema") != TOKEN_SCHEMA
            or token.get("wp_user") != wp_user or not good_password(token.get("application_password"))):
        raise _unavailable("token_invalid")
    return token


def load_credentials(account_cfg, principal_cfg) -> dict:
    from .. import accounts
    if (principal_cfg or {}).get("auth", "app_password") != "app_password":
        raise _unavailable("auth_unsupported")
    try:
        token = accounts.load_token(account_cfg)
    except (ValueError, OSError):
        raise _unavailable("token_invalid") from None
    if token is None:
        raise _unavailable("token_missing")
    check_token(token, principal_cfg.get("wp_user"))
    redact_mod.register_secret(token["application_password"])
    redact_mod.register_secret(token["application_password"].replace(" ", ""))
    return token


def factory(account_cfg, principal_cfg):
    """`hatena_publish.make_adapter` の既定（媒体 wordpress）。作れなければ `AdapterUnavailable(code)`。"""
    token = load_credentials(account_cfg, principal_cfg)
    return WordPressAdapter(account_cfg, principal_cfg, token=token)


def _user_agent(contact) -> str:
    from .. import __version__
    return f"thth/{__version__} (+{contact or DEFAULT_CONTACT})"


def _raw(field):
    """`{"raw": …, "rendered": …}` の raw（`context=edit`）。無ければ None（rendered は比べない）。"""
    if isinstance(field, dict):
        value = field.get("raw")
        return value if isinstance(value, str) else None
    return field if isinstance(field, str) else None


def classify(status, body: bytes | None, *, op: str) -> str:
    """HTTP の失敗を THTH の符丁に（REST が閉じている・鍵が落とされる・鍵が違う・権限・セキュリティの部品）。"""
    from ..hatena_publish import ENTRY_MISSING
    code = None
    is_json = False
    try:
        data = json.loads((body or b"").decode("utf-8"))
        if isinstance(data, dict):
            is_json = True
            code = data.get("code") if isinstance(data.get("code"), str) else None
    except (UnicodeDecodeError, ValueError):
        pass
    if code in _AUTH_DROPPED:
        return "wordpress_auth_header_dropped"
    if code in _AUTH_WRONG:
        return "wordpress_auth_rejected"
    if code in _APP_PASSWORDS_OFF:
        return "wordpress_app_passwords_disabled"
    if code in _SECURITY:
        return "wordpress_blocked_by_security"
    if code in _NO_PERMISSION:
        return "wordpress_permission_missing"
    if code in _REST_CLOSED:
        return "wordpress_rest_unavailable"
    if status in (404, 410) and code in ("rest_post_invalid_id", "rest_already_trashed") and op in (
            "update", "get"):
        return ENTRY_MISSING
    if status == 404 and not is_json:
        # WordPress の JSON でない 404: REST の入口が無い（パーマリンクが「基本」・入口を閉じている）。
        return "wordpress_rest_unavailable"
    if status in (401, 403) and not is_json:
        # JSON でない断り: 前に立つ WAF・セキュリティの部品が止めている。
        return "wordpress_blocked_by_security"
    if status in (401, 403):
        return "wordpress_auth_rejected" if status == 401 else "wordpress_permission_missing"
    if status == 429:
        return "provider_rate_limited"
    return f"wordpress_http_{status}"


def _endpoint_code(exc) -> str:
    text = str(exc)
    return "wordpress_https_required" if text.startswith("endpoint_http_forbidden") else "endpoint_rejected"


class WordPressAdapter:
    """1 つのサイト（主体の wp_user × 口座の blog_id）への口。"""

    def __init__(self, account_cfg, principal_cfg, *, token):
        from .. import principals
        self.wp_user = principal_cfg["wp_user"]
        self.blog_id = str(account_cfg["blog_id"]).strip().lower()
        self.site = principals.site_url(principal_cfg, self.blog_id)
        self.contact = principal_cfg.get("user_agent_contact") or DEFAULT_CONTACT
        timeout = account_cfg.get("timeout")
        self.timeout = timeout if isinstance(timeout, (int, float)) and timeout > 0 else 300
        password = token["application_password"]
        self._auth = "Basic " + base64.b64encode(f"{self.wp_user}:{password}".encode("utf-8")).decode("ascii")
        redact_mod.register_secret(self._auth.split(" ", 1)[1])
        self._terms = {}   # (kind, name) -> id／(kind, id) -> name

    # ----- URL ---------------------------------------------------------------

    @property
    def collection_url(self) -> str:
        return f"{self.site}{REST_PATH}/posts"

    def member_url(self, entry_id) -> str:
        text = str(entry_id or "")
        if not text.isdigit() or not text.isascii() or len(text) > 19 or text.startswith("0"):
            raise ValueError("entry_id_invalid")
        return f"{self.collection_url}/{text}"

    def _member(self, entry_id, edit_uri):
        url = self.member_url(entry_id)
        if edit_uri and edit_uri != url:
            raise ValueError("edit_uri_mismatch")
        return url

    # ----- HTTP ---------------------------------------------------------------

    def _request(self, method, url, *, payload=None):
        """1 回だけ投げて `(status, 応答の JSON)`。urllib の例外はそのまま上げる。"""
        headers = {"Accept": "application/json", "User-Agent": _user_agent(self.contact),
                   "Authorization": self._auth}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        limit = READ_TIMEOUT if method == "GET" else WRITE_TIMEOUT
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with httpsafe.urlopen(req, timeout=min(self.timeout, limit)) as response:
            status = response.status
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ResponseTooLarge("response_too_large")
        try:
            return status, json.loads(raw.decode("utf-8")) if raw.strip() else None
        except (UnicodeDecodeError, ValueError):
            return status, None

    @staticmethod
    def _error_body(exc) -> bytes:
        try:
            return exc.read(64 * 1024) or b""
        except Exception:  # noqa: BLE001
            return b""

    def _write(self, method, url, *, payload=None, op):
        from ..hatena_publish import EntryResult
        try:
            return self._request(method, url, payload=payload)
        except httpsafe.RedirectBlocked:
            return EntryResult(ok=False, ambiguous=True, error="redirect_blocked")
        except urllib.error.HTTPError as exc:
            return EntryResult(ok=False, ambiguous=exc.code >= 500,
                               error=classify(exc.code, self._error_body(exc), op=op))
        except httpsafe.EndpointRejected as exc:
            return EntryResult(ok=False, ambiguous=False, error=_endpoint_code(exc))
        except ResponseTooLarge:
            return EntryResult(ok=False, ambiguous=True, error="response_too_large")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
            return EntryResult(ok=False, ambiguous=True, error="wordpress_transport_failed")

    def _get(self, url, *, op="get"):
        """読む GET（失敗は例外）。消えた記事は `EntryMissing`、ほかは `WordPressError(符丁)`。"""
        from ..hatena_publish import ENTRY_MISSING, EntryMissing
        try:
            status, data = self._request("GET", url)
        except httpsafe.RedirectBlocked:
            raise WordPressError("redirect_blocked") from None
        except httpsafe.EndpointRejected as exc:
            raise WordPressError(_endpoint_code(exc)) from None
        except urllib.error.HTTPError as exc:
            code = classify(exc.code, self._error_body(exc), op=op)
            if code == ENTRY_MISSING:
                raise EntryMissing(ENTRY_MISSING) from None
            raise WordPressError(code) from None
        except ResponseTooLarge:
            raise WordPressError("response_too_large") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
            raise WordPressError("wordpress_transport_failed") from None
        if status != 200:
            raise WordPressError(f"wordpress_http_{status}")
        if data is None:
            # 200 でも JSON でない（REST の入口が HTML の頁に化けている）。
            raise WordPressError("wordpress_rest_unavailable")
        return data

    # ----- カテゴリとタグ -------------------------------------------------------

    @staticmethod
    def split_terms(categories) -> tuple:
        """原稿の `categories` を (カテゴリの名前, タグの名前) に分ける（`#` で始まればタグ）。"""
        cats, tags = [], []
        for name in categories or []:
            text = str(name).strip()
            if text.startswith(TAG_PREFIX) and text[len(TAG_PREFIX):].strip():
                tags.append(text[len(TAG_PREFIX):].strip())
            elif text:
                cats.append(text)
        return cats, tags

    def _term_id(self, kind, name):
        key = (kind, name)
        if key in self._terms:
            return self._terms[key]
        query = urllib.parse.urlencode({"search": name, "per_page": 100, "context": "view"})
        rows = self._get(f"{self.site}{REST_PATH}/{kind}?{query}")
        for row in rows if isinstance(rows, list) else []:
            if (isinstance(row, dict) and isinstance(row.get("id"), int)
                    and html.unescape(str(row.get("name") or "")) == name):
                self._terms[key] = row["id"]
                self._terms[(kind, row["id"])] = name
                return row["id"]
        return None

    def _resolve_terms(self, categories):
        """`(payload の欄, 断りの符丁 or None)`。無い名前は `wordpress_term_unknown`（送らない）。"""
        cats, tags = self.split_terms(categories)
        fields = {}
        try:
            for kind, names in (("categories", cats), ("tags", tags)):
                if names:
                    ids = [self._term_id(kind, name) for name in names]
                    if None in ids:
                        return None, "wordpress_term_unknown"
                    fields[kind] = ids
        except WordPressError as exc:
            return None, str(exc)
        except Exception:  # noqa: BLE001 — 名前を確かめられなければ送らない（出ない側）
            return None, "wordpress_term_lookup_failed"
        return fields, None

    def _term_names(self, kind, ids) -> list | None:
        """応答の用語の id から名前（分からない id があれば None＝比べない）。"""
        if not isinstance(ids, list):
            return None
        missing = [i for i in ids if (kind, i) not in self._terms]
        if missing:
            query = urllib.parse.urlencode({"include": ",".join(str(i) for i in missing), "per_page": 100,
                                            "context": "view"})
            try:
                rows = self._get(f"{self.site}{REST_PATH}/{kind}?{query}")
            except Exception:  # noqa: BLE001
                return None
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and isinstance(row.get("id"), int):
                    name = html.unescape(str(row.get("name") or ""))
                    self._terms[(kind, row["id"])] = name
                    self._terms[(kind, name)] = row["id"]
        out = []
        for i in ids:
            name = self._terms.get((kind, i))
            if name is None:
                return None
            out.append(name)
        return out

    def _categories_of(self, data, sent=None) -> list | None:
        cats = self._term_names("categories", data.get("categories"))
        tags = self._term_names("tags", data.get("tags", []))
        if cats is None or tags is None:
            return None
        # カテゴリを付けずに出すと WordPress は既定のカテゴリ（未分類）を付ける。それも含めて返す——更新の前の
        # 比べ先は、作ったときに WordPress が返した形（台帳の readback_categories）なので、差にならない。
        return cats + [f"{TAG_PREFIX}{t}" for t in tags]

    # ----- 応答 ---------------------------------------------------------------

    def _fields(self, data, sent=None) -> dict:
        entry_id = str(data.get("id")) if isinstance(data.get("id"), int) else None
        return {"entry_id": entry_id, "edit_uri": self.member_url(entry_id) if entry_id else None,
                "published_url": data.get("link") if isinstance(data.get("link"), str) else None,
                "content": _raw(data.get("content")), "title": _raw(data.get("title")),
                "categories": self._categories_of(data, sent), "content_type": None,
                "status": data.get("status"), "edited": data.get("modified_gmt"),
                "published": (data["date_gmt"] + "+00:00") if isinstance(data.get("date_gmt"), str) else None}

    def _accepted(self, out, *, expect, op, entry_id=None, statuses=None, sent=None):
        from ..hatena_publish import EntryResult
        from .. import postid
        if isinstance(out, EntryResult):
            return out
        status, data = out
        if status != expect:
            return EntryResult(ok=False, ambiguous=True, error=f"wordpress_http_{status}")
        if not isinstance(data, dict):
            return EntryResult(ok=False, ambiguous=True, error="response_invalid")
        try:
            fields = self._fields(data, sent)
        except ValueError:
            return EntryResult(ok=False, ambiguous=True, error="response_invalid")
        got = fields["entry_id"]
        if (not got or not postid.is_usable(got) or (op == "create" and not fields["published_url"])
                or (entry_id is not None and got != entry_id) or fields["content"] is None):
            return EntryResult(ok=False, ambiguous=True, error="response_invalid", entry_id=got)
        if statuses is not None and fields["status"] not in statuses:
            # 出たが、頼んだ状態でない（例: 公開の権限が無く pending になった）。人が見るまで出し直さない。
            return EntryResult(ok=False, ambiguous=True, error="wordpress_status_unexpected", entry_id=got,
                               edit_uri=fields["edit_uri"], published_url=fields["published_url"])
        return EntryResult(ok=True, entry_id=got, edit_uri=fields["edit_uri"],
                           published_url=fields["published_url"], readback_body=fields["content"],
                           content_type=None, readback_title=fields["title"],
                           readback_categories=fields["categories"])

    # ----- 口 -----------------------------------------------------------------

    def get_entry(self, *, entry_id, edit_uri) -> str:
        return self.read_entry(entry_id=entry_id, edit_uri=edit_uri)["content"]

    def read_entry(self, *, entry_id, edit_uri) -> dict:
        from ..hatena_publish import ENTRY_MISSING, EntryMissing, EntryRefused
        try:
            url = self._member(entry_id, edit_uri)
        except ValueError as exc:
            raise EntryRefused(str(exc)) from None
        data = self._get(f"{url}?context=edit")
        if not isinstance(data, dict):
            raise WordPressError("response_invalid")
        fields = self._fields(data)
        if fields["status"] == "trash":
            raise EntryMissing(ENTRY_MISSING)
        if fields["content"] is None:
            raise WordPressError("response_invalid")   # context=edit の raw が無い＝比べられない
        return fields

    def _payload(self, title, body, categories):
        terms, problem = self._resolve_terms(categories)
        if problem:
            return None, problem
        payload = {"title": title, "content": body}
        payload.update(terms)
        return payload, None

    def create_entry(self, *, title, body, categories, images, send_as="publish"):
        from ..hatena_publish import EntryResult
        if images:
            return EntryResult(ok=False, ambiguous=False, error="wp_image_unsupported")
        if send_as not in SEND_AS:
            return EntryResult(ok=False, ambiguous=False, error="send_as_unsupported")
        payload, problem = self._payload(title, body, categories)
        if problem:
            return EntryResult(ok=False, ambiguous=False, error=problem)
        payload["status"] = send_as
        out = self._write("POST", f"{self.collection_url}?context=edit", payload=payload, op="create")
        return self._accepted(out, expect=201, op="create", statuses=(send_as,), sent=categories)

    def update_entry(self, *, entry_id, edit_uri, title, body, categories, images):
        from ..hatena_publish import EntryResult
        if images:
            return EntryResult(ok=False, ambiguous=False, error="wp_image_unsupported")
        try:
            url = self._member(entry_id, edit_uri)
        except ValueError as exc:
            return EntryResult(ok=False, ambiguous=False, error=str(exc))
        payload, problem = self._payload(title, body, categories)
        if problem:
            return EntryResult(ok=False, ambiguous=False, error=problem)
        # 状態（公開・下書き）は変えない（`status` を送らない）。
        out = self._write("POST", f"{url}?context=edit", payload=payload, op="update")
        return self._accepted(out, expect=200, op="update", entry_id=entry_id, sent=categories)

    def delete_entry(self, *, entry_id, edit_uri):
        """この版では消さない（原稿の lint が `op_unsupported_for_media` で先に断る）。送らずに断る。"""
        from ..hatena_publish import EntryResult
        return EntryResult(ok=False, ambiguous=False, error="op_unsupported_for_media")

    def _list(self):
        """最近の記事（結果不明の照合の候補・`hatena_publish.candidates`）。"""
        query = urllib.parse.urlencode({"context": "edit", "per_page": 20, "orderby": "date",
                                        "status": "publish,draft,pending,private"})
        rows = self._get(f"{self.collection_url}?{query}")
        entries = []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and isinstance(row.get("id"), int):
                entries.append({"entry_id": str(row["id"]), "title": _raw(row.get("title")),
                                "published": (row["date_gmt"] + "+00:00") if isinstance(row.get("date_gmt"), str)
                                else None,
                                "edited": row.get("modified_gmt"), "content": _raw(row.get("content"))})
        return {"entries": entries}

    def whoami(self) -> dict:
        """鍵で自分を 1 回読む（`/wp/v2/users/me?context=edit`）。例外は上げない。鍵の値は出さない。

        `{"ok", "user", "roles", "strong", "error"}`。`strong` は管理者の鍵（断らずに、編集者の利用者を作って
        その鍵にするよう勧める）。
        """
        try:
            data = self._get(f"{self.site}{REST_PATH}/users/me?context=edit")
        except WordPressError as exc:
            return {"ok": False, "user": None, "roles": None, "strong": None, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "user": None, "roles": None, "strong": None,
                    "error": redact_mod.redact(exc.__class__.__name__)}
        name = data.get("username") if isinstance(data, dict) else None
        roles = [r for r in (data.get("roles") or []) if isinstance(r, str)] if isinstance(data, dict) else []
        if name != self.wp_user:
            return {"ok": False, "user": None, "roles": roles, "strong": None, "error": "wp_user_mismatch"}
        return {"ok": True, "user": name, "roles": roles, "strong": any(r in STRONG_ROLES for r in roles),
                "error": None}

    def probe(self) -> dict:
        """doctor の形（`{"ok", "status", "entries", "syntax", "error"}`）で `whoami` を返す。"""
        who = self.whoami()
        return {"ok": who["ok"], "status": 200 if who["ok"] else None, "entries": None, "syntax": None,
                "error": who["error"], "roles": who["roles"], "strong": who["strong"]}
