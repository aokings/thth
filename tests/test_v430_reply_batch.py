"""4.3.0: 返信はまとめて出す（masaru 10/19「返信が 10 分おきに出る、みたいなオペレーションになる」）。

設計 `docs/設計_上限と連投の柵_2026-10-17.md` の「返信の扱い（4.3）」。

- timer は時刻の来た返信を 1 回の run でまとめて出す。`max_per_run` は投稿にだけ掛ける。返信は返信の窓
  （`reply.burst`・既定 10 分に 10）と 1 回の時間（150 秒）に入るだけ。超えた分は queue に残り次の run が出す
  （口座は止めない・`publish_at` は書き換えない）。返信には最短間隔を掛けない。
- 承認の 2 段目（`--confirm`・`--confirm-file`）で、時刻の過ぎた返信をその場で送る。未来の返信・投稿は timer。
- 返信の窓は返信だけを数え、投稿の窓（`post.burst`）は返信を数えない。
"""
from __future__ import annotations

import datetime
import os

import pytest

from tests.conftest import FIXED_NOW_JST, run_git, write_queue_file
from tests.test_send import _digest_for
from thth import accounts as accounts_mod
from thth import cli, core, guard, jst, limits_policy, queuefile, sent
from thth.adapters import base

NOW = FIXED_NOW_JST  # 2026-09-09 10:00 JST（`frozen_now_jst` と同じ）


class _Media:
    """偽の媒体。出した順に (本文, 返信先) を控える。公開の時刻は run の時刻。"""

    def __init__(self, at=NOW):
        self.calls = []
        self.at = at

    def publish(self, post, *, dry_run=False, on_container_created=None, **_kwargs):
        self.calls.append((post.text, post.reply_to))
        return base.PublishResult(post_id=f"9{len(self.calls):06d}", url=None, ts=jst.iso(self.at))

    def fetch_post(self, post_id):
        return {"author_key": "0123456789abcdef"}


def _place(account, name, *, reply_to=None, minutes_ago=5, status="approved"):
    at = NOW - datetime.timedelta(minutes=minutes_ago)
    fm = {"account": account["name"], "publish_at": jst.iso(at), "reply_to": reply_to, "status": status}
    if status != "approved":
        fm.update(approved_sha=None, approved_at=None)
    return write_queue_file(account["queue_dir"], name, body=f"## threads\n\n{name} の本文です。\n",
                            fm_overrides=fm)


def _status(path):
    return queuefile.parse(path).front_matter.get("status")


def _tick(account, media, *, minutes=0):
    media.at = NOW + datetime.timedelta(minutes=minutes)
    lines = []
    result = core.throw_once(account["name"], production_flag=True, adapter_factory=lambda *_: media,
                             now=media.at, log=lines.append)
    return result, lines


@pytest.fixture
def account(isolated_account_factory):
    return isolated_account_factory(production=True, min_interval_hours=0)


# --------------------------------------------------------------------------
# timer
# --------------------------------------------------------------------------

def test_返信5本を1回のrunで全部出す_投稿はmax_per_runの1本(account):
    replies = [_place(account, f"r{i}.md", reply_to=f"1799{i}", minutes_ago=30 - i) for i in range(5)]
    posts = [_place(account, f"p{i}.md", minutes_ago=40 - i) for i in range(2)]
    media = _Media()
    result, lines = _tick(account, media)
    assert result.exit_code == 0
    assert len(media.calls) == 6
    assert sorted(c[1] for c in media.calls if c[1]) == [f"1799{i}" for i in range(5)]
    assert all(_status(p) == "posted" for p in replies)
    assert [_status(p) for p in posts] == ["posted", "approved"]     # 投稿は 1 本だけ
    assert any("返信を 5 本出しました" in line for line in lines)
    assert guard.stopped(account["name"]) is None


def test_返信12本なら10本出して2本はqueueに残る_口座は止まらない(account):
    replies = [_place(account, f"r{i:02d}.md", reply_to=f"17{i:02d}", minutes_ago=30 - i) for i in range(12)]
    media = _Media()
    result, lines = _tick(account, media)
    assert result.exit_code == 0 and len(media.calls) == 10
    assert [_status(p) for p in replies] == ["posted"] * 10 + ["approved"] * 2
    assert guard.stopped(account["name"]) is None
    line = next(line for line in lines if "返信を 10 本出しました" in line)
    # 窓が空くのは 10 本を出した時刻 + 10 分（窓は (t - 10 分, t]）。
    assert "2 本は返信の窓（10 分に 10）を超えたので 10:10 ごろの timer で出ます" in line
    assert "口座は止めていません" in line
    # 指紋（publish_at）は書き換えない。
    assert queuefile.parse(replies[-1]).front_matter["publish_at"] == jst.iso(NOW - datetime.timedelta(minutes=19))
    # 9 分後の run は窓の中にまだ 10 本ある → 出さない。10 分後（窓は (t - 10 分, t]）に残りの 2 本。
    _tick(account, media, minutes=9)
    assert len(media.calls) == 10
    _tick(account, media, minutes=10)
    assert len(media.calls) == 12 and all(_status(p) == "posted" for p in replies)


def test_返信には最短間隔を掛けない_投稿には掛ける(isolated_account_factory):
    account = isolated_account_factory(production=True, min_interval_hours=6, max_per_run=3)
    first = _place(account, "a-post.md", minutes_ago=60)
    replies = [_place(account, f"r{i}.md", reply_to=f"1799{i}", minutes_ago=50 - i) for i in range(3)]
    second = _place(account, "z-post.md", minutes_ago=10)
    media = _Media()
    _tick(account, media)
    assert _status(first) == "posted" and all(_status(p) == "posted" for p in replies)
    assert _status(second) == "approved"      # 投稿の間隔（6 時間）は今までどおり


def test_1回の時間を超えたら残りの返信は次のrun(account, monkeypatch):
    clock = iter([0, 100, 200, 300, 400, 500])
    monkeypatch.setattr(core, "_monotonic", lambda: next(clock))
    replies = [_place(account, f"r{i}.md", reply_to=f"1799{i}", minutes_ago=30 - i) for i in range(5)]
    media = _Media()
    _result, lines = _tick(account, media)
    # 1 本目は時間を見ない・2 本目は 100 秒・3 本目で 200 秒 > 150 秒 → 打ち切り。
    assert len(media.calls) == 2
    assert [_status(p) for p in replies] == ["posted"] * 2 + ["approved"] * 3
    assert any("3 本は 1 回の実行の時間（150 秒）を超えたので 次の timer で出ます" in line for line in lines)


def test_返信どうしの間は本番の時計でだけ置く(account, monkeypatch):
    pauses = []
    monkeypatch.setattr(core, "_pause", pauses.append)
    for i in range(4):
        _place(account, f"r{i}.md", reply_to=f"1799{i}", minutes_ago=30 - i)
    media = _Media()
    _tick(account, media)                      # 試験の時計（now を渡す）→ 待たない
    assert len(media.calls) == 4 and pauses == []
    for i in range(3):
        _place(account, f"s{i}.md", reply_to=f"1800{i}", minutes_ago=3 - i)
    core.throw_once(account["name"], production_flag=True, adapter_factory=lambda *_: media)  # 本番の時計
    assert len(media.calls) == 7 and pauses == [core.REPLY_GAP_SECONDS] * 2


def test_表が作れないときは返信もmax_per_runに数える(account, monkeypatch):
    """4.2 までの振る舞いに倒す（窓を読めないのに何本も出さない）。"""
    monkeypatch.setattr(guard, "reply_window", lambda *a, **k: None)
    for i in range(3):
        _place(account, f"r{i}.md", reply_to=f"1799{i}", minutes_ago=30 - i)
    media = _Media()
    _tick(account, media)
    assert len(media.calls) == 1


def test_dry_runもまとめて出したら何が出るかを見せる(isolated_account_factory):
    account = isolated_account_factory(production=False, min_interval_hours=0)
    replies = [_place(account, f"r{i}.md", reply_to=f"1799{i}", minutes_ago=30 - i) for i in range(3)]
    lines = []
    result = core.throw_once(account["name"], production_flag=False, now=NOW, log=lines.append)
    assert result.mode == "rehearsal" and all(_status(p) == "approved" for p in replies)
    assert any("返信を 3 本出すはずです（dry-run）" in line for line in lines)


# --------------------------------------------------------------------------
# 承認の場の送り
# --------------------------------------------------------------------------

def _digest_of(stdout):
    """1 本なら `digest: `、複数なら `束の digest: `。"""
    lines = stdout.splitlines()
    bundle = [line.split(": ", 1)[1].strip() for line in lines if line.startswith("束の digest: ")]
    return bundle[0] if bundle else [line.split(": ", 1)[1].strip() for line in lines
                                     if line.startswith("digest: ")][0]


@pytest.fixture
def live(monkeypatch):
    """承認の場の送りが本番の口座で媒体へ出る形（token は有る扱い・媒体は偽物）。"""
    media = _Media()
    monkeypatch.setattr(accounts_mod, "token_exists", lambda cfg: True)
    monkeypatch.setattr(core, "_pause", lambda seconds: None)   # 本番の時計の run の 3 秒は試験では待たない
    monkeypatch.setattr(core, "_default_adapter_factory", lambda *_: media)
    return media


def test_承認の2段目で時刻の過ぎた返信がその場で出る_未来の返信と投稿はtimer(account, live, capsys):
    due = [_place(account, f"r{i}.md", reply_to=f"1799{i}", minutes_ago=5 - i, status="draft") for i in range(3)]
    later = _place(account, "r-later.md", reply_to="17999", minutes_ago=-30, status="draft")
    post = _place(account, "p.md", minutes_ago=5, status="draft")
    paths = due + [later, post]
    assert cli.main(["approve", *paths]) == 1
    digest = _digest_of(capsys.readouterr().out)
    assert cli.main(["approve", *paths, "--confirm", digest, "--by", "masaru"]) == 0
    out = capsys.readouterr().out
    assert f"{account['name']}: 承認して 3 本送りました" in out
    assert [_status(p) for p in due] == ["posted"] * 3
    assert _status(later) == "approved" and _status(post) == "approved"
    assert sorted(c[1] for c in live.calls) == ["17990", "17991", "17992"]


def test_confirm_fileでも時刻の過ぎた返信がその場で出る(account, live, capsys, tmp_path):
    due = [_place(account, f"r{i}.md", reply_to=f"1799{i}", minutes_ago=5, status="draft") for i in range(2)]
    later = _place(account, "r-later.md", reply_to="17999", minutes_ago=-30, status="draft")
    lines = []
    for path in due + [later]:
        assert cli.main(["approve", path]) == 1
        lines.append(f"{path} {_digest_of(capsys.readouterr().out)}")
    confirm = tmp_path / "confirm.txt"
    confirm.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert cli.main(["approve", "--confirm-file", str(confirm), "--by", "masaru", "--json"]) == 0
    import json
    data = json.loads(capsys.readouterr().out)
    (row,) = data["sent_now"]
    assert (row["account"], row["sent"], row["waiting"], row["skipped"]) == (account["name"], 2, 0, None)
    assert [_status(p) for p in due] == ["posted"] * 2 and _status(later) == "approved"


def test_承認の場の送りも返信の窓を超えた分はtimerに残す(account, live, capsys):
    store = accounts_mod.state_dir_for(account["name"])
    for i in range(9):   # 窓の中にすでに返信 9 本
        sent.write(store, post_id=f"8{i:06d}", text=f"前{i}", body_hash="x",
                   sent_at=jst.iso(NOW - datetime.timedelta(minutes=5)), reply_to=f"1700{i}")
    due = [_place(account, f"r{i}.md", reply_to=f"1799{i}", minutes_ago=3, status="draft") for i in range(3)]
    assert cli.main(["approve", *due]) == 1
    digest = _digest_of(capsys.readouterr().out)
    assert cli.main(["approve", *due, "--confirm", digest, "--by", "masaru"]) == 0
    out = capsys.readouterr().out
    # 窓の中の最も古い返信（09:55）+ 10 分で 1 本分が空く。
    assert "承認して 1 本送りました・2 本は返信の窓を超えたので 10:05 ごろの timer で出ます" in out
    assert sorted(_status(p) for p in due) == ["approved", "approved", "posted"]
    assert guard.stopped(account["name"]) is None


def test_本番でない口座はその場で送らない(isolated_account_factory, live, capsys):
    account = isolated_account_factory(production=False, min_interval_hours=0)
    due = _place(account, "r.md", reply_to="17990", minutes_ago=5, status="draft")
    assert cli.main(["approve", due]) == 1
    digest = _digest_of(capsys.readouterr().out)
    assert cli.main(["approve", due, "--confirm", digest, "--by", "masaru"]) == 0
    assert "本番の口座でないので" in capsys.readouterr().out
    assert _status(due) == "approved" and live.calls == []


def test_timerが動いていればその場では送らない(account, live, capsys, monkeypatch):
    due = _place(account, "r.md", reply_to="17990", minutes_ago=5, status="draft")
    assert cli.main(["approve", due]) == 1
    digest = _digest_of(capsys.readouterr().out)
    real = core._account_locks

    def busy(*args, **kwargs):
        raise core.lock_mod.LockBusy("busy")
    monkeypatch.setattr(core, "_account_locks", busy)
    assert cli.main(["approve", due, "--confirm", digest, "--by", "masaru"]) == 0
    assert "いま timer が動いているので" in capsys.readouterr().out
    assert _status(due) == "approved" and live.calls == []
    monkeypatch.setattr(core, "_account_locks", real)


# --------------------------------------------------------------------------
# 返信の窓と投稿の窓
# --------------------------------------------------------------------------

def _send(account, spy, text, *, minute, reply_to=None):
    return core.send_once(account["name"], text=text, reply_to=reply_to, production_flag=True,
                          confirm=_digest_for(account["name"], text, reply_to=reply_to),
                          adapter_factory=lambda *_: spy, now=NOW + datetime.timedelta(minutes=minute))


def test_返信は投稿の窓に数えない_投稿3本と返信5本でも口座が止まらない(isolated_account_factory):
    # 1 日の上限（既定 8・返信も数える）には当てない。
    account = isolated_account_factory(production=True, min_interval_hours=0, daily_max_posts=20)
    spy = _Media()
    results = [_send(account, spy, f"投稿{i}", minute=0) for i in range(2)]
    results += [_send(account, spy, f"お返事{i}", minute=1, reply_to=f"1799{i}") for i in range(5)]
    results.append(_send(account, spy, "投稿2", minute=2))
    assert [r.action for r in results] == ["post"] * 8
    assert len(spy.calls) == 8 and guard.stopped(account["name"]) is None
    # 投稿の窓は今までどおり投稿で止める（投稿 3 本のあとの 4 本目）。
    r = _send(account, spy, "投稿3", minute=3)
    assert r.action == "guard" and r.error == "account_stopped"


def test_返信の窓は返信だけを数える(account):
    store = accounts_mod.state_dir_for(account["name"])
    cfg = accounts_mod.load_account(account["name"])
    for i in range(5):
        sent.write(store, post_id=f"7{i:06d}", text=f"投稿{i}", body_hash="x", sent_at=jst.iso(NOW))
    for i in range(3):
        sent.write(store, post_id=f"8{i:06d}", text=f"返信{i}", body_hash="x", sent_at=jst.iso(NOW),
                   reply_to=f"1799{i}")
    window = guard.reply_window(account["name"], cfg, now=NOW)
    assert (window["count"], window["minutes"], len(window["times"])) == (10, 10, 3)


def test_返信の窓の既定は媒体の台帳の10分に10_根拠つき():
    for media in limits_policy.SNS_MEDIA:
        assert limits_policy.default_value("reply.burst", media=media) == {"count": 10, "minutes": 10}
        row = limits_policy.resolved("reply.burst", cfg={"media": media})
        assert row["count_evidence"] == "thth_line" and not row["trait"].startswith("thth:")
    rule = limits_policy.rule("reply.burst")
    assert rule["actions"] == ("reply",) and limits_policy.rule("post.burst")["actions"] == ("post", "retract")
    # 投稿の窓の既定は今のまま（10 分に 3）。
    assert limits_policy.default_value("post.burst", media="threads") == {"count": 3, "minutes": 10}


def test_承認の場の送りの対象は時刻の過ぎた返信だけ(account):
    due = _place(account, "r.md", reply_to="17990", minutes_ago=5)
    later = _place(account, "r2.md", reply_to="17991", minutes_ago=-5)
    post = _place(account, "p.md", minutes_ago=5)
    assert core.due_reply_paths([due, later, post], now=NOW) == [due]
    run_git(account["repo_dir"], ["status", "--short"])  # repo は壊していない
    assert os.path.exists(due)
