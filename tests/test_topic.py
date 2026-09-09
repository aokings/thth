"""トピック（`topic_tag`）の正規化・検査（設計 §2.2・§4.1・masaru 裁定 2026-09-09。
T2c）。1 事故 1 テストの命名規約に従う。"""
from __future__ import annotations

from thth import queuefile


def test_省略はNone():
    assert queuefile.normalize_topic(None) is None


def test_空文字はNone():
    assert queuefile.normalize_topic("") is None
    assert queuefile.normalize_topic("   ") is None


def test_前後の空白を落とす():
    assert queuefile.normalize_topic("  苦味  ") == "苦味"


def test_先頭のシャープを落とす():
    # 人が `#苦味` と書きがち（ハッシュタグとは別物なので `#` は付けない・設計 §4.1）。
    assert queuefile.normalize_topic("#苦味") == "苦味"


def test_先頭のシャープと空白が両方あっても落ちる():
    assert queuefile.normalize_topic("  #苦味  ") == "苦味"


def test_正常な1から50字はOK():
    assert queuefile.topic_error("苦味") is None
    assert queuefile.topic_error("あ" * 50) is None
    assert queuefile.topic_error("a") is None


def test_51字はtopic_too_longを名指しする():
    err = queuefile.topic_error("あ" * 51)
    assert err == "topic_too_long(51)"


def test_ピリオドを含むとtopic_invalid_charを名指しする():
    err = queuefile.topic_error("苦味.")
    assert err == "topic_invalid_char(.)"


def test_アンパサンドを含むとtopic_invalid_charを名指しする():
    err = queuefile.topic_error("苦味&旨味")
    assert err == "topic_invalid_char(&)"
