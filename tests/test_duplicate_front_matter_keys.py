"""front-matter の重複鍵（セキュリティ監査 2026-09-16・発注 B-1）。

**見つかり方（発注元が実験で確定）**: `thth/queuefile.py::_parse_kv` は同じ鍵が
2 回あると**後勝ち**で読む。`thth/writeback.py::set_front_matter_fields` は
**最初に見つけた行だけ**書き換える。よって `status: approved` と
`approved_sha: <正しい値>` が 2 回書かれた原稿に `thth revoke` をかけると、
1 つ目の行が `draft`・空に書き換わり、読む側は 2 つ目を採るので **status は
approved・sha も有効のまま**。CLI は「承認を取り消しました」と言って rc=0。
timer はそのまま公開する。

**直し**: 重複した鍵を見つけたら `malformed` にする（`queuefile.parse_text()`・
`bundle.parse_text()`）。`writeback.set_front_matter_fields()` は重複があれば
書かずに `ValueError`。`thth lint` は名指しで error。
"""
from __future__ import annotations

import re

import pytest

from tests.conftest import run_thth, write_queue_file
from tests.test_atlas_review import setup_pair
from thth import bundle as bundle_mod
from thth import queuefile
from thth import writeback

# 発注書 B-1 の再現そのもの。
重複した原稿 = ("---\nthth: 1\naccount: a\nstatus: approved\n"
          "approved_sha: REAL\nstatus: approved\napproved_sha: REAL\n"
          "---\nhello\n")


# ------------------------------------------------------------ queuefile（読み）

def test_重複した鍵はmalformedになる():
    qf = queuefile.parse_text(重複した原稿, "x.md")
    assert qf.malformed is True
    assert qf.duplicate_keys == ["status", "approved_sha"]
    # **後勝ちの読み方自体は変えない**（他の読み手が広く前提にしているため）。
    assert qf.front_matter["status"] == "approved"
    assert qf.front_matter["approved_sha"] == "REAL"


def test_重複していなければ従来どおりmalformedにならない():
    txt = "---\nthth: 1\naccount: a\nstatus: approved\napproved_sha: REAL\n---\nhello\n"
    qf = queuefile.parse_text(txt, "x.md")
    assert qf.malformed is False
    assert qf.duplicate_keys == []


def test_重複メッセージは鍵名を含む():
    msg = queuefile.duplicate_keys_message(["status", "approved_sha"])
    assert msg == "front-matter の鍵が重複しています: status、approved_sha"


# ------------------------------------------------------------ bundle（thth: 2）

def test_束のtop_levelの重複鍵もmalformedになる():
    txt = ("---\nthth: 2\naccount: a\npublish_at: 2026-09-09T08:00:00+09:00\n"
           "continue_until: 2026-09-09T09:00:00+09:00\nstatus: approved\n"
           "status: approved\nposts:\n  - index: 1\n---\n## threads\n\nhello\n")
    b = bundle_mod.parse_text(txt, "x.md")
    assert b.malformed is True
    assert b.duplicate_keys == ["status"]


def test_束のcheckは重複鍵を名指しで返す():
    txt = ("---\nthth: 2\naccount: a\npublish_at: 2026-09-09T08:00:00+09:00\n"
           "continue_until: 2026-09-09T09:00:00+09:00\nstatus: approved\n"
           "status: approved\nposts:\n  - index: 1\n---\n## threads\n\nhello\n")
    b = bundle_mod.parse_text(txt, "x.md")
    errors = bundle_mod.check(b, account_cfg=None)
    assert errors == ["front-matter の鍵が重複しています: status"]


def test_束のwhy_malformedも重複鍵を名指しする():
    txt = ("---\nthth: 2\naccount: a\nstatus: approved\nstatus: draft\n"
           "posts:\n  - index: 1\n---\n## threads\n\nhello\n")
    assert bundle_mod.why_malformed(txt) == "front-matter の鍵が重複しています: status"


# ------------------------------------------------------------ writeback（書き）

def test_writebackは重複した鍵があれば書かずに断る(tmp_path):
    path = tmp_path / "a.md"
    path.write_text(重複した原稿, encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ValueError) as e:
        writeback.set_front_matter_fields(str(path), {"status": "draft"})
    assert "重複" in str(e.value)
    assert "status" in str(e.value)
    # **1 文字も書いていない。**
    assert path.read_text(encoding="utf-8") == before


def test_writebackは重複が無ければ従来どおり書ける(tmp_path):
    path = tmp_path / "a.md"
    write_queue_file(str(tmp_path), "a.md", commit=False)
    writeback.set_front_matter_fields(str(path), {"status": "draft"})
    assert queuefile.parse(str(path)).front_matter["status"] == "draft"


# ------------------------------------------------------------ CLI: lint

def _重複させる(text: str) -> str:
    """承認済みファイルの `status:`・`approved_sha:` の行をそのまま複製する
    （発注書 B-1 の再現と同じ形: 1 つ目が正しい値・2 つ目も同じ正しい値）。"""
    m = re.search(r"status: approved\napproved_sha: \S+\n", text)
    assert m, text
    block = m.group(0)
    return text.replace(block, block + block, 1)


def test_lintは重複鍵を名指しでerrorにする(tmp_path, isolated_account_factory):
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    corrupted = _重複させる(path.read_text(encoding="utf-8"))
    path.write_text(corrupted, encoding="utf-8")

    proc = run_thth(["lint", str(path)])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "front-matter の鍵が重複しています" in proc.stdout
    assert "status" in proc.stdout
    assert "approved_sha" in proc.stdout


# ------------------------------------------------------------ CLI: revoke（本丸）

def test_revokeは重複鍵のfront_matterを取り消せず嘘をつかない(tmp_path, isolated_account_factory):
    """B-1 の受け入れそのもの: 重複鍵の原稿に `thth revoke` をかけても、
    rc=0 で「取り消しました」と言わない・ファイルは 1 バイトも変わらない。"""
    pair, account, path = setup_pair(tmp_path, isolated_account_factory)
    before = path.read_text(encoding="utf-8")
    corrupted = _重複させる(before)
    assert corrupted != before
    path.write_text(corrupted, encoding="utf-8")

    proc = run_thth(["revoke", str(path), "--by", "masaru", "--reason", "テスト"])

    # **exit 0 で「取り消しました」と嘘をつかない。**
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "取り消しました" not in proc.stdout
    assert "front-matter が読めない" in proc.stderr

    # **ファイルは 1 バイトも変わっていない**（`status`・`approved_sha` は
    # 重複したまま・どちらの行も書き換わっていない）。
    after = path.read_text(encoding="utf-8")
    assert after == corrupted
    assert after.count("status: approved") == 2
    assert "status: draft" not in after


# ------------------------------------------------------------ select（公開側）

def test_selectは重複鍵の原稿を型外として扱い選ばない(tmp_path, isolated_account_factory):
    """B-1 項目 4: select（公開側）が malformed を公開しないことの確認。
    重複鍵の原稿は `qf.malformed=True` になるので、`_validate_all()` の
    型外（`type_mismatch`）判定で候補から外れる——`rejections` にすら積まれない
    （型外は失敗に数えない、という既存の扱いのまま）。"""
    from thth import jst
    from thth import select as select_mod

    qf = queuefile.parse_text(重複した原稿, "x.md")
    qf.verified = True
    account_name = "a"
    account_cfg = {"media": "threads", "hashtags": False, "quiet_hours": None,
                   "min_interval_hours": 0, "stale_days": 7}
    now = jst.now_jst()

    result = select_mod.select_one(
        [qf], account_name=account_name, account_cfg=account_cfg, now=now,
        last_post_at=None, recent_texts=set())

    assert result.chosen is None
    assert qf.path in result.type_mismatch
    assert all(rej.file != qf.path for rej in result.rejections)
