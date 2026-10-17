"""4.1.0 夜の監査（Opus・10/17）の直し: 中-1／中-2／低-1／低-2／低-4／低-5／低-7。

網には出ない（偽物の HTTP・127.0.0.1・tmp の台帳）。秘密の値は偽物だけ。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import shutil

import pytest

from tests.conftest import FIXTURES_DIR, init_git_pair
from tests.hatena_s1_flow_helpers import call, lane  # noqa: F401
from tests.hatena_s4_helpers import (ACCOUNT, OWN_URL, bm, bookmark_object, calls, cfg, fake_http,  # noqa: F401
                                     published, record_row, rows, world)
from tests.test_assess import NOW, SNS, hatena_loose, refused, sns_cfg, sns_post
from tests.test_limits_stage2 import FAKE, minutes_before
from tests.test_v400_s1_remote import key  # noqa: F401
from tests.test_v410_bookmark_batch import (URL_A1, URL_B1, approve, batch_file, bb, item, run,  # noqa: F401
                                            stage1, states, write)
from thth import (bookmark_batch, cli, core, deliveries, guard, hatena_bookmark, hatena_fotolife, jst,
                  limits_policy, media_cli, media_traits as mt, remote, server_writes)


@pytest.fixture
def broken(tmp_path, monkeypatch):
    """台帳のフォルダの写しに壊れた json を 1 本置く（柵は `invalid_media_traits` で断るはず）。"""
    dest = tmp_path / "media_traits"
    shutil.copytree(mt.TRAITS_DIR, dest)
    monkeypatch.setattr(mt, "TRAITS_DIR", dest)
    (dest / "hatenabookmark.json").write_text("{壊れた json", encoding="utf-8")
    limits_policy.reset()
    yield dest
    limits_policy.reset()


# ---------------------------------------------------------------- 中-1 コメントは全文・自分の記事には注意

def test_中1_1段目はコメントを切らずに全文で出す(bb):
    """柵: `_short(comment, 60)` に戻すと赤（59 字＋「…」で切って見せる）。"""
    comment = "あ" * 99 + "終"
    p = stage1(write(bb, [item("https://a.example.com/long", comment=comment)]))["preview"]
    text = "\n".join(bookmark_batch.lines(p))
    assert comment in text and "…" not in text.split("コメント:")[1].split("\n")[0]


def test_中1_自分の記事の札には運営者と分かる文にする注意を添える(bb):
    """柵: 注意の行を外すと赤。外部の札には出さない。"""
    dl = published(ACCOUNT, OWN_URL)
    p = stage1(write(bb, [{"delivery": dl, "comment": "自分の記事。まとめ", "tags": []},
                          item("https://b.example.org/y")]))["preview"]
    text = "\n".join(bookmark_batch.lines(p))
    assert text.count("運営者であることが分かる文にしてください") == 1
    assert "自分の記事。まとめ" in text


# ---------------------------------------------------------------- 中-2 台帳が壊れていても柵は断る（落ちない）

def test_中2_壊れた台帳でSNSの公開_反応_はてなの記事とブックマークは断りで残る(world, broken):
    """柵: `guard._rules_for` の変換を外すと赤（MediaTraitsError で落ちる）。"""
    e = refused(lambda: guard.check(SNS, sns_cfg(), "publish", now=NOW))
    assert str(e) == "invalid_media_traits" and e.reason is None
    assert str(refused(lambda: guard.check_reaction(SNS, sns_cfg(), "like", now=NOW))) == "invalid_media_traits"
    hatena_loose(world)
    cfg_h = cfg(ACCOUNT)
    assert str(refused(lambda: guard.check(ACCOUNT, cfg_h, "entry_create", now=NOW))) == "invalid_media_traits"
    assert str(refused(lambda: guard.check(ACCOUNT, cfg_h, "bookmark", now=NOW,
                                           bookmark_url="https://a.example.com/x"))) == "invalid_media_traits"
    # 断りであって止めではない（口座も主体も止めない）
    assert guard.stopped(SNS) is None and guard.stopped(ACCOUNT) is None


def test_中2_断りの符丁は静的で_thth_meの書きにも通る():
    assert "invalid_media_traits" in guard.REFUSALS
    assert "invalid_media_traits" in server_writes.SAFE_ERRORS


def test_中2_手元のsendは例外で落ちず断りで終わる(isolated_account_factory, broken):
    account = isolated_account_factory(production=True)
    from tests.test_send import _digest_for

    class _Spy:
        def publish(self, *a, **k):
            raise AssertionError("柵が断るはずなのに publish を呼んだ")

    digest = _digest_for(account["name"], "本文です")
    r = core.send_once(account["name"], text="本文です", production_flag=True, confirm=digest,
                       adapter_factory=lambda *_: _Spy())
    assert r.exit_code != 0 and r.action == "guard"
    assert "invalid_media_traits" in r.message


def test_中2_SNSのtimerの予約投稿は柵を通らないので止まらない(isolated_account_factory, tmp_path, monkeypatch, broken):
    """縛り: timer の `throw_once` は `guard.check` を呼ばない。台帳が壊れていても出る。"""
    from tests.test_roundtrip import NOW as RT_NOW, fake_ok_server
    with open(os.path.join(FIXTURES_DIR, "umami-bile.md"), encoding="utf-8") as f:
        seed = f.read()
    pair = init_git_pair(tmp_path, seed_content=seed, seed_name="2026-09-08-umami-bile.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True)
    monkeypatch.setenv("THTH_THREADS_WAIT_SECONDS", "0")
    with fake_ok_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        result = core.throw_once(account["name"], production_flag=True, now=RT_NOW)
    assert (result.exit_code, result.action) == (0, "post")


def test_中2_media_checkは壊れた台帳でrc1と理由を言う(broken, capsys):
    assert cli.main(["media", "check"]) == 1
    assert "ledger_unreadable" in capsys.readouterr().err


def test_中2_media_checkは柵の表を作れないときもrc1(monkeypatch, capsys):
    """柵: `full_check` が `build_policy` を通さないと赤（check は rc 0 なのに柵だけが全部断る）。"""
    assert mt.check()["ok"] is True
    monkeypatch.setattr(limits_policy, "LEDGER_MAP",
                        {k: v for k, v in limits_policy.LEDGER_MAP.items() if k != ("threads", "post.daily")})
    limits_policy.reset()
    try:
        assert cli.main(["media", "check"]) == 1
        err = capsys.readouterr().err
        assert "policy_build_failed" in err and "unmapped_ledger_row" in err
        assert media_cli.full_check()["ok"] is False
    finally:
        limits_policy.reset()


def test_中2_media_checkは正しい台帳ならrc0():
    assert cli.main(["media", "check"]) == 0


def test_中2_フォトライフの上げは台帳が壊れても例外にせず止める(monkeypatch, tmp_path):
    """柵: `FotolifeStop` への変換を外すと MediaTraitsError で落ちて赤。"""
    from tests.hatena_s3_helpers import PNG

    def broken_policy(*a, **k):
        raise mt.MediaTraitsError(mt.INVALID, "壊れている")
    monkeypatch.setattr(hatena_fotolife, "daily_max_uploads", broken_policy)
    sha = hashlib.sha256(PNG).hexdigest()
    monkeypatch.setattr(hatena_fotolife, "make_adapter", lambda *a, **k: object())
    monkeypatch.setattr(hatena_fotolife, "read_raw", lambda *a, **k: (None, None))
    monkeypatch.setattr(hatena_fotolife, "_read_image", lambda *a, **k: (PNG, "image/png"))
    monkeypatch.setattr(hatena_fotolife, "uploads_today", lambda *a, **k: 0)
    with pytest.raises(hatena_fotolife.FotolifeStop) as caught:
        hatena_fotolife.ensure_uploaded(
            {"principal": "p", "hatena_id": "h"}, {}, {"images": [{"path": "a.png", "source_sha256": sha}]})
    assert caught.value.code == "invalid_media_traits" and caught.value.exit_code == 2


# ---------------------------------------------------------------- 低-1 write.floor は返信で止めない

def test_低1_最低線は返信を止めない_返信以外は止める(world):
    """柵: `applies_to` に reply を戻すと赤。"""
    assert "reply" not in limits_policy.rule("write.floor")["applies_to"]
    assert "post" in limits_policy.rule("write.floor")["applies_to"]
    cfg_f = {"account": FAKE, "media": "fake"}
    for i in range(20):
        sns_post(world, FAKE, minutes_before(9) + datetime.timedelta(seconds=i))
    guard.check(FAKE, cfg_f, "publish", now=NOW, reply=True)       # 返信は止めない
    assert guard.stopped(FAKE) is None
    e = refused(lambda: guard.check(FAKE, cfg_f, "publish", now=NOW))
    assert str(e) == "account_stopped"


# ---------------------------------------------------------------- 低-2 遠くの道の札のファイルは 48 KB まで

def test_低2_48KBを超える札のファイルは送る前に案内する(bb, monkeypatch, capsys):
    """柵: 手元の大きさ検査を外すと赤（Worker の 413 まで行く）。"""
    sent_calls = []
    monkeypatch.setattr(remote, "call", lambda *a, **k: sent_calls.append(a) or {})
    big = write(bb, [item(f"https://h{i}.example.com/" + "p" * 480) for i in range(100)], "big.jsonl")
    assert os.path.getsize(big) > 48000
    args = argparse.Namespace(file=big, confirm=None, json=False)
    assert remote.COMMANDS["bookmark batch"](args, "far-account") == 2
    assert "48 KB を超えます。分けてください" in capsys.readouterr().err
    assert sent_calls == []


# ---------------------------------------------------------------- 低-4 はてなの 429 は待つ

def test_低4_はてなの429は止めずに待つ(bb, monkeypatch):
    """柵: 429 を blocked に戻すと赤。"""
    approve(write(bb, [item(URL_A1), item(URL_B1)]))

    def boom(*a, **k):
        raise hatena_bookmark.BookmarkError("hatena_http_429")
    monkeypatch.setattr(hatena_bookmark, "execute_locked", boom)
    now = jst.now_jst()
    out, _ = run(bb, now=now)
    assert out["status"] == "waiting" and not out.get("blocked")
    assert states(bb) == ["pending", "pending"]
    nxt = jst.parse(bookmark_batch.status(ACCOUNT)["next_at"])
    assert nxt - now == datetime.timedelta(minutes=bookmark_batch.RATE_LIMITED_MINUTES)


# ---------------------------------------------------------------- 低-5 自分の記事が published でないとき

def _own_batch(bb):
    dl = published(ACCOUNT, OWN_URL)
    approve(write(bb, [{"delivery": dl, "comment": "自分の記事。まとめ", "tags": []}]))
    return dl


def test_低5_配信が更新中なら最終の断りにせず翌日へ回す(bb):
    """柵: `_own_article_pending` を外すと refused になって赤。"""
    dl = _own_batch(bb)
    deliveries.advance(ACCOUNT, dl, state="inflight", at="2026-09-09T10:00:00+09:00")
    out, _ = run(bb)
    [it] = batch_file(bb)["items"]
    assert it["state"] == "pending" and it["wait"] == "bookmark_target_not_own" and it.get("not_before")
    assert out["deferred"] == 1 and out["refused"] == 0


def test_低5_配信が消えた_deletedなら今までどおり最終の断り(bb):
    dl = _own_batch(bb)
    deliveries.advance(ACCOUNT, dl, state="deleted", at="2026-09-09T10:00:00+09:00")
    run(bb)
    [it] = batch_file(bb)["items"]
    assert (it["state"], it["reason"]) == ("refused", "bookmark_target_not_own")


def test_低5_配信の台帳が無くなっていても最終の断り(bb):
    dl = _own_batch(bb)
    os.remove(deliveries.path_for(ACCOUNT, dl))
    run(bb)
    [it] = batch_file(bb)["items"]
    assert (it["state"], it["reason"]) == ("refused", "bookmark_target_not_own")


# ---------------------------------------------------------------- 低-7 ファイル名と approved_sha

def test_低7_まとめのファイル名とapproved_shaが違えば付けない(bb):
    """柵: `_check_integrity` の名前の照合を外すと赤（別の名前に置き換えられたまとめが付く）。"""
    approve(write(bb, [item(URL_A1)]))
    b = batch_file(bb)
    assert bookmark_batch._check_integrity(b) is True
    d = os.path.join(bb.state(ACCOUNT), "bookmark_batches")
    os.rename(os.path.join(d, b["batch"] + ".json"), os.path.join(d, "bb_0000000000000000.json"))
    [moved] = bookmark_batch.load_batches(ACCOUNT)
    assert moved["batch"] == "bb_0000000000000000"
    assert bookmark_batch._check_integrity(moved) is False
    run(bb)
    [it] = bookmark_batch.load_batches(ACCOUNT)[0]["items"]
    assert (it["state"], it["reason"]) == ("refused", "approval_stale") and calls(bb) == []
