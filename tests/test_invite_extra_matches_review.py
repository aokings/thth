"""審査の招待で足す権限（`scopes.META_INVITE_EXTRA`）は、今の申請の一覧（`appreview.names()`）に合う。

11/28: 申請から外した `pages_manage_cta`・`read_audience_network_insights`・`commerce_account_read_settings` が
招待に残り、`thth doctor` が申請しない権限を「足りない」と見ていた。片方だけを直すと、ここが赤になる。
"""
from __future__ import annotations

import re
from pathlib import Path

from thth import appreview, invites, scopes
from thth import permission_watch as pw

INVITE_OBJECT = Path(__file__).resolve().parent.parent / "callback" / "src" / "invite-object.js"


def review_invite_scopes():
    return {name for media in invites.META_MEDIA for name in scopes.meta_invite_scopes(media, review=True)}


def test_招待の足し分は申請の一覧にあるか_外した理由が書いてある():
    names = set(appreview.names())
    beyond = [name for extra in scopes.META_INVITE_EXTRA.values() for name in extra if name not in names]
    assert sorted(set(beyond) - set(scopes.META_INVITE_BEYOND_REVIEW)) == [], \
        "申請に無い権限を招待で求めている（足すなら META_INVITE_BEYOND_REVIEW に理由を書く）"


def test_申請から外して求め続ける物は_DEFERREDにあり理由があり_今も招待に載る():
    asked = review_invite_scopes()
    for name, reason in scopes.META_INVITE_BEYOND_REVIEW.items():
        assert name in appreview.DEFERRED and name not in appreview.names()
        assert name in asked, f"{name} を求め続けると書いたのに招待に無い"
        assert isinstance(reason, str) and reason.strip()


def test_pages_utility_messagingはpages_messagingと一緒に求め続ける():
    """B4 の推し（docs/一覧_Meta申請の録画までに要る物_2026-11-23.md §2）。"""
    facebook = scopes.META_INVITE_EXTRA["facebook"]
    assert "pages_utility_messaging" in facebook and "pages_messaging" in facebook


def test_申請に入れたMetaの権限は招待に載るか_載せない理由が書いてある():
    asked = review_invite_scopes()
    meta = [name for name in appreview.names()
            if name in pw.PERMISSIONS and pw.PERMISSIONS[name]["media"] in invites.META_MEDIA]
    assert sorted(set(meta) - asked - set(scopes.META_INVITE_LEFT_OUT)) == []
    for name, reason in scopes.META_INVITE_LEFT_OUT.items():
        assert name in appreview.names(), f"{name} は申請に無い（LEFT_OUT から外す）"
        assert name not in asked, f"{name} は招待に載っている（LEFT_OUT から外す）"
        assert isinstance(reason, str) and reason.strip()


def test_審査の招待の権限の数はWorkerの上限に収まる():
    text = INVITE_OBJECT.read_text(encoding="utf-8")
    found = re.search(r"body\.scopes\.length>(\d+)", text)
    assert found, "invite-object.js に権限の数の上限が無い"
    limit = int(found.group(1))
    for media in invites.META_MEDIA:
        listed = scopes.meta_invite_scopes(media, review=True)
        assert len(listed) <= limit, f"{media} の審査の招待は {len(listed)} 個（Worker は {limit} 個まで）"
