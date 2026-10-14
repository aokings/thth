"""3.19.2: X がアカウント側の断り（x_not_permitted）を返したら、自動の投稿を止める。

10/14 08:33 から kopicha-x の予約投稿が `POST /2/tweets` で 403 を返し続け、timer が
10 分ごとに同じ承認済み原稿を投げ直して 61 回断られた。ここで確かめること:

- 1 回断られたら `state/<account>/publish_hold/held.json` に印を置き、次の run は
  X に何も投げない（runs にも積もらない）。死活通知の理由は `x_not_permitted`、
  次の一手は `fix_account_then_resume`。
- 原稿ごとの断り（x_reply_not_engaged・x_duplicate_content）と、ほかの媒体は止めない。
- 手の `thth send` は止まっている間 dry-run でも断る。
- `thth account resume <口座> --by <名前>` で戻る（`--by` なしは断る）。
- 印が壊れていたら止まっている側に倒す。
- board・morning・`thth account status` に出る。
"""
from __future__ import annotations

import io
import json
import os
import urllib.error

import pytest

from thth import accounts as accounts_mod
from thth import core, healthcheck, publish_hold, runs
from thth.adapters import x as x_adapter
from tests.conftest import write_queue_file
from tests.test_fake_api import NOW
from tests.test_v214_x_media import set_post_budget

NOT_PERMITTED = (b'{"title":"Forbidden","type":"about:blank","status":403,'
                 b'"detail":"You are not permitted to perform this action."}')
NOT_ENGAGED = (b'{"title":"Forbidden","type":"about:blank","status":403,"detail":'
               b'"Reply to this conversation is not allowed because you have not been mentioned '
               b'or otherwise engaged by the author of the post you are replying to."}')


@pytest.fixture
def x_account(isolated_account_factory, monkeypatch):
    account = isolated_account_factory(production=True, media="x", handle="demo")
    write_queue_file(account["queue_dir"], "a.md", body="## x\n\n本文です。\n", media="x")
    set_post_budget(10)
    return account


@pytest.fixture
def wire(monkeypatch):
    """`POST /2/tweets` を数え、`state["body"]` の 403 を返す（X には届かない）。"""
    state = {"tweets": 0, "body": NOT_PERMITTED}

    def request(adapter, method, path, **kwargs):
        if path == "/2/tweets":
            state["tweets"] += 1
            if state["body"] is None:
                return 201, {"data": {"id": str(1900000000000000000 + state["tweets"]), "text": "x"}}
            raise urllib.error.HTTPError(adapter.base_url + path, 403, "Forbidden", {},
                                         io.BytesIO(state["body"]))
        raise AssertionError("unexpected request " + path)
    monkeypatch.setattr(x_adapter, "request", request)
    return state


def factory(name):
    def make(cfg, token):
        adapter = x_adapter.XAdapter(base_url="https://api.x.invalid", access_token="t" * 30,
                                     user_id="123", username="demo")
        adapter.auth_account = name
        return adapter
    return make


def throw(account):
    lines = []
    result = core.throw_once(account["name"], production_flag=True,
                             adapter_factory=factory(account["name"]), now=NOW, log=lines.append)
    return result, lines


def state_of(account):
    return accounts_mod.state_dir_for(account["name"])


# ----- 断りの符丁 ------------------------------------------------------------------

@pytest.mark.parametrize("error,code", [
    ("x_publish_http_403: x_not_permitted", "x_not_permitted"),
    ("media_publishing_http_403: x_not_permitted", "x_not_permitted"),
    ("x_publish_http_403: x_reply_not_engaged", None),
    ("x_publish_http_403: x_duplicate_content", None),
    ("x_publish_http_403", None),
    ("provider_rate_limited", None),
    ("x_not_permitted", None),
    (None, None),
])
def test_refusal_code_reads_only_the_account_refusal_after_a_4xx(error, code):
    assert publish_hold.refusal_code(error) == code


def test_other_media_never_hold(x_account):
    cfg = {"media": "threads"}
    assert publish_hold.note_refusal(x_account["name"], cfg, "x_publish_http_403: x_not_permitted") is None
    assert publish_hold.held(x_account["name"]) is None


# ----- timer の run ----------------------------------------------------------------

def test_first_refusal_holds_and_the_next_run_does_not_post(x_account, wire):
    first, lines = throw(x_account)
    assert first.action == "post" and first.exit_code == 1
    assert first.error == "x_publish_http_403: x_not_permitted"
    assert wire["tweets"] == 1
    row = publish_hold.held(x_account["name"])
    assert row["reason"] == "x_not_permitted" and row["file"] == "a.md" and row["held_at"]
    assert any("自動の投稿を止めています" in line for line in lines)
    rows_before = len(runs.read_runs(state_of(x_account)))

    second, lines = throw(x_account)
    assert wire["tweets"] == 1  # 2 回目は X に何も投げない
    assert second.action == "publish_held" and second.exit_code == 2
    assert second.error == "x_not_permitted" and second.file == "a.md"
    assert len(runs.read_runs(state_of(x_account))) == rows_before  # runs に積もらない
    assert any("thth account resume" in line for line in lines)

    for result in (first, second):
        diag = healthcheck.diagnostic(x_account["name"], "fail", state_dir=state_of(x_account),
                                      result=result)
        assert diag.reason_code == "x_not_permitted"
        assert diag.next_action_code == "fix_account_then_resume"
        assert "thth account resume" in diag.next_action
    assert healthcheck.has_blocking_state(x_account["name"], state_of(x_account)) is True
    # 運用通知は同じ原稿の 1 つの事象として束ねる（止めたあとの run で 2 通目を出さない）。
    from thth import incident
    cfg = accounts_mod.load_account(x_account["name"])
    assert incident.source(cfg, state_of(x_account), first) == "docs/sns/queue/a.md"
    assert incident.source(cfg, state_of(x_account), second) == "docs/sns/queue/a.md"


def test_rehearsal_still_shows_what_would_be_posted(x_account, wire):
    throw(x_account)
    lines = []
    result = core.throw_once(x_account["name"], production_flag=False,
                             adapter_factory=factory(x_account["name"]), now=NOW, log=lines.append)
    assert result.mode == "rehearsal" and result.action == "skip"
    assert any("自動の投稿を止めています" in line for line in lines)
    assert wire["tweets"] == 1


@pytest.mark.parametrize("body", [NOT_ENGAGED, b'{"detail":"duplicate content"}'])
def test_per_draft_refusals_do_not_hold(x_account, wire, body):
    wire["body"] = body
    first, _ = throw(x_account)
    assert first.action == "post" and "x_not_permitted" not in first.error
    assert publish_hold.held(x_account["name"]) is None
    throw(x_account)
    assert wire["tweets"] == 2  # 原稿ごとの断りは今までどおり次の run がまた試す


def test_unreadable_hold_is_held(x_account, wire):
    folder = os.path.join(state_of(x_account), "publish_hold")
    os.makedirs(folder, mode=0o700)
    with open(os.path.join(folder, publish_hold.HOLD_FILE), "w", encoding="utf-8") as f:
        f.write("{broken")
    os.chmod(os.path.join(folder, publish_hold.HOLD_FILE), 0o600)
    result, _ = throw(x_account)
    assert result.action == "publish_held" and result.error == "publish_hold_unreadable"
    assert wire["tweets"] == 0
    diag = healthcheck.diagnostic(x_account["name"], "fail", state_dir=state_of(x_account), result=result)
    assert diag.reason_code == "publish_hold_unreadable"
    assert diag.next_action_code == "fix_account_then_resume"


# ----- 再開 ------------------------------------------------------------------------

def test_resume_needs_by_and_then_the_run_posts_again(x_account, wire, capsys):
    from thth import cli
    throw(x_account)
    assert cli.main(["account", "resume", x_account["name"]]) == 2
    assert publish_hold.held(x_account["name"]) is not None
    assert cli.main(["account", "resume", x_account["name"], "--by", "masaru", "--json"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out == {"account": x_account["name"], "resumed": False, "publish_resumed": True}
    assert publish_hold.held(x_account["name"]) is None

    wire["body"] = None  # X の画面で直った
    result, _ = throw(x_account)
    assert result.action == "post" and result.exit_code == 0 and result.post_id
    assert wire["tweets"] == 2


def test_resume_while_still_refused_stops_again_after_one_request(x_account, wire):
    from thth import cli
    throw(x_account)
    assert cli.main(["account", "resume", x_account["name"], "--by", "masaru"]) == 0
    throw(x_account)
    throw(x_account)
    assert wire["tweets"] == 2  # 再開後に 1 回だけ試し、また止まる
    assert publish_hold.held(x_account["name"])["reason"] == "x_not_permitted"


# ----- 手の thth send -------------------------------------------------------------

def test_send_is_refused_while_held_even_on_dry_run(x_account, wire):
    throw(x_account)
    lines = []
    result = core.send_once(x_account["name"], text="手で出す", log=lines.append,
                            adapter_factory=factory(x_account["name"]))
    assert result.action == "publish_held" and result.exit_code == 2 and result.digest is None
    assert any("thth account resume" in line for line in lines)
    assert wire["tweets"] == 1


def test_send_refusal_also_holds(x_account, wire):
    dry = core.send_once(x_account["name"], text="手で出す", adapter_factory=factory(x_account["name"]))
    result = core.send_once(x_account["name"], text="手で出す", production_flag=True, confirm=dry.digest,
                            adapter_factory=factory(x_account["name"]))
    assert result.action == "post" and result.exit_code == 1
    assert publish_hold.held(x_account["name"])["via"] == "send"


# ----- 見える所 -------------------------------------------------------------------

def test_board_morning_and_status_say_it(x_account, wire, capsys):
    from thth import account_settings, morning, report
    from types import SimpleNamespace
    throw(x_account)
    rows = {row["account"]: row for row in report.board_summary(now=NOW)["accounts"]}
    hold = rows[x_account["name"]]["publish_hold"]
    assert hold["reason"] == "x_not_permitted" and "thth account resume" in hold["message"]

    value = morning._publish_hold_row(x_account["name"])
    assert value["reason"] == "x_not_permitted"
    assert value["next_action_code"] == "fix_account_then_resume"

    from thth import cli
    capsys.readouterr()
    assert cli.main(["board"]) == 0
    assert "自動の投稿を止めています" in capsys.readouterr().out
    cli.main(["morning", x_account["name"], "--no-world", "--no-mark"])
    assert "自動の投稿を止めています: x_not_permitted" in capsys.readouterr().out
    assert account_settings.cmd_status(SimpleNamespace(name=x_account["name"], rest=None, json=False)) == 0
    out = capsys.readouterr().out
    assert "自動の投稿を止めています" in out and "動いています" not in out
