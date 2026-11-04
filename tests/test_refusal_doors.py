"""段 B の柵: 断りの 3 つ（なぜ・通すには・もっと良く）が、どの口でも同じ（設計「監査と審査の機械化と
前向きな断り」§3「3 つの口で同じもの」・§7 段 B・原則 `docs/原則_前向きな断り.md`）。

- 代表 8 符丁が、CLI の文・CLI の `--json`・MCP の 3 つの口で、同じ why・fix・better を返す。
  3 つの正は目録の口（`thth/refusal.py` の `parts`）が作る 1 つの値で、口ごとにそれと比べる。
- 1 行目は今のまま（CLI の `符丁: なぜ` と `refusals.reason_code` が読む符丁・MCP の text の 1 行目の符丁）。
- `--json` は鍵を足すだけ（今の鍵は残る）。

代表 8 符丁（設計 §1 の 5 つ＋断りの口の呼び出しが多い 3 つ）:
invalid_draft・too_soon・daily_limit・invalid_request・clicks_unavailable（§1）、
scope_unavailable（25 か所）・invalid_options（16 か所）・account_stopped（12 か所・3 つの口すべてに出る安全装置）。

口: 遠くの道の CLI（`thth send <口座> --text …` → `remote.refuse`）・サーバ型の MCP（`server_call` の断り）・
手元の CLI の安全装置（`thth send --production` → `cmd_send`）。`clicks_unavailable` は欄の「言えない」なので、
口は欄そのもの（文 `scenario_cli.clicks_lines`・`--json` の `threads_clicks` の対応表・会話のダッシュボードが
描く `thth dashboard … campaign --json` の写し）。
"""
from __future__ import annotations

import datetime
import json

import pytest

from thth import cli, core, guard, jst, refusal, refusal_catalog, refusals
from tests.test_v212_server_writes import env  # noqa: F401 — MCP の口の credential と台帳

ACCOUNT = "alpha"
NEXT_AT = "2030-01-01T09:00:00+09:00"

# 遠くの道で Worker が返す断り（`remote._answer` が RemoteError にする形）。
REMOTE = {
    "invalid_draft": {"error": "invalid_draft", "reason": "hashtags_disabled_by_ledger"},
    "too_soon": {"error": "too_soon", "next_at": NEXT_AT},
    "daily_limit": {"error": "daily_limit", "next_at": NEXT_AT},
    "invalid_request": {"error": "invalid_request"},
    "scope_unavailable": {"error": "scope_unavailable"},
    "invalid_options": {"error": "invalid_options"},
    "account_stopped": {"error": "account_stopped", "reason": "burst"},
}
REPRESENTATIVE = (*REMOTE, "clicks_unavailable")
# 今の 1 行目（遠くの道の CLI・MCP）。ここが変わったら赤。
REMOTE_FIRST = {
    "invalid_draft": "invalid_draft: hashtags_disabled_by_ledger",
    "too_soon": "too_soon（次は 2030-01-01 09:00 JST から）",
    "daily_limit": "daily_limit（次は 2030-01-01 09:00 JST から）",
    "invalid_request": "invalid_request",
    "scope_unavailable": "scope_unavailable",
    "invalid_options": "invalid_options",
    "account_stopped": "account_stopped: burst",
}
MCP_FIRST = {
    "invalid_draft": "invalid_draft: hashtags_disabled_by_ledger",
    "too_soon": f"too_soon: next_at={NEXT_AT}",
    "daily_limit": f"daily_limit: next_at={NEXT_AT}",
    "invalid_request": "invalid_request",
    "scope_unavailable": "scope_unavailable",
    "invalid_options": "invalid_options",
    "account_stopped": "account_stopped: burst: 持ち主が戻すまで公開・削除・予約はできません",
}


def _values(code) -> dict:
    body = REMOTE[code]
    values = {"account": ACCOUNT}
    if body.get("next_at"):
        values["next_at"] = body["next_at"]
    if body.get("reason"):
        values["reason"] = body["reason"]
    return values


def _about(code) -> str:
    return refusal.focus(code, REMOTE.get(code, {}).get("reason"))


def _expected(code) -> dict:
    found = refusal.parts(_about(code), **_values(code))
    assert found and found["why"] and found["fix"] and found["better"], code
    return {"why": found["why"], "fix": found["fix"], "better": found["better"]}


def _three(value: dict) -> dict:
    return {key: value.get(key) for key in ("why", "fix", "better")}


# --------------------------------------------------------------------------
# 代表 8 符丁は目録にあり、3 つがそろう
# --------------------------------------------------------------------------

def test_代表8符丁は目録にあり3つがそろう():
    assert len(REPRESENTATIVE) == 8
    for code in REPRESENTATIVE:
        entry = refusal_catalog.get(code)
        assert entry and entry["why"] and entry["fix"]["text"] and entry["better"], code


# --------------------------------------------------------------------------
# 口 1・2: CLI の文と CLI の --json（遠くの道）
# --------------------------------------------------------------------------

@pytest.fixture
def remote_cli(tmp_path, monkeypatch):
    from tests.test_v3140_remote_cli import KEY, FakeWorker
    from thth import remote
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    config = tmp_path / "cfg" / "thth" / "remote.json"
    monkeypatch.setenv("THTH_REMOTE_CONFIG", str(config))
    monkeypatch.setattr(remote, "_sleep", lambda seconds: None)
    worker = FakeWorker()
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"url": worker.url, "key": KEY}))
    config.chmod(0o600)
    yield worker
    worker.close()


def _cli(capsys, *argv):
    rc = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


@pytest.mark.parametrize("code", list(REMOTE))
def test_CLIの文とjsonが同じ3つを返し1行目は今のまま(code, remote_cli, capsys):
    remote_cli.push(200, REMOTE[code])
    rc, out, err = _cli(capsys, "send", ACCOUNT, "--text", "こんにちは")
    assert rc != 0
    first = err.splitlines()[0]
    assert first == REMOTE_FIRST[code]
    assert refusals.reason_code(first, argv=["send", ACCOUNT, "--text", "こんにちは"], command="send") == code
    text = refusal.read(err, _about(code), **_values(code))
    assert text == _expected(code), err

    remote_cli.push(200, REMOTE[code])
    rc, out, err = _cli(capsys, "send", ACCOUNT, "--text", "こんにちは", "--json")
    payload = json.loads(out)
    # 今の鍵は残る（名も値も同じ）。足すのは why・fix・better・next_at。
    for key, value in REMOTE[code].items():
        assert payload[key] == value, key
    assert set(payload) - set(REMOTE[code]) <= set(refusal.JSON_KEYS)
    assert _three(payload) == _expected(code)
    assert payload["next_at"] == REMOTE[code].get("next_at")
    assert err.splitlines()[0] == REMOTE_FIRST[code]


# --------------------------------------------------------------------------
# 口 3: MCP（サーバ型の断り）
# --------------------------------------------------------------------------

@pytest.mark.parametrize("code", list(REMOTE))
def test_MCPが同じ3つを返しtextの1行目は今のまま(code, env, monkeypatch):
    from tests.test_mcp import _load_server_module
    from thth import server_writes
    from thth.report_service import ReportServiceError
    module = _load_server_module()
    monkeypatch.setenv("THTH_REPORT_CREDENTIALS", str(env["path"]))
    monkeypatch.setenv("THTH_REPORT_TOKEN", env["bearer"])
    body = REMOTE[code]

    def refuse(*_args, **_kwargs):
        if code in ("too_soon", "daily_limit", "account_stopped"):
            raise guard.GuardRefused(code, reason=body.get("reason"), next_at=body.get("next_at"))
        exc = ReportServiceError(code)
        exc.reason = body.get("reason")
        raise exc
    monkeypatch.setattr(server_writes, "execute", refuse)
    result = module.server_call("thth_draft_put", {"account": ACCOUNT, "body": "本文",
                                                   "publish_at": "2030-01-01T12:00:00+09:00"})
    assert result["isError"]
    text = result["content"][0]["text"]
    assert text.splitlines()[0] == MCP_FIRST[code]
    assert refusal.read(text, _about(code), **_values(code)) == _expected(code), text


# --------------------------------------------------------------------------
# 手元の CLI の安全装置（thth send --production）: 1 行目は安全装置の文のまま（それがなぜ）
# --------------------------------------------------------------------------

GUARD = {"too_soon": {"next_at": NEXT_AT}, "daily_limit": {"next_at": NEXT_AT},
         "account_stopped": {"reason": "burst"}}


@pytest.mark.parametrize("code", list(GUARD))
def test_手元のsendの安全装置も同じ3つを返す(code, tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    monkeypatch.setenv("THTH_REMOTE_CONFIG", str(tmp_path / "none.json"))
    refused = guard.GuardRefused(code, **GUARD[code])
    line = core.guard_refusal_message(ACCOUNT, refused)

    def send_once(*_args, **kwargs):
        kwargs["log"](line)
        return core.ThrowResult(exit_code=1, mode="production", action="guard", message=line, error=code,
                                next_at=refused.next_at, reason=refused.reason)
    monkeypatch.setattr(core, "send_once", send_once)
    expected = _expected(code)

    rc, out, err = _cli(capsys, "send", ACCOUNT, "--text", "こんにちは", "--production")
    assert rc == 1
    assert out.splitlines()[0] == line  # 1 行目は安全装置の文のまま
    assert refusal.read(out, code, **_values(code)) == expected, out
    assert refusal.WHY not in out  # 1 行目がなぜを言っているので重ねない

    rc, out, err = _cli(capsys, "send", ACCOUNT, "--text", "こんにちは", "--production", "--json")
    payload = json.loads(out)
    assert payload["error"] == code and payload["action"] == "guard"
    assert _three(payload) == expected
    assert payload["next_at"] == refused.next_at
    assert err.splitlines()[0] == line


# --------------------------------------------------------------------------
# clicks_unavailable: 欄の「言えない」（口は欄そのもの）
# --------------------------------------------------------------------------

def _clicks_block():
    from thth import thread_clicks
    rows = [{"file": "a.md", "account": ACCOUNT, "medium": thread_clicks.MEDIUM, "thread": "t1",
             "posted_at": "2026-11-01T10:00:00+09:00", "clicks_72h": None, "window_days": 3,
             "cannot_say": "clicks_unavailable"}]
    return thread_clicks.summarize(rows)


def test_clicks_unavailableは欄の文とjsonとダッシュボードで同じ3つ():
    from thth import dashboard_cli, scenario_cli
    expected = {key: value for key, value in refusal.parts("clicks_unavailable").items()
                if key in ("why", "fix", "better")}
    block = _clicks_block()
    # --json（scenario show の threads_clicks）: 対応表で同じ 3 つ。今の鍵は残る。
    assert {key: block[key]["clicks_unavailable"] for key in ("why", "fix", "better")} == expected
    assert block["reasons"] == {"clicks_unavailable": 1}
    # 会話のダッシュボード（dashboard campaign --json の outcomes.threads_clicks）: 同じ写し。
    cleaned = dashboard_cli._clean(block)
    assert {key: cleaned[key]["clicks_unavailable"] for key in ("why", "fix", "better")} == expected
    # 文: 「言えない本数」の行は今のまま、続けて 3 つ。
    lines = scenario_cli.clicks_lines(block)
    assert lines[1] == "   言えない本数: clicks_unavailable 1"
    text = "\n".join(line.strip() for line in lines if "clicks_unavailable: " in line or line.strip().startswith(
        (refusal.FIX, refusal.BETTER)))
    assert refusal.read(text, "clicks_unavailable") == expected, text


def test_clicksが全部言えるときは鍵も行も足さない():
    from thth import scenario_cli, thread_clicks
    rows = [{"file": "a.md", "account": ACCOUNT, "medium": thread_clicks.MEDIUM, "thread": "t1",
             "posted_at": "2026-11-01T10:00:00+09:00", "clicks_72h": 3, "window_days": 3, "cannot_say": None}]
    block = thread_clicks.summarize(rows)
    assert not {"why", "fix", "better"} & set(block)
    assert not any(line.strip().startswith((refusal.FIX, refusal.BETTER)) for line in scenario_cli.clicks_lines(block))


# --------------------------------------------------------------------------
# 口の決まり（穴埋め・上書きしない・目録に無い符丁）
# --------------------------------------------------------------------------

def test_jsonは今の鍵を上書きしない():
    body = {"error": "too_soon", "next_at": "既にある値", "why": "既にある値"}
    refusal.add_fields(body, "too_soon", next_at=NEXT_AT)
    assert body["next_at"] == "既にある値" and body["why"] == "既にある値"
    assert body["fix"] and body["better"]


def test_穴に入るのは形を検査した値だけ():
    bad = refusal.parts("too_soon", account="../etc", next_at="明日の朝 /etc/passwd", reason="本文")
    assert "passwd" not in json.dumps(bad, ensure_ascii=False)
    assert "<時刻>" in bad["fix"]
    good = refusal.parts("too_soon", next_at=NEXT_AT)
    assert NEXT_AT in good["fix"]
    # 安全装置の詳細は決まった語だけ（ほかの語はなぜを変えない）。
    assert refusal.parts("account_stopped", reason="自由文")["why"] == refusal_catalog.CATALOG["account_stopped"]["why"]


def test_目録に無い符丁は1行目だけ(capsys):
    assert refusal.tail("no_such_code_here") == []
    assert refusal.fields("no_such_code_here") == {}
    assert refusal.mcp_text("no_such_code_here") == "no_such_code_here"


def test_安全装置の文と目録のなぜは詳細ごとにも食い違わない():
    """`core.guard_refusal_message` は残す（設計 §3）。詳細つきのなぜも、その 1 行の中にある。"""
    for code, reason in (("account_stopped", "burst"), ("account_stopped", "owner"), ("daily_limit", "reply_daily"),
                         ("burst_limit", "bookmark_burst")):
        why = refusal_catalog.guard_why(code, reason)
        assert why, (code, reason)
        refused = guard.GuardRefused(code, reason=reason)
        assert why in core.guard_refusal_message("<口座>", refused), (code, reason)
        assert refusal.says(core.guard_refusal_message(ACCOUNT, refused),
                            refusal.parts(code, account=ACCOUNT, reason=reason)["why"]), (code, reason)


def test_通るときは黙る(tmp_path, monkeypatch, capsys):
    """断らない send は 1 字も変わらない（目録の行も鍵も足さない）。"""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    monkeypatch.setenv("THTH_REMOTE_CONFIG", str(tmp_path / "none.json"))

    def send_once(*_args, **kwargs):
        kwargs["log"]("投稿しました")
        return core.ThrowResult(exit_code=0, mode="production", action="post", message="投稿しました", post_id="1")
    monkeypatch.setattr(core, "send_once", send_once)
    rc, out, err = _cli(capsys, "send", ACCOUNT, "--text", "こんにちは", "--production")
    assert rc == 0 and out == "投稿しました\n" and err == ""
    rc, out, err = _cli(capsys, "send", ACCOUNT, "--text", "こんにちは", "--production", "--json")
    assert rc == 0 and not set(refusal.JSON_KEYS) - {"next_at"} & set(json.loads(out))
    assert "next_at" not in json.loads(out)


def test_時刻の穴と今の時刻():
    # 安全装置の next_at（jst.iso の形）は時刻の穴の検査を通る。
    value = jst.iso(datetime.datetime(2030, 1, 1, 9, 0, tzinfo=jst.JST))
    assert value in refusal.parts("too_soon", next_at=value)["fix"]
