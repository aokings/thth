"""命令の札: 括りの中・管理者だけ・括りなし（括りの段 0）。

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md §5。
- `ws`    括りの中で済む。鍵を持つレーンが、自分の括りの中で打てる（今は遠くの道に出ていない物も含む）。
- `admin` 管理者だけ。ssh で入り、括りを名指しして打つ。括りをまたぐ・口座を作る/やめる・認可と秘密・常駐。
- `local` 括りなし。手元の機械だけで動き、VM の置き場に触れない。

一番上の命令は全部ここに札がある（tests/test_command_scope.py が parser を歩いて確かめる）。
子の命令で札が親と違う物だけ `CHILD_OVERRIDES` に書く。今は札を見て断る所はまだ無い（段 2・3 で使う）。
"""
from __future__ import annotations

WS = "ws"
ADMIN = "admin"
LOCAL = "local"
SCOPES = (WS, ADMIN, LOCAL)

TOP: dict[str, str] = {
    # §5.1 もう遠くの道にある
    "send": WS, "schedule": WS, "retract": WS, "edit": WS, "like": WS, "unlike": WS, "reply": WS,
    "posts": WS, "replies": WS, "measured": WS, "collect": WS, "mentions": WS, "thread": WS, "where": WS,
    "who": WS, "embed": WS, "upcoming": WS, "profile": WS, "location": WS, "topics": WS,
    "account": WS, "queue": WS, "inflight": WS, "doctor": WS, "events": WS, "summary": WS, "dashboard": WS,
    "bookmark": WS, "dm": WS, "live": WS, "page": WS, "brand": WS, "creators": WS, "shop": WS, "webhook": WS,
    "ads": WS,
    # §5.2 括りの中で済む（通す）
    "board": WS, "morning": WS, "observe": WS, "counts": WS, "list": WS, "unanswered": WS, "after": WS,
    "review": WS, "analytics-report": WS, "study-report": WS, "handoff-report": WS, "trends": WS,
    "where-log": WS, "approve": WS, "approvals": WS, "revoke": WS, "withdraw": WS, "hold": WS,
    "repost": WS, "unrepost": WS, "comments": WS, "community": WS, "article": WS, "hatena": WS,
    "fotolife": WS, "conflict": WS, "archive": WS, "deliveries": WS, "media": WS,
    "plaza": WS, "map": WS, "report": WS, "reply-mark": WS, "scenario": WS, "link": WS, "register": WS,
    "wa": WS, "notifications": WS, "quota": WS, "principal": WS, "project": WS, "owner": WS,
    "study": WS, "threads": WS, "forms": WS,
    # §5.3 管理者だけ
    "admin": ADMIN, "auth": ADMIN, "refresh": ADMIN, "token": ADMIN, "app": ADMIN,
    "run": ADMIN, "throw": ADMIN, "pull": ADMIN, "systemd": ADMIN, "maintain": ADMIN, "worker": ADMIN,
    "serve-reports": ADMIN, "approval-worker": ADMIN,
    # §5.5 括りなし
    "login": LOCAL, "logout": LOCAL, "lint": LOCAL, "preview": LOCAL, "ask": LOCAL,
    "gads": LOCAL, "gsc": LOCAL, "gbp": LOCAL,
}

# 親と札が違う子の命令（`親 子`）。§5.3 の「口座を作る・やめる」「認可」「動かす側」。
CHILD_OVERRIDES: dict[str, str] = {
    "account add": ADMIN, "account leave": ADMIN, "account resume": ADMIN, "account migrate": ADMIN,
    "principal add": ADMIN,
    "ads accounts": ADMIN, "ads leave": ADMIN, "ads auth": ADMIN,
    "webhook pull": ADMIN,
    "map collect": ADMIN,
}


def scope_of(command: str, child: str | None = None) -> str | None:
    """札。知らない命令は None（呼ぶ側が断る）。"""
    if child:
        found = CHILD_OVERRIDES.get(f"{command} {child}")
        if found:
            return found
    return TOP.get(command)
