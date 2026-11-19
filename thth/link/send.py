"""束を出す（設計 店の変化の連動 §3.3・§3.4・段 5）。網に出るのはここ（Google は `thth/gbp/api.py`・IG は `core.send_once`）。

- 自動（`auto=True`・VM の timer）: 今効いている同意（`linkage.consent_for`・**出す時に読み直す**）のある物だけを、
  確認なしで出す。同意の無い物・取り消した物は `waiting` のまま（次の回も出さない）。止めた物（`stopped`）は人を待つ。
- 1 回ずつ（`bundle_id`）: その束のまだ出ていない物を、束ごと 1 回の二段確認で出す（一段目は何も変えずに送る中身と
  digest を出す）。
- 出す前に台帳を読み直す: 行が束を作ったあとに変わった・柵を通らなくなった → `stopped`（`register_link_row_changed`）、
  出さない期限を過ぎた → `expired`。
- Google マップの臨時の時間: Google の今のその日の値が、THTH が前に出した値（`bundle.last_sent_value`）から変わって
  いたら（Google が変えた・人が手で変えた）、**自動では出さずに止める**（`register_link_google_changed`）。前に出して
  いない日に、出す値と違う値が既にあるときも同じ。1 回ずつの二段確認では、置き換えることを一段目に出して通す。
  Google から読んだ値はファイルに残さない（digest に混ぜるだけ）。残すのは THTH が書いた値（`sent_value`）だけ。
- IG（フィード・ストーリー）: 画像を口座の repo の `register-media/<台帳>/<sha256 の頭 16>.jpg` に写し、`thth send` と
  同じ道（`core.send_once`・ロック・inflight・安全装置・`sent/` の記録）で出す。ストーリーは `post_options: {story: true}`。
- 出した物は `sent` にし、二度出さない（出た物は選ばない・束は外に出る中身の指紋で決める `bundle.py`）。
- **1 件ずつ記録する**（監査の 1）: 出す前に `sending` の印を書き、出したらすぐ書く。途中で落ちて `sending` が残って
  いれば、次の回に `unknown`（出たか分からない）にして、自動では出し直さない。
- **出し先が台帳の店のものか**（監査の 2）: IG の口座の project が台帳の project と違えば出さない。Google マップの店は、
  台帳の店の行（`kind: store`）の `gbp_location`（認める操作は二段確認）と一致するものだけに出す。
- **同意は控えと合うものだけ**（監査の 3・`linkage.with_kept`）。
- **一時の断り**（安全装置の too_soon・quiet_hours、Google の 429・503、鍵がまだ無い）は止めずに次の回へ回す。
  安全装置が次の時刻を返せば、その時刻まで数えずに待つ。止めるのは回数でなく時間（束の出さない期限まで・それが
  無ければ最初の断りから `RETRY_HOURS` 時間）（監査の 6・2 回目の E）。
- **網に出る前の手元の読み書きの失敗**（OSError）は `stopped`（詳細 `local_io`）にして、ほかの物を続ける（2 回目の I）。
- 台帳がまとめての入れ替えの途中なら、束を作るのも出すのも断る（`keep.refuse_if_pending`・2 回目の C）。
- **書く直前に Google を読み直す**（監査の 8）: 計画を作ったときの読みと違えば書かずに止める。
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import hashlib
import json
import os

from .. import accounts as accounts_mod
from .. import admin_log, jst, redact
from ..gbp import api as gbp_api
from ..gbp.wire import GbpRefused
from ..register import ACTION_PREFIX, bundle as bundle_mod, fence, form, keep as keep_mod, kinds, ledger, linkage
from ..register.errors import RegisterRefused

MEDIA_DIR = "register-media"
IMAGE_MAX_BYTES = 8_000_000
DIGEST_LENGTH = 12
LOCK_FILE = linkage.LOCK_FILE
# 出さない期限の無い物の、一時の断りを待つ長さ（最初の断りから）【推】。
RETRY_HOURS = 24
TEMPORARY_GBP = frozenset(("gbp_quota_exceeded", "gbp_unavailable", "gbp_token_missing", "gbp_client_missing",
                           "gbp_token_refresh_failed"))
TEMPORARY_IG = frozenset(("guard", "locked", "rate_limited", "queued"))


class _Retry(Exception):
    """一時の断り（次の回にもう一度・`next_at` は安全装置が返した次に出せる時刻）。"""

    def __init__(self, detail, next_at=None):
        super().__init__(detail)
        self.detail = detail
        self.next_at = next_at


class _Stop(Exception):
    def __init__(self, state, code, detail=None):
        super().__init__(code)
        self.state, self.code, self.detail = state, code, detail


@contextlib.contextmanager
def locked(name: str):
    """台帳ごとに 1 つ（timer と手の命令が同じ束を同時に書かない）。"""
    directory = ledger.state_dir_for(name)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(directory, LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def open_register(name: str):
    cfg = ledger.load(name)
    root = ledger.root_of(cfg)
    keep_mod.refuse_if_pending(root)
    decl = linkage.load(root, name)
    if decl is None:
        raise RegisterRefused("register_linkage_invalid", "linkage_missing")
    return cfg, root, linkage.with_kept(decl, name)


def _fresh(name: str, decl: dict, now) -> dict:
    """ロックの中で、ファイルから消えた控えを取り消しにしてから、控えを読み直す（2 回目の B）。"""
    linkage.retire_missing(name, decl, at=jst.iso(now))
    return linkage.with_kept(decl, name)


def settle_interrupted(bundles: list) -> int:
    """前の回が出す途中で落ちた物（`sending`）を `unknown` にする（自動では出し直さない）。直した数。"""
    count = 0
    for b in bundles:
        for item in b["items"]:
            if item.get("state") == "sending":
                item.update(state="unknown", code="register_link_unknown", detail="interrupted")
                count += 1
    return count


def record(event: str, name: str, by: str, diff: dict) -> None:
    try:
        admin_log.append(ACTION_PREFIX + event, name, {"media": "register"}, by=by, diff=diff)
    except (OSError, ValueError, admin_log.AdminLogError):
        raise RegisterRefused("register_log_unavailable") from None


def make(name: str, *, by: str, now: datetime.datetime) -> list:
    """束を作る（今出す頃のきっかけ・柵を通った行・宣言の出し先のあるものだけ）。新しく作った束を返す。"""
    _cfg, root, decl = open_register(name)
    scanned = form.scan(root)
    records = ledger.read_confirmed(name)
    with locked(name):
        decl = _fresh(name, decl, now)
        made, bundles = bundle_mod.make(name, root, scanned, records, decl, now)
        if settle_interrupted(bundles) and not made:
            bundle_mod.write(name, bundles)
        if made:
            record("link_made", name, by, {"bundles": [[], [b["id"] for b in made]]})
            bundle_mod.write(name, bundles)
    return made


CONSENT_NOTE = ("控えると、この出し先には確認なしで出ます。前に THTH が Google マップに書いた臨時の日を、台帳から"
                "無くなったときに外すことも含みます")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def reset_consent(name: str, *, by: str, now: datetime.datetime, confirm: str | None) -> dict:
    """壊れた控えを別の名前に退けて空から始める（二段確認・消さない）。控え直すまで、どの同意も自動に効かない。"""
    ledger.load(name)
    path = linkage.kept_path(name)
    try:
        with open(path, "rb") as f:
            seen = hashlib.sha256(f.read()).hexdigest()
    except FileNotFoundError:
        seen = None
    digest = _digest({"reset": name, "file": seen})
    if confirm != digest:
        if confirm is not None:
            raise RegisterRefused("register_link_confirm_mismatch", digest=digest)
        return {"stage": 1, "register": name, "reset": True, "exists": seen is not None, "digest": digest,
                "note": "控えを別の名前に退けて空から始めます。控え直すまで、どの同意も自動には効きません"}
    with locked(name):
        aside = linkage.reset_kept(name, at=jst.iso(now))
        record("link_consent", name, by, {"reset": [None, aside or "none"]})
    return {"stage": 2, "register": name, "reset": True, "aside": aside, "digest": digest}


def keep_consent(name: str, *, by: str, now: datetime.datetime, confirm: str | None, revoke: bool = False,
                 to: str | None = None) -> dict:
    """宣言の同意のうち、まだ控えていない物を二段確認で控える（一段目は何も書かずに全文と digest を返す）。
    `revoke=True` は控えた同意（`to` があればその出し先だけ）を THTH の側で取り消す（二度と自動に使わない）。"""
    _cfg, _root, decl = open_register(name)
    if revoke:
        rows = [r for r in linkage.read_kept(name) if not r.get("revoked_at") and (to is None or r["to"] == to)]
        shown = [{k: r.get(k) for k in ("to", "triggers", "until", "by", "fingerprint")} for r in rows]
    else:
        pending = linkage.unkept(decl, now)
        shown = [{k: c[k] for k in ("to", "triggers", "until", "by", "at", "revoked_at", "fingerprint")} for c in pending]
    digest = _digest({"revoke": revoke, "consent": shown})
    if not shown:
        return {"stage": 2, "register": name, "revoke": revoke, "kept": [], "revoked": [], "digest": None}
    if confirm != digest:
        if confirm is not None:
            raise RegisterRefused("register_link_confirm_mismatch", digest=digest)
        note = ("取り消した同意は二度と自動に使いません。続けるなら、新しく書いた同意を控え直してください"
                if revoke else CONSENT_NOTE)
        return {"stage": 1, "register": name, "revoke": revoke, "consent": shown, "digest": digest, "note": note}
    with locked(name):
        rows = linkage.read_kept(name)
        if revoke:
            done = linkage.revoke_rows(rows, {c["fingerprint"] for c in shown}, by=by, at=jst.iso(now), why="revoked")
            linkage.write_kept(name, rows)
            record("link_consent", name, by, {"revoked": [[], done]})
            return {"stage": 2, "register": name, "revoke": True, "kept": [], "revoked": shown, "digest": digest}
        have = {r["fingerprint"] for r in rows}
        for c in shown:
            if c["fingerprint"] not in have:
                rows.append({"fingerprint": c["fingerprint"], "to": c["to"], "triggers": c["triggers"],
                             "until": c["until"], "by": c["by"], "kept_by": by, "kept_at": jst.iso(now)})
        linkage.write_kept(name, rows)
        record("link_consent", name, by, {"consent": [[], [c["fingerprint"] for c in shown]]})
    return {"stage": 2, "register": name, "revoke": False, "kept": shown, "revoked": [], "digest": digest}


# ------------------------------------------------------------------ 選ぶ・読み直す

def _pick(name, bundles, decl, *, bundle_id, auto, now):
    picked = []
    if auto:
        for b in bundles:
            for item in b["items"]:
                if item["state"] not in ("ready", "waiting", "blocked") or item.get("hold"):
                    continue   # hold: 行が見つからないだけの片づけ（同意があっても持ち主の二段確認を待つ）
                after = jst.parse(item.get("retry_after")) if item.get("retry_after") else None
                if after is not None and now < after:
                    continue   # 安全装置が返した次に出せる時刻まで、数えずに待つ
                if linkage.consent_for(decl, name, item["to"], b["trigger"], now) is not None:
                    picked.append((b, item))
                elif item["state"] == "ready":
                    item["state"] = "waiting"   # 同意を取り消した・期限を過ぎた（次の回から出さない）
        return picked
    found = next((b for b in bundles if b["id"] == bundle_id), None)
    if found is None:
        raise RegisterRefused("register_link_bundle_missing", bundle_id if isinstance(bundle_id, str) else None)
    # 出たか分からない物も、持ち主が今を確かめて二段確認で通すときは選ぶ（一段目に出る・自動では選ばない）。
    return [(found, item) for item in found["items"] if item["state"] in bundle_mod.OPEN_STATES + ("unknown",)]


def _recheck(name, root, decl, bundles, b, item, scanned, records, now, claimed=None):
    """`(状態, 符丁)` で止めるか、None で出してよい。行の字でなく、外に出る中身が束を作った時と同じかで見る（監査の 7）。"""
    until = jst.parse(b.get("not_after"))
    if until is None or now > until:
        return "expired", "register_link_expired"
    row = form.find(scanned["rows"], b["row"])
    if b.get("clear_only"):
        days = bundle_mod.current_days(row, records, jst.to_jst(now).date())
        if days is None or days & set(item.get("clear") or []):
            return "stopped", "register_link_row_changed"
        return None
    if row is None or fence.state(row, records, jst.to_jst(now).date()) is not None:
        return "stopped", "register_link_row_changed"
    outlet = next((o for o in decl["outlets"] if o["to"] == item["to"] and b["trigger"] in o["triggers"]), None)
    if outlet is None:
        return "stopped", "register_link_row_changed"
    before = bundles[:next(n for n, one in enumerate(bundles) if one["id"] == b["id"])]
    again = bundle_mod.item_for(name, root, decl, outlet, b["trigger"], row, now, bundles=before, claimed=claimed)
    if bundle_mod.outward_of(again, root) != item.get("outward"):
        return "stopped", "register_link_row_changed"
    return None


def bound_locations(scanned, records, now) -> set:
    """台帳の店の行（認めた・退役していない）が結ぶ Google マップの店の番号。"""
    today = jst.to_jst(now).date()
    out = set()
    for row in scanned["rows"]:
        if row.kind == "store" and kinds.GBP_LOCATION_FIELD in row.head and fence.state(row, records, today) in (
                None, "register_row_not_public"):
            out.add(row.head[kinds.GBP_LOCATION_FIELD].split("/", 1)[1])
    return out


def _outlet_ok(cfg, item, bound) -> None:
    """出し先がこの台帳の店のものか（違えば `_Stop`・持ち主が直すまで出さない）。"""
    if item["kind"] in ("gbp_special", "gbp_event"):
        if linkage.location_id(item["to"]) not in bound:
            raise _Stop("blocked", "register_link_store_unbound", "gbp_location")
        return
    account = _ig_account(item)
    if account.get("project") != cfg["project"]:
        raise _Stop("blocked", "register_link_account", "project_mismatch")


# ------------------------------------------------------------------ Google マップの臨時の時間

def _norm(period) -> tuple:
    """比べるための形（0 時 0 分が `{}` になる揺れ・省いた endDate を吸う）。"""
    if not isinstance(period, dict):
        return ("?",)
    start = gbp_api.date_of(period.get("startDate"))
    if period.get("closed") is True:
        return (start, "closed")
    end = gbp_api.date_of(period.get("endDate")) or start
    return (start, end, gbp_api._minutes(period.get("openTime") or {}), gbp_api._minutes(period.get("closeTime") or {}))


def _period(day: dict) -> dict:
    when = datetime.date.fromisoformat(day["date"])
    if day["closed"]:
        return gbp_api.special_period(when, closed=True)
    return gbp_api.special_period(when, closed=False, open_time=gbp_api.parse_time(day["open"]),
                                  close_time=gbp_api.parse_time(day["close"]))


class _Google:
    """1 回の send で 1 つ（店ごとに 1 回だけ読む・読んだ中身はファイルに書かない）。"""

    def __init__(self, transport):
        self.transport = transport
        self._client = None
        self.locations = {}

    @property
    def client(self):
        if self._client is None:
            self._client = gbp_api.Client(transport=self.transport)
        return self._client

    def location(self, to: str) -> dict:
        if to not in self.locations:
            self.locations[to] = self.client.location(linkage.location_id(to))
        return self.locations[to]


def _on_day(periods, day: str) -> list:
    when = datetime.date.fromisoformat(day)
    return [p for p in periods if gbp_api.date_of(p.get("startDate")) == when]


def _agrees(on_google: list, before, period) -> bool:
    """Google のその日が、THTH の前に書いた値（無ければ何も無いこと）か、今書く値と同じか。"""
    if period is not None and len(on_google) == 1 and _norm(on_google[0]) == _norm(period):
        return True      # 既に今書く値（出たか分からなかった前の回が、実は書けていた など）
    if period is None and not on_google:
        return True      # 片づける日が、もう無い
    if before is None:
        return not on_google
    if before["period"] is None:
        return not on_google
    return len(on_google) == 1 and _norm(on_google[0]) == _norm(before["period"])


def _seen(periods) -> list:
    return sorted(json.dumps(_norm(p), default=str) for p in periods)


def _special_plan(google, bundles, b, item, now) -> dict:
    """送る臨時の時間（その店の今の全部に、その日だけを足し替え・片づける日を外した並び）と、Google 側が変わっていたか。"""
    location = google.location(item["to"])
    if not gbp_api.has_regular_hours(location):
        raise GbpRefused("gbp_regular_hours_missing")
    current = gbp_api.periods_of(location)
    changed, ours = [], []
    for day in item["days"]:
        ours.append({"date": day["date"], "period": _period(day)})
    today = jst.to_jst(now).date().isoformat()
    for day in item.get("clear") or []:
        if day >= today:   # 過ぎた日は外さない（指紋には入れたまま・2 回目の F）
            ours.append({"date": day, "period": None})
    for one in ours:
        before = bundle_mod.last_sent_value(bundles, item["to"], one["date"], before=b["id"])
        if not _agrees(_on_day(current, one["date"]), before, one["period"]):
            changed.append(one["date"])
    return {"merged": _apply(current, ours), "ours": ours, "google_changed": changed, "seen": _seen(current)}


def _apply(periods, ours) -> list:
    merged = list(periods)
    for one in ours:
        if one["period"] is None:
            merged = [p for p in merged if gbp_api.date_of(p.get("startDate")) != datetime.date.fromisoformat(one["date"])]
        else:
            merged, _dropped = gbp_api.replace_day(merged, one["period"])
    return merged


# ------------------------------------------------------------------ IG（フィード・ストーリー）

def _ig_account(item) -> dict:
    name = linkage.target_of(item["to"])
    try:
        cfg = accounts_mod.load_account(name)
    except (accounts_mod.AccountError, ValueError):
        raise RegisterRefused("register_link_account", "account") from None
    if cfg.get("media") != "instagram" or not cfg.get("repo_dir"):
        raise RegisterRefused("register_link_account", "not_instagram")
    return cfg


def _image_bytes(root: str, image: str) -> bytes:
    problem = bundle_mod.image_problem(root, image)
    if problem:
        raise RegisterRefused("register_link_image_missing", problem)
    with open(os.path.join(root, image), "rb") as f:
        data = f.read(IMAGE_MAX_BYTES + 1)
    if len(data) > IMAGE_MAX_BYTES:
        raise RegisterRefused("register_link_image_missing", "image_too_large")
    return data


def _stage_image(cfg: dict, name: str, data: bytes) -> str:
    """口座の repo に画像を写す（同じ中身なら同じ名前・あれば中身を確かめて使う）。返すのは repo の中の道。"""
    sha = hashlib.sha256(data).hexdigest()[:16]
    relative = f"{MEDIA_DIR}/{name}/{sha}.jpg"
    target = os.path.join(cfg["repo_dir"], relative)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        with open(target, "rb") as f:
            if hashlib.sha256(f.read(IMAGE_MAX_BYTES + 1)).hexdigest()[:16] != sha:
                raise RegisterRefused("register_link_image_missing", "image_conflict") from None
        return relative
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return relative


def _send_ig(item, cfg, rows, adapter_factory):
    from .. import core
    options = {"story": True} if item["kind"] == "ig_story" else None
    common = dict(text=item["text"], media_rows=rows, adapter_factory=adapter_factory, post_options=options,
                  origin={"via": "cli", "credential": None}, log=lambda _line: None)
    first = core.send_once(cfg["account"], production_flag=False, **common)
    if not first.digest:
        if first.action in TEMPORARY_IG:
            raise _Retry(_word(first.error or first.action) or first.action, getattr(first, "next_at", None))
        return "stopped", "register_link_send_failed", _word(first.error or first.action), None
    result = core.send_once(cfg["account"], production_flag=True, confirm=first.digest, **common)
    if result.action in TEMPORARY_IG:
        raise _Retry(_word(result.error or result.action) or result.action, getattr(result, "next_at", None))
    if result.action == "post" and result.post_id:
        return "sent", None, None, {"ref": result.post_id, "url": result.url}
    if result.action == "inflight":
        return "unknown", "register_link_unknown", _word(result.error or "inflight"), None
    return "stopped", "register_link_send_failed", _word(result.error or result.action), None


def _word(value) -> str | None:
    """断りの詳細に残す静的な語（符丁の頭だけ・本文や URL は残さない）。"""
    text = redact.redact(str(value or ""))
    head = text.split(":", 1)[0].split("（", 1)[0].strip()
    return head[:60] if head and all(c.isalnum() or c in "_./-" for c in head) else None


# ------------------------------------------------------------------ 送る

def _content(b, item, root) -> dict:
    """二段確認の digest に入る中身（画像は中身の sha256）。"""
    value = {"bundle": b["id"], "to": item["to"], "kind": item["kind"], "text": item.get("text"),
             "days": item.get("days"), "clear": item.get("clear"), "event": item.get("event"), "alt": item.get("alt")}
    if item["kind"] in ("ig", "ig_story") and item.get("image"):
        try:
            value["image_sha256"] = hashlib.sha256(_image_bytes(root, item["image"])).hexdigest()
        except RegisterRefused as exc:
            value["image_sha256"] = exc.detail
        except OSError:
            value["image_sha256"] = "local_io"
    return value


def _line(b, item) -> str:
    what = {"gbp_special": "Google マップの臨時の時間", "gbp_event": "Google マップのイベントの投稿",
            "ig": "IG のフィード", "ig_story": "IG のストーリー"}[item["kind"]]
    return f"{b['id']} {item['to']}（{what}）"


def send(name: str, *, by: str, now: datetime.datetime, bundle_id: str | None = None, auto: bool = False,
         confirm: str | None = None, transport=None, adapter_factory=None) -> dict:
    """束を出す。1 回ずつの道で `confirm` が digest と違えば何も変えずに一段目の値を返す（`stage: 1`）。"""
    if auto == (bundle_id is not None):
        raise RegisterRefused("register_link_args", "bundle_or_auto")
    cfg, root, decl = open_register(name)
    scanned = form.scan(root)
    records = ledger.read_confirmed(name)
    bound = bound_locations(scanned, records, now)
    claimed = bundle_mod.claimed_days(scanned, records, jst.to_jst(now).date())
    google = _Google(transport)
    with locked(name):
        decl = _fresh(name, decl, now)
        bundles = bundle_mod.read(name)
        settled = settle_interrupted(bundles)
        picked = _pick(name, bundles, decl, bundle_id=bundle_id, auto=auto, now=now)
        plans, stops, contents, notes = [], [], [], []
        for b, item in picked:
            stop = _recheck(name, root, decl, bundles, b, item, scanned, records, now, claimed)
            if stop is not None:
                stops.append((b, item, stop[0], stop[1], None))
                continue
            plan = None
            try:
                _outlet_ok(cfg, item, bound)
                if item["kind"] == "gbp_special":
                    plan = _special_plan(google, bundles, b, item, now)
            except _Stop as exc:
                stops.append((b, item, exc.state, exc.code, exc.detail))
                continue
            except RegisterRefused as exc:
                stops.append((b, item, "blocked", exc.code, exc.detail))
                continue
            except GbpRefused as exc:
                stops.append((b, item, "retry" if exc.code in TEMPORARY_GBP else "stopped",
                              "register_link_send_failed", exc.code))
                continue
            if plan and plan["google_changed"]:
                if auto:
                    stops.append((b, item, "stopped", "register_link_google_changed", plan["google_changed"][0]))
                    continue
                notes.append(f"{_line(b, item)}: Google 側の {'・'.join(plan['google_changed'])} の臨時の時間が、"
                             "THTH が前に出した値か出す値と違います。送ると置き換えます")
            if item.get("clear"):
                today = jst.to_jst(now).date().isoformat()
                gone = [d for d in item["clear"] if d >= today]
                if gone:
                    notes.append(f"{_line(b, item)}: 外す日: {'・'.join(gone)}（THTH が前に書いた臨時の時間を Google から外します）")
            if item["state"] == "unknown":
                notes.append(f"{_line(b, item)}: 前の回に出たか分からなかった物です。先に "
                             + ("thth gbp show / thth gbp posts" if item["kind"].startswith("gbp") else "thth inflight")
                             + " で今を確かめ、出ていなければ通してください（出ていれば二度出ます）")
            plans.append((b, item, plan))
            contents.append({**_content(b, item, root), "google_now": plan["seen"] if plan else None,
                             "was_unknown": item["state"] == "unknown"})
        digest = hashlib.sha256(json.dumps(contents, ensure_ascii=False, sort_keys=True, default=str)
                                .encode("utf-8")).hexdigest()[:DIGEST_LENGTH]
        if not auto and confirm != digest:
            if settled:
                bundle_mod.write(name, bundles)
            if confirm is not None:
                raise RegisterRefused("register_link_confirm_mismatch", digest=digest)
            return {"stage": 1, "register": name, "bundle": bundle_id, "digest": digest, "notes": notes,
                    "items": [dict(c, state="will_send") for c in contents],
                    "stops": [{"to": i["to"], "kind": i["kind"], "code": code, "detail": detail}
                              for _b, i, _s, code, detail in stops]}
        done = []
        for b, item, state, code, detail in stops:
            _mark(item, state, code, detail if isinstance(detail, str) else None, b=b, now=now)
            done.append((b, item))
        if bundles:
            bundle_mod.write(name, bundles)   # 止めた物と、同意が外れて waiting に戻した物
        for b, item, plan in plans:
            # 出す前に「出している途中」の印を書く（落ちたら次の回に unknown・二度出さない）。
            prior = item["state"]
            item.update(state="sending", sending_at=jst.iso(now))
            bundle_mod.write(name, bundles)
            _execute(name, root, b, item, plan, google, adapter_factory, now, prior=prior)
            item.pop("sending_at", None)
            bundle_mod.write(name, bundles)   # 1 件出すたびに残す
            done.append((b, item))
        rows = [{"bundle": b["id"], "row": b["row"], "trigger": b["trigger"], "to": i["to"], "kind": i["kind"],
                 "state": i["state"], "code": i.get("code"), "detail": i.get("detail"), "ref": i.get("ref")}
                for b, i in done]
        if done:
            counts = {}
            for row in rows:
                counts[row["state"]] = counts.get(row["state"], 0) + 1
            record("link_sent", name, by, {"bundles": [[], sorted({r["bundle"] for r in rows})],
                                            "states": [None, ",".join(f"{k}={v}" for k, v in sorted(counts.items()))],
                                            "mode": [None, "auto" if auto else "confirmed"]})
    waiting = sum(1 for b in bundles for i in b["items"] if i["state"] == "waiting")
    return {"stage": 2 if not auto else None, "register": name, "auto": auto, "digest": None if auto else digest,
            "items": rows, "notes": notes, "waiting": waiting}


def _mark(item, state, code, detail, *, b=None, now=None, next_at=None) -> None:
    """止まりを書く。`retry` は一時の断り: 状態は前のまま（同意ありは ready）で、回数でなく時間で見る。次に出せる時刻
    （`retry_after`）があればそれまで数えずに待ち、束の出さない期限（無ければ最初の断りから `RETRY_HOURS` 時間）を
    過ぎたら止める。"""
    if state == "retry":
        since = item.get("retry_since") or (jst.iso(now) if now else None)
        when = next_at if isinstance(next_at, str) else (jst.iso(next_at) if next_at is not None else None)
        limit = jst.parse((b or {}).get("not_after")) if b else None
        if limit is None and since:
            limit = jst.parse(since) + datetime.timedelta(hours=RETRY_HOURS)
        back = "ready" if item["state"] == "ready" else "waiting"
        over = now is not None and limit is not None and now >= limit
        item.update(code=code, detail=detail, retry_since=since, retry_after=when, state="stopped" if over else back)
        item.pop("tries", None)
        return
    item.update(state=state, code=code, detail=detail)


def _execute(name, root, b, item, plan, google, adapter_factory, now, *, prior) -> None:
    before_state = prior if prior in ("ready", "waiting") else "waiting"
    try:
        if item["kind"] == "gbp_special":
            # 書く直前に読み直す（計画のあとに人や Google が変えたら書かない・監査の 8）。同じ回に前の物で書いた分は
            # 手元の控え（google.locations）に重ねてあるので、それと比べる。
            location_id = linkage.location_id(item["to"])
            fresh = google.client.location(location_id)
            if _seen(gbp_api.periods_of(fresh)) != _seen(gbp_api.periods_of(google.location(item["to"]))):
                raise _Stop("stopped", "register_link_google_changed", "between_read_and_write")
            if not plan["ours"]:
                # 片づける日がもう過ぎていた（外す物が無い）。書かずに済ませる。
                item.update(state="sent", code=None, detail=None, sent_at=jst.iso(now), sent_value=[])
                return
            merged = _apply(gbp_api.periods_of(fresh), plan["ours"])
            google.client.patch_special(location_id, merged)
            fresh["specialHours"] = {"specialHourPeriods": merged}
            google.locations[item["to"]] = fresh
            item.update(state="sent", code=None, detail=None, sent_at=jst.iso(now), sent_value=plan["ours"])
            return
        if item["kind"] == "gbp_event":
            event = item["event"]
            body = gbp_api.post_body(item["text"], event=gbp_api.event_of(
                event["title"], gbp_api.parse_minute(event["start"]), gbp_api.parse_minute(event["end"])))
            location = linkage.location_id(item["to"])
            account = google.client.account_of(location)
            created = google.client.create_post(account, location, body)
            ref = created.get("name") if isinstance(created.get("name"), str) else None
            item.update(state="sent", code=None, detail=None, sent_at=jst.iso(now), ref=ref)
            return
        cfg = _ig_account(item)
        try:
            relative = _stage_image(cfg, name, _image_bytes(root, item["image"]))
        except OSError:
            # 網に出る前の手元の読み書き（画像を読む・写す・置き場を作る）の失敗。出ていないので止めて続ける（2 回目の I）。
            raise _Stop("stopped", "register_link_send_failed", "local_io") from None
        state, code, detail, out = _send_ig(item, cfg, [{"file": relative, "alt": item["alt"]}], adapter_factory)
        item.update(state=state, code=code, detail=detail)
        if out:
            item.update(sent_at=jst.iso(now), ref=out["ref"], url=out.get("url"))
    except _Stop as exc:
        item.update(state=exc.state, code=exc.code, detail=exc.detail)
    except _Retry as exc:
        item["state"] = before_state
        _mark(item, "retry", "register_link_send_failed", exc.detail, b=b, now=now, next_at=exc.next_at)
    except GbpRefused as exc:
        if exc.code == "gbp_write_unknown":
            item.update(state="unknown", code="register_link_unknown", detail=exc.code)
        elif exc.code in TEMPORARY_GBP:
            item["state"] = before_state
            _mark(item, "retry", "register_link_send_failed", exc.code, b=b, now=now)
        else:
            item.update(state="stopped", code="register_link_send_failed", detail=exc.code)
    except RegisterRefused as exc:
        if exc.code == "register_link_image_missing":
            item.update(state="refused", code=exc.code, detail=exc.detail)
        else:
            item.update(state="stopped", code=exc.code, detail=exc.detail)
    if item.get("state") == "sent":
        for key in ("retry_since", "retry_after"):
            item.pop(key, None)
