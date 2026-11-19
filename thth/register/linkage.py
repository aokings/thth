"""店の変化の連動の宣言（設計 `docs/設計_店の変化の連動_2026-11-19.md` §3.1・段 3）。**読むだけ・網に出ない。**

持ち主が台帳のフォルダの `.thth/linkage.json`（`thth.linkage/1`）に書く。THTH はこのファイルを**書かない**
（同意を書き換えない）。取り消しは `thth link consent <台帳> --revoke --by <名前>`（二段確認）で THTH の側に記録する
のが正の道で、取り消した指紋は以後二度と自動に使わない（続けるなら新しく書いた同意を控え直す）。ファイルから同意を
消す・`revoked_at` を書くのも、出す回ごとに控えから外して取り消しとして記録する（同じ文を戻しても生き返らない）。

```json
{"schema": "thth.linkage/1",
 "outlets": [
   {"to": "gbp:locations/6353608778993100589", "triggers": ["special_soon", "event_soon"]},
   {"to": "ig:mihon-ig", "triggers": ["special_soon", "event_soon"], "image": "画像/告知.jpg", "image_alt": "店の入口"},
   {"to": "ig-story:mihon-ig", "triggers": ["special_soon"]}],
 "consent": [
   {"register": "cafe-sample", "to": "gbp:locations/6353608778993100589", "triggers": ["special_soon"],
    "until": "2027-03-31", "by": "店主の名前", "at": "2026-11-19T12:00:00+09:00"}]}
```

- 出し先（`outlets`）: `gbp:<店>`（Google マップ・臨時の時間とイベントの投稿）・`ig:<口座>`（IG のフィード）・
  `ig-story:<口座>`（IG のストーリー）。`<店>` は `locations/…` か `accounts/…/locations/…`、`<口座>` は THTH の口座の名前。
  gbp が受けるきっかけは `special_soon`（臨時の時間）と `event_soon`（イベントの投稿）だけ。
  `image`・`image_alt` は IG の既定の画像（台帳のフォルダの中の相対の道）。行の頭の `image`・`image_alt` が先。
- 同意（`consent`）: **前もっての具体的で明示の同意**（Google の決まり・設計 §2）。店（`register`＝THTH の台帳の
  名前）・出し先（`to`・宣言した出し先のどれか）・きっかけ（`triggers`・その出し先が受けるもの）・期限（`until`・
  その日の終わりまで・`at` から最長 `CONSENT_MAX_DAYS` 日）・書いた人（`by`）と日時（`at`・時差つき）の全部が要る。
  1 つでも外れる変化は自動で出さず、1 回ずつの二段確認に回す。無い・期限を過ぎた・取り消した同意は無いのと同じ。
- **同意の控え**（監査の 3）: ファイルは素の文字なので、書かれているだけでは持ち主の手と言えない。持ち主が
  `thth link consent <台帳> --by <名前>`（`--by` は必須）の二段確認（一段目で同意の全文と digest を見せる）で認めた同意だけを、その
  指紋（`fingerprint`・台帳の名前と同意の全部の欄から作る）で THTH の側（`register_state/<台帳>/linkage_consent.json`）に
  控える。自動に使うのは**控えの指紋と合う同意だけ**。ファイルだけに書かれた同意・書き換えた同意（取り消しの
  `revoked_at` を足したものも指紋が変わる）は「承認待ち」に倒す。行の「認めた」記録（`fence`）と同じ考え。
"""
from __future__ import annotations

import datetime
import json
import os
import re

import hashlib

from .. import accounts, jst, secrets_fs
from . import ledger, triggers as triggers_mod
from .errors import RegisterRefused

DIR = ".thth"
FILE = "linkage.json"
KEPT_FILE = "linkage_consent.json"
LOCK_FILE = "linkage.lock"
KEPT_SCHEMA = "thth.linkage_consent/1"
SCHEMA = "thth.linkage/1"
FILE_MAX_BYTES = 64 * 1024
OUTLETS_MAX = 16
CONSENT_MAX = 32
BY_MAX = 64
ALT_MAX = 200
# 同意の期限の上限（設計 §5「推: 無期限にせず、最長 1 年」・閏年を入れて 366 日）。
CONSENT_MAX_DAYS = 366
# 出し先の種類 → 受けるきっかけ。
OUTLET_TRIGGERS = {
    "gbp": ("special_soon", "event_soon"),
    "ig": triggers_mod.TRIGGERS,
    "ig-story": triggers_mod.TRIGGERS,
}
_LOCATION = re.compile(r"(?:accounts/[0-9]{1,32}/)?locations/([0-9]{1,32})\Z")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")

PROBLEM_TEXT = {
    "linkage_missing": f"台帳のフォルダに .thth/{FILE} がありません。出し先と同意を書いたファイルを置いてください（形は thth link check の注）",
    "linkage_unreadable": "linkage.json を UTF-8 の JSON として読めません。JSON の形（括弧・引用符・, の数）を直してください",
    "linkage_too_large": f"linkage.json は {FILE_MAX_BYTES // 1024} KB までにしてください",
    "linkage_schema": f"頭に \"schema\": \"{SCHEMA}\" を書いてください",
    "linkage_key": "使える鍵は schema・outlets・consent だけです（綴りを確かめてください）",
    "outlets_form": f"outlets は出し先の並び（{OUTLETS_MAX} まで）にしてください",
    "outlet_to": "to は gbp:locations/<数字>・ig:<口座>・ig-story:<口座> のどれかにしてください",
    "outlet_twice": "同じ to の出し先が 2 つあります。1 つにまとめてください",
    "outlet_triggers": "triggers は、その出し先が受けるきっかけの並びにしてください（gbp は special_soon と event_soon だけ）",
    "outlet_key": "出し先に使える鍵は to・triggers・image・image_alt だけです",
    "outlet_image": "image は台帳のフォルダの中の相対の道（.. と . で始まる名を使わない）にし、gbp には付けないでください",
    "outlet_image_alt": f"image_alt は 1 行・{ALT_MAX} 字までの画像の説明にしてください",
    "consent_form": f"consent は同意の並び（{CONSENT_MAX} まで）にしてください",
    "consent_key": "同意に使える鍵は register・to・triggers・until・by・at・revoked_at だけです",
    "consent_missing": "同意には register・to・triggers・until・by・at の 6 つが全部要ります（具体的に書いた同意だけを自動に使います）",
    "consent_register": "register は、この台帳を THTH に控えた名前（thth register add の名前）にしてください",
    "consent_to": "同意の to は、outlets に書いた出し先のどれかにしてください",
    "consent_triggers": "同意の triggers は、その出し先の triggers の中から選んでください",
    "consent_until": "until は YYYY-MM-DD の日付で、at の日と同じか後にしてください",
    "consent_too_long": f"同意の期限は at から {CONSENT_MAX_DAYS} 日までにしてください（無期限の同意は受けません）",
    "consent_by": f"by は同意を書いた人の名前（1 行・{BY_MAX} 字まで）にしてください",
    "consent_at": "at は時差つきの日時（例 2026-11-19T12:00:00+09:00）にしてください",
    "consent_revoked_at": "revoked_at は時差つきの日時にしてください（取り消すなら、その同意を消しても同じです）",
}


def path_of(root: str) -> str:
    return os.path.join(root, DIR, FILE)


def exists(root: str) -> bool:
    return os.path.lexists(path_of(root))


def outlet_kind(to) -> str | None:
    """`gbp:…`・`ig:…`・`ig-story:…` の頭の語（形が違えば None）。"""
    if not isinstance(to, str) or ":" not in to:
        return None
    head, _, rest = to.partition(":")
    if head == "gbp":
        return head if _LOCATION.fullmatch(rest) else None
    if head in ("ig", "ig-story"):
        return head if accounts.name_is_safe(rest) else None
    return None


def target_of(to: str) -> str:
    return to.partition(":")[2]


def location_id(to: str) -> str:
    return _LOCATION.fullmatch(target_of(to)).group(1)


def _plain_text(value, limit) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= limit and not any(
        ord(c) < 32 or ord(c) == 127 for c in value)


def relative_ok(value) -> bool:
    """台帳のフォルダの中の相対の道（`..`・`.` で始まる名・絶対の道・\\ を使わない）。"""
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value or value.startswith("/"):
        return False
    parts = value.split("/")
    return all(p and not p.startswith(".") for p in parts) and not any(ord(c) < 32 for c in value)


def _when(value):
    found = jst.parse(value) if isinstance(value, str) else None
    return found if found is not None and found.tzinfo is not None else None


def _triggers(value, allowed) -> list | None:
    if not isinstance(value, list) or not value or len(value) > len(triggers_mod.TRIGGERS):
        return None
    if not all(isinstance(t, str) and t in allowed for t in value) or len(set(value)) != len(value):
        return None
    return list(value)


def parse(data, register: str) -> tuple[dict, list]:
    """`(宣言, 問題の語の並び)`。宣言は形の合った出し先と同意だけ（問題があれば使わない）。"""
    problems: list = []
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {"outlets": [], "consent": []}, ["linkage_schema"]
    if set(data) - {"schema", "outlets", "consent"}:
        problems.append("linkage_key")
    outlets, consent = [], []
    raw_outlets = data.get("outlets", [])
    if not isinstance(raw_outlets, list) or len(raw_outlets) > OUTLETS_MAX:
        problems.append("outlets_form")
        raw_outlets = []
    seen = set()
    for index, raw in enumerate(raw_outlets):
        where = f":{index}"
        if not isinstance(raw, dict):
            problems.append("outlet_to" + where)
            continue
        if set(raw) - {"to", "triggers", "image", "image_alt"}:
            problems.append("outlet_key" + where)
        kind = outlet_kind(raw.get("to"))
        if kind is None:
            problems.append("outlet_to" + where)
            continue
        if raw["to"] in seen:
            problems.append("outlet_twice" + where)
            continue
        seen.add(raw["to"])
        found = _triggers(raw.get("triggers"), OUTLET_TRIGGERS[kind])
        if found is None:
            problems.append("outlet_triggers" + where)
            continue
        if "image" in raw and (kind == "gbp" or not relative_ok(raw["image"])):
            problems.append("outlet_image" + where)
        if "image_alt" in raw and not _plain_text(raw["image_alt"], ALT_MAX):
            problems.append("outlet_image_alt" + where)
        outlets.append({"to": raw["to"], "kind": kind, "triggers": found,
                        "image": raw.get("image"), "image_alt": raw.get("image_alt")})
    by_to = {o["to"]: o for o in outlets}
    raw_consent = data.get("consent", [])
    if not isinstance(raw_consent, list) or len(raw_consent) > CONSENT_MAX:
        problems.append("consent_form")
        raw_consent = []
    for index, raw in enumerate(raw_consent):
        where = f":{index}"
        if not isinstance(raw, dict):
            problems.append("consent_missing" + where)
            continue
        if set(raw) - {"register", "to", "triggers", "until", "by", "at", "revoked_at"}:
            problems.append("consent_key" + where)
        if any(key not in raw for key in ("register", "to", "triggers", "until", "by", "at")):
            problems.append("consent_missing" + where)
            continue
        before = len(problems)
        if raw["register"] != register:
            problems.append("consent_register" + where)
        outlet = by_to.get(raw["to"])
        if outlet is None:
            problems.append("consent_to" + where)
        found = _triggers(raw["triggers"], outlet["triggers"]) if outlet else None
        if outlet is not None and found is None:
            problems.append("consent_triggers" + where)
        at = _when(raw["at"])
        if at is None:
            problems.append("consent_at" + where)
        until = (datetime.date.fromisoformat(raw["until"])
                 if isinstance(raw["until"], str) and _DAY.fullmatch(raw["until"]) else None)
        if until is None or (at is not None and until < jst.to_jst(at).date()):
            problems.append("consent_until" + where)
        elif at is not None and (until - jst.to_jst(at).date()).days > CONSENT_MAX_DAYS:
            problems.append("consent_too_long" + where)
        if not _plain_text(raw["by"], BY_MAX):
            problems.append("consent_by" + where)
        revoked = None
        if "revoked_at" in raw:
            revoked = _when(raw["revoked_at"])
            if revoked is None:
                problems.append("consent_revoked_at" + where)
        if len(problems) == before:
            entry = {"register": register, "to": raw["to"], "triggers": found, "until": raw["until"],
                     "by": raw["by"].strip(), "at": jst.iso(at), "revoked_at": jst.iso(revoked) if revoked else None}
            entry["fingerprint"] = fingerprint(entry)
            consent.append(entry)
    return {"outlets": outlets, "consent": consent}, problems


def read(root: str, register: str) -> tuple[dict | None, list]:
    """`(宣言 or None（ファイルが無い）, 問題の語)`。symlink は辿らない。"""
    path = path_of(root)
    if not os.path.lexists(path):
        return None, []
    if os.path.islink(path) or not os.path.isfile(path):
        return {"outlets": [], "consent": []}, ["linkage_unreadable"]
    try:
        with open(path, "rb") as f:
            raw = f.read(FILE_MAX_BYTES + 1)
    except OSError:
        return {"outlets": [], "consent": []}, ["linkage_unreadable"]
    if len(raw) > FILE_MAX_BYTES:
        return {"outlets": [], "consent": []}, ["linkage_too_large"]
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"outlets": [], "consent": []}, ["linkage_unreadable"]
    return parse(data, register)


def load(root: str, register: str) -> dict | None:
    """形の合った宣言（無ければ None）。問題が 1 つでもあれば断る（崩れた宣言から自動で出さない）。"""
    decl, problems = read(root, register)
    if problems:
        raise RegisterRefused("register_linkage_invalid", problems[0], problems=problems)
    return decl


def problem_text(word: str) -> str:
    return PROBLEM_TEXT.get(word.split(":", 1)[0], "linkage.json の形を直してください")


def consent_state(entry: dict, now: datetime.datetime) -> str:
    """`valid`・`not_yet`（at が先）・`expired`（until を過ぎた）・`revoked`（取り消した）。"""
    if entry.get("revoked_at") and jst.parse(entry["revoked_at"]) <= now:
        return "revoked"
    if jst.parse(entry["at"]) > now:
        return "not_yet"
    if jst.to_jst(now).date() > datetime.date.fromisoformat(entry["until"]):
        return "expired"
    return "valid"


def consent_for(decl: dict | None, register: str, to: str, trigger: str, now: datetime.datetime) -> dict | None:
    """その店・出し先・きっかけで、今効いている同意（無ければ None）。全部が合い、**控えの指紋と合う**ものだけ
    （`decl["kept"]`・`with_kept` が足す。控えの無い宣言では、どの同意も効かない）。"""
    kept = (decl or {}).get("kept") or frozenset()
    for entry in (decl or {}).get("consent", []):
        if (entry["register"] == register and entry["to"] == to and trigger in entry["triggers"]
                and consent_state(entry, now) == "valid" and entry.get("fingerprint") in kept):
            return entry
    return None


# ------------------------------------------------------------------ 同意の控え（持ち主の二段確認で認めた同意の指紋）

def fingerprint(entry: dict) -> str:
    """同意の全部の欄（台帳・出し先・きっかけ・期限・書いた人と日時・取り消し）から作る指紋。1 字でも変われば別の物。"""
    raw = json.dumps({key: entry.get(key) for key in ("register", "to", "triggers", "until", "by", "at", "revoked_at")},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(("thth.linkage_consent/1\x1f" + raw).encode("utf-8")).hexdigest()[:24]


def kept_path(name: str) -> str:
    return os.path.join(ledger.state_dir_for(name), KEPT_FILE)


def read_kept(name: str) -> list:
    """控えた同意（`fingerprint`・`to`・`triggers`・`until`・`kept_by`・`kept_at`）。無ければ空。"""
    try:
        with open(kept_path(name), encoding="utf-8") as f:
            value = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        raise RegisterRefused("register_ledger_invalid", "consent") from None
    rows = value.get("kept") if isinstance(value, dict) and value.get("schema") == KEPT_SCHEMA else None
    if not isinstance(rows, list) or not all(isinstance(r, dict) and isinstance(r.get("fingerprint"), str) for r in rows):
        raise RegisterRefused("register_ledger_invalid", "consent")
    return rows


def write_kept(name: str, rows: list) -> None:
    secrets_fs.atomic_write_json(kept_path(name), {"schema": KEPT_SCHEMA, "kept": rows}, mode=0o600)


def with_kept(decl: dict, name: str) -> dict:
    """宣言に控えの指紋の集まり（`kept`・取り消していない物）と取り消した指紋（`revoked`）を足す（`consent_for` が見る）。"""
    rows = read_kept(name)
    return dict(decl, kept=frozenset(r["fingerprint"] for r in rows if not r.get("revoked_at")),
                revoked=frozenset(r["fingerprint"] for r in rows if r.get("revoked_at")))


def unkept(decl: dict, now: datetime.datetime) -> list:
    """まだ控えていない、効きうる同意（期限切れ・取り消し済み・THTH の側で取り消した指紋は入れない）。"""
    kept = decl.get("kept") or frozenset()
    revoked = decl.get("revoked") or frozenset()
    return [c for c in decl["consent"] if c["fingerprint"] not in kept and c["fingerprint"] not in revoked
            and consent_state(c, now) in ("valid", "not_yet")]


def revoke_rows(rows: list, fingerprints, *, by: str, at: str, why: str) -> list:
    """控えのうち `fingerprints` を取り消しにする（消さない・取り消した時刻と人と訳を足す）。取り消した物の指紋。"""
    done = []
    for row in rows:
        if row["fingerprint"] in fingerprints and not row.get("revoked_at"):
            row.update(revoked_at=at, revoked_by=by, revoked_why=why)
            done.append(row["fingerprint"])
    return done


def retire_missing(name: str, decl: dict, *, at: str) -> list:
    """ファイルから消えた（書き換えた・`revoked_at` を足したも含む）控えを取り消しにする（出す回ごと・同じ文を戻しても
    生き返らない・2 回目の B）。取り消した指紋。書くのは変わったときだけ。"""
    rows = read_kept(name)
    present = {c["fingerprint"] for c in decl["consent"] if consent_state(c, jst.parse(at)) != "revoked"}
    gone = {r["fingerprint"] for r in rows if not r.get("revoked_at")} - present
    done = revoke_rows(rows, gone, by="thth", at=at, why="removed_from_file")
    if done:
        write_kept(name, rows)
    return done


def reset_kept(name: str, *, at: str) -> str | None:
    """壊れた控えを別の名前に退けて（消さない）空から始める。退けた先の名前（無ければ None）。"""
    path = kept_path(name)
    if not os.path.lexists(path):
        return None
    aside = f"{path}.broken-{at.replace(':', '').replace('+', '_')}"
    os.replace(path, aside)
    return os.path.basename(aside)
