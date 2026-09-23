"""知らせる先が無いことを言う（設計 3.3.0 A4）。**読むだけ・値は出さない。**

timer に載る（`scheduled`・既定 true）account が止まったとき、知らせる経路は 2 つ:

- 死活通知（`HEALTHCHECK_URL`・`healthcheck.configured_url()`）
- 運用通知（SMTP と宛先・`incident.readiness()`）

両方とも無ければ、止まっても誰にも届かない——`no_notification_route`。片方だけ
無ければ、欠けている側を名指しして `notification_route_partial: healthcheck`
（死活通知が無い）か `notification_route_partial: incident`（運用通知が無い）。
9/21 の 46 本の停止は、どちらが設定されていても通知されなかった（run が成功扱い
だった・A1 で直す）。ここは「経路そのものが無い」を黙らないための口。

URL・宛先・認証値は返さない（有無だけ）。
"""
from __future__ import annotations

NO_ROUTE = "no_notification_route"
PARTIAL = "notification_route_partial"


def scheduled(cfg) -> bool:
    return bool((cfg or {}).get("scheduled", True))


def status(cfg) -> dict:
    """`{"healthcheck": bool, "incident": bool, "cannot_say": 静的な符丁 | None}`。"""
    from . import healthcheck, incident
    try:
        has_healthcheck = healthcheck.configured_url(cfg) is not None
    except Exception:  # noqa: BLE001 — 読めない env は「無い」と同じく知らせられない
        has_healthcheck = False
    ready = incident.readiness(cfg)
    has_incident = bool(ready.get("smtp_configured")
                        and (ready.get("user_configured") or ready.get("admin_configured")))
    if not has_healthcheck and not has_incident:
        reason = NO_ROUTE
    elif not has_healthcheck:
        reason = f"{PARTIAL}: healthcheck"
    elif not has_incident:
        reason = f"{PARTIAL}: incident"
    else:
        reason = None
    return {"healthcheck": has_healthcheck, "incident": has_incident, "cannot_say": reason}


def missing(configs: dict) -> list:
    """scheduled なのに経路が欠けている account（`[{account, cannot_say}]`・名前順）。"""
    out = []
    for name in sorted(configs):
        cfg = configs[name]
        if not scheduled(cfg):
            continue
        reason = status(cfg)["cannot_say"]
        if reason is not None:
            out.append({"account": name, "cannot_say": reason})
    return out


def line(reason: str) -> str:
    """人向けの 1 行（静的）。"""
    if reason == NO_ROUTE:
        return (f"{NO_ROUTE}: 死活通知（HEALTHCHECK_URL）も運用通知（SMTP）も未設定です。"
                "止まっても誰にも届きません（thth notifications config と account の env に"
                " HEALTHCHECK_URL）")
    if reason == f"{PARTIAL}: healthcheck":
        return (f"{reason}: 死活通知（HEALTHCHECK_URL）が未設定です。VM や timer ごと止まると"
                "運用通知は出ません（account の env に HEALTHCHECK_URL）")
    return (f"{reason}: 運用通知（SMTP と宛先）が未設定です。止まった理由と原稿はメールで"
            "届きません（thth notifications config／status）")
