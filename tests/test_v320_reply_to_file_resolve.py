"""3.2.0 `reply_to_file` の解決・待ち・承認の指紋・記録（設計 3.2.0 §2・§3・§6）。

約束（§0）: 返信先を「同じ queue の原稿の名前」で書け、その原稿が出るまで道具は
待つ。**黙って root に落とさない**——解決できない候補は 1 文字も出ない。承認の
対象は「どの原稿への返信か」であり、解決後の post_id は承認に含めない。
"""
from __future__ import annotations

import datetime
import os

import pytest

from tests.conftest import make_queue_text, parse_verified
from tests.test_max_per_run import _init_git_pair_multi
from thth import accounts as accounts_mod
from thth import approval as approval_mod
from thth import core
from thth import jst
from thth import queuefile
from thth import runs as runs_mod
from thth import select as select_mod
from thth import sent as sent_mod
from thth.adapters import base as adapter_base

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
Q = "q.md"
A = "a.md"
Q_POST_ID = "17900000000000001"
ACCOUNT_CFG = {"media": "threads", "quiet_hours": None, "min_interval_hours": 0,
               "stale_days": 7, "hashtags": False}


def _q_text(**overrides):
    fm = {"status": "posted", "post_id": Q_POST_ID,
          "posted_at": "2026-09-08T23:00:00+09:00",
          "publish_at": "2026-09-08T23:00:00+09:00", **overrides}
    return make_queue_text(fm_overrides=fm, body="## threads\n\n今日の問い。\n")


def _a_text(**overrides):
    fm = {"reply_to_file": Q, "publish_at": "2026-09-09T08:00:00+09:00", **overrides}
    return make_queue_text(fm_overrides=fm, body="## threads\n\n昨日の答え。\n")


def _write(dir_path, name, text):
    path = os.path.join(dir_path, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _select(files, **kwargs):
    return select_mod.select_one(files, account_name="nigamilab-threads",
                                 account_cfg=ACCOUNT_CFG, now=NOW, last_post_at=None,
                                 recent_texts=set(), **kwargs)


def _reasons(result):
    return {os.path.basename(r.file): r.reason for r in result.rejections}


# ---------------------------------------------------------------- select（単体）

def test_指した原稿が出ていれば解決してそのpost_idに返す(tmp_path):
    q = parse_verified(_write(str(tmp_path), Q, _q_text()))
    a = parse_verified(_write(str(tmp_path), A, _a_text()))
    result = _select([q, a])
    assert result.chosen is a
    assert result.resolution == {"file": Q, "post_id": Q_POST_ID}


def test_指した原稿がまだ出ていなければ待つ(tmp_path):
    q = parse_verified(_write(str(tmp_path), Q, _q_text(
        status="approved", post_id=None, posted_at=None)))
    a = parse_verified(_write(str(tmp_path), A, _a_text()))
    result = _select([q, a])
    assert result.chosen is not a
    assert _reasons(result)[A] == "reply_to_unresolved: waiting_for q.md"
    assert a.path in result.needs_review
    assert select_mod.waiting_target(_reasons(result)[A]) == Q


@pytest.mark.parametrize("q_overrides,expected", [
    ({"retracted_at": "2026-09-09T09:00:00+09:00"}, "reply_to_unresolved: target_retracted"),
    ({"post_id": ".."}, "reply_to_unresolved: target_unreadable"),
    ({"status": "approved"}, "reply_to_unresolved: target_unreadable"),
    ({"account": "kopicha-threads"}, "reply_to_file_account_mismatch"),
])
def test_指した原稿の状態で断る(tmp_path, q_overrides, expected):
    q = parse_verified(_write(str(tmp_path), Q, _q_text(**q_overrides)))
    a = parse_verified(_write(str(tmp_path), A, _a_text()))
    result = _select([q, a])
    assert result.chosen is None
    assert _reasons(result)[A] == expected
    assert a.path in result.needs_review


def test_指した原稿が型外なら読めないと言う(tmp_path):
    q = parse_verified(_write(str(tmp_path), Q, "front-matter の無い原稿\n"))
    a = parse_verified(_write(str(tmp_path), A, _a_text()))
    result = _select([q, a])
    assert result.chosen is None
    assert _reasons(result)[A] == "reply_to_unresolved: target_unreadable"


def test_指した原稿が消えていれば断る(tmp_path):
    a = parse_verified(_write(str(tmp_path), A, _a_text()))
    result = _select([a])
    assert result.chosen is None
    assert _reasons(result)[A] == "reply_to_unresolved: target_missing"


def test_同期を確かめていない指した原稿は信じず待つ(tmp_path):
    """手元で post_id を書き足しただけの原稿に返信先を向けさせない。"""
    q = queuefile.parse(_write(str(tmp_path), Q, _q_text()))      # verified=False
    a = parse_verified(_write(str(tmp_path), A, _a_text()))
    result = _select([q, a])
    assert result.chosen is None
    assert _reasons(result)[A] == "reply_to_unresolved: waiting_for q.md"


def test_自分自身を指す原稿は承認済みでも出さない(tmp_path):
    a = parse_verified(_write(str(tmp_path), A, _a_text(reply_to_file=A)))
    result = _select([a])
    assert result.chosen is None
    assert _reasons(result)[A] == "reply_to_file_self"


def test_待ちは時刻に依存せず未来の予約でも要確認に出る(tmp_path):
    q = parse_verified(_write(str(tmp_path), Q, _q_text(
        status="approved", post_id=None, posted_at=None,
        publish_at="2026-09-10T23:00:00+09:00")))
    a = parse_verified(_write(str(tmp_path), A, _a_text(publish_at="2026-09-11T23:00:00+09:00")))
    result = _select([q, a])
    assert _reasons(result)[A] == "reply_to_unresolved: waiting_for q.md"


def test_poolから探すので候補から外した問いでも無いとは言わない(tmp_path):
    q = parse_verified(_write(str(tmp_path), Q, _q_text()))
    a = parse_verified(_write(str(tmp_path), A, _a_text()))
    result = _select([a], pool=[q, a])
    assert result.chosen is a and result.resolution["post_id"] == Q_POST_ID


# ---------------------------------------------------------------- 承認の指紋

def test_指紋のreply_to欄はfile印で解決したpost_idは入らない():
    fm = {"reply_to_file": Q}
    assert approval_mod.reply_to_for_fingerprint(fm) == "file:q.md"
    assert approval_mod.reply_to_for_fingerprint({"reply_to": "123"}) == "123"
    both = approval_mod.reply_to_for_fingerprint({"reply_to": "123", "reply_to_file": Q})
    assert both not in ("file:q.md", "123")


def test_承認のあとにreply_to_fileを変えるとapproval_stale(tmp_path):
    q = parse_verified(_write(str(tmp_path), Q, _q_text()))
    _write(str(tmp_path), "q2.md", _q_text())
    text = _a_text().replace("reply_to_file: q.md", "reply_to_file: q2.md")
    a = parse_verified(_write(str(tmp_path), A, text))
    result = _select([q, a])
    assert result.chosen is None
    assert _reasons(result)[A] == "approval_stale"


def test_承認のあとにreply_toへ書き換えるとapproval_stale(tmp_path):
    q = parse_verified(_write(str(tmp_path), Q, _q_text()))
    text = _a_text().replace("reply_to_file: q.md", "reply_to_file: ").replace(
        "reply_to: \n", f"reply_to: {Q_POST_ID}\n")
    a = parse_verified(_write(str(tmp_path), A, text))
    assert a.front_matter.get("reply_to") == Q_POST_ID and not a.front_matter.get("reply_to_file")
    result = _select([q, a])
    assert result.chosen is None
    assert _reasons(result)[A] == "approval_stale"


def test_待っている間に承認して問いが出たあとも承認は生きている(tmp_path):
    """解決した post_id は指紋に入らない——問いが出ても答えの承認は古くならない。"""
    waiting_q = _q_text(status="approved", post_id=None, posted_at=None)
    q = parse_verified(_write(str(tmp_path), Q, waiting_q))
    a = parse_verified(_write(str(tmp_path), A, _a_text()))
    assert _select([q, a]).chosen is not a
    q = parse_verified(_write(str(tmp_path), Q, _q_text()))
    result = _select([q, a])
    assert result.chosen is a and result.resolution["post_id"] == Q_POST_ID


# ---------------------------------------------------------------- 公開（本物の git）

class RecordingAdapter:
    """publish に渡った Post を控える偽アダプタ（送った本文と返信先を見る）。"""

    def __init__(self):
        self.posts = []

    def publish(self, post, dry_run=False, on_container_created=None, before_publish=None):
        if before_publish is not None and before_publish():
            return adapter_base.PublishResult(None, None, jst.iso(), error="vetoed",
                                              failure="publish_vetoed")
        self.posts.append(post)
        return adapter_base.PublishResult(f"1800000000000{len(self.posts):04d}", None, jst.iso())

    def fetch_post(self, post_id):
        return {"author_key": None}


def _account(isolated_account_factory, tmp_path, files, **overrides):
    pair = _init_git_pair_multi(tmp_path, files)
    account = isolated_account_factory(repo_dir=pair["work"], production=True,
                                       min_interval_hours=0, **overrides)
    return pair, account


def _throw(account, adapter, production=True):
    return core.throw_once(account["name"], production_flag=production,
                           adapter_factory=lambda _c, _t: adapter, now=NOW)


def test_問いが出ていれば答えはその投稿への返信として出て記録に解決元が残る(
        isolated_account_factory, tmp_path):
    pair, account = _account(isolated_account_factory, tmp_path, {Q: _q_text(), A: _a_text()})
    adapter = RecordingAdapter()
    result = _throw(account, adapter)
    assert result.exit_code == 0 and result.action == "post", result.message
    assert len(adapter.posts) == 1
    assert adapter.posts[0].reply_to == Q_POST_ID
    assert adapter.posts[0].text == "昨日の答え。"

    state_dir = accounts_mod.state_dir_for(account["name"])
    sent = sent_mod.read(state_dir, result.post_id)
    assert sent["reply_to"] == Q_POST_ID
    assert sent["reply_to_file"] == Q
    assert sent["resolved_from"] == {"file": Q, "post_id": Q_POST_ID}
    ok = [r for r in runs_mod.read_runs(state_dir) if r["action"] == "post" and r["status"] == "ok"]
    assert ok[-1]["reply_to_file"] == Q
    assert ok[-1]["resolved_from"] == {"file": Q, "post_id": Q_POST_ID}

    fm = queuefile.parse(os.path.join(pair["queue_dir"], A)).front_matter
    assert fm["status"] == "posted" and fm["post_id"] == result.post_id
    assert fm["reply_to_resolved"] == Q_POST_ID
    assert fm["reply_to_file"] == Q, "reply_to_file は残す"
    # 書き戻し後も承認の指紋は変わらない（reply_to_resolved は指紋の外）。
    assert core._current_fingerprint(os.path.join(pair["queue_dir"], A), "threads") \
        == fm["approved_sha"]


def test_rehearsalは返信先の解決をログに出すだけ(isolated_account_factory, tmp_path):
    _pair, account = _account(isolated_account_factory, tmp_path, {Q: _q_text(), A: _a_text()})
    lines = []
    adapter = RecordingAdapter()
    result = core.throw_once(account["name"], production_flag=False,
                             adapter_factory=lambda _c, _t: adapter, now=NOW, log=lines.append)
    assert result.action == "skip"
    assert f"返信先: {Q} → {Q_POST_ID}" in lines
    assert adapter.posts == []


@pytest.mark.parametrize("case", ["no_post_id", "retracted", "unreadable", "missing"])
def test_解決できない答えは1文字も出ない(isolated_account_factory, tmp_path, case):
    files = {A: _a_text()}
    if case == "no_post_id":
        files[Q] = _q_text(status="draft", post_id=None, posted_at=None)
    elif case == "retracted":
        files[Q] = _q_text(retracted_at="2026-09-09T09:00:00+09:00")
    elif case == "unreadable":
        files[Q] = _q_text(post_id="..")
    _pair, account = _account(isolated_account_factory, tmp_path, files)
    adapter = RecordingAdapter()
    result = _throw(account, adapter)
    assert adapter.posts == [], "解決できない候補が出た（root に落ちた）"
    assert result.action == "none"
    reasons = {r["file"]: r["reason"] for r in (result.rejections or [])}
    assert reasons[A].startswith("reply_to_unresolved: ")


def test_同じrunで問いを出した直後の答えは次のrunで出る(isolated_account_factory, tmp_path):
    files = {Q: _q_text(status="approved", post_id=None, posted_at=None,
                        publish_at="2026-09-09T07:00:00+09:00"),
             A: _a_text()}
    pair, account = _account(isolated_account_factory, tmp_path, files, max_per_run=3)
    adapter = RecordingAdapter()
    first = _throw(account, adapter)
    assert first.exit_code == 0
    assert [p.reply_to for p in adapter.posts] == [None], "この run で出るのは問いだけ"
    q_post_id = queuefile.parse(os.path.join(pair["queue_dir"], Q)).front_matter["post_id"]

    second = _throw(account, adapter)
    assert second.exit_code == 0 and second.action == "post"
    assert [p.reply_to for p in adapter.posts] == [None, q_post_id]


def test_解決していない候補を渡されてもcoreはrootに落とさない(isolated_account_factory, tmp_path):
    """select を通らずに来ても（resolution 無し）、公開の門が断る（二重の守り）。"""
    pair, account = _account(isolated_account_factory, tmp_path, {Q: _q_text(), A: _a_text()})
    cfg = accounts_mod.load_account(account["name"])
    state_dir = accounts_mod.state_dir_for(account["name"])
    chosen = parse_verified(os.path.join(pair["queue_dir"], A))
    adapter = RecordingAdapter()
    for resolution in (None, {"file": "other.md", "post_id": Q_POST_ID},
                       {"file": Q, "post_id": "99999999999"}):
        result = core._throw_chosen(account["name"], cfg, state_dir, "run1", "production",
                                    chosen, "昨日の答え。", NOW, lambda _l: None,
                                    lambda _c, _t: adapter, resolution=resolution)
        assert result.exit_code == 2 and result.error == "reply_to_unresolved"
    assert adapter.posts == []


def test_承認の一段目は返信先の原稿の名前を見せる(isolated_account_factory, tmp_path):
    pair, _account_row = _account(isolated_account_factory, tmp_path, {
        Q: _q_text(), A: _a_text(status="draft")})
    from thth import cli
    prepared, problem = cli._prepare_one(os.path.join(pair["queue_dir"], A))
    assert problem is None
    assert prepared["reply_to_file"] == Q
    expected = approval_mod.compute_approved_sha(
        section="昨日の答え。", account="nigamilab-threads", reply_to="file:q.md",
        topic=None, publish_at="2026-09-09T08:00:00+09:00")
    assert prepared["approved_sha"] == expected
