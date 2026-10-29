"""Meta の計画の段 0（下ごしらえ・10/29）: FB の穴を塞いだことの柵。

正本は `docs/設計_MetaAPIを使い倒す計画_2026-10-29.md` の「段 0」。媒体の表の一致は
`tests/test_media_registry.py`。ここは残り（断り文のページ名・ページ id の案内・文字数の線・
データに触れる期限・認可のログ）を縛る。偽の Graph API（loopback）と試験の中で作った値だけを使う。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from thth import account_cli, auth_log, cli, dashboard_cli, events_cli, jst, lint, maintain, queuefile
from thth.adapters import base
from thth.adapters import facebook as fb
from tests.test_facebook_adapter import adapter as fb_adapter
from tests.test_facebook_adapter import fake_graph
from tests.test_facebook_auth import PAGE, V, env, run, saved, write_token  # noqa: F401 — env は fixture

ROOT = Path(__file__).resolve().parents[1]
WRONG_EXAMPLE_ID = "61596075596464"   # 10/29 に手順書の例に載っていた URL の id（人の画面の id）
RIGHT_EXAMPLE_ID = "1361492233719922"  # こぴちゃのページの透明性の「ページID」


def _days(n):
    return int((jst.now_jst() + datetime.timedelta(days=n)).timestamp())


# ------------------------------------------------------------------ 1. 断り文にページ名

def test_認可で台帳のページが許可に無ければ名前つきで並べる(env):
    env["behavior"]["accounts"] = (200, {"data": [
        {"id": "111", "name": "Kopi+cha（コピチャ）", "access_token": env["other_token"]},
        {"id": "222", "name": "NIGAMI LAB", "access_token": env["other_token"]}]})
    assert run(env) == 2 and saved(env) is None
    text = "\n".join(env["lines"])
    assert "許可されたページ: Kopi+cha（コピチャ） 111・NIGAMI LAB 222" in text
    assert f"page_id {PAGE} は許可されたページに無い" in text
    assert "ページの透明性" in text and env["other_token"] not in text


def test_認可の断り文は名前の無いページでも落ちない(env):
    env["behavior"]["accounts"] = (200, {"data": [
        {"id": "111", "access_token": env["other_token"]},
        {"id": "222", "name": None, "access_token": env["other_token"]},
        {"id": "333", "name": "\n\t", "access_token": env["other_token"]},
        {"id": 444, "name": "id が数", "access_token": env["other_token"]}]})
    assert run(env) == 2 and saved(env) is None
    text = "\n".join(env["lines"])
    assert "facebook_page_not_granted" in text and "許可されたページ: 111・222・333" in text


def test_page_labelは名前の制御文字を畳み長い名前を切る():
    assert fb.page_label("こぴちゃ", "1") == "こぴちゃ 1"
    assert fb.page_label(None, "1") == "1" and fb.page_label("  ", "1") == "1"
    assert fb.page_label("a\nb", "1") == "a b 1"
    long = fb.page_label("x" * 200, "1")
    assert long.endswith("… 1") and len(long) < 100


def test_組み立てで台帳と鍵のページが違えば鍵のページ名を言う(monkeypatch):
    monkeypatch.setenv(fb.BASE_URL_ENV, "http://127.0.0.1:9")
    with pytest.raises(ValueError, match="facebook_page_mismatch") as caught:
        fb.FacebookAdapter.from_account({"media": "facebook", "page_id": PAGE},
                                        {"access_token": "x", "user_id": "999", "page_name": "別のページ"})
    assert "別のページ 999" in str(caught.value) and PAGE in str(caught.value)
    with pytest.raises(ValueError, match="facebook_page_mismatch"):
        fb.FacebookAdapter.from_account({"media": "facebook", "page_id": PAGE},
                                        {"access_token": "x", "user_id": "999"})


def test_whoamiの取り違えは鍵のページ名を言う():
    with fake_graph({("GET", f"{V}/me"): (200, {"id": "999", "name": "別のページ"})}) as (url, _):
        with pytest.raises(base.AdapterError, match="facebook_page_mismatch") as caught:
            fb_adapter(url).whoami()
    assert "別のページ 999" in str(caught.value) and PAGE in str(caught.value)


# ------------------------------------------------------------------ 2. ページ id の案内

def _add_args(**kw):
    value = dict(name="kopicha-fb", by="tester", media="facebook", project="kopicha", handle=None,
                 instance=None, repo_dir=None, redirect_uri=None, force=False, json=False)
    value.update(kw)
    return SimpleNamespace(**value)


@pytest.mark.parametrize("handle", [None, "https://www.facebook.com/profile.php?id=1", "abc"])
def test_account_addの断り文は透明性のページIDを案内し正しい例を出す(handle, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("THTH_ROOT", str(tmp_path))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(tmp_path / "accounts"))
    assert account_cli.cmd_add(_add_args(handle=handle)) == 2
    err = capsys.readouterr().err
    assert "ページの透明性" in err and "profile.php?id=" in err
    assert RIGHT_EXAMPLE_ID in err and WRONG_EXAMPLE_ID not in err
    assert not (tmp_path / "accounts" / "kopicha-fb.json").exists()


def test_account_addのhelpにページIDの引き方がある(capsys):
    with pytest.raises(SystemExit):
        cli.main(["account", "--help"])
    out = capsys.readouterr().out
    assert "ページの透明性" in out and "profile.php?id=" in out


def test_道具の中に間違った例のidが残っていない():
    """10/29: 例の文が URL の id のままだった（コード 2 か所・文書 2 つ）。コードには 1 つも残さない。"""
    hits = [str(p.relative_to(ROOT)) for p in sorted((ROOT / "thth").rglob("*.py"))
            if WRONG_EXAMPLE_ID in p.read_text(encoding="utf-8")]
    hits += [name for name in ("mcp/server.py", "skills/thth/SKILL.md", "docs/リリースノート_5.1.0_2026-10-29.md")
             if WRONG_EXAMPLE_ID in (ROOT / name).read_text(encoding="utf-8")]
    assert hits == []


def test_台帳にpage_idが無い断り文も正しい例を出す(monkeypatch):
    monkeypatch.setenv(fb.BASE_URL_ENV, "http://127.0.0.1:9")
    with pytest.raises(ValueError) as caught:
        fb.FacebookAdapter.from_account({"media": "facebook"}, {"access_token": "x"})
    assert RIGHT_EXAMPLE_ID in str(caught.value) and "ページの透明性" in str(caught.value)


# ------------------------------------------------------------------ 3. 文字数の線

def test_facebookの文字数の上限はTHTHの線の5000で台帳で上げられる():
    assert queuefile.limit_for("facebook") == 5000
    assert queuefile.limit_for("facebook", {"char_limit": 63206}) == 63206
    note = queuefile.limit_note("facebook")
    assert "THTH の線" in note and "char_limit" in note
    assert queuefile.limit_note("facebook", {"char_limit": 63206}) == ""
    assert queuefile.limit_note("threads") == ""


def _manuscript(tmp_path, n):
    path = tmp_path / f"long-{n}.md"
    path.write_text("---\nthth: 1\naccount: alpha\nstatus: draft\npublish_at: 2026-11-01T09:00:00+09:00\n"
                    "---\n\n## facebook\n\n" + "あ" * n + "\n", encoding="utf-8")
    return str(path)


def test_lintは5000字を超えたfacebookの原稿に上げ方を添えて断る(env, tmp_path):
    text = "\n".join(lint.lint_file(_manuscript(tmp_path, 5001)))
    assert "facebook は 5000 字以内（5001 字）" in text and "THTH の線" in text and "char_limit" in text
    assert "length:" not in "\n".join(lint.lint_file(_manuscript(tmp_path, 5000)))


# ------------------------------------------------------------------ 6. データに触れる期限

def _inspect(env, value):
    write_token(env, data_access_expires_at=value)
    return maintain.inspect("alpha", now=jst.now_jst())


def test_maintainはデータの期限が10日後ならDATA_ACCESS_EXPIRING(env):
    row = _inspect(env, _days(10))
    assert row["state"] == maintain.DATA_ACCESS_EXPIRING and maintain.needs_attention(row)
    assert 9 < row["data_access_remaining_days"] <= 10 and row["no_expiry"] is True
    assert "thth auth alpha --by <名前>" in row["message"]


def test_maintainはデータの期限が1日前ならDATA_ACCESS_EXPIRED(env):
    row = _inspect(env, _days(-1))
    assert row["state"] == maintain.DATA_ACCESS_EXPIRED and maintain.needs_attention(row)


def test_maintainはデータの期限に余裕があればokで残りを書く(env):
    row = _inspect(env, _days(60))
    assert row["state"] == maintain.OK and not maintain.needs_attention(row)
    assert 59 < row["data_access_remaining_days"] <= 60


@pytest.mark.parametrize("value", [None, 0, True, "1800000000"])
def test_maintainはデータの期限が無い_0_形違いなら今までどおりok(env, value):
    row = _inspect(env, value)
    assert row["state"] == maintain.OK and "data_access_remaining_days" not in row


def test_maintainの画面にデータの期限が出て終了コードが1(env):
    write_token(env, data_access_expires_at=_days(10))
    lines = []
    assert maintain.run_maintain("alpha", check=True, log=lines.append) == 1
    text = "\n".join(lines)
    assert "data_access_expiring" in text and "データの期限 残り" in text and env["page_token"] not in text


def test_eventsとダッシュボードの要対応にデータの期限が載る(env):
    write_token(env, data_access_expires_at=_days(10))
    payload = events_cli.build("alpha")
    rows = [r for r in payload["events"] if r["kind"] == "token_data_access_expiring"]
    assert len(rows) == 1 and rows[0]["ongoing"] is True
    assert rows[0]["state"] == maintain.DATA_ACCESS_EXPIRING and "thth auth alpha" in rows[0]["summary"]
    assert "token_data_access_expiring" in events_cli.KINDS
    assert "token_data_access_expiring" in dashboard_cli.KIND_JA
    assert env["page_token"] not in json.dumps(payload, ensure_ascii=False)
    write_token(env, data_access_expires_at=_days(60))
    assert not [r for r in events_cli.build("alpha")["events"] if r["kind"] == "token_data_access_expiring"]


def test_doctorのprobeは今のデータの期限を言い_記録と違えばそう言う(env):
    env["routes"][("GET", V + "/debug_token")] = (200, {"data": {"scopes": list(fb.SCOPES), "expires_at": 0,
                                                              "data_access_expires_at": _days(10)}})
    env["routes"][("GET", V + f"/{PAGE}/posts")] = (200, {"data": []})
    write_token(env, data_access_expires_at=_days(80))
    from thth import doctor
    report = doctor.diagnose("alpha")
    row = next(p for p in report["probes"] if p["key"] == fb.FacebookAdapter.DATA_ACCESS_KEY)
    assert row["ok"] is True and "14 日を切りました" in row["detail"] and ".token の記録と違います" in row["detail"]
    # doctor は .token を書き直さない（読むだけの道具・認可の世代を変えない）。
    assert abs(json.loads(Path(env["cfg"]["token"]).read_text())["data_access_expires_at"] - _days(80)) <= 2
    env["routes"][("GET", V + "/debug_token")] = (200, {"data": {"scopes": list(fb.SCOPES), "expires_at": 0,
                                                              "data_access_expires_at": _days(-1)}})
    row = next(p for p in doctor.diagnose("alpha")["probes"] if p["key"] == fb.FacebookAdapter.DATA_ACCESS_KEY)
    assert row["ok"] is False and "過ぎています" in row["detail"]


# ------------------------------------------------------------------ 10. 認可のログ

def test_authは既定のログ先に画面と同じ行を0600で書く(env, capsys):
    assert cli.main(["auth", "alpha", "--by", "operator", "--code", "", ]) in (0, 2)
    err = capsys.readouterr().err
    found = re.search(r"ログ: (\S+)", err)
    assert found, err
    path = Path(found.group(1))
    assert path.parent == Path(os.environ["THTH_ROOT"]) / "state" / "_auth"
    assert re.fullmatch(r"alpha-\d{8}-\d{6}\.log", path.name)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    body = path.read_text(encoding="utf-8")
    assert body.strip()
    for value in (env["app_secret"], env["page_token"], env["long"], env["short"]):
        assert value not in body


def test_authのrehearseはログのファイルを作らない(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("THTH_ROOT", str(tmp_path))
    with auth_log.open_for("alpha", write=False) as tee:
        tee.log("行")
    assert not (tmp_path / "state").exists()
    assert "ログ:" not in capsys.readouterr().err


def test_ログを開けなくても画面だけで続ける(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "state"
    blocker.write_text("ファイルなので dir を作れない")
    monkeypatch.setenv("THTH_ROOT", str(tmp_path))
    with auth_log.open_for("alpha") as tee:
        tee.log("続ける")
    captured = capsys.readouterr()
    assert "続ける" in captured.out and "ログを書けません" in captured.err
