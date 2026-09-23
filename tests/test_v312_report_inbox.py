"""報告の口 第 1 段——記録と利用者の CLI（設計 3.1.2 §1・§2・§5）。

見るのは:
  - `thth report file → list → show` の往復（CLI）。
  - 置き場は `state/_reports/`（0600・0700）で、SNS の台帳には 1 バイトも書かない。
  - 秘密らしき値が混ざっていたら**置かずに** `secret_detected`（伏字にして置かない）。
  - 長さ（title 120・本文 8,000・再現 4,000）・重複（24 時間・既存 id）・
    件数（1 account 1 日 20 件）。
  - 変更ログ `report_filed` は presence-only（本文は入らない）。
  - `report list <account|project>` は自分の project の報告だけ。
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import stat

import pytest

from thth import admin_log, cli, jst, redact, report_inbox

BODY = "thth replies が他の account の返信まで返してきます。\n手順: replies を読む"


@pytest.fixture
def two_projects(isolated_account_factory):
    mine = isolated_account_factory("kopicha-threads", project="kopicha")
    isolated_account_factory("kopicha-bsky", project="kopicha", media="bluesky")
    isolated_account_factory("other-threads", project="other")
    return mine


def _body_file(tmp_path, text=BODY, name="body.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _file(tmp_path, capsys, account="kopicha-threads", *, title="replies が他 account を返す",
          body=BODY, kind="bug", by="kopicha-session", extra=()):
    rc = cli.main(["report", "file", account, "--kind", kind, "--title", title,
                   "--body-file", _body_file(tmp_path, body), *(["--by", by] if by else []),
                   *extra, "--json"])
    captured = capsys.readouterr()
    return rc, json.loads(captured.out) if captured.out.strip() else None, captured.err


def _reports_dir():
    return Path(os.environ["THTH_ROOT"]) / "state" / "_reports"


# ------------------------------------------------------------------ 往復

def test_file_list_show_の往復(two_projects, tmp_path, capsys):
    rc, filed, _ = _file(tmp_path, capsys)
    assert rc == 0 and filed["status"] == "open" and filed["kind"] == "bug"
    report_id = filed["report_id"]
    assert report_inbox.REPORT_ID.match(report_id) and report_id.startswith("r20260909-")
    assert filed["project"] == "kopicha" and filed["tool_version"]

    assert cli.main(["report", "list", "kopicha", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [row["report_id"] for row in listed["reports"]] == [report_id]
    assert listed["n"] == 1 and listed["denominator"] == 1 and listed["open"] == 1
    # 一覧には本文を出さない（show で読む）。
    assert "body" not in listed["reports"][0] and BODY not in json.dumps(listed, ensure_ascii=False)

    assert cli.main(["report", "show", report_id, "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["body"] == BODY.strip() and shown["reporter"] == "kopicha-session"
    assert shown["via"] == "cli" and shown["medium"] == "threads"
    assert shown["replies"] == [] and shown["closed"] is None


def test_人向けの出力にも_id_と返事の在り処(two_projects, tmp_path, capsys):
    assert cli.main(["report", "file", "kopicha-threads", "--kind", "request", "--title", "欲しい形",
                     "--body-file", _body_file(tmp_path), "--by", "s"]) == 0
    out = capsys.readouterr().out
    assert "報告を置きました: r20260909-" in out and "要望" in out
    assert "handoff-report --since-last-read" in out


def test_本文は標準入力からも読める(two_projects, capsys, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("標準入力の本文"))
    assert cli.main(["report", "file", "kopicha-threads", "--kind", "bug", "--title", "stdin",
                     "--body-file", "-", "--by", "s", "--json"]) == 0
    report_id = json.loads(capsys.readouterr().out)["report_id"]
    assert report_inbox.show(report_id)["body"] == "標準入力の本文"


def test_再現手順は任意で置ける(two_projects, tmp_path, capsys):
    rc, filed, _ = _file(tmp_path, capsys, extra=("--repro-file", _body_file(tmp_path, "1. 打つ", "r.txt")))
    assert rc == 0
    assert report_inbox.show(filed["report_id"])["repro"] == "1. 打つ"


# ---------------------------------------------------------------- 置き場

def test_置き場は私有でSNSの台帳には書かない(two_projects, tmp_path, capsys):
    root = Path(os.environ["THTH_ROOT"])
    repo = Path(two_projects["repo_dir"])
    before_repo = sorted(str(p) for p in repo.rglob("*") if ".git" not in p.parts)
    rc, filed, _ = _file(tmp_path, capsys)
    assert rc == 0
    path = _reports_dir() / (filed["report_id"] + ".json")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(_reports_dir().stat().st_mode) == 0o700
    # SNS の台帳（account の state・repo）に何も増えない。
    assert not (root / "state" / "kopicha-threads").exists()
    assert sorted(str(p) for p in repo.rglob("*") if ".git" not in p.parts) == before_repo


def test_byが無ければ断って置かない(two_projects, tmp_path, capsys):
    rc, payload, err = _file(tmp_path, capsys, by=None)
    assert rc == 2 and payload["cannot_say"] == ["by_required"]
    assert err.startswith("by_required: --by")
    assert payload["report_channel"] == report_inbox.CHANNEL
    assert not _reports_dir().exists() or not list(_reports_dir().glob("r*.json"))


def test_知らないaccountは断る(two_projects, tmp_path, capsys):
    rc, payload, _ = _file(tmp_path, capsys, account="nobody")
    assert rc == 2 and payload["cannot_say"] == ["account_unavailable"]
    rc, payload, _ = _file(tmp_path, capsys, account="../x")
    assert rc == 2 and payload["cannot_say"] == ["invalid_account"]


def test_題が複数行や空なら断る(two_projects, tmp_path, capsys):
    for title in ("一行目\n二行目", "   "):
        rc, payload, _ = _file(tmp_path, capsys, title=title)
        assert rc == 2 and payload["cannot_say"] == ["invalid_report"]
    rc, payload, _ = _file(tmp_path, capsys, body="  \n ")
    assert rc == 2 and payload["cannot_say"] == ["invalid_report"]


# ------------------------------------------------------------------ 秘密

SECRETS = [
    "access_token=AbC123def456GHI789jkl",
    "Authorization: Bearer ya29a0AfH6SMBx1234567890",
    "curl -H 'Bearer AbCdEf0123456789xyz'",
    "key は sk-ant-api03-abcdefghijklmnop です",
    "THAAabcdefghijklmnopqrstuvwxyz12 が期限切れ",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig",
    "-----BEGIN RSA PRIVATE KEY-----",
    "token: AbCdEf0123456789xyz0",
    "password=hunter2hunter2x9",
    "client secret 0123456789abcdef0123456789abcdef を貼ります",
    "ping https://hc-ping.com/1234abcd-12ab-34cd-56ef-1234567890ab",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_秘密らしき値は置かずに断る(two_projects, tmp_path, capsys, secret):
    rc, payload, err = _file(tmp_path, capsys, body="前置き\n" + secret)
    assert rc == 2 and payload["cannot_say"] == ["secret_detected"]
    assert secret not in err and secret not in json.dumps(payload, ensure_ascii=False)
    assert not _reports_dir().exists() or not list(_reports_dir().glob("r*.json"))


@pytest.mark.parametrize("field", ["title", "repro"])
def test_題と再現手順も秘密を検査する(two_projects, tmp_path, capsys, field):
    secret = "sk-ant-api03-abcdefghijklmnop"
    extra = ("--repro-file", _body_file(tmp_path, secret, "r.txt")) if field == "repro" else ()
    rc, payload, _ = _file(tmp_path, capsys, title=secret if field == "title" else "題", extra=extra)
    assert rc == 2 and payload["cannot_say"] == ["secret_detected"]


def test_登録された秘密の値そのものも当てる(two_projects, tmp_path, capsys):
    value = "zzzzqqqqwwwweeee"   # 綴りも形も無い値
    redact.register_secret(value)
    try:
        rc, payload, _ = _file(tmp_path, capsys, body="貼ってしまった " + value)
    finally:
        redact._clear_registered()
    assert rc == 2 and payload["cannot_say"] == ["secret_detected"]


@pytest.mark.parametrize("text", [
    "exit code: 2", "error code: invalid_scope", "Authorization: Bearer <token>",
    "token: <token>", "sha dcb633a で再現", "report r20260909-1a2b3c4d の続き",
    "access_token=*** と伏せてあります", "Bearer abc",
])
def test_不具合の報告に普通に出る綴りでは断らない(text):
    assert not redact.looks_like_secret(text)


# ------------------------------------------------------------------ 長さ

@pytest.mark.parametrize("field,limit", [("title", 120), ("body", 8000), ("repro", 4000)])
def test_長さの上限ちょうどは置け_1字超えは断る(two_projects, tmp_path, capsys, field, limit):
    def attempt(n):
        title = "題" * n if field == "title" else "題"
        body = "本" * n if field == "body" else BODY + str(n)
        extra = ("--repro-file", _body_file(tmp_path, "再" * n, f"r{n}.txt")) if field == "repro" else ()
        return _file(tmp_path, capsys, title=title, body=body, extra=extra)
    rc, payload, _ = attempt(limit + 1)
    assert rc == 2 and payload["cannot_say"] == ["report_too_long"]
    rc, payload, _ = attempt(limit)
    assert rc == 0, payload


# ------------------------------------------------------------ 重複と件数

def test_同じ題と本文は24時間以内なら既存のidを返して断る(two_projects, tmp_path, capsys, monkeypatch):
    rc, first, _ = _file(tmp_path, capsys)
    assert rc == 0
    rc, payload, err = _file(tmp_path, capsys)
    assert rc == 2 and payload["cannot_say"] == ["duplicate_report"]
    assert payload["report_id"] == first["report_id"]
    # 別の account（同じ project）なら重複とはみなさない——他の account の id を返さない。
    rc, other, _ = _file(tmp_path, capsys, account="kopicha-bsky")
    assert rc == 0 and other["report_id"] != first["report_id"]
    # 24 時間を過ぎれば置ける。
    later = jst.now_jst() + datetime.timedelta(hours=24, seconds=1)
    monkeypatch.setattr(jst, "now_jst", lambda: later)
    rc, again, _ = _file(tmp_path, capsys)
    assert rc == 0 and again["report_id"] != first["report_id"]


def test_1accountにつき1日20件まで(two_projects, tmp_path, capsys):
    for n in range(report_inbox.DAILY_LIMIT):
        rc, _, _ = _file(tmp_path, capsys, title=f"題 {n}")
        assert rc == 0
    rc, payload, _ = _file(tmp_path, capsys, title="21 件目")
    assert rc == 2 and payload["cannot_say"] == ["report_rate_limited"]
    rc, _, _ = _file(tmp_path, capsys, account="other-threads", title="別の account")
    assert rc == 0


# ------------------------------------------------------------ 変更ログ

def test_変更ログはpresence_onlyで本文を入れない(two_projects, tmp_path, capsys):
    rc, filed, _ = _file(tmp_path, capsys)
    assert rc == 0
    rows, broken = admin_log.read(event="report_filed")
    assert broken == 0 and len(rows) == 1
    row = rows[0]
    assert row["account"] == "kopicha-threads" and row["by"] == "kopicha-session"
    assert row["diff"] == {"report": ["absent", "present"]} and row["via"] == "cli"
    dumped = json.dumps(row, ensure_ascii=False)
    assert "他の account の返信" not in dumped and "replies が他 account" not in dumped


def test_変更ログに書けなければ置いた1件を戻す(two_projects, tmp_path, capsys, monkeypatch):
    def broken(*args, **kwargs):
        raise admin_log.AdminLogError("admin_log_write_failed")
    monkeypatch.setattr(admin_log, "append", broken)
    rc, payload, _ = _file(tmp_path, capsys)
    assert rc == 2 and payload["cannot_say"] == ["report_log_unavailable"]
    assert not list(_reports_dir().glob("r*.json"))


# ------------------------------------------------------------ 読む範囲

def test_listは自分のprojectだけ(two_projects, tmp_path, capsys):
    _, mine, _ = _file(tmp_path, capsys)
    _, theirs, _ = _file(tmp_path, capsys, account="other-threads", title="他の project")
    assert cli.main(["report", "list", "kopicha-bsky", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [row["report_id"] for row in listed["reports"]] == [mine["report_id"]]
    assert listed["denominator"] == 1 and listed["unreadable"] is None
    assert cli.main(["report", "list", "other", "--json"]) == 0
    assert [row["report_id"] for row in json.loads(capsys.readouterr().out)["reports"]] == [
        theirs["report_id"]]


def test_Scopeは他のprojectの報告を読ませない(two_projects, tmp_path, capsys):
    _, theirs, _ = _file(tmp_path, capsys, account="other-threads", title="他の project")
    scope = report_inbox.Scope(["kopicha-threads"], ["kopicha"])
    with pytest.raises(report_inbox.ReportError, match="^report_not_found$"):
        report_inbox.show(theirs["report_id"], scope=scope)
    with pytest.raises(report_inbox.ReportError, match="^report_not_found$"):
        report_inbox.show("r20260909-00000000", scope=scope)
    assert report_inbox.list_reports(scope=scope, status="all")["reports"] == []


def test_壊れた1件で一覧を止めない(two_projects, tmp_path, capsys):
    _, mine, _ = _file(tmp_path, capsys)
    (_reports_dir() / "r20260909-deadbeef.json").write_text("{", encoding="utf-8")
    payload = report_inbox.list_reports(status="all")
    assert payload["n"] == 1 and payload["unreadable"] == 1


# ------------------------------------------------------------ 絶対パス

def test_本文のTHTH_ROOTとホームの絶対パスは畳んで置く(two_projects, tmp_path, capsys):
    root = os.environ["THTH_ROOT"]
    home = os.path.expanduser("~")
    body = f"{root}/repos/kopicha/docs/sns/queue/a.md が読めない\n{home}/x も"
    rc, filed, _ = _file(tmp_path, capsys, body=body)
    assert rc == 0
    stored = report_inbox.show(filed["report_id"])["body"]
    assert root not in stored and home not in stored
    assert "$THTH_ROOT/repos/kopicha/docs/sns/queue/a.md" in stored and "~/x" in stored


def test_置き場がまだ無ければ0件_symlinkなら読まずに断る(two_projects, tmp_path):
    assert report_inbox.list_reports(status="all")["n"] == 0
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    state = Path(os.environ["THTH_ROOT"]) / "state"
    state.mkdir(exist_ok=True)
    (state / "_reports").symlink_to(elsewhere)
    with pytest.raises(report_inbox.ReportError, match="^report_store_unavailable$"):
        report_inbox.list_reports(status="all")
    with pytest.raises(report_inbox.ReportError, match="^report_store_unavailable$"):
        report_inbox.file_report("kopicha-threads", kind="bug", title="t", body="b", by="s")
    assert list(elsewhere.iterdir()) == []
