"""Google ビジネス プロフィールの REST（urllib だけ）。

欄の名前と道は公式の参照（2026-11-19 に読んだ）で確かめた:

- `GET  https://mybusinessaccountmanagement.googleapis.com/v1/accounts`（`pageSize` は既定も上限も 20・`pageToken`）
  → `accounts[]`・`nextPageToken`。本人の個人のアカウントが先に来る。
- `GET  https://mybusinessbusinessinformation.googleapis.com/v1/{accounts/*}/locations`（`readMask` は必須・
  `pageSize` は既定 10・上限 100）→ `locations[]`・`nextPageToken`。
- `GET  https://mybusinessbusinessinformation.googleapis.com/v1/{locations/*}?readMask=…` → Location。
- `PATCH https://mybusinessbusinessinformation.googleapis.com/v1/{locations/*}?updateMask=specialHours`（本文は Location）。
  `specialHours` は「ふだんの時間が無いと書けない」（Location の頁: "This field cannot be set without regular hours."）。
  `SpecialHourPeriod` は `startDate`（必須・Date）・`openTime`・`closeTime`（TimeOfDay・00:00〜24:00・`closed` が偽なら
  必須）・`endDate`（省くと startDate・startDate と同じか 1 日あとまで）・`closed`（真なら終日休み・ほかの欄は無視）。
  0 時 0 分は JSON で `{}` になりうる（proto3 は 0 を省く）。
- `OpenInfo.status` は OPEN・CLOSED_PERMANENTLY・CLOSED_TEMPORARILY（と UNSPECIFIED）。
- `GET  https://mybusiness.googleapis.com/v4/{accounts/*/locations/*}/localPosts`（`pageSize` は既定 20・上限 100）
  → `localPosts[]`・`nextPageToken`。
- `POST https://mybusiness.googleapis.com/v4/{accounts/*/locations/*}/localPosts`（本文は LocalPost）。LocalPost は
  `languageCode`・`summary`・`callToAction{actionType, url}`・`topicType`（STANDARD・EVENT・OFFER・ALERT）・
  `state`（REJECTED・LIVE・PROCESSING）・`searchUrl`・`name`・`createTime`。actionType は BOOK・ORDER・SHOP・
  LEARN_MORE・SIGN_UP・CALL（CALL は url を付けない・GET_OFFER は古い）。商品の投稿は API で作れない（posts-data）。

- イベントの投稿（連動 段 2・11/19）: LocalPost の `topicType: EVENT` と `event`（LocalPostEvent: `title`・
  `schedule`＝TimeInterval の `startDate`・`startTime`・`endDate`・`endTime`）。本文（`summary`）とボタンは
  ふつうの投稿と同じ。

**【推】** イベントの題の上限 58 字は API の参照に無い（ビジネス プロフィールの投稿の画面の上限による）。

**【推】** 投稿の本文の上限 1,500 字は API の参照に無い（ビジネス プロフィールのヘルプの「投稿は 1,500 文字まで」による）。
数えるのは Python の字の数（Google の数え方が UTF-16 か書記素かは確かめていない）。行動の呼びかけの URL の長さの上限も
参照に無いので、THTH の他の口と同じ 2,048 字で止める【推】。

scope は `business.manage` で全部呼べる（各頁の Authorization scopes）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
import urllib.parse

from . import auth
from .wire import GbpRefused, default_transport

ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
INFO_BASE = "https://mybusinessbusinessinformation.googleapis.com/v1/"
V4_BASE = "https://mybusiness.googleapis.com/v4/"
LIST_MASK = "name,title,storefrontAddress,websiteUri,regularHours,specialHours,openInfo,metadata"
SHOW_MASK = "name,title,websiteUri,regularHours,specialHours,openInfo,metadata"
ACCOUNTS_PAGE = 20
LOCATIONS_PAGE = 100
POSTS_PAGE_MAX = 100
MAX_PAGES = 20
POST_TEXT_MAX = 1500  # 【推】ヘルプの上限（API の参照に数は無い）
EVENT_TITLE_MAX = 58  # 【推】投稿の画面の上限（API の参照に数は無い）
URL_MAX = 2048
ACTIONS = ("LEARN_MORE", "BOOK", "ORDER", "SHOP", "SIGN_UP")
OPEN_STATUSES = ("OPEN", "CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY")
DAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
DIGEST_VERSION = "thth-gbp-1"
DIGEST_LENGTH = 12

_ID = re.compile(r"[0-9]{1,32}\Z")
_LOC = re.compile(r"(?:accounts/([0-9]{1,32})/)?locations/([0-9]{1,32})\Z")
_ENUM = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_SERVICE = re.compile(r"[a-z][a-z0-9.]{0,62}\.googleapis\.com\Z")
_HHMM = re.compile(r"([0-9]{1,2}):([0-9]{2})\Z")


# ------------------------------------------------------------------ 形の検査

def parse_location(value) -> tuple[str | None, str]:
    """`locations/123`・`accounts/9/locations/123`・`123` → (account の id か None, location の id)。"""
    text = str(value or "").strip()
    if _ID.fullmatch(text):
        return None, text
    found = _LOC.fullmatch(text)
    if not found:
        raise GbpRefused("gbp_location_invalid")
    return found.group(1), found.group(2)


def parse_date(value) -> datetime.date:
    try:
        return datetime.date.fromisoformat(str(value or ""))
    except ValueError:
        raise GbpRefused("gbp_date_invalid") from None


def parse_time(value) -> dict:
    """`HH:MM`（00:00〜24:00・24:00 はその日の終わり）→ TimeOfDay。"""
    found = _HHMM.fullmatch(str(value or "").strip())
    if not found:
        raise GbpRefused("gbp_time_invalid")
    hours, minutes = int(found.group(1)), int(found.group(2))
    if not (0 <= hours <= 24 and 0 <= minutes <= 59) or (hours == 24 and minutes):
        raise GbpRefused("gbp_time_invalid")
    return {"hours": hours, "minutes": minutes}


def _minutes(tod) -> int | None:
    if not isinstance(tod, dict):
        return None
    hours, minutes = tod.get("hours", 0), tod.get("minutes", 0)
    if not isinstance(hours, int) or not isinstance(minutes, int):
        return None
    return hours * 60 + minutes


def time_text(tod) -> str:
    value = _minutes(tod)
    return "—" if value is None else f"{value // 60:02d}:{value % 60:02d}"


def date_of(value) -> datetime.date | None:
    if not isinstance(value, dict):
        return None
    try:
        return datetime.date(int(value.get("year")), int(value.get("month")), int(value.get("day")))
    except (TypeError, ValueError):
        return None


def date_obj(day: datetime.date) -> dict:
    return {"year": day.year, "month": day.month, "day": day.day}


def check_text(text) -> str:
    """投稿の本文: 前後の空白を落とし、1〜1,500 字・改行とタブのほかの制御文字なし。"""
    if not isinstance(text, str):
        raise GbpRefused("gbp_post_text_invalid", "empty")
    text = text.strip()
    if not text:
        raise GbpRefused("gbp_post_text_invalid", "empty")
    if len(text) > POST_TEXT_MAX:
        raise GbpRefused("gbp_post_text_invalid", f"length {len(text)} over {POST_TEXT_MAX}")
    if any((ord(c) < 32 and c not in "\n\t") or ord(c) == 127 for c in text):
        raise GbpRefused("gbp_post_text_invalid", "control_character")
    return text


def check_url(value) -> str:
    text = str(value or "").strip()
    if not text or len(text) > URL_MAX or any(ord(c) <= 32 or ord(c) == 127 for c in text):
        raise GbpRefused("gbp_post_url_invalid")
    try:
        parsed = urllib.parse.urlsplit(text)
        parsed.port  # noqa: B018 （port の数が壊れていれば ValueError）
    except ValueError:
        raise GbpRefused("gbp_post_url_invalid") from None
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise GbpRefused("gbp_post_url_invalid")
    return text


# ------------------------------------------------------------------ 臨時の時間

def special_period(day: datetime.date, *, closed: bool, open_time: dict | None = None,
                   close_time: dict | None = None) -> dict:
    """1 日の臨時の時間（休みか、開く時刻と閉める時刻）。閉める時刻が開く時刻より前なら日をまたぐ（endDate は翌日）。"""
    if closed:
        return {"startDate": date_obj(day), "closed": True}
    start, end = _minutes(open_time), _minutes(close_time)
    if start is None or end is None or start == end or start == 24 * 60:
        raise GbpRefused("gbp_time_invalid")
    period = {"startDate": date_obj(day), "openTime": open_time, "closeTime": close_time, "closed": False}
    if end < start:
        period["endDate"] = date_obj(day + datetime.timedelta(days=1))
    else:
        period["endDate"] = date_obj(day)
    return period


def periods_of(location: dict) -> list[dict]:
    special = location.get("specialHours") if isinstance(location, dict) else None
    periods = special.get("specialHourPeriods") if isinstance(special, dict) else None
    return [p for p in periods if isinstance(p, dict)] if isinstance(periods, list) else []


def has_regular_hours(location: dict) -> bool:
    regular = location.get("regularHours") if isinstance(location, dict) else None
    periods = regular.get("periods") if isinstance(regular, dict) else None
    return isinstance(periods, list) and any(isinstance(p, dict) for p in periods)


def replace_day(periods: list[dict], period: dict) -> tuple[list[dict], list[dict]]:
    """その日（startDate が同じ）の行だけを新しい 1 行に替え、ほかの日はそのまま。返すのは (新しい全体, 外した行)。"""
    day = date_of(period["startDate"])
    kept = [p for p in periods if date_of(p.get("startDate")) != day]
    dropped = [p for p in periods if date_of(p.get("startDate")) == day]
    merged = kept + [period]
    merged.sort(key=lambda p: (date_of(p.get("startDate")) or datetime.date.min, _minutes(p.get("openTime")) or 0))
    return merged, dropped


def period_text(period: dict) -> str:
    day = date_of(period.get("startDate"))
    head = day.isoformat() if day else "日付不明"
    if period.get("closed"):
        return f"{head} 終日休み"
    end_day = date_of(period.get("endDate"))
    tail = "（翌日）" if day and end_day and end_day > day else ""
    return f"{head} {time_text(period.get('openTime'))}〜{time_text(period.get('closeTime'))}{tail}"


def digest_of(kind: str, *parts) -> str:
    """二段確認の digest（sha256 の先頭 12 桁）。中身の 1 つでも変われば変わる（いまの臨時の時間も含める）。"""
    joined = "\x1f".join([DIGEST_VERSION, kind, *[json.dumps(p, ensure_ascii=False, sort_keys=True) for p in parts]])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def parse_minute(value) -> datetime.datetime:
    """`YYYY-MM-DD HH:MM`（店の時刻・日本の時刻として読む）。"""
    try:
        return datetime.datetime.strptime(str(value or "").strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        raise GbpRefused("gbp_event_invalid", "minute_form") from None


def event_of(title, start: datetime.datetime, end: datetime.datetime) -> dict:
    """LocalPostEvent（題・始まりと終わりの日と時刻）。終わりは始まりより後。"""
    text = str(title or "").strip()
    if not text or len(text) > EVENT_TITLE_MAX or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise GbpRefused("gbp_event_invalid", "title")
    if end <= start:
        raise GbpRefused("gbp_event_invalid", "range")
    return {"title": text, "schedule": {
        "startDate": date_obj(start.date()), "startTime": {"hours": start.hour, "minutes": start.minute},
        "endDate": date_obj(end.date()), "endTime": {"hours": end.hour, "minutes": end.minute}}}


def event_text(event: dict) -> str:
    schedule = event["schedule"]
    start, end = date_of(schedule["startDate"]), date_of(schedule["endDate"])
    tail = time_text(schedule["endTime"]) if start == end else f"{end.isoformat()} {time_text(schedule['endTime'])}"
    return f"{event['title']}（{start.isoformat()} {time_text(schedule['startTime'])}〜{tail}）"


def post_body(text: str, *, url: str | None = None, action: str | None = None, event: dict | None = None) -> dict:
    body = {"languageCode": "ja", "summary": check_text(text), "topicType": "STANDARD"}
    if event is not None:
        body["topicType"] = "EVENT"
        body["event"] = event
    if url is not None:
        body["callToAction"] = {"actionType": action or "LEARN_MORE", "url": check_url(url)}
    elif action is not None:
        raise GbpRefused("gbp_post_url_invalid", "action_without_url")
    return body


# ------------------------------------------------------------------ Google の断り → THTH の符丁

def _error(payload):
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, dict) else None


def _details(payload):
    error = _error(payload)
    items = error.get("details") if error else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _reasons(payload):
    """`error.details[].reason`（ErrorInfo）と `error.errors[].reason` の語を順に（形を検査したものだけ）。"""
    error = _error(payload)
    if not error:
        return
    for key in ("details", "errors"):
        items = error.get(key)
        for item in items if isinstance(items, list) else ():
            reason = item.get("reason") if isinstance(item, dict) else None
            if isinstance(reason, str) and _ENUM.fullmatch(reason):
                yield reason


def _quota_zero(payload) -> bool:
    """許可リストの外の project は、上限の値が 0（毎分 0 回）で断られる（prereqs: 0 なら未承認・300 なら承認）。"""
    for item in _details(payload):
        meta = item.get("metadata")
        if isinstance(meta, dict) and str(meta.get("quota_limit_value", "")).strip() == "0":
            return True
        violations = item.get("violations") if isinstance(item.get("violations"), list) else []
        for v in violations:
            if isinstance(v, dict) and str(v.get("quotaValue", v.get("quota_limit_value", ""))).strip() == "0":
                return True
    return False


def _service(payload) -> str | None:
    for item in _details(payload):
        meta = item.get("metadata")
        name = meta.get("service") if isinstance(meta, dict) else None
        if isinstance(name, str) and _SERVICE.fullmatch(name):
            return name
    return None


_DISABLED = {"SERVICE_DISABLED", "accessNotConfigured", "API_DISABLED"}
_SCOPE = {"ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientPermissions"}
_QUOTA = {"RATE_LIMIT_EXCEEDED", "rateLimitExceeded", "quotaExceeded", "userRateLimitExceeded",
          "dailyLimitExceeded", "RESOURCE_EXHAUSTED"}


def refusal_for(status: int, payload, *, write: bool = False) -> GbpRefused:
    """Google の答え（HTTP の数と本文）から THTH の断り。詳細は Google の理由の名前だけ（本文の文は入れない）。"""
    reasons = list(_reasons(payload))
    error = _error(payload)
    status_word = error.get("status") if error and isinstance(error.get("status"), str) else None
    if status_word is not None and not _ENUM.fullmatch(status_word):
        status_word = None
    words = set(reasons) | ({status_word} if status_word else set())
    detail = (reasons[0] if reasons else None) or status_word or f"HTTP {status}"
    if words & _DISABLED:
        word = sorted(words & _DISABLED)[0]
        service = _service(payload)
        return GbpRefused("gbp_api_not_enabled", f"{word} {service}" if service else word)
    if (status in (403, 429) or words & _QUOTA) and _quota_zero(payload):
        return GbpRefused("gbp_not_allowlisted", detail)
    if words & _SCOPE:
        return GbpRefused("gbp_authentication_error", sorted(words & _SCOPE)[0])
    if status == 429 or words & _QUOTA:
        return GbpRefused("gbp_quota_exceeded", detail)
    if status == 401:
        return GbpRefused("gbp_authentication_error", detail)
    if status == 403:
        return GbpRefused("gbp_permission_denied", detail)
    if status == 404:
        return GbpRefused("gbp_location_unknown", detail)
    if status >= 500:
        return GbpRefused("gbp_write_unknown" if write else "gbp_unavailable", detail)
    return GbpRefused("gbp_request_rejected", detail)


# ------------------------------------------------------------------ 呼ぶ口

def _text(value) -> str | None:
    return value if isinstance(value, str) else None


def _https(value) -> str | None:
    return value if isinstance(value, str) and value.startswith("https://") else None


def _address(value) -> str | None:
    if not isinstance(value, dict):
        return None
    parts = [value.get("administrativeArea"), value.get("locality"),
             *(value.get("addressLines") if isinstance(value.get("addressLines"), list) else [])]
    text = " ".join(p for p in parts if isinstance(p, str) and p)
    return text or None


def location_row(item: dict, account: str | None) -> dict | None:
    name = item.get("name") if isinstance(item, dict) else None
    found = _LOC.fullmatch(name) if isinstance(name, str) else None
    if not found:
        return None
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    info = item.get("openInfo") if isinstance(item.get("openInfo"), dict) else {}
    status = info.get("status")
    return {"location": f"locations/{found.group(2)}",
            "account": f"accounts/{account}" if account else None,
            "title": _text(item.get("title")),
            "address": _address(item.get("storefrontAddress")),
            "website": _text(item.get("websiteUri")),
            "open_status": status if status in OPEN_STATUSES else None,
            "has_regular_hours": has_regular_hours(item),
            "special_hours": len(periods_of(item)),
            "maps": _https(meta.get("mapsUri"))}


class Client:
    """1 つの命令で 1 つ。初めて呼ぶときに refresh token から access token を取る（残さない）。"""

    def __init__(self, *, transport=None):
        self.transport = transport or default_transport
        self.client = auth.load_client()
        self.token = auth.load_token()
        self._access = None

    def _access_token(self) -> str:
        if self._access is None:
            self._access = auth.refresh(self.client, self.token, transport=self.transport)
        return self._access

    def call(self, method: str, url: str, body: dict | None = None, *, write: bool = False) -> dict:
        headers = {"Authorization": f"Bearer {self._access_token()}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            status, raw = self.transport(method, url, headers, data)
        except GbpRefused as exc:
            # 書く依頼を送ったあとに届かなくなったら、Google が受けたかどうかは分からない。
            if write and exc.code == "gbp_unavailable":
                raise GbpRefused("gbp_write_unknown", "unreachable") from None
            raise
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            payload = None
        if write and 200 <= status < 300:
            pass  # 書く依頼の 2xx は Google が受けた（201・202 も。監査 5.27.0 の 2）
        elif write and 300 <= status < 400:
            raise GbpRefused("gbp_write_unknown", f"HTTP {status}")  # 受けたかどうか言えない
        elif status != 200:
            raise refusal_for(status, payload, write=write)
        if not isinstance(payload, dict):
            raise GbpRefused("gbp_write_unknown" if write else "gbp_response_invalid")
        return payload

    def _pages(self, url: str, params: dict, key: str, *, limit: int | None = None) -> list[dict]:
        out, token = [], None
        for _ in range(MAX_PAGES):
            query = dict(params, **({"pageToken": token} if token else {}))
            payload = self.call("GET", url + "?" + urllib.parse.urlencode(query))
            items = payload.get(key, [])
            if not isinstance(items, list):
                raise GbpRefused("gbp_response_invalid")
            out.extend(i for i in items if isinstance(i, dict))
            token = payload.get("nextPageToken")
            if not isinstance(token, str) or not token or (limit is not None and len(out) >= limit):
                break
        return out[:limit] if limit is not None else out

    # ---- 読む

    def accounts(self) -> list[dict]:
        rows = []
        for item in self._pages(ACCOUNTS_URL, {"pageSize": ACCOUNTS_PAGE}, "accounts"):
            name = item.get("name")
            if isinstance(name, str) and re.fullmatch(r"accounts/[0-9]{1,32}", name):
                kind = item.get("type")
                rows.append({"account": name, "name": _text(item.get("accountName")),
                             "type": kind if isinstance(kind, str) and _ENUM.fullmatch(kind) else None})
        return rows

    def locations(self, account: str, *, mask: str = LIST_MASK) -> list[dict]:
        account_id = account.split("/", 1)[1]
        url = INFO_BASE + f"accounts/{account_id}/locations"
        rows = self._pages(url, {"readMask": mask, "pageSize": LOCATIONS_PAGE}, "locations")
        return [row for row in (location_row(i, account_id) for i in rows) if row is not None]

    def all_locations(self) -> list[dict]:
        out = []
        for account in self.accounts():
            for row in self.locations(account["account"]):
                row["account_name"] = account["name"]
                out.append(row)
        return out

    def location(self, location_id: str, *, mask: str = SHOW_MASK) -> dict:
        url = INFO_BASE + f"locations/{location_id}?" + urllib.parse.urlencode({"readMask": mask})
        payload = self.call("GET", url)
        if not isinstance(payload.get("name"), str):
            raise GbpRefused("gbp_response_invalid")
        return payload

    def account_of(self, location_id: str) -> str:
        """投稿の口（v4）は `accounts/A/locations/L` が要る。見える account の中から、その店を持つものを探す。"""
        for account in self.accounts():
            for row in self.locations(account["account"], mask="name"):
                if row["location"] == f"locations/{location_id}":
                    return account["account"].split("/", 1)[1]
        raise GbpRefused("gbp_location_unknown", "not_in_accounts")

    def posts(self, account_id: str, location_id: str, *, limit: int = 20) -> list[dict]:
        url = V4_BASE + f"accounts/{account_id}/locations/{location_id}/localPosts"
        rows = self._pages(url, {"pageSize": min(max(limit, 1), POSTS_PAGE_MAX)}, "localPosts", limit=limit)
        out = []
        for item in rows:
            action = item.get("callToAction") if isinstance(item.get("callToAction"), dict) else {}
            out.append({"name": _text(item.get("name")), "topic": _text(item.get("topicType")),
                        "state": _text(item.get("state")), "summary": _text(item.get("summary")),
                        "created": _text(item.get("createTime")), "link": _https(item.get("searchUrl")),
                        "action": _text(action.get("actionType")), "action_url": _text(action.get("url"))})
        return out

    # ---- 書く（cli.py の二段確認の二段目からだけ呼ぶ）

    def patch_special(self, location_id: str, periods: list[dict]) -> dict:
        url = INFO_BASE + f"locations/{location_id}?" + urllib.parse.urlencode({"updateMask": "specialHours"})
        return self.call("PATCH", url, {"specialHours": {"specialHourPeriods": periods}}, write=True)

    def create_post(self, account_id: str, location_id: str, body: dict) -> dict:
        url = V4_BASE + f"accounts/{account_id}/locations/{location_id}/localPosts"
        return self.call("POST", url, body, write=True)
