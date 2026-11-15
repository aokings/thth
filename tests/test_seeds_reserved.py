"""原稿の頭の `seeds:`（台帳の `種類:番号` を空白区切り）は、利用者のために名前を控えた欄（台帳の器 段 11）。

こぴちゃとにがみラボが、原稿が使った台帳の記録の番号を書く 1 行として使い始める。THTH は:
- 知らない欄として読んで残す（型外にしない・値を変えない）。v1 の原稿も v2 の束も。
- 承認の指紋（approved_sha）に入れない（書き足しても、承認が古くならない）。
- 名前を `queuefile.RESERVED_USER_KEYS` に控え、将来この名前を別の意味で使わない。
"""
from __future__ import annotations

from tests.conftest import make_queue_text
from thth import bundle, core, queuefile

SEEDS = "claim:c-origin-brazil-001 evidence:e-origin-brazil-001 common:source:src-behrens"


def test_seedsは控えた名前(tmp_path):
    assert "seeds" in queuefile.RESERVED_USER_KEYS


def test_v1の原稿はseedsを知らない欄として残し承認の指紋に入れない(tmp_path):
    plain = tmp_path / "a.md"
    plain.write_text(make_queue_text(), encoding="utf-8")
    seeded = tmp_path / "b.md"
    text = make_queue_text()
    head, sep, rest = text.partition("\n")
    seeded.write_text(head + sep + f"seeds: {SEEDS}\n" + rest, encoding="utf-8")
    qf = queuefile.parse(str(seeded))
    assert not qf.malformed and qf.front_matter["seeds"] == SEEDS
    assert core._current_fingerprint(str(plain), "threads") is not None
    assert core._current_fingerprint(str(seeded), "threads") == core._current_fingerprint(str(plain), "threads")


def test_v2の束の頭でもseedsを知らない欄として読む():
    top, posts, duplicates = bundle.parse_front_matter(f"thth: 2\naccount: kopicha-x\nseeds: {SEEDS}\n")
    assert top["seeds"] == SEEDS and not duplicates
