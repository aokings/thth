"""3.17.0: X の非公開リスト（`thth list create|add|remove|show`）。

**本物の api.x.com は叩かない**——`thth.adapters.x.request` を偽物に差し替えて、要求の形と回数・
予算の台帳を数える。確かめること:
- create は常に `private: true`・名前と説明の長さ・作る 2 単位（0.010 USD）を月の予算に。
- add／remove は 1 人 1 回・10 人を超えたら SNS に送らない・@handle は users/by/username（0.010）。
- scope（list.read・list.write）が無いトークンは叩かずに scope_missing。
- X 以外は unsupported_on_platform・production の門・--by。
- show は Posts: Read の台帳（1 日の本数にも）・where と同じ行の形・本文を保存しない。
- 認可の URL に list.read・list.write（世代＝SCOPES は変えない）。
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
from decimal import Decimal

import pytest

from thth import accounts as accounts_mod
from thth import budget_x as b
from thth import list_cli
from thth.adapters import auth_x
from thth.adapters import x as x_adapter

ACCOUNT = "kanto-x"
TOKEN = "SYNTHETIC-X-TOKEN-0123456789"
FULL_SCOPES = ["tweet.read", "tweet.write", "users.read", "offline.access", "media.write",
               "list.read", "list.write"]


def _account(tmp_path, factory, *, name=ACCOUNT, media="x", scopes=FULL_SCOPES, production=True):
    data = {"access_token": TOKEN, "user_id": "123", "username": "kanto"}
    if scopes is not None:
        data.update(scopes=list(scopes), scopes_source="response")
    token = tmp_path / f"{name}.token"
    token.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(token, 0o600)
    factory(name, media=media, handle="kanto", project="kanto", token=str(token),
            production=production)
    return name


@pytest.fixture
def x_account(tmp_path, isolated_account_factory):
    _account(tmp_path, isolated_account_factory)
    b.configure("15", by="operator")
    return ACCOUNT


@pytest.fixture
def fake(monkeypatch):
    """`x.request` の偽物。`routes[(method, path)]` に応答（dict か例外か関数）。"""
    state = {"calls": [], "routes": {}}

    def request(adapter, method, path, *, query=None, json_body=None, **kwargs):
        state["calls"].append({"method": method, "path": path, "query": dict(query or {}),
                               "body": json_body})
        answer = state["routes"].get((method, path))
        if callable(answer):
            answer = answer(query or {})
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            raise urllib.error.HTTPError(path, 404, "nf", {}, None)
        return 200, answer
    monkeypatch.setattr(x_adapter, "request", request)
    return state


def run(capsys, op, **kw):
    base = dict(list_op=op, account=ACCOUNT, json=True, by="masaru", wait=0)
    base.update(kw)
    rc = list_cli.cmd_list(argparse.Namespace(**base))
    out = capsys.readouterr()
    payload = json.loads(out.out) if base["json"] and out.out.strip() else None
    return rc, payload, out


def list_rows():
    return list(b.read_posts(b.LISTS)["reservations"].values())


# ----- 認可 -------------------------------------------------------------------

def test_optional_scopes_carry_list_scopes_without_changing_the_generation():
    assert {"list.read", "list.write", "like.write"} <= set(auth_x.OPTIONAL_SCOPES)
    assert "list.read" not in auth_x.SCOPES and "list.write" not in auth_x.SCOPES


# ----- create -----------------------------------------------------------------

def test_create_is_always_private_and_charges_two_units(x_account, fake, capsys):
    fake["routes"][("POST", "/2/lists")] = {"data": {"id": "1441162269824405510", "name": "関東の人"}}
    rc, payload, _ = run(capsys, "create", name="関東の人", description="選んだ人だけ")
    assert rc == 0 and payload["ok"] and payload["private"] is True
    assert payload["list_id"] == "1441162269824405510"
    [call] = fake["calls"]
    assert call["method"] == "POST" and call["path"] == "/2/lists"
    assert call["body"] == {"name": "関東の人", "description": "選んだ人だけ", "private": True}
    [row] = list_rows()
    assert row["state"] == "settled" and row["count"] == 2 and row["usd"] == "0.010"
    report = b.report()
    assert report["spent_estimate_usd"] == "0.010"
    assert report["list_write"]["month"]["requests"] == 1
    assert payload["x_cost_estimate"]["usd"] == "0.010"
    assert any("非公開リストの書き込み" in line for line in b.cost_lines(report))


def test_create_has_no_public_flag():
    parser = argparse.ArgumentParser()
    list_cli.register(parser.add_subparsers())
    with pytest.raises(SystemExit):
        parser.parse_args(["list", "create", ACCOUNT, "n", "--public"])
    with pytest.raises(SystemExit):
        parser.parse_args(["list", "create", ACCOUNT, "n", "--private", "false"])


@pytest.mark.parametrize("name,description", [("", None), ("あ" * 26, None), ("ok", "い" * 101),
                                              ("a\nb", None)])
def test_create_refuses_bad_lengths_before_the_request(x_account, fake, capsys, name, description):
    rc, payload, _ = run(capsys, "create", name=name, description=description)
    assert rc == 2 and payload["error"] == "invalid_request"
    assert fake["calls"] == [] and list_rows() == []


def test_create_http_refusal_settles_zero(x_account, fake, capsys):
    fake["routes"][("POST", "/2/lists")] = urllib.error.HTTPError("/2/lists", 400, "bad", {}, None)
    rc, payload, _ = run(capsys, "create", name="n", description=None)
    assert rc == 1 and payload["ok"] is False
    [row] = list_rows()
    assert row["state"] == "settled" and row["count"] == 0
    assert Decimal(b.report()["spent_estimate_usd"]) == 0


def test_create_lost_response_is_uncertain_and_not_retried(x_account, fake, capsys):
    fake["routes"][("POST", "/2/lists")] = urllib.error.URLError("boom " + TOKEN)
    rc, payload, out = run(capsys, "create", name="n", description=None)
    assert rc == 1 and TOKEN not in out.out + out.err
    assert len(fake["calls"]) == 1
    [row] = list_rows()
    assert row["state"] == "uncertain" and row["usd"] == "0.010"


def test_budget_exhausted_refuses_before_the_request(x_account, fake, capsys):
    b.configure("0.005", by="operator")
    rc, payload, _ = run(capsys, "create", name="n", description=None)
    assert rc == 1 and payload["error"] == "budget_exhausted"
    assert fake["calls"] == []


# ----- add / remove -----------------------------------------------------------

def test_add_ids_and_handles_one_request_each(x_account, fake, capsys):
    fake["routes"][("GET", "/2/users/by/username/alice")] = {"data": {"id": "42", "username": "alice"}}
    fake["routes"][("POST", "/2/lists/99/members")] = {"data": {"is_member": True}}
    rc, payload, _ = run(capsys, "add", list_id="99", users=["7", "@alice", "7"])
    assert rc == 0, payload
    assert [row["user_id"] for row in payload["done"]] == ["7", "42"]
    posts = [c for c in fake["calls"] if c["method"] == "POST"]
    assert [c["body"] for c in posts] == [{"user_id": "7"}, {"user_id": "42"}]
    # 費用: List: Manage 2 回（0.010）＋ handle の id 引き 1 回（0.010・本人確認の台帳）。
    assert payload["x_cost_estimate"]["usd"] == "0.020"
    assert [row["usd"] for row in list_rows()] == ["0.005", "0.005"]
    assert len([r for r in b.read()["reservations"].values() if r["state"] == "settled"]) == 1
    assert b.report()["spent_estimate_usd"] == "0.020"


def test_remove_uses_delete(x_account, fake, capsys):
    fake["routes"][("DELETE", "/2/lists/99/members/7")] = {"data": {"is_member": False}}
    rc, payload, _ = run(capsys, "remove", list_id="99", users=["7"])
    assert rc == 0 and payload["operation"] == "remove"
    assert [(c["method"], c["path"]) for c in fake["calls"]] == [("DELETE", "/2/lists/99/members/7")]


def test_more_than_ten_people_is_refused_before_anything(x_account, fake, capsys):
    rc, payload, _ = run(capsys, "add", list_id="99", users=[str(n) for n in range(1, 12)])
    assert rc == 2 and payload["error"] == "too_many_members"
    assert fake["calls"] == [] and list_rows() == []


@pytest.mark.parametrize("user", ["alice", "@bad-name", "@" + "a" * 16, "12x"])
def test_bad_user_spec_is_refused(x_account, fake, capsys, user):
    rc, payload, _ = run(capsys, "add", list_id="99", users=[user])
    assert rc == 2 and payload["error"] == "invalid_user" and fake["calls"] == []


def test_stops_at_the_first_failure_and_says_what_was_done(x_account, fake, capsys):
    def answer(query):
        raise urllib.error.HTTPError("/", 429, "rate", {"retry-after": "60"}, None)
    fake["routes"][("POST", "/2/lists/99/members")] = {"data": {"is_member": True}}
    fake["routes"][("DELETE", "/2/lists/99/members/8")] = answer
    fake["routes"][("DELETE", "/2/lists/99/members/7")] = {"data": {"is_member": False}}
    rc, payload, _ = run(capsys, "remove", list_id="99", users=["7", "8", "9"])
    assert rc == 1 and payload["ok"] is False
    assert [row["user_id"] for row in payload["done"]] == ["7"]
    assert payload["not_done"] == ["8", "9"]
    assert len(fake["calls"]) == 2


# ----- 門 -----------------------------------------------------------------------

def test_missing_list_scope_is_refused_before_the_request(tmp_path, isolated_account_factory, fake, capsys):
    _account(tmp_path, isolated_account_factory,
             scopes=["tweet.read", "tweet.write", "users.read", "offline.access", "media.write"])
    b.configure("15", by="operator")
    for op, kw in (("create", dict(name="n", description=None)),
                   ("add", dict(list_id="99", users=["7"])),
                   ("show", dict(list_id="99", limit=None))):
        rc, payload, _ = run(capsys, op, **kw)
        assert rc == 2 and payload["error"] == "scope_missing", (op, payload)
        assert f"thth auth {ACCOUNT}" in payload["message"]
    assert fake["calls"] == []


def test_non_x_account_is_refused(tmp_path, isolated_account_factory, fake, capsys):
    _account(tmp_path, isolated_account_factory, name="masto", media="mastodon")
    rc, payload, _ = run(capsys, "show", account="masto", list_id="99", limit=None)
    assert rc == 2 and payload["error"] == "unsupported_on_platform" and payload["cannot_say"]
    rc, payload, _ = run(capsys, "create", account="masto", name="n", description=None)
    assert rc == 2 and payload["error"] == "unsupported_on_platform"
    assert fake["calls"] == []


def test_production_gate_and_by(tmp_path, isolated_account_factory, fake, capsys, monkeypatch):
    _account(tmp_path, isolated_account_factory, production=False)
    rc, payload, _ = run(capsys, "create", name="n", description=None)
    assert rc == 1 and payload["error"] == "production_disabled"
    monkeypatch.delenv("THTH_ACTOR", raising=False)
    rc, payload, _ = run(capsys, "create", name="n", description=None, by=None)
    assert rc == 1 and payload["error"] == "by_required"
    assert fake["calls"] == []


# ----- show ---------------------------------------------------------------------

def _tweets(n):
    return {"data": [{"id": str(1000 + i), "text": f"本文{i} とても長い本文", "author_id": "u1",
                      "created_at": "2026-09-09T00:30:00.000Z", "conversation_id": str(1000 + i)}
                     for i in range(n)],
            "includes": {"users": [{"id": "u1", "username": "alice"}]},
            "meta": {"result_count": n, "next_token": "abc"}}


def test_show_reads_through_the_posts_ledger_and_returns_where_rows(x_account, fake, capsys):
    fake["routes"][("GET", "/2/lists/99/tweets")] = _tweets(3)
    rc, payload, _ = run(capsys, "show", list_id="99", limit=5)
    assert rc == 0
    [call] = fake["calls"]
    assert call["query"]["max_results"] == 5 and call["query"]["expansions"] == "author_id"
    assert payload["n"] == 3 and payload["storage"] == "display_only"
    post = payload["posts"][0]
    assert {"post_id", "permalink", "timestamp", "author", "author_key", "preview", "replied"} <= set(post)
    assert "text" not in post and post["author"] == "alice"
    assert payload["x_read_cost"]["posts"] == 3 and payload["x_read_cost"]["today"] == 3
    assert any("続きがあります" in line for line in payload["cannot_say"])
    assert b.posts_status(ACCOUNT)["today"] == 3
    # 本文はどこにも保存しない（runs にも書かない）。
    root = accounts_mod.state_dir_for(ACCOUNT)
    for folder, _dirs, files in os.walk(root):
        for name in files:
            with open(os.path.join(folder, name), encoding="utf-8", errors="ignore") as fh:
                assert "本文0" not in fh.read()


def test_show_respects_the_daily_cap(x_account, fake, capsys):
    from thth import cli
    assert cli.main(["account", "set", ACCOUNT, "x_daily_reads", "4", "--by", "operator"]) == 0
    capsys.readouterr()
    fake["routes"][("GET", "/2/lists/99/tweets")] = _tweets(3)
    rc, payload, _ = run(capsys, "show", list_id="99", limit=5)
    assert rc == 1 and payload["error"] == "x_daily_read_cap"
    assert fake["calls"] == []


@pytest.mark.parametrize("limit", [0, 26])
def test_show_limit_out_of_range(x_account, fake, capsys, limit):
    rc, payload, _ = run(capsys, "show", list_id="99", limit=limit)
    assert rc == 2 and fake["calls"] == []


def test_show_human_output(x_account, fake, capsys):
    fake["routes"][("GET", "/2/lists/99/tweets")] = _tweets(1)
    rc, _payload, out = run(capsys, "show", list_id="99", limit=None, json=False)
    assert rc == 0 and "@alice" in out.out and "X の読み取り" in out.out
    assert fake["calls"][0]["query"]["max_results"] == 10


# ----- 2 つ目の X アカウント（kopicha-x も使う・masaru 10/12） ---------------------

def test_second_x_account_uses_the_same_commands_and_its_own_daily_count(
        tmp_path, isolated_account_factory, fake, capsys):
    _account(tmp_path, isolated_account_factory)
    _account(tmp_path, isolated_account_factory, name="kopicha-x")
    b.configure("15", by="operator")
    fake["routes"][("POST", "/2/lists")] = {"data": {"id": "555", "name": "こぴちゃ"}}
    fake["routes"][("POST", "/2/lists/555/members")] = {"data": {"is_member": True}}
    fake["routes"][("GET", "/2/lists/555/tweets")] = _tweets(2)
    rc, payload, _ = run(capsys, "create", account="kopicha-x", name="こぴちゃ", description=None)
    assert rc == 0 and payload["account"] == "kopicha-x" and payload["private"] is True
    rc, payload, _ = run(capsys, "add", account="kopicha-x", list_id="555", users=["7"])
    assert rc == 0 and payload["done"] == [{"user": "7", "user_id": "7"}]
    rc, payload, _ = run(capsys, "show", account="kopicha-x", list_id="555", limit=5)
    assert rc == 0 and payload["account"] == "kopicha-x" and payload["n"] == 2
    assert {row["account"] for row in list_rows()} == {"kopicha-x"}
    # 1 日の本数は account ごと・月の予算は全 X で分け合う。
    assert b.posts_status("kopicha-x")["today"] == 2 and b.posts_status(ACCOUNT)["today"] == 0
    assert b.report()["spent_estimate_usd"] == "0.025"
