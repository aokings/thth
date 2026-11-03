"""5.11.0 段 7（設計 効果測定と連動 §7.1 の 5・8・§7.2「置く前に見る」「訪問が来ない間」）。

見るのは（柵）:
  - `thth plaza post … --preview`: 範囲を問わず中身を全部作って見せ、**台帳に何も書かない**
    （広場の件数・ファイルの mtime・`.lock`・変更ログが変わらない。置き場が無ければ作らない）。
    重複と上限はロックを取らずに確かめる。open の二段確認は今までどおり。MCP の `preview`。
  - 置いた本人が自分の 1 件を隠す口（`thth plaza hide <id> --as <account>`）: 他の持ち主の id は
    `plaza_not_found`・隠し方は管理者と同じ・変更ログに残る。管理者が戻す口
    （`thth admin plaza unhide <id> --reason --by`・変更ログ `plaza_unhidden`）。
  - 監査のあとの直し: preview は `.lock` があってもロックを取らない（`--from`・宣言つきでも）・
    伏せる登録は X の refresh を走らせない・近所の照合は account の Threads 原稿すべて・
    `referrers` が list でなければ言えない・IPv4 は捨てる。
  - Threads の投稿ごとのクリック: 糸の show・dashboard campaign・seeds・plaza の `--from scenario` に、
    GA4 の訪問とは別の欄で。分母（言える本数／糸の Threads の本数）・Threads 以外は null と
    `per_post_clicks_unavailable:<媒体>`。
  - `referrers` を project の dashboard に host と数だけ（username・本文・URL の path が入らない）。
  - seeds の返信の列の `nonzero` と `replies_source: ledger`。
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from tests.test_after_cli import _write_ndjson
from tests.test_v340_plaza_open import join
from tests.test_v340_plaza_store import BODY, SCOPE, owners, post, viewer  # noqa: F401  (fixture)
from tests.test_v500_scenario import NOW, PROJECT, _sns, _write, kp  # noqa: F401
from thth import accounts as accounts_mod
from thth import after_cli, cli, dashboard_cli, jst, plaza, plaza_from, plaza_moments
from thth import scenario, scenario_seeds, site_visits
from thth import sent as sent_mod


def _tree(*parts):
    """`state/<parts>` の下の全ファイル（名前・mtime・大きさ）。無ければ None。"""
    root = os.path.join(os.environ["THTH_ROOT"], "state", *parts)
    if not os.path.lexists(root):
        return None
    out = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            info = os.stat(path)
            out[os.path.relpath(path, root)] = (info.st_mtime_ns, info.st_size)
    return out


def _snapshot():
    return {"plaza": _tree("_plaza"), "admin": _tree("_admin"), "n": len(plaza.load_all()[0])}


# ================================================================== 置く前に見る

def test_previewは何も書かない_置き場が無ければ作らない(owners):
    assert _tree("_plaza") is None
    before = _snapshot()
    result = plaza.post("kopicha-threads", kind="finding", title="冒頭を問いにする", body=BODY,
                        by="s", scope_note=SCOPE, preview=True)
    assert result["report_type"] == "plaza_post_preview" and result["posted"] is False
    assert result["plaza_id"] is None and result["scope"] == "project"
    assert result["view"]["body"] == BODY and result["view"]["scope_note"] == SCOPE
    assert result["digest"] is None
    assert _snapshot() == before
    assert _tree("_plaza") is None, "置き場も .lock も作らない"


def test_previewは台帳に何も書かない_件数とmtimeとlockが変わらない(owners):
    post()   # 置き場と .lock がある状態
    assert ".lock" in _tree("_plaza")
    before = _snapshot()
    for visibility in (None, "project"):
        result = plaza.post("kopicha-threads", kind="question", title="朝と夜のどちらが", body="どちら？",
                            by="s", scope_note=SCOPE, visibility=visibility, preview=True)
        assert result["report_type"] == "plaza_post_preview"
    assert _snapshot() == before


def _no_lock(monkeypatch):
    """`STORE.locked` が呼ばれたら赤（監査 段7 の 1-a: preview はロックを取らない）。"""
    def locked(*_args, **_kwargs):
        raise AssertionError("preview が STORE.locked を呼んだ")
    monkeypatch.setattr(plaza.STORE, "locked", locked)


def test_previewは_lockが既にあってもロックを取らない(owners, monkeypatch):
    post()   # 置き場と .lock がある状態
    join("kopicha", "other")
    assert ".lock" in _tree("_plaza")
    before = _snapshot()
    _no_lock(monkeypatch)
    for visibility in (None, "project", "open"):
        result = plaza.post("kopicha-threads", kind="question", title="朝と夜のどちらが", body="どちら？",
                            by="s", scope_note=SCOPE, visibility=visibility, preview=True)
        assert result["report_type"] == "plaza_post_preview" and result["posted"] is False
    assert _snapshot() == before


def test_from_scenarioとfrom_afterとdeclaration付きのpreviewも何も書かない(clicks_world, monkeypatch):
    monkeypatch.setattr(after_cli, "reaction_lookup", lambda account, **kw: lambda post_id: {"covered": False})
    before = _snapshot()
    _no_lock(monkeypatch)
    decl = {"schema_version": 1, "id": "d-preview", "account": "kp-threads", "hypothesis": "h",
            "change": "c", "decision": {"status": "proposed"},
            "baseline_post_ids": [], "changed_post_ids": []}
    shapes = (
        {"kind": "measure", "from_source": ("scenario", PROJECT, "clk-01")},
        {"kind": "finding", "from_source": ("after", "kp-threads", 7)},
        {"kind": "measure", "declarations": [decl], "how": "thth study-report d.json"},
    )
    for shape in shapes:
        result = plaza.post("kp-threads", title="下見の見本", body="人の解釈", by="kp-session",
                            scope_note="煎茶の糸", preview=True, **shape)
        assert result["report_type"] == "plaza_post_preview" and result["posted"] is False, shape
        assert result["plaza_id"] is None
    assert _snapshot() == before
    assert _tree("_plaza") is None, "置き場も .lock も作らない"


def test_previewは重複と上限をロックなしで確かめる(owners):
    first = post()
    before = _snapshot()
    with pytest.raises(plaza.PlazaError, match="^duplicate_post$") as caught:
        post(preview=True)
    assert caught.value.plaza_id == first["plaza_id"]
    assert _snapshot() == before


def test_previewは組の既定でownerと言う(owners):
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    before = _snapshot()
    result = post(preview=True)
    assert result["scope"] == "owner" and result["visibility_default"] == "masaru"
    assert _snapshot() == before


def test_openのpreviewは一段目と同じ中身で書かない_二段確認は今までどおり(owners):
    join("kopicha", "other")
    before = _snapshot()
    preview = post(visibility="open", preview=True, confirm=None)
    assert preview["report_type"] == "plaza_post_preview" and preview["scope"] == "open"
    first = plaza.post("kopicha-threads", kind="finding", title="冒頭を問いにする", body=BODY,
                       by="kopicha-session", scope_note=SCOPE, visibility="open")
    assert first["report_type"] == "plaza_open_preview" and first["opened"] is False
    assert preview["digest"] == first["digest"]
    assert preview["visible_to_other_owners"] == first["visible_to_other_owners"]
    assert _snapshot() == before
    # digest 違いは断る・同じ digest の 2 回目で置く（今までどおり）。
    with pytest.raises(plaza.PlazaError, match="^open_digest_mismatch$"):
        plaza.post("kopicha-threads", kind="finding", title="冒頭を問いにする", body=BODY,
                   by="kopicha-session", scope_note=SCOPE, visibility="open", confirm="0" * 20)
    done = plaza.post("kopicha-threads", kind="finding", title="冒頭を問いにする", body=BODY,
                      by="kopicha-session", scope_note=SCOPE, visibility="open", confirm=first["digest"])
    assert done["report_type"] == "plaza_posted" and done["scope"] == "open"
    assert len(plaza.load_all()[0]) == 1


def test_CLIのpreviewは一段目の道_rc1で何も書かない(owners, tmp_path, capsys):
    body = tmp_path / "body.md"
    body.write_text(BODY, encoding="utf-8")
    args = ["plaza", "post", "kopicha-threads", "--kind", "finding", "--title", "冒頭を問いにする",
            "--body-file", str(body), "--scope", SCOPE, "--by", "s"]
    before = _snapshot()
    assert cli.main(args + ["--preview"]) == 1
    out = capsys.readouterr().out
    assert "下見: まだ置いていません" in out and BODY.splitlines()[0] in out
    assert "--preview を外して打ってください" in out
    assert cli.main(args + ["--preview", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["report_type"] == "plaza_post_preview" and payload["view"]["title"] == "冒頭を問いにする"
    assert _snapshot() == before
    assert cli.main(args) == 0
    assert len(plaza.load_all()[0]) == 1


def test_MCPのpreview(tmp_path, monkeypatch):
    from tests.test_v340_plaza_mcp import _post, _tenant, _text
    _root, server = _tenant(tmp_path, monkeypatch)
    before = _snapshot()
    result = _post(server, preview=True)
    assert not result.get("isError"), result
    payload = json.loads(_text(result))
    assert payload["report_type"] == "plaza_post_preview" and payload["posted"] is False
    assert payload["view"]["title"] == "朝は伸びる"
    assert _snapshot() == before
    assert _post(server, preview="yes").get("isError")
    posted = json.loads(_text(_post(server)))
    assert posted["report_type"] == "plaza_posted"


def test_observeの置く命令にまずpreview(monkeypatch):
    step = {"kind": "plaza", "candidate": "post_week", "account": "a", "command": "thth plaza post a --kind finding"}
    monkeypatch.setattr(plaza_moments, "_week_step", lambda name, since, now: dict(step))
    monkeypatch.setattr(plaza_moments, "_scenario_steps", lambda configs, records, now: [])
    steps = plaza_moments.trigger_steps({"a": {"project": "p", "media": "threads"}}, since=NOW, now=NOW)
    assert steps[0]["preview_command"] == "thth plaza post a --kind finding --preview"


# ================================================================== 置いた本人が隠す

def test_自分の1件を隠す_他のprojectのidは断る(owners, capsys):
    mine = post()
    theirs = post("other-threads", title="別の持ち主の書き込み")
    before_theirs = json.dumps(plaza.STORE.get(theirs["plaza_id"]), sort_keys=True)
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.hide(theirs["plaza_id"], by="s", reason="間違えた", account="kopicha-threads")
    assert json.dumps(plaza.STORE.get(theirs["plaza_id"]), sort_keys=True) == before_theirs
    # CLI も同じ断り（あるかどうかを言わない）。
    assert cli.main(["plaza", "hide", theirs["plaza_id"], "--as", "kopicha-threads", "--reason", "x",
                     "--by", "s"]) == 2
    assert "plaza_not_found" in capsys.readouterr().err
    # 同じ project の別の account からでも、自分の持ち主の 1 件は隠せる。
    assert cli.main(["plaza", "hide", mine["plaza_id"], "--as", "kopicha-bsky", "--reason", "書き直す",
                     "--by", "s", "--json"]) == 0
    hidden = json.loads(capsys.readouterr().out)["hidden"]
    assert hidden["reason"] == "書き直す" and hidden["by"] == "s" and hidden["by_poster"] is True
    # 他の持ち主からは見えない・返信と更新は止まる（管理者の非表示と同じ）。
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.show(mine["plaza_id"], viewer("other-threads"))
    with pytest.raises(plaza.PlazaError, match="^already_hidden$"):
        plaza.hide(mine["plaza_id"], by="s", reason="もう一度", account="kopicha-threads")
    assert "置いた本人" in plaza.NEXT["plaza_hidden"] and "管理者が非表示にした" not in plaza.NEXT["plaza_hidden"]


def test_隠す口の変更ログ(owners):
    mine = post()
    plaza.hide(mine["plaza_id"], by="s", reason="書き直す", account="kopicha-threads")
    path = os.path.join(os.environ["THTH_ROOT"], "state", "_admin", "accounts.ndjson")
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    last = [r for r in rows if r["event"] == "plaza_hidden"][-1]
    assert last["account"] == "kopicha-threads" and last["diff"]["hidden"] == ["absent", "present"]
    assert "書き直す" not in json.dumps(last, ensure_ascii=False)


def _log_rows(event):
    path = os.path.join(os.environ["THTH_ROOT"], "state", "_admin", "accounts.ndjson")
    with open(path, encoding="utf-8") as f:
        return [r for r in (json.loads(line) for line in f if line.strip()) if r["event"] == event]


def test_管理者が非表示を戻す_本人の非表示も管理者の非表示も(owners, capsys):
    mine = post()
    theirs = post("other-threads", title="別の持ち主の書き込み")
    plaza.hide(mine["plaza_id"], by="s", reason="書き直す", account="kopicha-threads")
    plaza.hide(theirs["plaza_id"], by="operator", reason="確かめる")
    # 本人の非表示は show に「（置いた本人）」。
    assert cli.main(["admin", "plaza", "show", mine["plaza_id"]]) == 0
    assert "[非表示]（置いた本人）" in capsys.readouterr().out
    assert cli.main(["admin", "plaza", "show", theirs["plaza_id"]]) == 0
    shown = capsys.readouterr().out
    assert "[非表示] " in shown and "（置いた本人）" not in shown
    # 戻す（CLI・理由つき）。管理者の口なので、どの持ち主の 1 件でも戻せる。
    for pid in (mine["plaza_id"], theirs["plaza_id"]):
        assert cli.main(["admin", "plaza", "unhide", pid, "--reason", "直った", "--by", "operator",
                         "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["report_type"] == "plaza_unhidden" and payload["hidden"] is None
        record = plaza.STORE.get(pid)
        assert record["hidden"] is None
        assert record["unhidden"]["reason"] == "直った" and record["unhidden"]["by"] == "operator"
    # 戻すと他の持ち主の読み・返信・更新が元に戻る（隠す前と同じ）。
    assert plaza.show(mine["plaza_id"], viewer("kopicha-bsky"))["hidden"] is None
    rows = _log_rows("plaza_unhidden")
    assert len(rows) == 2 and all(r["diff"] == {"hidden": ["present", "absent"]} for r in rows)
    assert "直った" not in json.dumps(rows, ensure_ascii=False), "理由は変更ログに入れない"
    # 非表示でない 1 件・理由なし・by なしは断る（何も書かない）。
    with pytest.raises(plaza.PlazaError, match="^not_hidden$"):
        plaza.unhide(mine["plaza_id"], by="operator", reason="もう一度")
    with pytest.raises(plaza.PlazaError, match="^reason_required$"):
        plaza.unhide(mine["plaza_id"], by="operator", reason=" ")
    with pytest.raises(plaza.PlazaError, match="^plaza_not_found$"):
        plaza.unhide("p20261103-00000000", by="operator", reason="x")
    assert cli.main(["admin", "plaza", "unhide", mine["plaza_id"], "--reason", "x", "--by", "operator"]) == 2
    assert "not_hidden" in capsys.readouterr().err
    assert len(_log_rows("plaza_unhidden")) == 2
    # 案内の文は、戻す口を名指しする（戻す口が無いのに「管理者」と言わない）。
    for code in ("plaza_hidden", "already_hidden"):
        assert "thth admin plaza unhide" in plaza.NEXT[code]
    assert "not_hidden" in plaza.REASONS and plaza.NEXT["not_hidden"]


def test_unhideのhelpとhideのhelpが戻す口を言う(capsys):
    with pytest.raises(SystemExit):
        cli.main(["admin", "plaza", "unhide", "--help"])
    assert "--reason" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["plaza", "hide", "--help"])
    assert "thth admin plaza unhide" in capsys.readouterr().out.replace("\n", "")


def test_伏せる登録はXのrefreshを走らせない(monkeypatch):
    from thth import admin_log
    calls = []

    def load_token(cfg, *, refresh_x=True):
        calls.append(refresh_x)
        return {"access_token": "FAKE-REGISTER-ONLY"}
    monkeypatch.setattr(accounts_mod, "load_token", load_token)
    monkeypatch.setattr(accounts_mod, "_refresh_x_if_due",
                        lambda *a, **k: pytest.fail("登録のためだけに X の refresh を走らせた"))
    admin_log.register_account_secrets({"media": "x", "token": "/nonexistent"}, via="mcp")
    assert calls == [False]


def test_伏せる登録は本物のload_tokenで値を登録する_refreshなしで(isolated_account_factory, monkeypatch):
    """作り物でなく本物の `load_token(cfg, refresh_x=False)` を通して、値が伏せられること（登録が抜け落ちない）。"""
    import secrets
    from thth import admin_log, redact
    isolated_account_factory("reg-x", project="reg", media="x", handle="reg_x")
    cfg = accounts_mod.load_account("reg-x")
    value = secrets.token_urlsafe(30)
    os.makedirs(os.path.dirname(cfg["token"]), exist_ok=True)
    with open(cfg["token"], "w", encoding="utf-8") as f:
        json.dump({"access_token": value}, f)
    monkeypatch.setattr(accounts_mod, "_refresh_x_if_due",
                        lambda *a, **k: pytest.fail("登録のためだけに X の refresh を走らせた"))
    admin_log.register_account_secrets(cfg, via="mcp")
    assert value not in redact.redact(f"token は {value} です")


# ================================================================== Threads のクリック

THREAD = """---
thth_scenario: 1
kind: thread
id: clk-01
title: クリックの見本
project: kp
started: 2026-10-10
---
- promote: threads,x need=1
"""

LINK_A = "https://kp.example/a"
LINK_B = "https://kp.example/b"


def _insight(name, post_id, posted, *, replies=0, with_replies=True):
    cfg = accounts_mod.load_account(name)
    folder = accounts_mod.data_dirs(cfg, name)["insights_posts"]
    metrics = {"views": 100, "likes": 1, "reposts": 0}
    if with_replies:
        metrics["replies"] = replies
    _write_ndjson(os.path.join(folder, f"{post_id}.ndjson"), [{
        "account": name, "post_id": post_id, "posted_at": jst.iso(posted),
        "collected_at": jst.iso(posted + datetime.timedelta(hours=25)), "age_hours": 25, "marks": [24],
        "reply_to": None, "topic": None, "metrics": metrics}])


def _daily(name, rows):
    cfg = accounts_mod.load_account(name)
    folder = accounts_mod.data_dirs(cfg, name)["insights_account"]
    _write_ndjson(os.path.join(folder, f"{name}-2026-10.ndjson"),
                  [{"account": name, "date": day, "metrics": metrics} for day, metrics in rows])


def _clicks(url_values):
    return {"clicks": sum(url_values.values()),
            "clicks_by_url": [{"link_url": url, "value": value} for url, value in url_values.items()]}


@pytest.fixture
def clicks_world(kp, monkeypatch):  # noqa: F811
    """糸 clk-01: Threads 3 本（言える 2・リンク先の記録が無い 1）と X 1 本。"""
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    _write(os.path.join(kp["scen"], "clk-01.md"), THREAD)
    posts = (("th-c1.md", "C1", datetime.datetime(2026, 10, 12, 7, 0, tzinfo=jst.JST), LINK_A),
             ("th-c2.md", "C2", datetime.datetime(2026, 10, 16, 7, 0, tzinfo=jst.JST), None),
             ("th-c3.md", "C3", datetime.datetime(2026, 10, 20, 7, 0, tzinfo=jst.JST), LINK_B))
    for file, pid, at, link in posts:
        _write(os.path.join(kp["queue"], file),
               _sns("kp-threads", status="posted", publish_at=jst.iso(at), thread="clk-01",
                    extra={"post_id": pid, "posted_at": jst.iso(at)}))
        _insight("kp-threads", pid, at, replies=3 if pid == "C1" else 0, with_replies=pid != "C3")
        if link:
            sent_mod.write(accounts_mod.state_dir_for("kp-threads"), post_id=pid, text=f"記事 {link}",
                           body_hash="h", sent_at=jst.iso(at), goal="click")
    x_at = "2026-10-21T07:00:00+09:00"
    _write(os.path.join(kp["queue"], "x-c1.md"),
           _sns("kp-x", status="posted", publish_at=x_at, thread="clk-01", media="x",
                extra={"post_id": "XC1", "posted_at": x_at}))
    _daily("kp-threads", [("2026-10-12", _clicks({LINK_A: 3})), ("2026-10-13", _clicks({LINK_A: 4})),
                          ("2026-10-14", {"clicks": 0}),
                          ("2026-10-15", _clicks({LINK_A: 50})),   # 窓の外
                          ("2026-10-16", {"clicks": 0}), ("2026-10-17", {"clicks": 0}),
                          ("2026-10-18", {"clicks": 0}), ("2026-10-19", {"clicks": 0}),
                          ("2026-10-20", _clicks({LINK_B: 2})), ("2026-10-21", _clicks({LINK_B: 3})),
                          ("2026-10-22", {"clicks": 0})])
    return kp


def test_糸のshowにクリック_分母とThreads以外のnull_訪問とは別の欄(clicks_world, capsys):
    payload = scenario.show(PROJECT, "clk-01", now=NOW)
    clicks = payload["threads_clicks"]
    assert clicks["clicks_72h"] == 12, "C1 は 3+4+0（15 日は窓の外）・C3 は 2+3+0"
    assert (clicks["n"], clicks["of"]) == (2, 3)
    assert clicks["reasons"] == {"link_unrecorded": 1}
    assert clicks["other_media"] == {"x": {"posts": 1, "clicks_72h": None,
                                           "cannot_say": "per_post_clicks_unavailable"}}
    assert "per_post_clicks_unavailable:x" in clicks["cannot_say"]
    assert [(p["file"], p["clicks_72h"]) for p in clicks["posts"]] == [
        ("th-c1.md", 7), ("th-c2.md", None), ("th-c3.md", 5)]
    # GA4 の訪問とは別の欄（足さない・混ぜない）。
    assert payload["site_visits"]["sessions"] is None
    assert payload["site_visits"]["cannot_say"] == "site_visits_not_imported"
    assert "clicks" not in json.dumps(payload["site_visits"])
    assert cli.main(["scenario", "show", PROJECT, "clk-01"]) == 0
    out = capsys.readouterr().out
    assert "Threads のリンクのクリック（THTH の実測・投稿から 72h）: 12（言える 2/3 本・訪問とは別の欄）" in out
    assert "x 1 本: —（per_post_clicks_unavailable:x）" in out


def test_別の糸の同じURLの投稿があればurl_shared_72hで言えない_糸とseeds(clicks_world):
    # 別の糸（clk-02）の Threads の投稿 D1 が、C3 の 2 日前に同じリンク先 LINK_B を使った。
    # D1 は実測（insights）にまだ無い。糸の中の投稿だけで照合すると見落として C3 に寄せてしまう。
    d_at = datetime.datetime(2026, 10, 18, 7, 0, tzinfo=jst.JST)
    _write(os.path.join(clicks_world["queue"], "th-d1.md"),
           _sns("kp-threads", status="posted", publish_at=jst.iso(d_at), thread="clk-02",
                extra={"post_id": "D1", "posted_at": jst.iso(d_at)}))
    sent_mod.write(accounts_mod.state_dir_for("kp-threads"), post_id="D1", text=f"別の糸 {LINK_B}",
                   body_hash="h2", sent_at=jst.iso(d_at), goal="click")
    clicks = scenario.show(PROJECT, "clk-01", now=NOW)["threads_clicks"]
    by_file = {p["file"]: p for p in clicks["posts"]}
    assert by_file["th-c3.md"]["clicks_72h"] is None
    assert by_file["th-c3.md"]["cannot_say"] == "url_shared_72h"
    assert clicks["clicks_72h"] == 7 and (clicks["n"], clicks["of"]) == (1, 3)
    assert clicks["reasons"] == {"link_unrecorded": 1, "url_shared_72h": 1}
    # seeds も同じ: 窓（9 日）の外の D1 も照合に入る。
    payload = scenario_seeds.build(PROJECT, days=9, now=NOW)
    threads = next(c for c in payload["columns"] if c["key"] == "clicks")["by_medium"]["threads"]
    assert threads["rows"] == [] and threads["reasons"] == {"url_shared_72h": 1}


def test_dashboard_campaignのoutcomesに別の欄で(clicks_world):
    got = dashboard_cli.build(PROJECT, "campaign", "clk-01", now=NOW)
    outcomes = got["campaign"]["outcomes"]
    assert outcomes["threads_clicks"]["clicks_72h"] == 12 and outcomes["threads_clicks"]["of"] == 3
    assert outcomes["article_visits"]["sessions"] is None
    assert "threads_clicks" not in outcomes["cannot_say"]


def test_plazaのfrom_scenarioの観測にクリック_openの写しは数だけ(clicks_world, monkeypatch):
    monkeypatch.setattr(after_cli, "reaction_lookup", lambda account, **kw: lambda post_id: {"covered": False})
    entry = plaza_from.scenario_numbers(PROJECT, "clk-01", account="kp-threads", project=PROJECT, now=NOW)
    clicks = entry["numbers"]["threads_clicks"]
    assert (clicks["clicks_72h"], clicks["n"], clicks["of"]) == (12, 2, 3)
    assert "posts" not in clicks, "原稿の名前と post_id は観測に写さない"
    assert entry["observed"] is True, "言えたクリックは道具の実測"
    assert entry["numbers"]["site_visits"]["sessions"] is None
    draft = plaza_from.scenario_draft(entry)
    assert "Threads のリンクのクリック（THTH の実測・72h・訪問とは別）: 12（言える 2/3 本・言えない link_unrecorded 1）" in draft
    opened = plaza_from.scenario_open_numbers(entry["numbers"])
    text = json.dumps(opened["threads_clicks"], ensure_ascii=False)
    assert opened["threads_clicks"]["clicks_72h"] == 12
    assert "th-c" not in text and "C1" not in text and "kp.example" not in text


def test_seedsにクリックの列_Threads以外はnull(clicks_world):
    payload = scenario_seeds.build(PROJECT, now=NOW)
    column = next(c for c in payload["columns"] if c["key"] == "clicks")
    threads = column["by_medium"]["threads"]
    assert [(r["file"], r["count"]) for r in threads["rows"]] == [("th-c1.md", 7), ("th-c3.md", 5)]
    assert (threads["posts"], threads["n"], threads["comparable"]) == (3, 2, False)
    assert threads["reasons"] == {"link_unrecorded": 1}
    assert column["by_medium"]["x"]["clicks_72h"] is None
    assert "per_post_clicks_unavailable:x" in column["cannot_say"]
    visits = next(c for c in payload["columns"] if c["key"] == "visits")
    assert visits["cannot_say"] == ["site_visits_not_imported"], "訪問の列には混ぜない"


def test_seedsの返信の列にnonzeroとreplies_source(clicks_world, monkeypatch):
    # C3 は 24h の行に replies が無い → 返信の台帳から数える（出所は ledger）。
    monkeypatch.setattr(after_cli, "_replies_back_from_ledger", lambda account, post_id, **kw: 2)
    payload = scenario_seeds.build(PROJECT, now=NOW)
    replies = next(c for c in payload["columns"] if c["key"] == "replies")["by_medium"]["threads"]
    assert (replies["nonzero"], replies["n"]) == (2, 3), "C1 の 3 と C3 の 2（C2 は 0）"
    by_file = {r["file"]: r for r in replies["rows"]}
    assert by_file["th-c3.md"]["replies_source"] == "ledger"
    assert "replies_source" not in by_file["th-c1.md"]
    # 既定の reaction_lookup の形は変わらない（who_cli などの出力は今までどおり）。
    plain = after_cli.reaction_lookup("kp-threads")("C3")
    assert "replies_source" not in plain
    assert after_cli.reaction_lookup("kp-threads", with_source=True)("C1")["replies_source"] == "measured_24h"


# ================================================================== referrers

def _visits_file(tmp_path, referrers):
    data = {"schema": "hub.site_visits/1", "site": "kp.example", "generated_at": "2026-10-28T06:00:00+09:00",
            "window": {"from": "2026-10-21", "to": "2026-10-27"}, "rows": [], "referrers": referrers}
    path = tmp_path / "visits.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_referrersはhostと数だけ_usernameと本文とpathは入らない(kp, tmp_path):  # noqa: F811
    refs = [{"host": "https://someone@b.hatena.ne.jp:443/entry/kp.example/secret-path?u=alice#frag", "sessions": 5},
            {"host": "b.hatena.ne.jp", "sessions": 2},
            {"host": "T.CO", "sessions": 4},
            {"host": "(direct)", "sessions": 9},
            {"host": "この記事よかった alice", "sessions": 1},
            {"host": "/only/path", "sessions": 3}]
    site_visits.import_file(PROJECT, _visits_file(tmp_path, refs), now=NOW)
    got = dashboard_cli.build(PROJECT, now=NOW)
    block = got["site_referrers"]
    site = block["sites"][0]
    assert site["referrers"] == [{"host": "b.hatena.ne.jp", "sessions": 7}, {"host": "t.co", "sessions": 4}]
    assert site["dropped"] == {"rows": 3, "sessions": 13}
    text = json.dumps(block, ensure_ascii=False)
    for leaked in ("someone", "alice", "secret-path", "entry", "frag", "443", "この記事", "(direct)", "/only"):
        assert leaked not in text
    assert "campaign" not in text and "thread" not in text, "糸には結ばない"


def test_referrer_hostの整え方():
    h = site_visits.referrer_host
    assert h("HTTP://User:pw@Example.COM:8080/a?b#c") == "example.com"
    assert h("example.com.") == "example.com"
    assert h("(not set)") is None and h("") is None and h(None) is None and h("[::1]") is None
    assert h("a_b.example") is None
    # IPv4（全ラベルが数字）も IPv6 と同じく捨てる。数字を含むホスト名は残す。
    assert h("192.168.0.1") is None and h("http://10.0.0.1:8080/x") is None and h("1.2.3") is None
    assert h("123.example") == "123.example" and h("t.co") == "t.co"


def test_referrersがlistでなければ言えない_水増ししない():
    base = {"site": "kp.example", "generated_at": "2026-10-28T06:00:00+09:00",
            "window": {"from": "2026-10-21", "to": "2026-10-27"}, "rows": []}
    for value in ("b.hatena.ne.jp", {"host": "t.co", "sessions": 3}, 5, None):
        block = site_visits.referrers_block(PROJECT, records=[dict(base, referrers=value)], cannot=[])
        site = block["sites"][0]
        assert site["cannot_say"] == "referrers_unavailable", value
        assert site["referrers"] == [] and site["dropped"] == {"rows": 0, "sessions": 0}, value
    ok = site_visits.referrers_block(PROJECT, records=[dict(base, referrers=[
        {"host": "t.co", "sessions": 3}, {"host": "192.168.0.1", "sessions": 2}])], cannot=[])
    assert ok["sites"][0]["referrers"] == [{"host": "t.co", "sessions": 3}]
    assert ok["sites"][0]["dropped"] == {"rows": 1, "sessions": 2}
