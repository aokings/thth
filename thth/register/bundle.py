"""1 つの変化 → 束（設計 `docs/設計_店の変化の連動_2026-11-19.md` §3.2・段 4）。**網に出ない。**

きっかけ（`triggers.find` の今出す頃・柵 `fence` を通った行）1 つから、宣言（`linkage.py`）の出し先のうち、その
きっかけを受けるものの数だけの「出す物」を 1 つの束にする。束は THTH の側（`register_state/<台帳>/`）に残し、
台帳のフォルダには書かない。

- 束は「外に出る中身」の指紋（`outward`・出し先ごとの 文・日・片づける日・イベント・画像の sha256・画像の説明）で
  決める（監査の 7: 行の字が変わっても外に出る中身が同じなら作らない）。同じ行・同じきっかけの**最後の束**と外に出る
  中身が同じなら作らない（同じ変化を二度出さない）。違えば、その行の何代目かを入れた id で出し直しの束を作り、前の束の
  まだ出ていない物を `superseded` にする（監査の 4: 行を元に戻しても、外に今出ている値と違えば作る）。出し先ごとに、
  その行の最後に出た物と中身が同じなら `unchanged`（出し直さない）。
- 臨時の時間の片づけ（監査の 5）: その行で前に Google へ書いた日のうち、今の行に無い日（日を動かした・縮めた）は
  `clear` に入れて specialHours から外す（指紋に入れる `clear` は日付で切らない・過ぎた日は出すときに飛ばす）。
  **ほかの今使える臨時の行が持つ日と、その日に最後に書いたのが別の行である日は外さない**（監査 2 回目の A）。
  行を退役した・違うと言った・日を窓の外へ動かした行も、片づけだけの束（`clear_only`）を作り、きっかけ
  `special_soon` の同意の範囲で出す。**行が見つからないだけのときは、同意があっても承認待ち**（`hold`・
  消したのか読めないだけかを決められないので、持ち主の二段確認を待つ・2 回目の C）。
- 出す物ごとの状態: `ready`（作った時に同意あり）・`waiting`（承認待ち）・`blocked`（直す所がある: 出し先が台帳の店と
  結ばれていない など・承認では通らない）・`sending`（出している途中・落ちたら次の回に `unknown`）・`sent`（出た）・
  `unchanged`（その行で前に出た中身と同じ・出し直さない）・`refused`（このままでは出せない・前向きな断り）・
  `stopped`（止めた・持ち主に聞く）・`unknown`（出たか分からない）・`superseded`・`expired`。
  同意は**出す時にもう一度読む**（取り消しは次の回から効く・`thth/link/send.py`）。
- 本文は `draft.body_lines`（外に出してよい欄だけ・**休みの理由は既定で入らない**）。
- 出した値の控え（`sent_value`）は THTH が自分で Google に書いた値だけ（Google から読んだ値は持たない・設計 §2）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os

from .. import jst, secrets_fs
from . import draft, fence, form, kinds, ledger, linkage, triggers as triggers_mod
from .errors import RegisterRefused

FILE = "linkage_bundles.json"
SCHEMA = "thth.linkage_bundles/1"
BUNDLES_MAX = 500
SPECIAL_DAYS_MAX = 14
# 画像の説明が行にも宣言にも無いときの既定（IG の alt・ストーリーは送らない）【推】。
DEFAULT_ALT = "お店からのお知らせの画像"
STATES = ("ready", "waiting", "blocked", "sending", "sent", "unchanged", "refused", "stopped", "unknown", "superseded",
          "expired")
# 外に出た（か出ているはずの）物。束の記録を短くするときも、これを持つ束は落とさない（監査の 10）。
KEEP_STATES = ("sending", "sent", "unchanged", "unknown")
OPEN_STATES = ("ready", "waiting", "blocked", "stopped")
KIND_OF = {("gbp", "special_soon"): "gbp_special", ("gbp", "event_soon"): "gbp_event"}


def path_for(name: str) -> str:
    return os.path.join(ledger.state_dir_for(name), FILE)


def read(name: str) -> list:
    try:
        with open(path_for(name), encoding="utf-8") as f:
            value = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        raise RegisterRefused("register_ledger_invalid", "bundles") from None
    rows = value.get("bundles") if isinstance(value, dict) and value.get("schema") == SCHEMA else None
    if not isinstance(rows, list) or not all(isinstance(b, dict) and isinstance(b.get("items"), list) for b in rows):
        raise RegisterRefused("register_ledger_invalid", "bundles")
    return rows


def write(name: str, bundles: list) -> None:
    secrets_fs.atomic_write_json(path_for(name), {"schema": SCHEMA, "bundles": trim(bundles)}, mode=0o600)


_SUMMARY_KEYS = ("to", "kind", "state", "outward", "sent_value", "sent_at", "ref", "code", "detail")


def trim(bundles: list) -> list:
    """`BUNDLES_MAX` を超えたら、古い順に、外に出た物の無い閉じた束を落とす。出た物を持つ束と、行ときっかけごとの最後の
    束は落とさない（二度出さない・前に書いた値と比べる材料）。まだ超えるなら、古い出た束を要約（出し先ごとの状態・
    指紋・書いた値・時刻・ref だけ）に寄せる。"""
    if len(bundles) <= BUNDLES_MAX:
        return bundles
    last = {}
    for b in bundles:
        last[(b.get("row"), b.get("trigger"))] = b["id"]
    over = len(bundles) - BUNDLES_MAX
    out = []
    for b in bundles:
        keep = (last.get((b.get("row"), b.get("trigger"))) == b["id"]
                or any(i.get("state") in KEEP_STATES + OPEN_STATES for i in b["items"]))
        if over > 0 and not keep:
            over -= 1
            continue
        out.append(b)
    if len(out) > BUNDLES_MAX:
        for b in out[:len(out) - BUNDLES_MAX]:
            # 開いた物（まだ出せる物）を持つ束は要約にしない（出すのに本文・日・画像が要る・2 回目の H）。
            if not b.get("summary") and not any(i.get("state") in OPEN_STATES + ("sending",) for i in b["items"]):
                b["items"] = [{k: i.get(k) for k in _SUMMARY_KEYS} for i in b["items"]]
                b["summary"] = True
    return out


def bundle_id(name: str, row_id: str, trigger: str, outward: str, generation: int = 0) -> str:
    raw = "\x1f".join(("thth.linkage_bundle/2", name, row_id, trigger, outward, str(generation)))
    return "b" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def kind_of(outlet_kind: str, trigger: str) -> str:
    if outlet_kind == "gbp":
        return KIND_OF[("gbp", trigger)]
    return "ig_story" if outlet_kind == "ig-story" else "ig"


def special_days(row: form.Row) -> list:
    """臨時の行の日ごとの値（`date`・`closed`・`open`・`close`）。理由は入れない。"""
    start = kinds.parse_day(row.head.get("date"))
    end = kinds.parse_day(row.head.get("end_date")) or start
    days = (end - start).days + 1
    if days > SPECIAL_DAYS_MAX:
        raise RegisterRefused("register_link_too_long", f"{days} days")
    closed = row.head.get("closed") == "true"
    out = []
    for n in range(days):
        day = start + datetime.timedelta(days=n)
        one = {"date": day.isoformat(), "closed": closed}
        if not closed:
            one.update(open=row.head["open"], close=row.head["close"])
        out.append(one)
    return out


def image_of(row: form.Row, outlet: dict) -> tuple[str | None, str]:
    """IG の画像（行の頭の `image` が先・無ければ宣言の既定）と説明。"""
    image = row.head.get("image") or outlet.get("image")
    alt = row.head.get("image_alt") or outlet.get("image_alt") or DEFAULT_ALT
    return image, draft.clean(alt, linkage.ALT_MAX)


def image_problem(root: str, image) -> str | None:
    """画像が使えるか（台帳のフォルダの中の、symlink でない JPEG のファイル）。使えなければ断りの詳細の語。"""
    if not image:
        return "no_image"
    if not linkage.relative_ok(image):
        return "image_path"
    path = os.path.join(root, image)
    if os.path.islink(path) or not os.path.isfile(path):
        return "image_missing"
    if not image.lower().endswith((".jpg", ".jpeg")):
        return "image_not_jpeg"
    return None


IMAGE_MAX_BYTES = 8_000_000


def image_sha(root: str, image) -> str | None:
    """画像の中身の sha256（使えない画像なら None）。外に出る中身の指紋に入れる（画像を差し替えたら出し直す）。"""
    if image_problem(root, image):
        return None
    try:
        with open(os.path.join(root, image), "rb") as f:
            return hashlib.sha256(f.read(IMAGE_MAX_BYTES + 1)).hexdigest()
    except OSError:
        return None


def outward_of(item: dict, root: str) -> str:
    """出し先ごとの、外に出る中身の指紋（臨時の時間は日と片づける日だけ・本文は Google に出さない）。"""
    value = {key: item.get(key) for key in ("to", "kind", "days", "clear", "event", "alt")}
    if item["kind"] != "gbp_special":
        value["text"] = item.get("text")
    if item["kind"] in ("ig", "ig_story"):
        value["image_sha256"] = image_sha(root, item.get("image"))
    if item.get("state") == "refused":
        value["refused"] = [item.get("code"), item.get("detail")]
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _line_of(bundles: list, row_id: str, trigger: str) -> list:
    return [b for b in bundles if b.get("row") == row_id and b.get("trigger") == trigger]


def last_sent_item(bundles: list, row_id: str, to: str, kind: str):
    """その行・その出し先で最後に出た物（`sent`。無ければ None）。"""
    found = None
    for b in bundles:
        if b.get("row") != row_id:
            continue
        for item in b["items"]:
            if item.get("to") == to and item.get("kind") == kind and item.get("state") == "sent":
                found = item
    return found


def written_days(bundles: list, row_id: str, to: str) -> list:
    """その行が最後に Google へ書いた臨時の日（片づけた日は入れない）。"""
    last = last_sent_item(bundles, row_id, to, "gbp_special")
    return sorted(one["date"] for one in (last or {}).get("sent_value") or [] if one.get("period") is not None)


def last_writer(bundles: list, to: str, day: str) -> str | None:
    """その店・その日に最後に Google へ書いた（片づけも含む）行の番号。"""
    found = None
    for b in bundles:
        for item in b["items"]:
            if item.get("to") == to and item.get("state") == "sent" and item.get("kind") == "gbp_special":
                if any(one.get("date") == day for one in item.get("sent_value") or []):
                    if found is None or item.get("sent_at", "") >= found[0]:
                        found = (item.get("sent_at", ""), b.get("row"))
    return found[1] if found else None


def claimed_days(scanned: dict, records: dict, today) -> dict:
    """今使える（柵を通る）臨時の行が持つ日 → その行の番号の集まり。片づけでほかの行の日を外さないため。"""
    out: dict = {}
    for row in scanned["rows"]:
        if row.kind != "special" or row.id is None or fence.state(row, records, today) is not None:
            continue
        try:
            days = special_days(row)
        except RegisterRefused:
            continue
        for day in days:
            out.setdefault(day["date"], set()).add(row.id)
    return out


def clear_days(bundles: list, row_id: str, to: str, keep: set, claimed: dict) -> list:
    """その行が前に書いた日のうち、今の行に無く（`keep` の外）、ほかの今使える行が持たず、最後に書いたのもその行の日。"""
    return [d for d in written_days(bundles, row_id, to)
            if d not in keep and not (claimed.get(d, set()) - {row_id}) and last_writer(bundles, to, d) == row_id]


def item_for(name: str, root: str, decl: dict, outlet: dict, trigger: str, row: form.Row, now,
             bundles: list | None = None, claimed: dict | None = None) -> dict:
    kind = kind_of(outlet["kind"], trigger)
    item = {"to": outlet["to"], "kind": kind, "state": "waiting", "code": None, "detail": None,
            "consent": linkage.consent_for(decl, name, outlet["to"], trigger, now) is not None}
    try:
        text = "\n".join(draft.body_lines(trigger, row))
        if kind == "gbp_special":
            item["days"] = special_days(row)
            now_days = {d["date"] for d in item["days"]}
            item["clear"] = clear_days(bundles or [], row.id, outlet["to"], now_days, claimed or {})
        elif kind == "gbp_event":
            f = form.outward(row)
            item["event"] = {"title": draft.clean(f.get("name"), 58), "start": f["starts"], "end": f["ends"]}
        else:
            image, alt = image_of(row, outlet)
            problem = image_problem(root, image)
            item.update(image=image if linkage.relative_ok(image) else None, alt=alt)
            if problem:
                raise RegisterRefused("register_link_image_missing", problem)
        item["text"] = text
    except RegisterRefused as exc:
        item.update(state="refused", code=exc.code, detail=exc.detail)
        item.setdefault("text", None)
        return item
    item["state"] = "ready" if item["consent"] else "waiting"
    return item


def _new_bundle(name, root, bundles, row_id, trigger, digest, not_after, items, now, *, clear_only=False):
    """外に出る中身がその行の最後の束と違うときだけ束を作って足す（返すのは作った束か None）。"""
    for item in items:
        item["outward"] = outward_of(item, root)
    outward = hashlib.sha256("\x1f".join(sorted(i["outward"] for i in items)).encode("utf-8")).hexdigest()[:16]
    line = _line_of(bundles, row_id, trigger)
    if line and line[-1].get("outward") == outward:
        return None
    # 代は、その行の束の最大の代に 1 を足す（古い束を落としたあとも id が重ならない・2 回目の H）。
    generation = max((int(b.get("generation") or 0) for b in line), default=-1) + 1
    ident = bundle_id(name, row_id, trigger, outward, generation)
    while any(b["id"] == ident for b in bundles):
        generation += 1
        ident = bundle_id(name, row_id, trigger, outward, generation)
    for item in items:
        last = last_sent_item(line, row_id, item["to"], item["kind"])
        if last is not None and last.get("outward") == item["outward"] and item["state"] in ("ready", "waiting"):
            item.update(state="unchanged", code=None, detail=None, ref=last.get("ref"))
    replaces = None
    for old in line:
        replaces = old["id"]
        for item in old["items"]:
            if item["state"] in OPEN_STATES + ("refused",):
                item.update(state="superseded", code=None, detail=ident)
    bundle = {"id": ident, "row": row_id, "trigger": trigger, "digest": digest, "outward": outward,
              "generation": generation, "not_after": not_after, "made_at": jst.iso(now), "replaces": replaces,
              "items": items}
    if clear_only:
        bundle["clear_only"] = True
    bundles.append(bundle)
    return bundle


def current_days(row, records, today) -> set | None:
    """片づけで比べる、今の行の臨時の日（行が無い・退役・違うと言った → 空。認め直し待ちなど決められない → None）。
    行が無いときの片づけは、呼ぶ側が承認待ちに倒す（`hold`）。"""
    if row is None:
        return set()
    code = fence.state(row, records, today)
    if code in ("register_row_retired", "register_row_denied"):
        return set()
    if code is not None or row.kind != "special":
        return None
    try:
        return {d["date"] for d in special_days(row)}
    except RegisterRefused:
        return None


def _stale(name, root, bundles, scanned, records, decl, now, handled, claimed) -> list:
    """片づけだけの束（行を消した・退役した・日を窓の外へ動かした行の、前に書いた日。先の日が 1 つでもあるときだけ）。"""
    today = jst.to_jst(now).date()
    made = []
    outlets = [o for o in decl["outlets"] if o["kind"] == "gbp" and "special_soon" in o["triggers"]]
    rows = sorted({b.get("row") for b in bundles if b.get("trigger") == "special_soon"} - {None})
    for row_id in rows:
        if row_id in handled:
            continue
        row = form.find(scanned["rows"], row_id)
        days = current_days(row, records, today)
        if days is None:
            continue
        items = []
        for outlet in outlets:
            stale = clear_days(bundles, row_id, outlet["to"], days, claimed)
            if stale and max(stale) >= today.isoformat():
                consent = row is not None and linkage.consent_for(decl, name, outlet["to"], "special_soon", now) is not None
                item = {"to": outlet["to"], "kind": "gbp_special", "days": [], "clear": stale, "text": None,
                        "code": None, "detail": None, "consent": consent}
                if row is None:
                    item["hold"] = "row_missing"   # 消したのか読めないだけか決められない（同意があっても承認待ち）
                items.append(item)
        for item in items:
            item["state"] = "ready" if item["consent"] else "waiting"
        if not items:
            continue
        last_day = max(datetime.date.fromisoformat(d) for i in items for d in i["clear"])
        not_after = jst.iso(datetime.datetime.combine(last_day, datetime.time(23, 59, 59), tzinfo=jst.JST))
        digest = form.fingerprint(row.text) if row is not None else None
        bundle = _new_bundle(name, root, bundles, row_id, "special_soon", digest, not_after, items, now, clear_only=True)
        if bundle is not None:
            made.append(bundle)
    return made


def make(name: str, root: str, scanned: dict, records: dict, decl: dict, now: datetime.datetime) -> tuple[list, list]:
    """`(新しい束, 全部の束)`。書くのは呼ぶ側（`write`）。宣言の出し先が無いきっかけは束にしない。"""
    bundles = read(name)
    made, handled = [], set()
    claimed = claimed_days(scanned, records, jst.to_jst(now).date())
    for found in triggers_mod.find(scanned["rows"], records, now):
        if found["code"] is not None:
            continue   # 柵を通らない行（推測・見直し日を過ぎた・認めたあとに変わった など）からは作らない
        outlets = [o for o in decl["outlets"] if found["trigger"] in o["triggers"]]
        if not outlets:
            continue
        row = form.find(scanned["rows"], found["row"])
        if found["trigger"] == "special_soon":
            handled.add(row.id)
        items = [item_for(name, root, decl, o, found["trigger"], row, now, bundles=bundles, claimed=claimed)
                 for o in outlets]
        bundle = _new_bundle(name, root, bundles, row.id, found["trigger"], form.fingerprint(row.text),
                             found["not_after"], items, now)
        if bundle is not None:
            made.append(bundle)
    made += _stale(name, root, bundles, scanned, records, decl, now, handled, claimed)
    return made, bundles


def last_sent_value(bundles: list, to: str, day: str, *, before: str | None = None):
    """その店・その日に THTH が最後に Google へ書いた臨時の時間の値（無ければ None・片づけた日は `period` が None）。"""
    found = None
    for b in bundles:
        if before is not None and b["id"] == before:
            continue
        for item in b["items"]:
            if item.get("to") == to and item.get("state") == "sent" and item.get("kind") == "gbp_special":
                for one in item.get("sent_value") or []:
                    if one.get("date") == day and (found is None or item.get("sent_at", "") >= found[0]):
                        found = (item.get("sent_at", ""), one)
    return found[1] if found else None


def summary(bundle: dict) -> dict:
    counts = {}
    for item in bundle["items"]:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    return counts
