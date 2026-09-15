"""本文・段の制御文字を門で拒む（セキュリティ監査 2026-09-16・発注 B-2）。

**背景**: `thth/approval.py::compute_bundle_components()` は段の本文を `\x1e`
（ASCII record separator）で連結し、5 項目を `\x1f` で連結してハッシュする。
段の本文に `\x1e` が入ると、承認後に段の境界をずらしても同じハッシュになる
（`["a\x1eb","c"]` と `["a","b\x1ec"]`・段数も同じ）。**ハッシュの定義は変えない**
（承認済みの原稿が無効になる）。代わりに**制御文字を門で拒む**——`lint`・
`thth approve`・`select`（公開直前の照合）の 3 か所。

`\n`・`\t`・`\r` は許す（本文の改行・タブ・ネットワークの CRLF まで拒むと
普通の原稿が書けなくなる）。
"""
from __future__ import annotations

import datetime
import os

import pytest

from tests.conftest import init_git_pair, run_thth, write_queue_file
from tests.test_thread_publish import FakeAdapter, REL
from tests.test_thread_publish import NOW as BUNDLE_NOW
from tests.test_thread_publish import bundle_text
from thth import bundle as bundle_mod
from thth import core
from thth import lint as lint_mod
from thth import queuefile
from thth import threadthrow
from thth.adapters.base import PublishResult

# v1（単発）の queue ファイル用。`write_queue_file()` の既定 publish_at（08:00）
# より後・静かな時間帯（22:00〜07:00）の外。
NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")


# ------------------------------------------------------------ queuefile: find_control_char

def test_find_control_charは位置とコードポイントを返す():
    found = queuefile.find_control_char("ab\x1ecd")
    assert found == (2, 0x1E)


@pytest.mark.parametrize("text", ["改行\nタブ\tだけ", "復帰\rも許す", "普通の文です"])
def test_find_control_charは改行タブ復帰を許す(text):
    assert queuefile.find_control_char(text) is None


# ------------------------------------------------------------ lint（v1・単発）

def test_v1のlintは本文の制御文字を名指しでerrorにする(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md", commit=False,
                            fm_overrides={"status": "draft"},
                            body="## threads\n\n本文\x1eに制御文字があります。\n")
    errors = lint_mod.lint_file(path)
    assert any("制御文字" in e and "U+001E" in e for e in errors)


def test_v1のlintは改行タブだけの本文を通す(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "a.md", commit=False,
                            fm_overrides={"status": "draft"},
                            body="## threads\n\n1 行目\n2 行目\tタブ入り\n")
    errors = lint_mod.lint_file(path)
    assert not any("制御文字" in e for e in errors)


# ------------------------------------------------------------ approve（v1）

def test_v1のapproveは制御文字を含む原稿を承認しない(isolated_account_factory):
    account = isolated_account_factory(production=True, quiet_hours=None,
                                       min_interval_hours=0)
    path = write_queue_file(account["queue_dir"], "a.md",
                            fm_overrides={"status": "draft"},
                            body="## threads\n\n本文\x1eです。\n")
    proc = run_thth(["approve", path])
    assert proc.returncode != 0, proc.stdout + proc.stderr
    # **一段目にすら到達しない**（digest を出さない・lint の入口で断る）。
    # `"制御文字" in out` だけだと、この tmp_path 自体が pytest によって
    # テスト関数名（このテスト名に「制御文字」を含む）から作られるので、
    # パス文字列に化けて偽陽性になる——実際に確かめた（`idx` がパスの中を指した）。
    # 断り文の定型句「…が含まれています」まで含めて確かめる。
    assert "digest:" not in proc.stdout
    assert "制御文字が含まれています" in (proc.stdout + proc.stderr)
    text = open(path, encoding="utf-8").read()
    # **承認していない**（approved_sha が書かれていない・status も draft のまま）。
    assert "status: approved" not in text


# ------------------------------------------------------------ select（v1・公開直前）

def test_v1のselectは制御文字を含む本文を公開しない(isolated_account_factory):
    """承認時に混ざっていなくても、承認後に本文を直接書き換えて制御文字を
    紛れ込ませた場合（承認済みの `approved_sha` はその本文から正しく計算した
    値）でも、公開直前にもう一度見て止まる。"""
    account = isolated_account_factory(production=True, quiet_hours=None,
                                       min_interval_hours=0)
    write_queue_file(account["queue_dir"], "a.md",
                     fm_overrides={"status": "approved"},
                     body="## threads\n\n本文\x1eに制御文字があります。\n")

    calls = []

    class Spy:
        def publish(self, post, **kw):
            calls.append(post)
            return PublishResult("UNEXPECTED", None, NOW.isoformat())

    result = core.throw_once(account["name"], production_flag=True,
                             adapter_factory=lambda *_: Spy(), now=NOW)

    # **adapter.publish は 1 回も呼ばれない。**
    assert calls == []
    assert result.action != "post"
    # 理由が runs に残る（`_is_error_reason()` が `control_char` を拾う）。
    rejections = result.rejections or []
    assert any(r["reason"].startswith("control_char") for r in rejections), rejections


def test_v1のselectは改行タブだけの本文を公開する(isolated_account_factory):
    account = isolated_account_factory(production=True, quiet_hours=None,
                                       min_interval_hours=0)
    write_queue_file(account["queue_dir"], "a.md",
                     fm_overrides={"status": "approved"},
                     body="## threads\n\n1 行目\n2 行目\tタブ入り\n")

    calls = []

    class Spy:
        def publish(self, post, **kw):
            calls.append(post)
            return PublishResult("18001234567890", None, NOW.isoformat())

    result = core.throw_once(account["name"], production_flag=True,
                             adapter_factory=lambda *_: Spy(), now=NOW)

    assert len(calls) == 1
    assert result.exit_code == 0, result.message


# ------------------------------------------------------------ bundle（v2・thth: 2）

BUNDLE_SEGMENTS_制御文字入り = ["最初の文です。", "途中\x1eに制御文字がある文です。",
                       "最後の文です。"]


def test_v2のcheckは段の制御文字を名指しでerrorにする():
    text = bundle_text(segments=BUNDLE_SEGMENTS_制御文字入り, status="draft")
    b = bundle_mod.parse_text(text, "thread.md")
    errors = bundle_mod.check(b, account_cfg=None)
    assert any("2 段目に制御文字が含まれています" in e and "U+001E" in e for e in errors)


def test_v2のcheckは改行タブだけの段を通す():
    segments = ["1 行目\n2 行目です。", "タブ\t入りの段です。", "最後の段です。"]
    text = bundle_text(segments=segments, status="draft")
    b = bundle_mod.parse_text(text, "thread.md")
    errors = bundle_mod.check(b, account_cfg=None)
    assert not any("制御文字" in e for e in errors)


def test_v2のapproveは制御文字を含む束を承認しない(tmp_path, isolated_account_factory):
    pair = init_git_pair(tmp_path, seed_content=bundle_text(
        segments=BUNDLE_SEGMENTS_制御文字入り, status="draft"), seed_name="thread.md")
    isolated_account_factory(repo_dir=pair["work"], production=True,
                             quiet_hours=None, min_interval_hours=0)
    path = os.path.join(pair["work"], REL)

    proc = run_thth(["approve", path])
    assert proc.returncode != 0, proc.stdout + proc.stderr
    # v1 と同じ理由（tmp_path がテスト関数名から作られ、このテスト名自体が
    # 「制御文字」を含むので、パス文字列との偶然の一致を避ける——「一段目にすら
    # 到達しない」ことと定型句そのものを確かめる）。
    assert "digest:" not in proc.stdout
    assert "制御文字が含まれています" in (proc.stdout + proc.stderr)
    text = open(path, encoding="utf-8").read()
    assert "status: approved" not in text


def test_v2のselectは制御文字を含む段の束を公開しない(tmp_path, isolated_account_factory):
    """B-2 の受け入れそのもの: status: approved・approved_sha は
    `compute_bundle_sha()` で（変えていない定義のまま）正しく計算した値・
    段に `\x1e` を含む束を select に渡しても公開しない。"""
    pair = init_git_pair(tmp_path, seed_content=bundle_text(
        segments=BUNDLE_SEGMENTS_制御文字入り, status="approved"), seed_name="thread.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True,
                                       quiet_hours=None, min_interval_hours=0)

    adapter = FakeAdapter()
    results = threadthrow.publish_bundle(account["name"], REL,
                                         adapter_factory=lambda *_: adapter, now=BUNDLE_NOW)

    # **adapter.publish は 1 回も呼ばれない。**
    assert adapter.calls == []
    assert all(r.action != "published" for r in results)
    # 理由が記録される（StepResult.reason に制御文字と明示される）。
    assert any("制御文字" in r.reason for r in results), [r.reason for r in results]


def test_v2のselectは改行タブだけの束を公開する(tmp_path, isolated_account_factory):
    segments = ["1 行目\n2 行目です。", "タブ\t入りの段です。", "最後の段です。"]
    pair = init_git_pair(tmp_path, seed_content=bundle_text(
        segments=segments, status="approved"), seed_name="thread.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True,
                                       quiet_hours=None, min_interval_hours=0)

    adapter = FakeAdapter()
    results = threadthrow.publish_bundle(account["name"], REL,
                                         adapter_factory=lambda *_: adapter, now=BUNDLE_NOW)

    assert [r.action for r in results] == ["published"] * 3, [r.reason for r in results]
    assert [c["text"] for c in adapter.calls] == segments
