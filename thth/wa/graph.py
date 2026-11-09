"""WhatsApp の Graph への口（`thth wa` の網の出入りは、ここの `_get()` と `_post()` の 2 か所だけ）。

`tests/test_m6_wa.py` が縛ること: `.request("GET"` は `_get()` の 1 回、`.request("POST"` は `_post()` の 1 回、
DELETE・PUT・PATCH はどこにも無い。書く口（`create_template`・`send_template`）を呼ぶのは `thth/wa/cli.py` だけ。

**path と欄は公式の頁で 2026-11-09 に原文を確かめた**（Graph API の参照と WhatsApp の文書）:

  - `GET /{waba-id}`（WhatsApp Business Account の node。権限 `whatsapp_business_management`・
    `whatsapp_business_messaging`・`public_profile`。欄は既定で name・currency・timezone_id・message_template_namespace）
    — developers.facebook.com/docs/graph-api/reference/whats-app-business-account/
  - `GET /{waba-id}/phone_numbers`（応答は `data` の配列。各行に id・display_phone_number・verified_name・
    quality_rating。`fields` の指定は文書に無いので付けない）
    — developers.facebook.com/docs/whatsapp/business-management-api/manage-phone-numbers
  - `GET /{waba-id}/message_templates`（応答 `data` の各行に name・components・language・status・category・id。
    絞りは category・content・language・name・name_or_content・quality_score・status・since・until。
    `limit`・`fields` は文書に無いので付けない）
    — developers.facebook.com/docs/graph-api/reference/whats-app-business-account/message_templates/
  - `POST /{waba-id}/message_templates`（必須: name・category（UTILITY／MARKETING／AUTHENTICATION）・language・
    components。応答は id・status・category。名前は小文字の英数字と `_`。100 件／時／WABA）
    — 同じ参照の頁と developers.facebook.com/docs/whatsapp/business-management-api/message-templates
  - `POST /{phone-number-id}/messages`（本文は `messaging_product: "whatsapp"`・`recipient_type: "individual"`・`to`・
    `type: "template"`・`template: {name, language: {code}}`。応答は messaging_product・contacts[]・messages[]
    （id・message_status））
    — developers.facebook.com/docs/whatsapp/cloud-api/reference/messages と
      developers.facebook.com/documentation/business-messaging/whatsapp/templates/overview

**【未確認】**:
  - `to` の形（文書の例は `+16505551234`。符号と国番号が要るかの正式な規則は頁に無い）。THTH は 8〜15 桁の数字（先頭の
    `+` は任意）だけを通し、Meta にはそのまま渡す。
  - components の `type` の大文字小文字（文書の例は小文字の `body`）。小文字で送る。
  - 本文が `{{1}}` などの変数を含むテンプレートは `example` が要る。この口は変数なしのテンプレートだけ扱う。
  - 本物で、THTH の鍵（ユーザーの長い鍵）が上の edge を通すか、テンプレートの審査の状態遷移は、本物で 1 回打って確かめる。

**保存**: 答えは画面に出すだけ。相手の番号・本文は `_post()` を通るだけで、どこにも残さない。
"""
from __future__ import annotations

import json
import re
import urllib.error

from ..adapters import base
from ..ads.graphwrite import safe_user_msg
from ..meta import errors as meta_errors
from ..meta import graph as meta_graph
from . import ledger

CATEGORIES = ("UTILITY", "MARKETING", "AUTHENTICATION")
NAME_RE = re.compile(r"[a-z0-9_]{1,512}")
LANGUAGE_RE = re.compile(r"[a-z]{2,3}(_[A-Z]{2})?")
PHONE_RE = re.compile(r"\+?[0-9]{8,15}")
ID_RE = re.compile(r"[0-9]{1,32}")
BODY_MAX = 1024
MAX_PAGES = 5


class WaError(base.AdapterError):
    """Graph の失敗。`code` は静的な符丁（本文・URL・鍵は入れない）。"""

    def __init__(self, code, *, failure=None, http_status=None, user_msg=None):
        super().__init__(code)
        self.code = code
        self.failure = failure
        self.http_status = http_status
        self.user_msg = user_msg


class Graph:
    """1 つの WABA を扱う。鍵はその台帳の `.token`（ユーザーの長い鍵）。"""

    def __init__(self, name: str, cfg: dict, token: dict, *, base_url: str | None = None,
                 app_secret: str | None = None):
        access = token.get("access_token") if isinstance(token, dict) else None
        if not isinstance(access, str) or not access:
            raise WaError("wa_token_missing", failure=meta_errors.DEFINITE)
        self.name = name
        self.cfg = cfg
        self.waba = cfg["waba_id"]
        if app_secret is None:
            app_secret = meta_graph.load_app_secret()
        self.client = meta_graph.Client(base_url or meta_graph.origin(), access, app_secret=app_secret,
                                        usage_path=ledger.usage_path_for(name))
        self._hide = (access, app_secret)
        ledger.ensure_state_dir(name)

    # ------------------------------------------------------------------ 網の出入り（2 か所）

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            body = self.client.request("GET", path, params=params or {})
        except meta_errors.RateLimited:
            raise
        except urllib.error.HTTPError as exc:
            err = meta_errors.graph_error(exc)
            code = err.get("code") if isinstance(err.get("code"), int) else None
            raise WaError(f"wa_http_{exc.code}" + (f"_code_{code}" if code is not None else ""),
                          failure=meta_errors.classify(exc.code, err), http_status=exc.code,
                          user_msg=safe_user_msg(err, secrets=self._hide)) from None
        except (OSError, ValueError):
            raise WaError("wa_response_unavailable", failure=meta_errors.AMBIGUOUS) from None
        if not isinstance(body, dict):
            raise WaError("wa_response_invalid")
        return body

    def _post(self, path: str, payload: dict) -> dict:
        """**唯一の書く口。** 本文は JSON（文書の例の形）。結果が分からない失敗は `ambiguous`。"""
        data = dict(payload)
        data.update(self.client.auth_fields())
        try:
            body = self.client.request("POST", path, body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                                       headers={"Content-Type": "application/json"})
        except meta_errors.RateLimited:
            raise
        except urllib.error.HTTPError as exc:
            err = meta_errors.graph_error(exc)
            code = err.get("code") if isinstance(err.get("code"), int) else None
            raise WaError(f"wa_http_{exc.code}" + (f"_code_{code}" if code is not None else ""),
                          failure=meta_errors.classify(exc.code, err), http_status=exc.code,
                          user_msg=safe_user_msg(err, secrets=self._hide)) from None
        except (OSError, ValueError):
            raise WaError("wa_response_unavailable", failure=meta_errors.AMBIGUOUS) from None
        if not isinstance(body, dict):
            raise WaError("wa_response_invalid", failure=meta_errors.AMBIGUOUS)
        return body

    def _list(self, path: str) -> list:
        rows, after = [], None
        for _ in range(MAX_PAGES):
            body = self._get(path, {"after": after} if after else {})
            data = body.get("data")
            if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
                raise WaError("wa_list_invalid")
            rows.extend(data)
            paging = body.get("paging") if isinstance(body.get("paging"), dict) else {}
            cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
            after = cursors.get("after") if paging.get("next") else None
            if not isinstance(after, str) or not after:
                break
        return rows

    # ------------------------------------------------------------------ 読む

    def waba_node(self) -> dict:
        """`GET /{waba-id}`: 認可のときの照合（id と名前だけ）。"""
        body = self._get("/" + self.waba)
        if str(body.get("id") or "") != self.waba:
            raise WaError("wa_waba_mismatch")
        return {"waba_id": self.waba, "name": body.get("name") if isinstance(body.get("name"), str) else None}

    def phone_numbers(self) -> list:
        """番号の id と表示名と品質だけ（表示用の番号は出さない）。"""
        out = []
        for row in self._list(f"/{self.waba}/phone_numbers"):
            number_id = row.get("id")
            if not isinstance(number_id, str) or not ID_RE.fullmatch(number_id):
                continue
            out.append({"phone_number_id": number_id,
                        "verified_name": row.get("verified_name") if isinstance(row.get("verified_name"), str) else None,
                        "quality_rating": row.get("quality_rating") if isinstance(row.get("quality_rating"), str) else None})
        return out

    def message_templates(self) -> list:
        """名前と状態と言語（と分類）だけ。本文（components）は返さない。"""
        out = []
        for row in self._list(f"/{self.waba}/message_templates"):
            name = row.get("name")
            if not isinstance(name, str):
                continue
            out.append({"name": name,
                        "status": row.get("status") if isinstance(row.get("status"), str) else None,
                        "language": row.get("language") if isinstance(row.get("language"), str) else None,
                        "category": row.get("category") if isinstance(row.get("category"), str) else None})
        return out

    # ------------------------------------------------------------------ 書く（呼ぶのは cli.py の二段目だけ）

    def create_template(self, *, name: str, language: str, category: str, body: str) -> dict:
        if (category not in CATEGORIES or not NAME_RE.fullmatch(name or "") or not LANGUAGE_RE.fullmatch(language or "")
                or not body or len(body) > BODY_MAX):
            raise WaError("wa_template_invalid", failure=meta_errors.DEFINITE)
        reply = self._post(f"/{self.waba}/message_templates", {
            "name": name, "language": language, "category": category,
            "components": [{"type": "body", "text": body}]})
        return {"template_id": reply.get("id") if isinstance(reply.get("id"), str) else None,
                "status": reply.get("status") if isinstance(reply.get("status"), str) else None,
                "category": reply.get("category") if isinstance(reply.get("category"), str) else None}

    def send_template(self, *, phone_number_id: str, to: str, template: str, language: str) -> dict:
        if (not ID_RE.fullmatch(phone_number_id or "") or not PHONE_RE.fullmatch(to or "")
                or not NAME_RE.fullmatch(template or "") or not LANGUAGE_RE.fullmatch(language or "")):
            raise WaError("wa_send_invalid", failure=meta_errors.DEFINITE)
        reply = self._post(f"/{phone_number_id}/messages", {
            "messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "template",
            "template": {"name": template, "language": {"code": language}}})
        messages = reply.get("messages")
        first = messages[0] if isinstance(messages, list) and messages and isinstance(messages[0], dict) else {}
        if not isinstance(first.get("id"), str) or not first["id"]:
            # 送れたかは分からない（応答にメッセージの id が無い）。打ち直さない。
            raise WaError("wa_response_invalid", failure=meta_errors.AMBIGUOUS)
        status = first.get("message_status")
        return {"accepted": True, "message_status": status if isinstance(status, str) else None}
