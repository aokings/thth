"""段 B の柵: 断りの 3 つ（なぜ・通すには・もっと良く）を、どの口でも出す（設計「監査と審査の機械化と
前向きな断り」§3「3 つの口で同じもの」・§7 段 B・原則 `docs/原則_前向きな断り.md`）。

- 代表 8 符丁が、CLI の文・CLI の `--json`・MCP の口で、目録の部に書いた文を返す。
  **「同じ 3 つ」は「同じ趣旨で、その口で打てる道」と読む**（段 B の直し）。なぜ・もっと良くは口を問わず同じ、
  通すには口ごとの文（手元の CLI・遠くの道の CLI・MCP。目録の `fix_by_door`）。同じ口の文と `--json` は一字も違わない。
- 期待は目録を通さず、部に書いた文を**字面で**ここに書く（`refusal.parts()` が括弧を落とす・目録の命令が
  変わる、のどちらも赤にする）。
- 示す命令は、その口で実際に打って断られない（下の「通す道を打つ」）。
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
from tests.test_v212_server_writes import env, remote  # noqa: F401 — MCP の口の credential と台帳

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

# ---- 期待の字面（目録の部の文に、口座 alpha と時刻 NEXT_AT を入れたもの。parts() を通さない） ----

WHY = {
    "invalid_draft": "この口座の台帳は hashtags: false なのに、本文に # の語があります",
    "too_soon": "前の公開から最短間隔（min_interval_hours）が経っていません。返信には掛かりません",
    "daily_limit": "今日の公開が上限（daily_max_posts）に達しました",
    "invalid_request": "要求の形が受け付ける形と違います（知らない鍵・足りない鍵・型・- で始まる値など）。",
    "scope_unavailable": "この鍵・credential では、その口座か project を扱えません。",
    "invalid_options": "道具に渡した選択肢の型か組合せが、受け付ける形ではありません（例: by は compare_previous と一緒だけ）。",
    "account_stopped": "急な連投（burst）で口座を止めました。戻すのは thth account resume alpha --by <名前>",
}
BETTER = {
    "invalid_draft": "語は本文に入れ、話題は topic: に書くと、タグ無しでも届きます",
    "too_soon": "同じ趣旨なら間隔を空けて 2〜3 本に分けると、それぞれが違う時間帯の読者に届きます。",
    "daily_limit": "返信は投稿とは別の枠なので、今日は返信に回すと枠を使わず会話を伸ばせます",
    "invalid_request": "書く道具の前に読むだけの道具か dry-run で形を確かめると、往復が減ります",
    "scope_unavailable": "扱う口座が増えたら、持ち主に鍵の範囲を足してもらうと一本の鍵で済みます。",
    "invalid_options": "迷ったら選択肢を外して既定で一度読むと、どの欄が要るかが見えます",
    "account_stopped": "戻す前に止まった理由（連投なら間隔）を見直すと、同じ止めを繰り返しません",
}
_QUEUE = ("原稿の front-matter に publish_at: 2030-01-01T09:00:00+09:00 を書いて queue に置き、承認してください"
          "（1 段目の digest を --confirm に渡して確定・timer がその時刻に出します）")
_PUT = ("thth_draft_put に publish_at: 2030-01-01T09:00:00+09:00 で下書きを置き、返った draft_id を "
        "thth_schedule_request に渡して予約してください")
_SCHEDULE = "thth schedule alpha --text-file <ファイル> --at 2030-01-01T09:00:00+09:00 --production"
_HASHTAG_FIX = "本文から # の語を外して、もう一度送ってください（タグを使うなら、持ち主が口座の台帳の hashtags を true にします）"
_HELP_FIX = "1 行目の後ろに書いた所を直して、もう一度打ってください（形は thth <命令> --help）"
_OPTIONS_FIX = "真偽値は true／false、数は 1 以上の整数にし、by を使うなら compare_previous: true も付けてください"
_KEY_FIX = ("鍵を発行した口座の名前で打ち直してください。この口座も扱うなら、その口座の持ち主に "
            "https://thth.me/activity で鍵を発行してもらい、thth login で入れ替えてください")
_WEB_RESUME = "公開・削除・予約は止まっています。持ち主が https://thth.me/activity で戻すと、また出せます"
FIX = {
    "cli": {
        "too_soon": f"{_QUEUE}（thth approve <ファイル> --by <名前>）",
        "daily_limit": (f"{_QUEUE}。数を上げるなら持ち主が thth account set alpha daily_max_posts <数> --by <名前>"
                        "（thth approve <ファイル> --by <名前>）"),
        "account_stopped": "公開・削除・予約は止まっています。持ち主が戻すと、また出せます（thth account resume alpha --by <名前>）",
    },
    "remote": {
        "invalid_draft": _HASHTAG_FIX,
        "too_soon": f"次に出せる時刻に予約してください（{_SCHEDULE}）",
        "daily_limit": f"次に出せる時刻に予約してください（数を上げるのは持ち主が https://thth.me/activity で）（{_SCHEDULE}）",
        "invalid_request": _HELP_FIX,
        "scope_unavailable": _KEY_FIX,
        "invalid_options": _OPTIONS_FIX,
        "account_stopped": _WEB_RESUME,
    },
    "mcp": {
        "invalid_draft": _HASHTAG_FIX,
        "too_soon": _PUT,
        "daily_limit": f"{_PUT}（数を上げるのは持ち主が https://thth.me/activity で）",
        "invalid_request": "道具の説明（schema）にある鍵だけを正しい型で渡し、必須の鍵を足してください（key と value は両方そろえる）",
        "scope_unavailable": ("この credential が許す口座・project の名前で呼び直してください。ほかの口座も扱うなら、"
                              "持ち主に credential の範囲を足してもらってください"),
        "invalid_options": _OPTIONS_FIX,
        "account_stopped": _WEB_RESUME,
    },
}


def _expected(code, door) -> dict:
    return {"why": WHY[code], "fix": FIX[door][code], "better": BETTER[code]}


def _values(code, door) -> dict:
    body = REMOTE[code]
    values = {"account": ACCOUNT, "door": door}
    if body.get("next_at"):
        values["next_at"] = body["next_at"]
    if body.get("reason"):
        values["reason"] = body["reason"]
    return values


def _about(code) -> str:
    return refusal.focus(code, REMOTE.get(code, {}).get("reason"))


def _three(value: dict) -> dict:
    return {key: value.get(key) for key in ("why", "fix", "better")}


def test_代表8符丁は目録にあり3つがそろう():
    assert len(REPRESENTATIVE) == 8
    for code in REPRESENTATIVE:
        entry = refusal_catalog.get(code)
        assert entry and entry["why"] and entry["fix"]["text"] and entry["better"], code


# --------------------------------------------------------------------------
# 口 1・2: 遠くの道の CLI の文と --json
# --------------------------------------------------------------------------

@pytest.fixture
def remote_cli(tmp_path, monkeypatch):
    from tests.test_v3140_remote_cli import KEY, FakeWorker
    from thth import remote as remote_mod
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    config = tmp_path / "cfg" / "thth" / "remote.json"
    monkeypatch.setenv("THTH_REMOTE_CONFIG", str(config))
    monkeypatch.setattr(remote_mod, "_sleep", lambda seconds: None)
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
def test_遠くの道のCLIの文とjsonが同じ3つを返し1行目は今のまま(code, remote_cli, capsys):
    remote_cli.push(200, REMOTE[code])
    rc, out, err = _cli(capsys, "send", ACCOUNT, "--text", "こんにちは")
    assert rc != 0
    first = err.splitlines()[0]
    assert first == REMOTE_FIRST[code]
    assert refusals.reason_code(first, argv=["send", ACCOUNT, "--text", "こんにちは"], command="send") == code
    assert refusal.read(err, _about(code), **_values(code, "remote")) == _expected(code, "remote"), err
    assert f"{refusal.FIX}{FIX['remote'][code]}" in err.splitlines()

    remote_cli.push(200, REMOTE[code])
    rc, out, err = _cli(capsys, "send", ACCOUNT, "--text", "こんにちは", "--json")
    payload = json.loads(out)
    # 今の鍵は残る（名も値も同じ）。足すのは why・fix・better・next_at。
    for key, value in REMOTE[code].items():
        assert payload[key] == value, key
    assert set(payload) - set(REMOTE[code]) <= set(refusal.JSON_KEYS)
    assert _three(payload) == _expected(code, "remote")
    assert payload["next_at"] == REMOTE[code].get("next_at")
    assert err.splitlines()[0] == REMOTE_FIRST[code]


# --------------------------------------------------------------------------
# 口 3: MCP（サーバ型の断り）
# --------------------------------------------------------------------------

def _mcp(env, monkeypatch):
    from tests.test_mcp import _load_server_module
    module = _load_server_module()
    monkeypatch.setenv("THTH_REPORT_CREDENTIALS", str(env["path"]))
    monkeypatch.setenv("THTH_REPORT_TOKEN", env["bearer"])
    return module


def _raise(monkeypatch, code, body):
    from thth import server_writes
    from thth.report_service import ReportServiceError

    def refuse(*_args, **_kwargs):
        if code in ("too_soon", "daily_limit", "account_stopped"):
            raise guard.GuardRefused(code, reason=body.get("reason"), next_at=body.get("next_at"))
        exc = ReportServiceError(code)
        exc.reason = body.get("reason")
        raise exc
    monkeypatch.setattr(server_writes, "execute", refuse)


@pytest.mark.parametrize("code", list(REMOTE))
def test_MCPが同じ3つを返しtextの1行目は今のまま(code, env, monkeypatch):
    module = _mcp(env, monkeypatch)
    _raise(monkeypatch, code, REMOTE[code])
    result = module.server_call("thth_draft_put", {"account": ACCOUNT, "body": "本文",
                                                   "publish_at": "2030-01-01T12:00:00+09:00"})
    assert result["isError"]
    text = result["content"][0]["text"]
    assert text.splitlines()[0] == MCP_FIRST[code]
    assert refusal.read(text, _about(code), **_values(code, "mcp")) == _expected(code, "mcp"), text
    # MCP の口は CLI の命令を示さない（`thth ` で始まる打つ命令は無い）。
    fix = refusal.read(text, _about(code))["fix"]
    assert "（thth " not in fix and "thth schedule" not in fix and "thth account resume" not in fix


def test_MCPが呼ぶCLIの子は口がmcpになる(monkeypatch):
    """stdio の MCP（手元・遠くの道）は CLI を子で呼ぶ。子の断りの文も MCP の口の文にする。"""
    from tests.test_mcp import _load_server_module
    import subprocess
    module = _load_server_module()
    seen = {}

    def run(argv, **kwargs):
        seen.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(module.subprocess, "run", run)
    module.run_cli(["queue", "--json"])
    assert seen.get(refusal.DOOR_ENV) == "mcp"
    monkeypatch.setenv(refusal.DOOR_ENV, "mcp")
    assert refusal.parts("too_soon", door="remote", next_at=NEXT_AT)["fix"] == FIX["mcp"]["too_soon"]


# --------------------------------------------------------------------------
# 手元の CLI の安全装置（thth send --production）: 1 行目は安全装置の文のまま（それがなぜ）
# --------------------------------------------------------------------------

GUARD = {"too_soon": {"next_at": NEXT_AT}, "daily_limit": {"next_at": NEXT_AT},
         "account_stopped": {"reason": "burst"}}


def _local_send(monkeypatch, tmp_path, code, **guard_values):
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    monkeypatch.setenv("THTH_REMOTE_CONFIG", str(tmp_path / "none.json"))
    refused = guard.GuardRefused(code, **guard_values)
    line = core.guard_refusal_message(ACCOUNT, refused)

    def send_once(*_args, **kwargs):
        kwargs["log"](line)
        return core.ThrowResult(exit_code=1, mode="production", action="guard", message=line, error=code,
                                next_at=refused.next_at, reason=refused.reason)
    monkeypatch.setattr(core, "send_once", send_once)
    return refused, line


@pytest.mark.parametrize("code", list(GUARD))
def test_手元のsendの安全装置も同じ3つを返す(code, tmp_path, monkeypatch, capsys):
    refused, line = _local_send(monkeypatch, tmp_path, code, **GUARD[code])
    expected = _expected(code, "cli")

    rc, out, err = _cli(capsys, "send", ACCOUNT, "--text", "こんにちは", "--production")
    assert rc == 1
    assert out.splitlines()[0] == line  # 1 行目は安全装置の文のまま
    assert refusal.read(out, code, **_values(code, "cli")) == expected, out
    assert refusal.WHY not in out  # 1 行目がなぜを言っているので重ねない
    assert "thth schedule" not in out  # 手元の台帳では予約を刻む形は remote_only で断られる

    rc, out, err = _cli(capsys, "send", ACCOUNT, "--text", "こんにちは", "--production", "--json")
    payload = json.loads(out)
    assert payload["error"] == code and payload["action"] == "guard"
    assert _three(payload) == expected
    assert payload["next_at"] == refused.next_at
    assert err.splitlines()[0] == line


REPLY_DAILY = {
    "why": ("今日の返信が上限（reply.daily）に達しました。口座は止めていません。数は thth account set <口座> "
            "reply.daily <数> --by <名前> で変えられます"),
    "better": "投稿は返信とは別の枠なので、今日のうちに伝えたいことは返信でない 1 本にまとめると、会話を止めずに届けられます",
    "fix": {
        "cli": "返信は明日 0:00 以降にもう一度送ってください。数を上げるなら持ち主が thth account set alpha reply.daily <数> --by <名前>",
        "remote": "返信は明日 0:00 以降にもう一度送ってください（数を上げるのは持ち主が https://thth.me/activity で）",
        "mcp": "返信は明日 0:00 以降にもう一度送ってください（数を上げるのは持ち主が https://thth.me/activity で）",
    },
}


def test_返信の日の上限は返信を勧めない(tmp_path, monkeypatch, capsys):
    """reply_daily の daily_limit は、なぜ・通すには・もっと良くとも返信の枠の文（投稿の枠の「返信に回す」を言わない）。"""
    for door in ("cli", "remote", "mcp"):
        found = refusal.parts("daily_limit", account=ACCOUNT, door=door, reason="reply_daily", next_at=NEXT_AT)
        assert found["why"] == REPLY_DAILY["why"], door
        assert found["fix"] == REPLY_DAILY["fix"][door], door
        assert found["better"] == REPLY_DAILY["better"], door
        assert "返信に回す" not in json.dumps(found, ensure_ascii=False)
    _local_send(monkeypatch, tmp_path, "daily_limit", reason="reply_daily", next_at=NEXT_AT)
    rc, out, err = _cli(capsys, "send", ACCOUNT, "--text", "こんにちは", "--production", "--reply-to", "17999")
    assert rc == 1 and "返信に回す" not in out
    assert f"{refusal.FIX}{REPLY_DAILY['fix']['cli']}" in out.splitlines()
    assert f"{refusal.BETTER}{REPLY_DAILY['better']}" in out.splitlines()


# --------------------------------------------------------------------------
# 通す道を打つ: 示した命令が、その口で断られない
# --------------------------------------------------------------------------

def test_遠くの道の予約の命令は通る(remote_cli, capsys, tmp_path):
    """too_soon・daily_limit の remote の通す道（thth schedule <口座> --text-file … --at … --production）。"""
    body = tmp_path / "body.txt"
    body.write_text("次の時刻に出す本文", encoding="utf-8")
    remote_cli.push(200, {"draft_id": "d" * 64, "account": ACCOUNT, "status": "draft", "revision": "r" * 64})
    remote_cli.push(200, {"account": ACCOUNT, "draft_id": "d" * 64, "status": "approved",
                          "publish_at": NEXT_AT, "via": "api"})
    argv = _SCHEDULE.replace("<ファイル>", str(body)).split()[1:]
    rc, out, err = _cli(capsys, *argv)
    assert rc == 0, err
    assert [c["path"] for c in remote_cli.calls] == ["/api/v1/draft_put", "/api/v1/schedule_request"]
    assert remote_cli.calls[0]["body"]["publish_at"] == NEXT_AT


def test_MCPの予約の道具は通る(env, remote, monkeypatch):
    """too_soon・daily_limit の mcp の通す道（thth_draft_put に publish_at → thth_schedule_request）。"""
    module = _mcp(env, monkeypatch)
    put = module.server_call("thth_draft_put", {"account": ACCOUNT, "body": "次の時刻に出す本文", "publish_at": NEXT_AT})
    assert not put.get("isError"), put
    draft_id = json.loads(put["content"][0]["text"])["draft_id"]
    scheduled = module.server_call("thth_schedule_request", {"account": ACCOUNT, "draft_id": draft_id})
    assert not scheduled.get("isError"), scheduled
    assert json.loads(scheduled["content"][0]["text"])["status"] == "approved"


def test_手元の台帳の通す道は通る(isolated_account_factory, capsys):
    """手元の CLI の通す道: 承認（1 段目は断りでない）・数を上げる・止めた口座を戻す。"""
    from tests.conftest import run_thth, write_queue_file
    account = isolated_account_factory(production=True)
    name = account["name"]
    path = write_queue_file(account["queue_dir"], "next.md",
                            fm_overrides={"status": "draft", "publish_at": NEXT_AT, "approved_sha": None})
    first = run_thth(["approve", path, "--by", "masaru"])
    assert first.returncode == 1 and "digest: " in first.stdout, first.stdout + first.stderr
    assert "report_channel:" not in first.stderr  # 二段確認の 1 段目は断りに数えない
    assert cli.main(["account", "set", name, "daily_max_posts", "5", "--by", "masaru"]) == 0
    assert cli.main(["account", "set", name, "reply.daily", "5", "--by", "masaru"]) == 0
    guard.stop(name, "burst")
    assert cli.main(["account", "resume", name, "--by", "masaru"]) == 0
    assert guard.stopped(name) is None
    capsys.readouterr()


def test_手元の台帳では予約を刻む形は断られる(tmp_path, monkeypatch, capsys):
    """手元の CLI に `thth schedule … --at` を示さない理由（remote_only）。"""
    monkeypatch.setenv("THTH_REMOTE_CONFIG", str(tmp_path / "none.json"))
    rc = cli.main(["schedule", ACCOUNT, "--text", "x", "--at", NEXT_AT, "--production"])
    assert rc == 2 and capsys.readouterr().err.startswith("remote_only")


# --------------------------------------------------------------------------
# 寄せた口で、目録にある符丁の 3 つの行が落ちたら赤（MCP・遠くの道）
# --------------------------------------------------------------------------

def _lined(code):
    entry = refusal_catalog.CATALOG[code]
    return entry["fix"]["text"] and code not in refusal.VIEW_ONLY and not entry["retired"]


def test_MCPのサーバ型の断りは目録にある符丁ならどれも3つの行を持つ(env, monkeypatch):
    from thth.server_writes import SAFE_ERRORS
    module = _mcp(env, monkeypatch)
    codes = sorted(code for code in SAFE_ERRORS if code in refusal_catalog.CATALOG and _lined(code)
                   and code not in ("invalid_draft",))
    assert len(codes) >= 20
    missing = []
    for code in codes:
        _raise(monkeypatch, code, {})
        result = module.server_call("thth_draft_put", {"account": ACCOUNT, "body": "本文",
                                                       "publish_at": "2030-01-01T12:00:00+09:00"})
        rows = result["content"][0]["text"].splitlines()
        entry = refusal_catalog.CATALOG[code]
        if not any(row.startswith(refusal.FIX) and row != refusal.FIX for row in rows):
            missing.append(f"{code}: 通すには")
        if entry["better"] and not any(row.startswith(refusal.BETTER) for row in rows):
            missing.append(f"{code}: もっと良く")
        if not (any(row.startswith(refusal.WHY) for row in rows) or refusal.says(rows[0], entry["why"])):
            missing.append(f"{code}: なぜ")
    assert missing == []


def test_遠くの道の断りは目録にある符丁ならどれも3つの行を持つ(remote_cli, capsys):
    from thth import remote as remote_mod
    codes = [code for code in remote_mod.NEXT if code in refusal_catalog.CATALOG and _lined(code)]
    assert len(codes) >= 15
    missing = []
    for code in codes:
        remote_cli.push(200, {"error": code})
        rc, out, err = _cli(capsys, "send", ACCOUNT, "--text", "こんにちは")
        rows = err.splitlines()
        if rows[0].split("（")[0].split(":")[0] != code:
            missing.append(f"{code}: 1 行目 {rows[0]}")
        if not any(row.startswith(refusal.FIX) for row in rows):
            missing.append(f"{code}: 通すには")
        if refusal_catalog.CATALOG[code]["better"] and not any(row.startswith(refusal.BETTER) for row in rows):
            missing.append(f"{code}: もっと良く")
    assert missing == []


def test_遠くの道の入口の断りは遠くの道の通すにはを出す(monkeypatch, capsys):
    """`remote._fail`（login まわり）は口 `remote` で出す（監査 5.12.0「出したあとでよい」3）。"""
    from thth import remote as remote_mod
    entry = refusal_catalog.CATALOG["key_in_argument"]
    monkeypatch.setitem(entry, "doors", ("cli", "remote"))
    monkeypatch.setitem(entry, "fix_by_door", {"remote": {"text": "遠くの道の文", "command": None},
                                               "cli": {"text": "手元の文", "command": None}})
    assert remote_mod._fail("key_in_argument", "x") == 2
    rows = capsys.readouterr().err.splitlines()
    assert rows[0] == "key_in_argument: x"
    assert f"{refusal.FIX}遠くの道の文" in rows and not any("手元の文" in row for row in rows)


def test_read_coordinationの断りは3つの行を持つ(tmp_path, monkeypatch, capsys):
    """`conftest.is_refusal` は寄せていない口のために間が空でも通す。寄せた口は `with_tail` で 3 つの行まで見る。"""
    from tests.conftest import is_refusal
    from thth import read_coordination

    class Args:
        json = False
    rc = read_coordination.refusal(Args(), "account_leaving")
    err = capsys.readouterr().err + 'report_channel: thth report file <account> --title "account_leaving で断られた"\n'
    assert rc == 2 and is_refusal(err, "account_leaving", with_tail=True)
    assert not is_refusal("account_leaving\n" + err.splitlines()[-1], "account_leaving", with_tail=True)


# --------------------------------------------------------------------------
# clicks_unavailable: 欄の「言えない」（口は欄そのもの）
# --------------------------------------------------------------------------

CLICKS = {"why": "クリックを言えない",
          "fix": "この媒体の投稿ごとのクリックは THTH から採れないので、リンクに UTM を付けてリンク先の計測で数えてください",
          "better": "どの媒体のリンクも同じ UTM の付け方にそろえておくと、媒体を問わず比べられます"}


def _clicks_block():
    from thth import thread_clicks
    rows = [{"file": "a.md", "account": ACCOUNT, "medium": thread_clicks.MEDIUM, "thread": "t1",
             "posted_at": "2026-11-01T10:00:00+09:00", "clicks_72h": None, "window_days": 3,
             "cannot_say": "clicks_unavailable"}]
    return thread_clicks.summarize(rows)


def test_clicks_unavailableは欄の文とjsonとダッシュボードで同じ3つ():
    from thth import dashboard_cli, scenario_cli
    block = _clicks_block()
    # --json（scenario show の threads_clicks）: 対応表で同じ 3 つ。今の鍵は残る。
    assert {key: block[key]["clicks_unavailable"] for key in ("why", "fix", "better")} == CLICKS
    assert block["reasons"] == {"clicks_unavailable": 1}
    # 会話のダッシュボード（dashboard campaign --json の outcomes.threads_clicks）: 同じ写し。
    cleaned = dashboard_cli._clean(block)
    assert {key: cleaned[key]["clicks_unavailable"] for key in ("why", "fix", "better")} == CLICKS
    # 文: 「言えない本数」の行は今のまま、続けて 3 つ。
    lines = scenario_cli.clicks_lines(block)
    assert lines[1] == "   言えない本数: clicks_unavailable 1"
    assert f"   clicks_unavailable: {CLICKS['why']}" in lines
    assert f"     {refusal.FIX}{CLICKS['fix']}" in lines
    assert f"     {refusal.BETTER}{CLICKS['better']}" in lines


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
    bad = refusal.parts("too_soon", account="../etc", next_at="明日の朝 /etc/passwd", reason="本文", door="remote")
    assert "passwd" not in json.dumps(bad, ensure_ascii=False)
    assert "<時刻>" in bad["fix"] and "<口座>" in bad["fix"]
    good = refusal.parts("too_soon", next_at=NEXT_AT, door="remote")
    assert NEXT_AT in good["fix"]
    # 安全装置の詳細は決まった語だけ（ほかの語はなぜを変えない）。
    assert refusal.parts("account_stopped", reason="自由文")["why"] == refusal_catalog.CATALOG["account_stopped"]["why"]


@pytest.mark.parametrize("value", [
    "2030-01-01",                      # 日付だけ
    "2030-01-01T09:00:00",             # タイムゾーン無し
    "2030-01-01T09:00",                # タイムゾーン無し
    "2030-13-45T99:00:00+09:00",       # 存在しない日付・時刻
    "2030-02-30T09:00:00+09:00",       # 存在しない日付
    "2030-01-01 09:00:00+09:00",       # 空白区切り（--at の引数が 2 つに割れる）
    "2030-01-01T09:00:00+0900",        # 区切りの無いタイムゾーン
    "2030-01-01T09:00:00+09:00 rm",    # 後ろに語
])
def test_時刻の穴は日時とタイムゾーンのそろった実在する時刻だけ(value):
    found = refusal.parts("too_soon", next_at=value, door="remote")
    assert value not in found["fix"] and "<時刻>" in found["fix"]


@pytest.mark.parametrize("value", ["2030-01-01T09:00:00+09:00", "2030-01-01T09:00+09:00", "2030-01-01T00:00:00Z",
                                   "2030-01-01T09:00:00.123456+09:00"])
def test_時刻の穴はそろった時刻を通す(value):
    assert value in refusal.parts("too_soon", next_at=value, door="remote")["fix"]


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


def test_口ごとの文の命令は実在する():
    """目録の `fix_by_door`・`by_detail` の命令（CLI の口）が argparse に実在する（`test_fixの命令は実在する` と同じ確かめ）。"""
    from tests.command_helpers import command_problem
    parser = cli.build_parser()
    wrong = []
    for code, entry in refusal_catalog.CATALOG.items():
        doors = [entry.get("fix_by_door") or {}] + [d.get("fix_by_door") or {} for d in (entry.get("by_detail") or {}).values()]
        commands = [d.get("command") for table in doors for door, d in table.items() if door in ("cli", "remote")]
        commands += [d.get("command") for d in (entry.get("by_detail") or {}).values()]
        for command in filter(None, commands):
            problem = command_problem(parser, command)
            if problem is not None:
                wrong.append(f"{code}: `{command}` — {problem}")
        for door, d in (entry.get("fix_by_door") or {}).items():
            if door == "mcp" and d.get("command"):
                wrong.append(f"{code}: MCP の口に CLI の命令")
    assert wrong == []


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
    assert rc == 0 and not set(refusal.JSON_KEYS) & set(json.loads(out))


def test_時刻の穴と今の時刻():
    # 安全装置の next_at（jst.iso の形）は時刻の穴の検査を通る。
    value = jst.iso(datetime.datetime(2030, 1, 1, 9, 0, tzinfo=jst.JST))
    assert value in refusal.parts("too_soon", next_at=value, door="remote")["fix"]
