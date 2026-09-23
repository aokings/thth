"""3.3.0 A1「出すものが無い」と「出せるはずのものが出られない」を分ける（held）。

実測（設計 A1）: 承認済み 46 本が approval_stale で 2 日出なかった間、run は
「出すものが無い」で成功扱いになり、死活通知も運用通知も黙った。ここを `held` に
する。**通知は増えたときだけ・0 に戻ったら recovered**。時刻前と返信待ちは数えない。
通知の失敗で投稿の rc を変えない。
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import make_queue_text, parse_verified, run_git, write_queue_file
from thth import accounts, cli, healthcheck, incident, jst
from thth import select as select_mod

NOW = datetime.datetime(2026, 9, 9, 10, 0, 0, tzinfo=jst.JST)
CFG = {"media": "threads", "quiet_hours": None, "min_interval_hours": 0,
       "stale_days": 7, "hashtags": False}
STALE = {"approved_sha": "deadbeef"}


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self, _limit):
        return b"OK"


def _write(dir_path, name, text):
    path = os.path.join(dir_path, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _select(files):
    return select_mod.select_one(files, account_name="nigamilab-threads", account_cfg=CFG,
                                 now=NOW, last_post_at=None, recent_texts=set())


# ------------------------------------------------------------------ 単体

def test_時刻を過ぎた承認済みのapproval_staleはdueのheld(tmp_path):
    past = parse_verified(_write(str(tmp_path), "past.md", make_queue_text(
        {**STALE, "publish_at": "2026-09-09T08:00:00+09:00"})))
    future = parse_verified(_write(str(tmp_path), "future.md", make_queue_text(
        {**STALE, "publish_at": "2026-09-10T08:00:00+09:00"})))
    files = [past, future]
    items = select_mod.held_items(_select(files), files, NOW)
    by_file = {row["file"]: row for row in items}
    assert by_file["past.md"]["due"] is True
    assert by_file["past.md"]["category"] == "approval_stale"
    assert by_file["past.md"]["elapsed_hours"] == 2.0
    # 時刻前も名前は返す（board と morning に出す）が due ではない。
    assert by_file["future.md"]["due"] is False
    assert select_mod.held_reason_code(items) == "approved_but_held: approval_stale 1"


def test_返信待ちはheldに数えない(tmp_path):
    q = parse_verified(_write(str(tmp_path), "q.md", make_queue_text(
        {"publish_at": "2026-09-10T08:00:00+09:00"}, body="## threads\n\n問い。\n")))
    a = parse_verified(_write(str(tmp_path), "a.md", make_queue_text(
        {"reply_to_file": "q.md"}, body="## threads\n\n答え。\n")))
    files = [q, a]
    result = _select(files)
    assert a.path in result.needs_review  # 要確認には出る（3.2.0 のまま）
    assert select_mod.held_items(result, files, NOW) == []
    assert select_mod.held_reason_code([]) is None


def test_区分が混ざれば決まった順に本数を並べる():
    items = [{"due": True, "category": "stale"}, {"due": True, "category": "approval_stale"},
             {"due": True, "category": "approval_stale"}, {"due": False, "category": "invalid"}]
    code = select_mod.held_reason_code(items)
    assert code == "approved_but_held: approval_stale 2, stale 1"
    assert healthcheck.is_held_code(code) and healthcheck.held_total(code) == 3
    assert "[approved_but_held]" in healthcheck.reason_text(code)
    assert healthcheck.next_action_for(code) == "reapprove_or_fix_held"


@pytest.mark.parametrize("code", [
    "approved_but_held: approval_stale 0", "approved_but_held: 本文 3",
    "approved_but_held: approval_stale 1 /home/x", "approved_but_held:",
])
def test_形の違うheldの理由は通さない(code):
    assert not healthcheck.is_held_code(code)
    assert not healthcheck._reason_code_is_safe(code)


# ------------------------------------------------------------------ thth run

@pytest.fixture
def held_run(tmp_path, isolated_account_factory, monkeypatch, thth_root):
    for key in list(os.environ):
        if key.startswith("THTH_SMTP_") or key == "HEALTHCHECK_URL":
            monkeypatch.delenv(key)
    env_path = tmp_path / "account.env"
    env_path.write_text("HEALTHCHECK_URL=https://hc-ping.com/secret-check-id\n", encoding="utf-8")
    token_path = tmp_path / "account.token"
    token_path.write_text("{}\n", encoding="utf-8")
    account = isolated_account_factory(env=str(env_path), token=str(token_path),
                                       production=True, notification_email="user@example.org")
    incident._save(Path(thth_root) / incident.CONFIG_FILE, {
        "admin_email": "admin@example.org",
        "smtp": {"host": "smtp.example.org", "sender": "sender@example.org", "tls": "ssl"}})
    monkeypatch.setattr(cli.selfupdate_mod, "pull_and_reexec", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.collect_mod, "run_collect", lambda *_a, **_k: 0)
    pings, mails = [], []
    monkeypatch.setattr(healthcheck.httpsafe, "urlopen",
                        lambda req, **_k: pings.append((req.full_url, json.loads(req.data)))
                        or Response())
    monkeypatch.setattr(incident, "_send", lambda s, recipient, event, name:
                        mails.append((recipient, event["state"], event["reason"])) or True)

    def run():
        return cli.cmd_run(SimpleNamespace(account=account["name"]))

    return SimpleNamespace(account=account, pings=pings, mails=mails, run=run,
                           token=str(token_path), env=str(env_path),
                           state=accounts.state_dir_for(account["name"]))


def _remove_committed(account, *names):
    repo = account["repo_dir"]
    for name in names:
        run_git(repo, ["rm", "-q", os.path.join("docs", "sns", "queue", name)])
    run_git(repo, ["commit", "-qm", "rm"])
    run_git(repo, ["push", "-q"])


def test_承認済みが出られなければheldで知らせ増えなければ黙り0でrecovered(held_run, capsys):
    q = held_run.account["queue_dir"]
    write_queue_file(q, "one.md", fm_overrides=dict(STALE), body="## threads\n\n一本目。\n")

    assert held_run.run() == 0  # 投稿の rc は変えない（出すものが無い＝0）
    assert held_run.pings[-1][0].endswith("/secret-check-id/fail")
    assert held_run.pings[-1][1]["state"] == "held"
    assert held_run.pings[-1][1]["file"] is None
    assert "[approved_but_held]" in held_run.pings[-1][1]["reason"]
    status = healthcheck.read_status(held_run.state)
    assert status["last_state"] == "held"
    assert status["reason_code"] == "approved_but_held: approval_stale 1"
    assert held_run.mails == [
        ("user@example.org", "blocked", "approved_but_held: approval_stale 1"),
        ("admin@example.org", "blocked", "approved_but_held: approval_stale 1")]
    assert "承認済みで出られない原稿があります" in capsys.readouterr().err

    # 同じ本数のまま: 死活通知は held のまま（監視側は状態が変わらないので再通知しない）、
    # 運用通知（メール）は積まない。
    assert held_run.run() == 0
    assert held_run.pings[-1][1]["state"] == "held"
    assert len(held_run.mails) == 2

    # 増えた: もう一度知らせる。
    write_queue_file(q, "two.md", fm_overrides=dict(STALE), body="## threads\n\n二本目。\n")
    assert held_run.run() == 0
    assert len(held_run.mails) == 4
    assert held_run.mails[-1][2] == "approved_but_held: approval_stale 2"

    # 0 に戻った: recovered（held_cleared）と死活通知の success。
    _remove_committed(held_run.account, "one.md", "two.md")
    assert held_run.run() == 0
    assert held_run.pings[-1][1]["state"] == "success"
    assert held_run.mails[-2:] == [("user@example.org", "recovered", "held_cleared"),
                                   ("admin@example.org", "recovered", "held_cleared")]
    # 0 のままなら何も積まない。
    assert held_run.run() == 0
    assert len(held_run.mails) == 6


def test_減っただけなら知らせずまた増えたら知らせる(held_run):
    q = held_run.account["queue_dir"]
    write_queue_file(q, "one.md", fm_overrides=dict(STALE), body="## threads\n\n一本目。\n")
    write_queue_file(q, "two.md", fm_overrides=dict(STALE), body="## threads\n\n二本目。\n")
    assert held_run.run() == 0
    assert len(held_run.mails) == 2
    _remove_committed(held_run.account, "two.md")
    assert held_run.run() == 0
    assert len(held_run.mails) == 2  # 2 → 1 は黙る
    write_queue_file(q, "three.md", fm_overrides=dict(STALE), body="## threads\n\n三本目。\n")
    assert held_run.run() == 0
    assert len(held_run.mails) == 4  # 1 → 2 は知らせる


def test_時刻前のapproval_staleは知らせない(held_run):
    write_queue_file(held_run.account["queue_dir"], "later.md",
                     fm_overrides={**STALE, "publish_at": "2026-09-10T08:00:00+09:00"})
    assert held_run.run() == 0
    assert held_run.pings[-1][1]["state"] == "success"
    assert held_run.mails == []


def test_返信待ちだけなら知らせない(held_run):
    q = held_run.account["queue_dir"]
    write_queue_file(q, "q.md", fm_overrides={"publish_at": "2026-09-10T08:00:00+09:00"},
                     body="## threads\n\n問い。\n")
    write_queue_file(q, "a.md", fm_overrides={"reply_to_file": "q.md"},
                     body="## threads\n\n答え。\n")
    assert held_run.run() == 0
    assert held_run.pings[-1][1]["state"] == "success"
    assert held_run.mails == []


def test_rehearsalの台帳はheldにしない(held_run, isolated_account_factory):
    isolated_account_factory(env=held_run.env, token=held_run.token, production=False,
                             notification_email="user@example.org")
    write_queue_file(held_run.account["queue_dir"], "one.md", fm_overrides=dict(STALE))
    assert held_run.run() == 0
    assert held_run.pings[-1][1]["state"] == "success"
    assert held_run.mails == []


def test_heldを数えられなくてもrcは変えずhealthyを送らない(held_run, monkeypatch, capsys):
    def broken(*_a, **_k):
        raise OSError("/secret/path")
    monkeypatch.setattr(cli.core, "held_items_for_account", broken)
    assert held_run.run() == 0
    assert held_run.pings[-1][1]["state"] == "fail"
    err = capsys.readouterr().err
    assert "held_unavailable" in err and "/secret/path" not in err


def test_運用通知が壊れてもheldのrcは変わらない(held_run, monkeypatch):
    write_queue_file(held_run.account["queue_dir"], "one.md", fm_overrides=dict(STALE))

    def broken(*_a, **_k):
        raise ValueError("x")
    monkeypatch.setattr(incident, "notify", broken)
    assert held_run.run() == 0
    assert held_run.pings[-1][1]["state"] == "held"


def test_SMTPが無ければheldの事象を溜めない(held_run, thth_root):
    incident._save(Path(thth_root) / incident.CONFIG_FILE, {})
    write_queue_file(held_run.account["queue_dir"], "one.md", fm_overrides=dict(STALE))
    assert held_run.run() == 0
    assert held_run.run() == 0
    row = json.loads((Path(held_run.state) / incident.STATE_FILE).read_text())
    assert row["events"] == [] and row["held"] == 1


# ------------------------------------------------------------------ withdrawn（追加の裁定）

def test_指した原稿がwithdrawnなら待ちでなくtarget_withdrawnでheldに数える(tmp_path):
    q = parse_verified(_write(str(tmp_path), "q.md", make_queue_text(
        {"status": "withdrawn", "approved_sha": None}, body="## threads\n\n問い。\n")))
    a = parse_verified(_write(str(tmp_path), "a.md", make_queue_text(
        {"reply_to_file": "q.md"}, body="## threads\n\n答え。\n")))
    files = [q, a]
    result = _select(files)
    assert result.chosen is None
    reasons = {os.path.basename(r.file): r.reason for r in result.rejections}
    assert reasons["a.md"] == "reply_to_unresolved: target_withdrawn"
    assert select_mod.waiting_target(reasons["a.md"]) is None
    [row] = select_mod.held_items(result, files, NOW)
    assert row["file"] == "a.md" and row["category"] == "reply_to_unresolved" and row["due"]
    assert select_mod.held_reason_code([row]) == "approved_but_held: reply_to_unresolved 1"
    # runs に実エラーとして残る（retracted と同じ）。
    from thth import core
    assert core._is_error_reason(reasons["a.md"])
