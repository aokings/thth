"""媒体の宣言（`thth/media_registry.py`）と、媒体名を手で持つ表の一致（10/29 段 0・設計 MetaAPI 計画 B-3）。

facebook を足した 5.1.0 では、表の 7 か所が漏れ、漏れても試験が赤にならない所があった。ここは
**registry の媒体の集合と、各表の媒体の集合が一致すること**を縛る（載せない表は registry の
`NOT_LISTED` に理由を書く）。Instagram などを足すときは registry に 1 行足すと、この試験が赤を指した
表を順に埋めればよい。

わざと壊して赤を見たもの（段 0 の柵）: registry から facebook を外す → 下の表の試験がすべて赤。
`queuefile.MEDIA_LIMITS` から facebook を消す → `queuefile.MEDIA_LIMITS` の行が赤。
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from thth import (account_cli, accounts, adapters, dashboard_cli, leave_gate, limits_config,
                  limits_policy, media, media_capabilities, media_delivery, media_registry,
                  media_traits, queuefile, scenario, thread_read, webhook)

REGISTRY = media_registry.names()
NON_ADAPTER = media_registry.NON_ADAPTER_NAMES


def _candidates():
    """振る舞いで見る表の、試す名前（registry ∪ adapter の REGISTRY ∪ はてな・サイト）。"""
    return REGISTRY | set(adapters.REGISTRY) | NON_ADAPTER


def _leave_gate_native():
    """`leave_gate.bind` の native の型の並び（adapter のクラス名）に入っている媒体。"""
    source = inspect.getsource(leave_gate.bind)
    line = next(row for row in source.splitlines() if row.strip().startswith("native="))
    return {medium for medium, cls in adapters.REGISTRY.items() if re.search(rf"\b{cls.__name__}\b", line)}


DASHBOARD = Path(__file__).resolve().parents[1] / "skills" / "thth" / "dashboard"


def _template_medium(level):
    """会話の中のダッシュボードの型（`skills/thth/dashboard/<level>.html`）の `THTH_MEDIUM` の鍵。"""
    text = (DASHBOARD / f"{level}.html").read_text(encoding="utf-8")
    found = re.search(r"const THTH_MEDIUM=\{(.*?)\};", text)
    return set(re.findall(r'(\w+):"', found.group(1))) if found else set()


def _media_delivery_supported():
    return {name for name in _candidates() if not media_delivery.unavailable({"media": name})}


# 表の名前 → その表の媒体の集合（はてな・サイトの名前を含んでもよい）。
TABLES = {
    "adapters.REGISTRY": lambda: set(adapters.REGISTRY),
    "account_cli.MEDIA_CHOICES": lambda: set(account_cli.MEDIA_CHOICES),
    "accounts.DUMMY_HANDLES": lambda: set(accounts.DUMMY_HANDLES),
    "queuefile.MEDIA_LIMITS": lambda: set(queuefile.MEDIA_LIMITS),
    "limits_policy.SNS_MEDIA": lambda: set(limits_policy.SNS_MEDIA),
    "limits_policy.LEDGER_SNS_MEDIA": lambda: set(limits_policy.LEDGER_SNS_MEDIA),
    "limits_config.SNS_MEDIA": lambda: set(limits_config.SNS_MEDIA),
    "media_traits.SUPPORTED_MEDIA": lambda: set(media_traits.SUPPORTED_MEDIA),
    "media_capabilities.MEDIA": lambda: set(media_capabilities.MEDIA),
    "media.TYPED_ALLOWED": lambda: set(media.TYPED_ALLOWED),
    "media_delivery.unavailable": _media_delivery_supported,
    "leave_gate.bind(native)": _leave_gate_native,
    "scenario.SNS_MEDIA": lambda: set(scenario.SNS_MEDIA),
    "scenario.MEDIUM_JA": lambda: set(scenario.MEDIUM_JA),
    "dashboard_cli.MEDIUM_JA": lambda: set(dashboard_cli.MEDIUM_JA),
    "thread_read.SOURCE_BY_MEDIUM": lambda: set(thread_read.SOURCE_BY_MEDIUM),
    "thread_read.PERMISSION_BY_MEDIUM": lambda: set(thread_read.PERMISSION_BY_MEDIUM),
    # Meta 計画 段 6: Webhook を購読する欄（Meta の Graph の媒体だけ・ほかは registry に理由）。
    "webhook.SUBSCRIBE_FIELDS": lambda: set(webhook.SUBSCRIBE_FIELDS),
    **{f"dashboard/{level}.html THTH_MEDIUM": (lambda level=level: _template_medium(level))
       for level in ("all", "project", "campaign", "series")},
}


@pytest.mark.parametrize("table", sorted(TABLES))
def test_registryの媒体と表の媒体が一致する_載せない表は理由がある(table):
    have = TABLES[table]()
    unknown = have - REGISTRY - NON_ADAPTER
    assert not unknown, f"{table} に registry に無い媒体: {sorted(unknown)}（thth/media_registry.py の MEDIA に足す）"
    missing = REGISTRY - have
    excused = media_registry.not_listed(table)
    assert missing == excused, (
        f"{table} に無い媒体: {sorted(missing - excused)}（表に足すか、media_registry.NOT_LISTED に理由を書く）"
        if missing - excused else
        f"{table} の NOT_LISTED が古い: {sorted(excused - missing)} は表に載っている（NOT_LISTED から消す）")


def test_載せない理由はregistryの媒体と試験の表だけを指し_空でない():
    for (table, medium), reason in media_registry.NOT_LISTED.items():
        assert table in TABLES, table
        assert medium in REGISTRY, medium
        assert isinstance(reason, str) and reason.strip()


def test_表示名は画面の2つの表と同じ():
    for name, medium in media_registry.MEDIA.items():
        assert dashboard_cli.MEDIUM_JA[name] == medium.display
        assert scenario.MEDIUM_JA[name] == medium.display


def test_familyは柵の表の道で_SNSはどれもsns():
    for name, medium in media_registry.MEDIA.items():
        assert medium.family in limits_policy.FAMILIES
        assert (medium.family == "sns") == (name in limits_policy.SNS_MEDIA)


def test_性質の台帳の有無は台帳のファイルと柵の写像に一致する():
    with_traits = {name for name, medium in media_registry.MEDIA.items() if medium.traits}
    assert with_traits == set(limits_policy.LEDGER_SNS_MEDIA)
    assert with_traits <= set(media_traits.SUPPORTED_MEDIA)
    for name in with_traits:
        assert media_traits.load(name)["supported"] is True


def test_雛形のhandleは雛形の台帳とダミーの印に一致する():
    example = Path(__file__).resolve().parents[1] / "accounts.example"
    import json
    for name, medium in media_registry.MEDIA.items():
        if medium.default_handle is None:
            assert ("accounts.DUMMY_HANDLES", name) in media_registry.NOT_LISTED
            continue
        assert medium.default_handle in accounts.DUMMY_HANDLES[name]
        assert json.loads((example / f"{name}.json").read_text())["handle"] == medium.default_handle


def test_添付を媒体が取りに来るのはrelayを渡す媒体だけ():
    """段 3（10/29）: relay を渡すかは registry の `media_fetch: pull` だけで決める（`meta.media.relay_for`）。

    `media_delivery` は媒体名で relay を分けない（`relay_for` を呼ぶだけ）。段 2 までは
    `MediaRelay(...) if cfg['media']=='threads'` の 1 行だったのを、段 3 で Instagram を足すときに置き換えた。
    """
    from thth.meta import media as meta_media
    source = inspect.getsource(media_delivery)
    assert "meta_media.relay_for(cfg,has_items=bool(items))" in source
    assert not re.search(r"MediaRelay\([^)]*\) if cfg\['media'\]", source), "媒体名で relay を分ける行が戻っている"
    pulled = {name for name, medium in media_registry.MEDIA.items() if medium.media_fetch == "pull"}
    assert pulled == {"threads", "instagram"}
    for name in media_registry.MEDIA:
        relay = meta_media.relay_for({"media": name, "account": "demo-" + name}, has_items=True)
        assert (relay is not None) == (name in pulled), name
        assert meta_media.relay_for({"media": name, "account": "demo-" + name}, has_items=False) is None
    assert {m.media_fetch for m in media_registry.MEDIA.values()} <= {"push", "pull"}
