"""3.19.0: X のコミュニティ（`thth community search`・`send --community`・front-matter `community_id:`）。

**本物の api.x.com は叩かない**——`thth.adapters.x.request` を偽物に差し替えるか、loopback の偽の X
（`tests/helpers/fake_x`）に向ける。確かめること:
- search は `GET /2/communities/search` を 1 回・community.fields の 7 つ・下限 10 で押さえる・
  別の台帳（Community: Read 0.005）で月の予算に入る・1 日の本数には数えない・保存しない。
- X 以外は unsupported_on_platform・範囲外の --limit・予算が無いときは叩かない。
- 投稿の本体に community_id・返信との同時指定は断る・形の違う id は断る。
- send の digest と queue の承認の指紋に入る（値があるときだけ・無ければ今までと同じ）。
- lint・select・bundle が X 以外・形違い・返信との同時指定を断る。
- sent の記録に community_id が残る。断り（403）は publish_refusal の流儀で文面が残る。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import urllib.error

import pytest

from thth import accounts as accounts_mod
from thth import approval
from thth import budget_x as b
from thth import community_cli
from thth import lint as lint_mod
from thth.adapters import base
from thth.adapters import x as x_adapter
from tests.test_v214_x_media import env, wire  # noqa: F401

ACCOUNT = "kanto-x"
TOKEN = "SYNTHETIC-X-TOKEN-0123456789"
COMMUNITY = "1493446837214187523"


def _account(tmp_path, factory, *, name=ACCOUNT, media="x"):
    data = {"access_token": TOKEN, "user_id": "123", "username": "kanto",
            "scopes": ["tweet.read", "tweet.write", "users.read", "offline.access"],
            "scopes_source": "response"}
    token = tmp_path / f"{name}.token"
    token.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(token, 0o600)
    factory(name, media=media, handle="kanto", project="kanto", token=str(token), production=True)
    return name


@pytest.fixture
def x_account(tmp_path, isolated_account_factory):
    _account(tmp_path, isolated_account_factory)
    b.configure("15", by="operator")
    return ACCOUNT


@pytest.fixture
def fake(monkeypatch):
    state = {"calls": [], "routes": {}}

    def request(adapter, method, path, *, query=None, json_body=None, **kwargs):
        state["calls"].append({"method": method, "path": path, "query": dict(query or {}),
                               "body": json_body})
        answer = state["routes"].get((method, path))
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            raise urllib.error.HTTPError(path, 404, "nf", {}, None)
        return 200, answer
    monkeypatch.setattr(x_adapter, "request", request)
    return state


def _communities(n, *, more=False):
    data = [{"id": str(1493446837214187523 + i), "name": f"コーヒー部{i}",
             "description": f"説明{i}\n二行目", "member_count": 1200 + i,
             "join_policy": "Open", "access": "Public", "created_at": "2024-01-02T03:04:05.000Z"}
            for i in range(n)]
    out = {"data": data, "meta": {"result_count": n}}
    if more:
        out["meta"]["next_token"] = "t"
    return out


def run(capsys, *words, account=ACCOUNT, limit=None, as_json=True):
    args = argparse.Namespace(account=account, words=list(words), limit=limit, json=as_json)
    rc = community_cli.cmd_community_search(args)
    out = capsys.readouterr()
    payload = json.loads(out.out) if as_json and out.out.strip() else None
    return rc, payload, out


def community_rows():
    return list(b.read_posts(b.COMMUNITIES)["reservations"].values())


# ----- 探す ---------------------------------------------------------------------

def test_search_calls_once_with_all_fields_and_charges_the_returned_count(x_account, fake, capsys):
    fake["routes"][("GET", "/2/communities/search")] = _communities(3, more=True)
    rc, payload, _ = run(capsys, "コーヒー", "豆", limit=5)
    assert rc == 0
    [call] = fake["calls"]
    assert call["path"] == "/2/communities/search"
    assert call["query"] == {"query": "コーヒー 豆", "max_results": 10,
                             "community.fields": "access,created_at,description,id,join_policy,member_count,name"}
    assert payload["n"] == 3 and payload["storage"] == "display_only"
    row = payload["communities"][0]
    assert row == {"community_id": "1493446837214187523", "name": "コーヒー部0",
                   "description": "説明0 二行目", "member_count": 1200, "join_policy": "Open",
                   "access": "Public", "created_at": "2024-01-02T03:04:05.000Z",
                   "url": "https://x.com/i/communities/1493446837214187523"}
    assert any("続きがあります" in line for line in payload["cannot_say"])
    assert any("参加しているかは分かりません" in line for line in payload["cannot_say"])
    # 別の台帳（Community: Read・押さえは下限 10・確定は返ってきた 3 件）。
    [ledger] = community_rows()
    assert ledger["state"] == "settled" and ledger["limit"] == 10 and ledger["count"] == 3
    assert ledger["usd"] == "0.015"
    cost = payload["x_read_cost"]
    assert cost["communities"] == 3 and cost["usd_estimate"] == "0.015" and cost["requests"] == 1
    assert cost["daily_read_cap"] == "not_counted"
    report = b.report()
    assert report["spent_estimate_usd"] == "0.015"
    assert report["community_read"]["month"]["communities"] == 3
    assert any("コミュニティの検索" in line for line in b.cost_lines(report))
    # 1 日の本数（他人の投稿の読み取り）には数えない。
    assert b.posts_status(ACCOUNT)["today"] == 0
    # 保存しない（runs にも名前・語を書かない）。
    root = accounts_mod.state_dir_for(ACCOUNT)
    for folder, _dirs, files in os.walk(root):
        for name in files:
            with open(os.path.join(folder, name), encoding="utf-8", errors="ignore") as fh:
                body = fh.read()
            assert "コーヒー部0" not in body and "コーヒー 豆" not in body


def test_search_human_output_names_the_send_route(x_account, fake, capsys):
    fake["routes"][("GET", "/2/communities/search")] = _communities(1)
    rc, _payload, out = run(capsys, "coffee", as_json=False)
    assert rc == 0
    assert "1493446837214187523" in out.out and "人数=1200" in out.out and "join_policy=Open" in out.out
    assert "--community <id>" in out.out and "community_id:" in out.out
    assert "1 日の本数には数えない" in out.out


def test_search_zero_is_not_called_absence(x_account, fake, capsys):
    fake["routes"][("GET", "/2/communities/search")] = {"meta": {"result_count": 0}}
    rc, payload, _ = run(capsys, "nothing")
    assert rc == 0 and payload["n"] == 0
    assert any("区別できません" in line for line in payload["cannot_say"])
    [ledger] = community_rows()
    assert ledger["state"] == "settled" and ledger["count"] == 0


def test_search_refuses_other_media(tmp_path, isolated_account_factory, fake, capsys):
    _account(tmp_path, isolated_account_factory, name="kanto-threads", media="threads")
    rc, payload, _ = run(capsys, "coffee", account="kanto-threads")
    assert rc == 2 and payload["error"] == "unsupported_on_platform"
    assert payload["cannot_say"] and fake["calls"] == []


@pytest.mark.parametrize("limit", [0, 26])
def test_search_limit_out_of_range_never_calls(x_account, fake, capsys, limit):
    rc, payload, _ = run(capsys, "coffee", limit=limit)
    assert rc == 2 and payload["error"] == "invalid_request" and fake["calls"] == []


def test_search_limit_above_ten_reserves_that_many(x_account, fake, capsys):
    fake["routes"][("GET", "/2/communities/search")] = _communities(2)
    rc, _payload, _ = run(capsys, "coffee", limit=25)
    assert rc == 0 and fake["calls"][0]["query"]["max_results"] == 25
    assert community_rows()[0]["limit"] == 25


def test_search_without_budget_never_calls(tmp_path, isolated_account_factory, fake, capsys):
    _account(tmp_path, isolated_account_factory)
    rc, payload, _ = run(capsys, "coffee")
    assert rc == 1 and payload["error"] == "budget_exhausted" and fake["calls"] == []


def test_search_refusal_keeps_a_short_reason_and_charges_nothing(x_account, fake, capsys):
    body = b'{"title":"Forbidden","detail":"You are not permitted to access this feature.","status":403}'
    fake["routes"][("GET", "/2/communities/search")] = urllib.error.HTTPError(
        "u", 403, "Forbidden", {}, io.BytesIO(body))
    rc, payload, _ = run(capsys, "coffee")
    assert rc == 1 and payload["error"] == "upstream_refused"
    assert "x_community_search_http_403: x_not_permitted" in payload["message"]
    [ledger] = community_rows()
    assert ledger["state"] == "settled" and ledger["count"] == 0 and ledger["usd"] == "0.000"


def _run_errors():
    root = accounts_mod.state_dir_for(ACCOUNT)
    out = []
    for name in sorted(os.listdir(root)):
        if name.startswith("runs-"):
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                out += [json.loads(line).get("error") for line in fh if line.strip()]
    return out


def test_search_refusal_leaves_only_static_codes_in_runs(x_account, fake, capsys):
    """10/13: 両レーンの失敗が `community_search_failed` だけで原因が追えなかった。"""
    body = b'{"title":"Forbidden","detail":"You are not permitted to access this feature.","status":403}'
    fake["routes"][("GET", "/2/communities/search")] = urllib.error.HTTPError(
        "u", 403, "Forbidden", {}, io.BytesIO(body))
    run(capsys, "coffee")
    assert _run_errors() == ["community_search_failed: x_community_search_http_403 x_not_permitted"]


def test_search_unreadable_response_names_the_exception_and_stays_uncertain(x_account, fake, capsys):
    fake["routes"][("GET", "/2/communities/search")] = TimeoutError("read timed out コーヒー豆")
    rc, payload, _ = run(capsys, "コーヒー豆")
    assert rc == 1 and payload["error"] == "upstream_refused"
    [error] = _run_errors()
    assert error == "community_search_failed: x_read_failed TimeoutError"
    [ledger] = community_rows()
    assert ledger["state"] == "uncertain"


def test_search_parser_is_registered():
    from thth import cli
    args = cli.build_parser().parse_args(["community", "search", ACCOUNT, "a", "b", "--limit", "3", "--json"])
    assert args.func is community_cli.cmd_community_search and args.words == ["a", "b"] and args.limit == 3


# ----- 出す: 投稿の本体 ----------------------------------------------------------

def test_tweet_body_carries_the_community():
    assert x_adapter.tweet_body(base.Post("本文", community_id=COMMUNITY)) == {
        "text": "本文", "community_id": COMMUNITY}
    assert "community_id" not in x_adapter.tweet_body(base.Post("本文"))


def test_tweet_body_refuses_reply_with_community():
    with pytest.raises(x_adapter.AdapterError):
        x_adapter.tweet_body(base.Post("本文", reply_to="1900000000000000000", community_id=COMMUNITY))


def test_adapter_publish_posts_into_the_community(env, wire):
    result = env[1].publish(base.Post("コミュニティへ", community_id=COMMUNITY), dry_run=False)
    assert result.post_id and result.failure == "none"
    assert wire["tweets"] == [{"text": "コミュニティへ", "community_id": COMMUNITY}]


@pytest.mark.parametrize("post", [
    base.Post("x", community_id="12ab"),
    base.Post("x", community_id=COMMUNITY, reply_to="1900000000000000000"),
])
def test_adapter_refuses_bad_community_before_any_request(env, wire, post):
    result = env[1].publish(post, dry_run=False)
    assert result.error == "x_community_id_invalid" and result.failure == "publish_vetoed"
    assert wire["calls"] == []


def test_a_community_refusal_keeps_the_short_x_text(env, wire, monkeypatch):
    original = x_adapter.request
    body = (b'{"type":"https://api.twitter.com/2/problems/not-authorized-for-resource",'
            b'"title":"Forbidden","detail":"You are not a member of this community."}')

    def request(adapter, method, path, **kwargs):
        if path == "/2/tweets":
            raise urllib.error.HTTPError(adapter.base_url + path, 403, "Forbidden", {}, io.BytesIO(body))
        return original(adapter, method, path, **kwargs)
    monkeypatch.setattr(x_adapter, "request", request)
    result = env[1].publish(base.Post("本文", community_id=COMMUNITY), dry_run=False)
    assert result.failure == "publish_definite"
    assert result.error == ("x_publish_http_403: x_unknown_refusal（type=not-authorized-for-resource"
                            " / title=Forbidden / detail=You are not a member of this community.）")


# ----- 出す: thth send --community ----------------------------------------------

def _send(env, monkeypatch, **kw):
    from thth import core
    monkeypatch.setattr(core, "_append_run", lambda *a, **k: None)
    lines = []
    result = core.send_once("alpha", log=lines.append,
                            adapter_factory=lambda cfg, token: env[1], **kw)
    return result, lines


def test_send_digest_includes_the_community_only_when_given(env, wire, monkeypatch):
    plain, _ = _send(env, monkeypatch, text="本文")
    into, lines = _send(env, monkeypatch, text="本文", community_id=COMMUNITY)
    assert plain.exit_code == into.exit_code == 0
    assert plain.digest != into.digest
    assert plain.digest == approval.compute_send_digest(text="本文", account="alpha", reply_to=None, topic=None)
    assert any("コミュニティ: " + COMMUNITY in line for line in lines)
    assert wire["calls"] == []


def test_send_production_posts_into_the_community_and_records_it(env, wire, monkeypatch):
    from thth import sent as sent_mod
    dry, _ = _send(env, monkeypatch, text="本文", community_id=COMMUNITY)
    result, _ = _send(env, monkeypatch, text="本文", community_id=COMMUNITY,
                      production_flag=True, confirm=dry.digest)
    assert result.exit_code == 0 and result.post_id
    assert wire["tweets"] == [{"text": "本文", "community_id": COMMUNITY}]
    record = json.load(open(sent_mod.path_for(accounts_mod.state_dir_for("alpha"), result.post_id),
                            encoding="utf-8"))
    assert record["community_id"] == COMMUNITY


def test_send_confirm_without_community_does_not_post_into_it(env, wire, monkeypatch):
    plain, _ = _send(env, monkeypatch, text="本文")
    result, _ = _send(env, monkeypatch, text="本文", community_id=COMMUNITY,
                      production_flag=True, confirm=plain.digest)
    assert result.exit_code == 1 and wire["calls"] == []


@pytest.mark.parametrize("kw,needle", [
    ({"community_id": "12ab"}, "19 桁までの数字"),
    ({"community_id": COMMUNITY, "reply_to": "1900000000000000000"}, "返信"),
])
def test_send_refuses_bad_community_even_on_dry_run(env, wire, monkeypatch, kw, needle):
    result, lines = _send(env, monkeypatch, text="本文", **kw)
    assert result.exit_code == 2 and result.error == "community_id_invalid" and result.digest is None
    assert any(needle in line for line in lines) and wire["calls"] == []


def test_send_refuses_community_on_other_media(env, wire, monkeypatch):
    env[0]["media"] = "threads"
    result, lines = _send(env, monkeypatch, text="本文", community_id=COMMUNITY)
    assert result.exit_code == 2 and result.error == "community_id_invalid"
    assert any("X だけ" in line for line in lines)


def test_send_cli_flag_reaches_core(monkeypatch):
    from thth import cli, core, read_coordination
    seen = {}

    def fake_send_once(account, **kw):
        seen.update(kw)
        return core.ThrowResult(exit_code=0, mode="rehearsal", action="skip", message="")
    monkeypatch.setattr(core, "send_once", fake_send_once)
    monkeypatch.setattr(read_coordination, "invoke", lambda args, name, fn=None: fn() if fn else args.func(args))
    assert cli.main(["send", "alpha", "--text", "本文", "--community", COMMUNITY]) == 0
    assert seen["community_id"] == COMMUNITY


# ----- queue の front-matter（承認の指紋・lint・select・束） ----------------------

def _sha(fm):
    return approval.compute_approved_sha(
        section="本文", account="alpha", reply_to=approval.reply_to_for_fingerprint(fm),
        topic=None, publish_at="2026-10-14T08:00:00+09:00", **approval.publish_options(fm))


def test_fingerprint_is_unchanged_without_community_and_changes_with_it():
    from thth import fingerprint_golden
    assert fingerprint_golden.drift() == []
    assert _sha({}) == _sha({"community_id": ""})
    assert _sha({}) != _sha({"community_id": COMMUNITY})
    assert _sha({"community_id": COMMUNITY}) != _sha({"community_id": "1493446837214187524"})
    parts = approval.compute_approved_components(
        section="本文", account="alpha", reply_to=None, topic=None,
        publish_at="2026-10-14T08:00:00+09:00", community_id=COMMUNITY)
    assert parts["community_id"] == COMMUNITY and "community_id" in approval.COMPARED_KEYS


@pytest.mark.parametrize("fm,cfg,expected", [
    ({"community_id": COMMUNITY}, {"media": "x"}, []),
    ({}, {"media": "threads"}, []),
    ({"community_id": COMMUNITY}, {"media": "threads"}, ["X だけ"]),
    ({"community_id": COMMUNITY}, None, ["X だけ"]),
    ({"community_id": "abc"}, {"media": "x"}, ["19 桁までの数字"]),
    ({"community_id": "12345678901234567890"}, {"media": "x"}, ["19 桁までの数字"]),
    ({"community_id": COMMUNITY, "reply_to": "1900000000000000000"}, {"media": "x"}, ["返信"]),
    ({"community_id": COMMUNITY, "reply_to_file": "q.md"}, {"media": "x"}, ["返信"]),
])
def test_lint_community_rules(fm, cfg, expected):
    problems = lint_mod.community_id_errors(fm, cfg)
    assert len(problems) == len(expected)
    for problem, needle in zip(problems, expected):
        assert problem.startswith("community_id: ") and needle in problem
    if problems:
        assert lint_mod.reason_code(problems) == "invalid_publish_option"
        assert problems == [p for p in lint_mod.publish_option_errors(fm, cfg) if p.startswith("community_id")]


def test_bundle_refuses_community():
    from thth import bundle as bundle_mod
    text = ("---\nthth: 2\naccount: alpha\npublish_at: 2026-10-14T08:00:00+09:00\n"
            "continue_until: 2026-10-14T12:00:00+09:00\ncommunity_id: " + COMMUNITY + "\n---\n"
            "## x\n一段目。\n\n---\n\n二段目。\n")
    parsed = bundle_mod.parse_text(text, "b.md")
    assert any(p.startswith("community_id: 連投") for p in bundle_mod.check(parsed, account_cfg={"media": "x"}))


def _queue_file(isolated_account_factory, tmp_path, *, media, community, approve=True):
    import datetime
    from thth import jst, queuefile, select
    info = isolated_account_factory("alpha", media=media, production=True)
    queue = info["queue_dir"]
    os.makedirs(queue, exist_ok=True)
    publish_at = (jst.now_jst() - datetime.timedelta(minutes=5)).replace(microsecond=0).isoformat()
    cfg = accounts_mod.load_account("alpha")
    fm = {"community_id": community} if community else {}
    section = "コミュニティへの本文"
    sha = approval.compute_approved_sha(
        section=approval.effective_section(section, cfg, None), account="alpha", reply_to=None,
        topic=None, publish_at=publish_at, **approval.publish_options(fm))
    lines = ["---", "thth: 1", "account: alpha", f"publish_at: {publish_at}"]
    if community:
        lines.append(f"community_id: {community}")
    if approve:
        lines += ["status: approved", f"approved_sha: {sha}"]
    lines += ["---", f"## {media}", section, ""]
    path = os.path.join(queue, "c.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path, cfg, select, queuefile


def test_select_rejects_community_on_a_non_x_account(isolated_account_factory, tmp_path):
    path, cfg, select, queuefile = _queue_file(isolated_account_factory, tmp_path, media="threads",
                                       community=COMMUNITY)
    from thth import jst
    result = select.select_one([queuefile.parse(path)], account_name="alpha", account_cfg=cfg, now=jst.now_jst(),
                               last_post_at=None, recent_texts=set())
    assert [r.reason for r in result.rejections if r.file == path] == ["community_id_invalid"]


def test_lint_file_refuses_community_on_a_non_x_account(isolated_account_factory, tmp_path):
    path, *_ = _queue_file(isolated_account_factory, tmp_path, media="threads",
                           community=COMMUNITY, approve=False)
    problems = [p for p in lint_mod.lint_file(path) if not lint_mod.is_warning(p)]
    assert any(p.startswith("community_id: ") for p in problems)
