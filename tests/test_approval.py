"""`thth.approval` の hash の定義を固定する（外部レビュー §1・§1b・§3・受け入れ 11）。

対象・順序・区切り・正規化の有無が将来ぶれないように、ここで定義そのものを
テストで凍結する。`test_approved_shaの連結順序と区切りを固定する()` と
`test_send_digestの連結順序と区切りを固定する()` は `thth/approval.py` の実装を
一切呼ばずに期待値を組み立て、実装の出力と突き合わせる（実装をそのままなぞる
tautology にしない）。この 2 本が落ちたら、それは定義を変えたということ——
意図的なら期待値ごと書き換えること。
"""
from __future__ import annotations

import datetime
import hashlib

from thth import approval

_SEP = "\x1f"


def test_approved_shaの連結順序と区切りを固定する():
    """`section・account・reply_to・topic・publish_at` をこの順で \\x1f 区切りに
    連結した sha256 と一致する（`thth/approval.py` の実装を経由せず独立に組み立てる）。
    """
    joined = _SEP.join([
        "本文の中身",
        "nigamilab-threads",
        "12345",
        "苦味",
        "2026-09-09T08:00:00+09:00",
    ])
    expected = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    actual = approval.compute_approved_sha(
        section="本文の中身", account="nigamilab-threads", reply_to="12345",
        topic="苦味", publish_at="2026-09-09T08:00:00+09:00")
    assert actual == expected
    assert len(actual) == 64  # sha256 hexdigest


def test_send_digestの連結順序と区切りを固定する():
    """`compute_send_digest()` は `text・account・reply_to・topic` の 4 つだけ
    （`publish_at` は含めない・§1b）で、先頭 12 桁を返す。"""
    joined = _SEP.join(["本文の中身", "nigamilab-threads", "12345", "苦味"])
    expected = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]
    actual = approval.compute_send_digest(
        text="本文の中身", account="nigamilab-threads", reply_to="12345", topic="苦味")
    assert actual == expected
    assert len(actual) == 12


def test_approved_shaは前後の空白を落とす():
    a = approval.compute_approved_sha(section="  本文  ", account="a", reply_to=None,
                                       topic=None, publish_at="2026-09-09T08:00:00+09:00")
    b = approval.compute_approved_sha(section="本文", account="a", reply_to=None,
                                       topic=None, publish_at="2026-09-09T08:00:00+09:00")
    assert a == b


def test_approved_shaはtopicの先頭シャープを正規化する():
    a = approval.compute_approved_sha(section="本文", account="a", reply_to=None,
                                       topic="#苦味", publish_at="2026-09-09T08:00:00+09:00")
    b = approval.compute_approved_sha(section="本文", account="a", reply_to=None,
                                       topic="苦味", publish_at="2026-09-09T08:00:00+09:00")
    assert a == b


def test_approved_shaはNoneのreply_toとtopicを空文字列として扱う():
    a = approval.compute_approved_sha(section="本文", account="a", reply_to=None,
                                       topic=None, publish_at="2026-09-09T08:00:00+09:00")
    b = approval.compute_approved_sha(section="本文", account="a", reply_to="",
                                       topic="", publish_at="2026-09-09T08:00:00+09:00")
    assert a == b


def test_approved_shaはpublish_atの文字列とdatetimeで同じ結果になる():
    a = approval.compute_approved_sha(
        section="本文", account="a", reply_to=None, topic=None,
        publish_at="2026-09-09T08:00:00+09:00")
    b = approval.compute_approved_sha(
        section="本文", account="a", reply_to=None, topic=None,
        publish_at=datetime.datetime.fromisoformat("2026-09-09T08:00:00+09:00"))
    assert a == b


def test_approved_shaは本文が変わると変わる():
    a = approval.compute_approved_sha(section="本文A", account="a", reply_to=None,
                                       topic=None, publish_at="2026-09-09T08:00:00+09:00")
    b = approval.compute_approved_sha(section="本文B", account="a", reply_to=None,
                                       topic=None, publish_at="2026-09-09T08:00:00+09:00")
    assert a != b


def test_approved_shaはaccountが変わると変わる():
    a = approval.compute_approved_sha(section="本文", account="a", reply_to=None,
                                       topic=None, publish_at="2026-09-09T08:00:00+09:00")
    b = approval.compute_approved_sha(section="本文", account="b", reply_to=None,
                                       topic=None, publish_at="2026-09-09T08:00:00+09:00")
    assert a != b


def test_approved_shaはreply_toが変わると変わる():
    a = approval.compute_approved_sha(section="本文", account="a", reply_to="111",
                                       topic=None, publish_at="2026-09-09T08:00:00+09:00")
    b = approval.compute_approved_sha(section="本文", account="a", reply_to="222",
                                       topic=None, publish_at="2026-09-09T08:00:00+09:00")
    assert a != b


def test_approved_shaはtopicが変わると変わる():
    a = approval.compute_approved_sha(section="本文", account="a", reply_to=None,
                                       topic="苦味", publish_at="2026-09-09T08:00:00+09:00")
    b = approval.compute_approved_sha(section="本文", account="a", reply_to=None,
                                       topic="うま味", publish_at="2026-09-09T08:00:00+09:00")
    assert a != b


def test_approved_shaはpublish_atが変わると変わる():
    a = approval.compute_approved_sha(section="本文", account="a", reply_to=None,
                                       topic=None, publish_at="2026-09-09T08:00:00+09:00")
    b = approval.compute_approved_sha(section="本文", account="a", reply_to=None,
                                       topic=None, publish_at="2026-09-10T08:00:00+09:00")
    assert a != b


def test_send_digestはpublish_atを含まない_同じ本文なら常に同じ():
    d1 = approval.compute_send_digest(text="本文", account="a", reply_to=None, topic=None)
    d2 = approval.compute_send_digest(text="本文", account="a", reply_to=None, topic=None)
    assert d1 == d2


def test_send_digestは本文が変わると変わる():
    a = approval.compute_send_digest(text="本文A", account="a", reply_to=None, topic=None)
    b = approval.compute_send_digest(text="本文B", account="a", reply_to=None, topic=None)
    assert a != b


def test_body_hashは本文だけを見る():
    assert approval.compute_body_hash("本文") == approval.compute_body_hash("本文")
    assert approval.compute_body_hash("本文") == hashlib.sha256("本文".encode("utf-8")).hexdigest()
    assert approval.compute_body_hash("  本文  ") == approval.compute_body_hash("本文")
    assert approval.compute_body_hash("本文A") != approval.compute_body_hash("本文B")
