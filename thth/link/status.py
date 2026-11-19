"""束の現在地（設計 店の変化の連動 段 6）: `thth scenario` の `links` と、`thth events` の 1 行（hub が読む）。**読むだけ。**

- `links_for(project)`: その project の台帳ごとの束（id・行・きっかけ・作った時刻・出し先ごとの状態と符丁）。本文は入れない。
- `events_for(project, make)`: `link_published`（出した・起きた時刻つき・出し先と種類）と `link_waiting`（承認待ち・
  止めた・出たか分からない物のある束・続いている状態）。Google の決まりの「変えたことは 48 時間以内に持ち主に知らせる」を
  hub が読む口で満たす（THTH は押さない・`thth/events_cli.py` の頭の注）。
"""
from __future__ import annotations

import hashlib

from .. import jst
from ..register import bundle as bundle_mod, ledger
from ..register.errors import RegisterRefused

EVENT_PREFIX = "thth.linkage/1"
KIND_JA = {"gbp_special": "Google マップの臨時の時間", "gbp_event": "Google マップのイベントの投稿",
           "ig": "IG のフィード", "ig_story": "IG のストーリー"}
# 持ち主の手が要る物（承認待ち・直す所がある・止めた・出たか分からない・画像が無くて断った）。
ATTENTION = ("waiting", "blocked", "stopped", "unknown", "refused")


def _names(project: str) -> list:
    try:
        return ledger.of_project(project)
    except RegisterRefused:
        return []


def links_for(project: str, cannot: set | None = None) -> list:
    out = []
    for name in _names(project):
        try:
            bundles = bundle_mod.read(name)
        except RegisterRefused:
            if cannot is not None:
                cannot.add("links_unreadable")
            continue
        for b in bundles:
            out.append({"register": name, "bundle": b["id"], "row": b["row"], "trigger": b["trigger"],
                        "made_at": b.get("made_at"), "not_after": b.get("not_after"), "replaces": b.get("replaces"),
                        "states": bundle_mod.summary(b),
                        "items": [{"to": i["to"], "kind": i["kind"], "state": i["state"], "code": i.get("code"),
                                   "sent_at": i.get("sent_at")} for i in b["items"]]})
    return out


def event_id(*parts) -> str:
    raw = "|".join((EVENT_PREFIX,) + tuple(str(p) for p in parts))
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def events_for(project: str, make, *, now) -> tuple:
    """`make(kind, account, occurred, ref, summary, _id, ongoing, **extra)`。`(出来事, 読めなかった符丁)`。

    待ちの 1 行は、出さない期限の前の束だけ（期限を過ぎた束は、もう出せないので人を呼ばない）。"""
    out, cannot = [], set()
    for row in links_for(project, cannot):
        until = jst.parse(row.get("not_after"))
        open_now = until is not None and now <= until
        waiting = [i for i in row["items"] if i["state"] in ATTENTION] if open_now else []
        for item in row["items"]:
            at = jst.parse(item.get("sent_at")) if item["state"] == "sent" else None
            if at is not None:
                out.append(make("link_published", project, at, row["bundle"],
                                f"台帳 {row['register']} の {row['trigger']} を {KIND_JA[item['kind']]} に出しました",
                                event_id(row["register"], row["bundle"], item["to"], "sent"), False,
                                register=row["register"], to=item["to"], link_kind=item["kind"], project=project))
        if waiting:
            states = "・".join(sorted({i["state"] for i in waiting}))
            out.append(make("link_waiting", project, None, row["bundle"],
                            f"台帳 {row['register']} の束 {row['bundle']}（{row['trigger']}）に {len(waiting)} 件"
                            f"（{states}）。thth link show {row['register']} で見て、承認か直しを",
                            event_id(row["register"], row["bundle"], states, len(waiting)), True,
                            register=row["register"], project=project, count=len(waiting)))
    return out, cannot
