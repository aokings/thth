"""4.0 S4 §13: SNS の告知を記事に結ぶ（T-S4-9a〜9e）と、`thth deliveries --article`（T-S4-10a）。

- はてなの口座 `lane-a-hatenablog`（project lane-a）の配信の台帳に配信を 1 本置き、同じ project の
  Threads の口座 `lane-a-threads`（本物の git repo）の原稿から `announces` で結ぶ。
- 送信の口は偽物（`Spy`・何も送らない）。はてなには出ない。秘密の値は置かない。
"""
from __future__ import annotations

import datetime
import json
import os
import sys

import pytest

from tests.conftest import init_git_pair, make_account_json
from tests.hatena_s1_flow_helpers import approve, call, fm_of
from tests.hatena_s1_helpers import world  # noqa: F401
from thth import (accounts, approval, core, deliveries, deliveries_cli, inflight, inflight_resolve, jst, lint,
                  select, sent)
from thth.adapters.base import PublishResult

NOW = datetime.datetime(2026, 9, 9, 10, 0, 0, tzinfo=jst.JST)
SNS = "lane-a-threads"
HATENA = "lane-a-hatenablog"
ARTICLE_URL = "https://a.hatenablog.com/entry/2026/09/09/090000"
BODY = "アラビカの 9 月の値動きをまとめました。{article_url}"


class Lane:
    def __init__(self, world, repo, queue_dir, delivery):  # noqa: F811
        self.world = world
        self.repo = repo
        self.queue_dir = queue_dir
        self.delivery = delivery

    @property
    def state(self):
        return self.world.state(SNS)

    def manuscript(self, name="t.md", *, announces=None, article="lane-a/arabica", body=BODY, extra=None,
                   account=SNS):
        lines = ["---", "thth: 1", f"account: {account}", "status: draft",
                 "publish_at: 2026-09-09T08:30:00+09:00"]
        if announces is not False:
            lines.append(f"announces: {announces or self.delivery}")
        if article:
            lines.append(f"article: {article}")
        for key, value in (extra or {}).items():
            lines.append(f"{key}: {value}")
        lines += ["---", "", "## threads", "", body, ""]
        path = os.path.join(self.queue_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        from tests.conftest import commit_and_push_path
        commit_and_push_path(path)
        return path

    def advance(self, state, url=None):
        fields = {"published_url": url} if url else None
        deliveries.advance(HATENA, self.delivery, state=state, at="2026-09-09T09:00:00+09:00", fields=fields)


@pytest.fixture
def sns(world):  # noqa: F811
    pair = init_git_pair(world.tmp / "sns", seed_content="", seed_name=".gitkeep")
    make_account_json(str(world.accounts_dir), SNS, repo_dir=pair["work"], project="lane-a", production=True,
                      handle="lane_a")
    record = deliveries.create(HATENA, principal="hatena-main", blog_id="a.hatenablog.com", article="lane-a/arabica",
                               at="2026-09-09T08:00:00+09:00")
    return Lane(world, pair["work"], pair["queue_dir"], record["delivery"])


class Spy:
    def __init__(self, lane):
        self.lane = lane
        self.posts = []
        self.inflight = []

    def publish(self, post, **kw):
        self.posts.append(post)
        self.inflight.append(inflight.read(self.lane.state))
        return PublishResult(f"1790000000000{len(self.posts)}", None, NOW.isoformat())


def throw(spy):
    return core.throw_once(SNS, production_flag=True, adapter_factory=lambda *_: spy, now=NOW)


def reasons(result):
    return [r["reason"] for r in (result.rejections or [])]


def lint_codes(path):
    return [lint.reason_code([e]) for e in lint.lint_file(path) if not lint.is_warning(e)]


# ---------------------------------------------------------------- T-S4-9a lint

def test_T_S4_9a_lintの断り(sns, world):  # noqa: F811
    assert lint_codes(sns.manuscript()) == []
    assert lint_codes(sns.manuscript("a.md", announces="dl_xyz")) == ["invalid_announces"]
    assert lint_codes(sns.manuscript("b.md", announces="dl_000000000000")) == ["announces_not_found"]
    other = deliveries.create("lane-c-hatenablog", principal="hatena-other", blog_id="c.hatenablog.com",
                              article="lane-c/x", at="2026-09-09T08:00:00+09:00")["delivery"]
    assert lint_codes(sns.manuscript("c.md", announces=other)) == ["announces_other_lane"]
    assert lint_codes(sns.manuscript("d.md", body="{article_url} と {article_url}")) == [
        "article_url_placeholder_twice"]
    assert lint_codes(sns.manuscript("e.md", announces=False, article=None)) == ["article_url_without_announces"]
    assert lint_codes(sns.manuscript("f.md", article="lane-a/other")) == ["article_mismatch"]
    # はてなの口座の原稿には書けない
    from tests.hatena_s1_flow_helpers import manuscript
    path = os.path.join(sns.queue_dir, "h.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(manuscript(extra={"announces": sns.delivery}).replace("account: lane-a-hatenablog",
                                                                       f"account: {HATENA}"))
    assert "announces_not_allowed" in [lint.reason_code([e]) for e in lint.lint_file(path)
                                       if e.startswith("announces_not_allowed")]


def test_T_S4_9a_文字数は仮のURLで数える_XはURLを23字(sns, world):  # noqa: F811
    placeholder = "https://a.hatenablog.com/entry/0000/00/00/000000"
    fm = {"announces": sns.delivery}
    cfg = accounts.load_account(SNS)
    from thth import announce, queuefile
    assert announce.counted_text(BODY, fm, cfg) == BODY.replace("{article_url}", placeholder)
    # Threads の上限（500）を仮の URL で越える本文は断る
    near = "あ" * (500 - len(placeholder) + 1) + "{article_url}"
    assert len(near) < 500
    assert "too_long" in lint_codes(sns.manuscript("long.md", body=near))
    # X は URL を 23 字に数える既存の数え方がそのまま効く
    assert queuefile.count_for("x", announce.counted_text("本文 {article_url}", fm, cfg)) == (
        queuefile.count_for("x", "本文 ") + 23)


# ---------------------------------------------------------------- T-S4-9b 待ちと held

@pytest.mark.parametrize("state", ["wanted", "inflight", "unknown"])
def test_T_S4_9b_記事が出るまで待つ_heldに数えない(sns, world, state):  # noqa: F811
    """柵: 待ちを held にすると赤（held_category が None でない）。"""
    path = sns.manuscript()
    approve(path)
    if state != "wanted":
        sns.advance(state)
    spy = Spy(sns)
    result = throw(spy)
    assert spy.posts == [] and result.action != "post"
    reason = f"announces_unresolved: waiting_for {sns.delivery}"
    assert reason in reasons(result)
    assert select.held_category(reason) is None
    assert select.waiting_target(reason) == sns.delivery
    assert fm_of(path)["status"] == "approved"


@pytest.mark.parametrize("state,detail", [("conflict", "target_conflict"), ("deleted", "target_deleted"),
                                          ("no_follow", "target_no_follow")])
def test_T_S4_9b_conflictやdeletedはheld(sns, world, state, detail):  # noqa: F811
    path = sns.manuscript()
    approve(path)
    sns.advance("published", ARTICLE_URL)
    sns.advance(state)
    spy = Spy(sns)
    result = throw(spy)
    assert spy.posts == []
    reason = f"announces_unresolved: {detail}"
    assert reason in reasons(result) and select.held_category(reason) == "announces_unresolved"
    assert select.held_reason_code([{"due": True, "category": "announces_unresolved"}]) == (
        "approved_but_held: announces_unresolved 1")
    from thth import healthcheck
    assert healthcheck._HELD_CODE.fullmatch("approved_but_held: approval_stale 1, announces_unresolved 2")


def test_T_S4_9b_台帳が無い_読めないもheld(sns, world):  # noqa: F811
    path = sns.manuscript()
    approve(path)
    target = deliveries.path_for(HATENA, sns.delivery)
    with open(target, "w", encoding="utf-8") as f:
        f.write("{broken")
    result = throw(Spy(sns))
    assert "announces_unresolved: target_unreadable" in reasons(result)
    os.unlink(target)
    result = throw(Spy(sns))
    assert "announces_unresolved: target_missing" in reasons(result)


# ---------------------------------------------------------------- T-S4-9c 置き換え

def test_T_S4_9c_承認は置き換え前の本文で_出る本文と記録と結果待ちは置き換え後(sns, world):  # noqa: F811
    """柵: 指紋を置き換え後の本文で取ると赤（approval_stale で出ない）。"""
    path = sns.manuscript()
    approve(path)
    fm = fm_of(path)
    expected = approval.compute_approved_sha(section=BODY, account=SNS, reply_to=None, topic=None,
                                             publish_at=fm["publish_at"], announces=sns.delivery)
    assert fm["approved_sha"] == expected
    sns.advance("published", ARTICLE_URL)
    spy = Spy(sns)
    result = throw(spy)
    assert result.action == "post", (result.message, reasons(result))
    sent_text = BODY.replace("{article_url}", ARTICLE_URL)
    assert spy.posts[0].text == sent_text
    assert spy.inflight[0]["text_fingerprint"] == inflight_resolve.text_fingerprint(sent_text)
    [row] = sent.records(sns.state)
    assert row["text"] == sent_text and row["approved_fingerprint"] == expected
    assert row["body_hash"] == approval.compute_body_hash(sent_text)
    # 書き戻しは今まで（本文は {article_url} のまま・承認は生きたまま）
    after = fm_of(path)
    assert after["status"] == "posted" and "{article_url}" in open(path, encoding="utf-8").read()


def test_T_S4_9c_承認のあとにannouncesを書き換えればapproval_stale(sns, world):  # noqa: F811
    path = sns.manuscript()
    approve(path)
    other = deliveries.create(HATENA, principal="hatena-main", blog_id="a.hatenablog.com", article="lane-a/arabica",
                              at="2026-09-09T08:00:00+09:00")["delivery"]
    text = open(path, encoding="utf-8").read().replace(sns.delivery, other)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    from tests.conftest import commit_and_push_path
    commit_and_push_path(path)
    sns.advance("published", ARTICLE_URL)
    result = throw(Spy(sns))
    assert "approval_stale" in reasons(result)


def test_T_S4_9c_article_urlの無い原稿は置き換えない(sns, world):  # noqa: F811
    path = sns.manuscript(body="記事を書きました（URL はプロフィールに）。")
    approve(path)
    sns.advance("published", ARTICLE_URL)
    spy = Spy(sns)
    assert throw(spy).action == "post"
    assert spy.posts[0].text == "記事を書きました（URL はプロフィールに）。"
    [row] = sent.records(sns.state)
    assert row["announces"] == sns.delivery and row["article_url"] == ARTICLE_URL


# ---------------------------------------------------------------- T-S4-9d sent の欄

def test_T_S4_9d_告知の行に3つの欄_無い原稿の行は1バイトも変わらない(sns, world):  # noqa: F811
    path = sns.manuscript()
    approve(path)
    sns.advance("published", ARTICLE_URL)
    assert throw(Spy(sns)).action == "post"
    [row] = sent.records(sns.state)
    assert (row["announces"], row["article"], row["article_url"]) == (sns.delivery, "lane-a/arabica", ARTICLE_URL)
    # announces の無い原稿の行（今までと同じ鍵だけ）
    plain = sns.manuscript("plain.md", announces=False, article=None, body="ふつうの投稿です。")
    approve(plain)
    spy = Spy(sns)
    assert throw(spy).action == "post"
    pid = fm_of(plain)["post_id"]
    with open(sent.path_for(sns.state, pid), encoding="utf-8") as f:
        raw = f.read()
    data = json.loads(raw)
    assert not {"announces", "article", "article_url"} & set(data)
    # 同じ引数で書けば同じバイト列（欄を足しても無ければ何も変えない）
    tmp = world.tmp / "plain-state"
    sent.write(str(tmp), post_id=pid, text=data["text"], body_hash=data["body_hash"], sent_at=data["sent_at"],
               approved_fingerprint=data["approved_fingerprint"], reply_to=None,
               **{k: data[k] for k in ("attachment_kinds", "link_urls") if k in data},
               **({"goal": data["goal"]} if "goal" in data else {}))
    with open(sent.path_for(str(tmp), pid), encoding="utf-8") as f:
        assert f.read() == raw
    with pytest.raises(ValueError, match="invalid_announces"):
        sent.write(str(tmp), post_id="x1", text="t", body_hash="h", sent_at="2026-09-09T10:00:00+09:00",
                   announces="dl_bad")


# ---------------------------------------------------------------- T-S4-9e 道

def test_T_S4_9e_send_requestはannouncesを受けず_draft_putは受ける(sns, world, monkeypatch):  # noqa: F811
    from thth import server_writes as writes
    from thth.report_service import ReportServiceError
    calls = []
    monkeypatch.setattr(writes, "current", lambda context, account, write=False:
                        calls.append(account) or accounts.load_account(account))
    with pytest.raises(ReportServiceError) as caught:
        writes.execute(object(), {"operation": "send_request", "account": SNS, "body": "b",
                                  "announces": sns.delivery}, via="mcp")
    assert str(caught.value) == "invalid_request"
    # draft_put は鍵を受ける（この先の managed repo の門で止まる＝鍵では断っていない）
    with pytest.raises(ReportServiceError) as caught:
        writes.draft_put(object(), {"operation": "draft_put", "account": SNS, "body": BODY,
                                    "publish_at": "2026-09-09T08:30:00+09:00", "announces": sns.delivery,
                                    "article": "lane-a/arabica"}, "mcp")
    assert str(caught.value) == "managed_repo_required"
    # 指紋に入る（`publish_options` が拾う）
    assert approval.publish_options({"announces": sns.delivery})["announces"] == sns.delivery
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
    import server
    tool = next(t for t in server.SERVER_TOOLS if t["name"] == "thth_draft_put")
    assert {"announces", "article"} <= set(tool["inputSchema"]["properties"])
    send = next(t for t in server.SERVER_TOOLS if t["name"] == "thth_send_request")
    assert "announces" not in send["inputSchema"]["properties"]


# ---------------------------------------------------------------- T-S4-10a deliveries --article

def test_T_S4_10a_deliveriesのarticleに告知とブクマの行(sns, world):  # noqa: F811
    path = sns.manuscript()
    approve(path)
    sns.advance("published", ARTICLE_URL)
    assert throw(Spy(sns)).action == "post"
    post_id = fm_of(path)["post_id"]
    url = ARTICLE_URL
    sent.write(world.state(HATENA), post_id=sent.bookmark_post_id(url), text="c", body_hash="h",
               sent_at="2026-09-09T10:00:00+09:00", kind="bookmark_add", url=url, url_canonical=url,
               target="own", delivery=sns.delivery, article="lane-a/arabica", result="confirmed")
    payload = deliveries_cli.build("lane-a", article="lane-a/arabica")
    assert [(t["account"], t["post_id"], t["announces"]) for t in payload["announcements"]] == [
        (SNS, post_id, sns.delivery)]
    assert [(b["delivery"], b["result"]) for b in payload["bookmarks"]] == [(sns.delivery, "confirmed")]
    assert payload["cannot_say"] == []
    rc, out, err = call(["deliveries", "lane-a", "--article", "lane-a/arabica"])
    assert rc == 0, err
    assert "ブクマ: 付けた（9/9）" in out and f"告知: {SNS}" in out
    # 台帳に写しを作らない
    assert "bookmark" not in deliveries.read(HATENA, sns.delivery)
    # 読めない SNS の記録は cannot_say
    with open(os.path.join(sent.dir_for(sns.state), "broken.json"), "w", encoding="utf-8") as f:
        f.write("{")
    payload = deliveries_cli.build("lane-a", article="lane-a/arabica")
    assert "sent_unreadable" in payload["cannot_say"]
    assert payload["announcements"]                           # 読めた行は出す


# ---------------------------------------------------------------- 見つけたこと 14（10/17 実機）

def _first_stage(path, *, json_out=False):
    from tests.hatena_s1_flow_helpers import first_stage_digest
    if json_out:
        rc, out, err = call(["approve", path, "--json"])
        assert rc == 1, (out, err)
        return json.loads(out)
    return first_stage_digest(path)[1]


@pytest.mark.parametrize("state", ["wanted", "inflight", "unknown"])
def test_見つけたこと14_記事がまだなら承認の1段目はすぐ出ますと言わない(sns, world, monkeypatch, state):  # noqa: F811
    """予定時刻を過ぎた告知の原稿でも、記事がまだなら「記事の公開を待って出ます（いまは <状態>）」と言う。

    柵: `_late_warning` が告知を見ずに「承認するとすぐ出ます」と言うと赤。
    """
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)   # 予定時刻 08:30 の 1.5 時間後（stale ではない）
    if state != "wanted":
        sns.advance(state)
    path = sns.manuscript()
    out = _first_stage(path)
    assert "承認するとすぐ出ます" not in out
    expected = (f"記事 {sns.delivery} の公開を待って出ます（いまは {state}）・"
                "`{article_url}` は公開した URL に置き換わります")
    assert f"告知      : {expected}" in out
    assert "承認してもすぐには出ません" in out
    data = _first_stage(path, json_out=True)
    note = data["files"][0]["announce"]
    assert note == {"delivery": sns.delivery, "state": state, "url": None, "waiting": True, "line": expected}
    assert "承認するとすぐ出ます" not in data["files"][0]["warning"]


def test_見つけたこと14_予定時刻が先でも告知の行は出る_公開済みなら置き換え後のURL(sns, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(jst, "now_jst", lambda: NOW - datetime.timedelta(hours=3))   # 予定時刻の前
    path = sns.manuscript()
    out = _first_stage(path)
    assert f"記事 {sns.delivery} の公開を待って出ます（いまは wanted）" in out
    assert "⚠" not in out   # 予定時刻の前なので警告は無い
    # 記事が出たあと: 置き換え後の URL を見せる。予定時刻を過ぎていれば本当にすぐ出るので「すぐ出ます」のまま。
    sns.advance("published", ARTICLE_URL)
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    out = _first_stage(path)
    assert f"`{{article_url}}` は {ARTICLE_URL} に置き換わります" in out
    assert "承認するとすぐ出ます" in out


def test_見つけたこと14_conflictの記事なら出ないと言う(sns, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    sns.advance("published", ARTICLE_URL)
    sns.advance("conflict")
    out = _first_stage(sns.manuscript())
    assert "承認するとすぐ出ます" not in out
    assert f"記事 {sns.delivery} は conflict です。このままでは出ません" in out


def test_見つけたこと14_告知の行は指紋に入らない_告知の無い原稿は変わらない(sns, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    path = sns.manuscript()
    before = _first_stage(path, json_out=True)["files"][0]["digest"]
    sns.advance("published", ARTICLE_URL)
    assert _first_stage(path, json_out=True)["files"][0]["digest"] == before
    plain = sns.manuscript("plain.md", announces=False, article=None, body="告知でない投稿")
    data = _first_stage(plain, json_out=True)["files"][0]
    assert "announce" not in data and "承認するとすぐ出ます" in data["warning"]
