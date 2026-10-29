"""Meta の計画の段 1（FB の仕上げ・10/29）の柵。

正本は `docs/設計_MetaAPIを使い倒す計画_2026-10-29.md` の「段 1」。偽の Graph API（loopback）と
試験の中で作った値だけを使う。本物の Meta には触らない。

1. 複数の写真（`published=false` → `/feed` の `attached_media`・10 枚まで）
2. ページの日次（`account_insights`・取れない指標は `unavailable` に名前）・doctor の probe
3. 投稿の編集 `thth edit`（THTH で出した投稿だけ・二段確認・`sent/` を直す）
4. コメントの非表示・戻す・削除 `thth reply hide|unhide|delete`
＋ 新しい口が全部 `leave_gate.bind` に包まれること（止めた口座では呼べない）
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from thth import accounts, approval, collect, edit_cli, jst, leave_gate, media_delivery, sent
from thth.adapters import base
from thth.adapters import facebook as fb
from tests.test_facebook_adapter import TOKEN, adapter, fake_graph, photo_row, photo_post
from tests.test_facebook_auth import PAGE, V, env, write_token  # noqa: F401 — env は fixture

POST = PAGE + "_1001"
COMMENT = "1001_555"


# ================================================================== 1. 複数の写真

def _routes_for_carousel(ids):
    pending = list(ids)

    def photos(query, form, raw):
        return 200, {"id": pending.pop(0)}

    return {("POST", f"{V}/{PAGE}/photos"): photos,
            ("POST", f"{V}/{PAGE}/feed"): (200, {"id": POST})}


def test_写真2枚以上はpublished_falseで上げてattached_mediaに原稿の順で並べる():
    rows = [photo_row(index=i, alt=f"写真{i}", public_sha256=f"{i:02d}" * 32) for i in (1, 2, 3)]
    post, phases = photo_post(rows)
    with fake_graph(_routes_for_carousel(["901", "902", "903"])) as (url, calls):
        result = adapter(url).publish(post, dry_run=False)
    assert result.failure == "none" and result.post_id == POST
    uploads = [c for c in calls if c["path"].endswith("/photos")]
    assert len(uploads) == 3
    for i, call in enumerate(uploads, start=1):
        assert b'name="published"\r\n\r\nfalse' in call["raw"]
        assert f"写真{i}".encode() in call["raw"]          # alt_text_custom
        assert b'name="caption"' not in call["raw"]        # 本文は /feed にだけ
    feed = [c for c in calls if c["path"].endswith("/feed")]
    assert len(feed) == 1 and calls[-1] is feed[0]
    form = feed[0]["form"]
    assert form["message"] == ["新しい豆"]
    attached = sorted(k for k in form if k.startswith("attached_media"))
    assert attached == ["attached_media[0]", "attached_media[1]", "attached_media[2]"]
    assert [json.loads(form[k][0]) for k in attached] == [
        {"media_fbid": "901"}, {"media_fbid": "902"}, {"media_fbid": "903"}]
    assert TOKEN not in json.dumps(feed[0]["query"])
    assert [m["remote_id"] for m in result.media] == ["901", "902", "903"]
    assert [m["sha256"] for m in result.media] == [f"{i:02d}" * 32 for i in (1, 2, 3)]
    assert [p for p, _ in phases] == ["uploading"] * 3 + ["publishing", "published"]


def test_写真は10枚まで通り11枚は要求の前に断る():
    ten = [photo_row(index=i) for i in range(1, fb.CAROUSEL_MAX + 1)]
    assert fb.intent_error({"files": ten, "attachments": [], "post_options": {}, "captions": []}) is None
    eleven = ten + [photo_row(index=fb.CAROUSEL_MAX + 1)]
    manifest = {"files": eleven, "attachments": [], "post_options": {}, "captions": []}
    assert fb.intent_error(manifest).startswith("media_limit_exceeded: count")
    assert media_delivery.error_for({"media": "facebook"}, manifest).startswith("media_limit_exceeded")
    post, _ = photo_post(eleven)
    with fake_graph({}) as (url, calls):
        result = adapter(url).publish(post, dry_run=False)
    assert result.failure == "publish_definite" and calls == []


def test_複数の写真に動画や形式違いが混ざれば要求の前に断る():
    rows = [photo_row(index=1), photo_row(index=2, kind="video", format="mp4")]
    post, _ = photo_post(rows)
    with fake_graph({}) as (url, calls):
        result = adapter(url).publish(post, dry_run=False)
    assert result.failure == "publish_definite" and calls == []
    assert result.error.startswith("unsupported_attachment: facebook/mp4")


@pytest.mark.parametrize("status", [400, 500])
def test_写真を上げている間の失敗は出ていない_feedを呼ばない(status):
    rows = [photo_row(index=1), photo_row(index=2)]
    post, phases = photo_post(rows)
    calls_box = {"n": 0}

    def photos(query, form, raw):
        calls_box["n"] += 1
        return (200, {"id": "901"}) if calls_box["n"] == 1 else (status, {"error": {"message": "x", "code": 1}})

    routes = {("POST", f"{V}/{PAGE}/photos"): photos, ("POST", f"{V}/{PAGE}/feed"): (200, {"id": POST})}
    with fake_graph(routes) as (url, calls):
        result = adapter(url).publish(post, dry_run=False)
    assert result.failure == "publish_definite" and result.post_id is None
    assert not any(c["path"].endswith("/feed") for c in calls)
    assert phases[-1][0] == "failed"


@pytest.mark.parametrize("status,body,failure", [
    (400, {"error": {"message": "bad", "code": 100}}, "publish_definite"),
    (403, {"error": {"message": "(#200) permission", "code": 200}}, "permission"),
    (500, {"error": {"message": "down", "code": 2}}, "media_ambiguous"),
    (200, {"nope": 1}, "media_ambiguous"),
])
def test_feedの失敗の種類(status, body, failure):
    post, phases = photo_post([photo_row(index=1), photo_row(index=2)])
    routes = _routes_for_carousel(["901", "902"])
    routes[("POST", f"{V}/{PAGE}/feed")] = (status, body)
    with fake_graph(routes) as (url, _):
        result = adapter(url).publish(post, dry_run=False)
    assert result.failure == failure and result.post_id is None


def test_写真を上げたあと関門で止められたらfeedを呼ばずpublish_vetoed():
    post, _ = photo_post([photo_row(index=1), photo_row(index=2)])
    seen = []

    def gate():
        seen.append(1)
        return "account_stopped" if len(seen) == 2 else None

    with fake_graph(_routes_for_carousel(["901", "902"])) as (url, calls):
        result = adapter(url).publish(post, dry_run=False, before_publish=gate)
    assert result.failure == "publish_vetoed" and len(seen) == 2
    assert not any(c["path"].endswith("/feed") for c in calls)


def test_写真1枚は今までどおりphotosの1回だけ():
    post, _ = photo_post()
    with fake_graph({("POST", f"{V}/{PAGE}/photos"): (200, {"id": "777", "post_id": POST})}) as (url, calls):
        assert adapter(url).publish(post, dry_run=False).post_id == POST
    assert len(calls) == 1 and b'name="published"' not in calls[0]["raw"]


# ================================================================== 2. ページの日次

def _metric_rows(names, value=7):
    return {"data": [{"name": n, "period": "day", "values": [{"value": 1, "end_time": "x"},
                                                              {"value": value, "end_time": "y"}]}
                     for n in names]}


def test_ページの日次は表の指標をまとめて訊き最後の値を採りフォロワーを別名でも持つ():
    with fake_graph({("GET", f"{V}/{PAGE}/insights"): (200, _metric_rows(fb.PAGE_DAILY_METRICS))}) as (url, calls):
        out = adapter(url).account_insights(PAGE, since="1", until="2")
    assert calls[0]["query"]["metric"] == [",".join(fb.PAGE_DAILY_METRICS)]
    assert calls[0]["query"]["period"] == ["day"]
    assert calls[0]["query"]["since"] == ["1"] and calls[0]["query"]["until"] == ["2"]
    assert {k: v for k, v in out.items() if k in fb.PAGE_DAILY_METRICS} == {n: 7 for n in fb.PAGE_DAILY_METRICS}
    assert out["followers_count"] == 7 and "unavailable" not in out


def test_応答に無い指標は0で埋めずunavailableに名前を書く():
    dropped = fb.PAGE_DAILY_METRICS[3]
    names = [n for n in fb.PAGE_DAILY_METRICS if n != dropped]
    with fake_graph({("GET", f"{V}/{PAGE}/insights"): (200, _metric_rows(names))}) as (url, _):
        out = adapter(url).account_insights(PAGE, since="1", until="2")
    assert dropped not in out and set(out["unavailable"]) == {dropped}


def test_まとめて断られたら1つずつ訊き直し断られた名前だけunavailable():
    dead = "page_views_total"

    def insights(query, form, raw):
        metric = query["metric"][0]
        if "," in metric or metric == dead:
            return 400, {"error": {"message": f"(#100) The value must be a valid insights metric: {dead}", "code": 100}}
        return 200, _metric_rows([metric], value=3)

    with fake_graph({("GET", f"{V}/{PAGE}/insights"): insights}) as (url, calls):
        out = adapter(url).account_insights(PAGE, since="1", until="2")
    assert len(calls) == 1 + len(fb.PAGE_DAILY_METRICS)
    assert set(out["unavailable"]) == {dead} and "valid insights metric" in out["unavailable"][dead]
    assert all(out[n] == 3 for n in fb.PAGE_DAILY_METRICS if n != dead)
    assert TOKEN not in json.dumps(out)


def test_ページの日次の網の失敗と権限は上げる():
    with fake_graph({("GET", f"{V}/{PAGE}/insights"): (500, {"error": {"message": "down", "code": 2}})}) as (url, _):
        with pytest.raises(base.AdapterError):
            adapter(url).account_insights(PAGE, since="1", until="2")
    body = {"error": {"message": "(#10) permission", "code": 10}}
    with fake_graph({("GET", f"{V}/{PAGE}/insights"): (403, body)}) as (url, _):
        with pytest.raises(base.PermissionMissing):
            adapter(url).account_insights(PAGE, since="1", until="2")
    with pytest.raises(base.AdapterError, match="違う id"):
        adapter("http://127.0.0.1:9").account_insights("999", since="1", until="2")


def test_collectは取れない指標を行のunavailableに分けて残す(tmp_path, monkeypatch):
    class Fake:
        def capabilities(self):
            return {"account_insights"}

        def account_insights(self, user_id, *, since, until):
            return {"page_follows": 5, "followers_count": 5, "unavailable": {"page_views_total": "x"}}

    monkeypatch.setattr(collect.accounts_mod, "load_token", lambda cfg: {"user_id": PAGE})
    errors = []
    path = collect._collect_account_daily("alpha", {}, Fake(), now=jst.now_jst(), errors=errors,
                                          account_dir=str(tmp_path))
    row = json.loads(Path(path).read_text().splitlines()[0])
    assert row["metrics"] == {"page_follows": 5, "followers_count": 5}
    assert row["unavailable"] == {"page_views_total": "x"} and errors == []


def test_collectは指標が1つも取れなければ書かずにerrorsに名前を言う(tmp_path, monkeypatch):
    class Fake:
        def capabilities(self):
            return {"account_insights"}

        def account_insights(self, user_id, *, since, until):
            return {"unavailable": {"page_follows": "x"}}

    monkeypatch.setattr(collect.accounts_mod, "load_token", lambda cfg: {"user_id": PAGE})
    errors = []
    assert collect._collect_account_daily("alpha", {}, Fake(), now=jst.now_jst(), errors=errors,
                                          account_dir=str(tmp_path)) is None
    assert errors and "page_follows" in errors[0]


def test_doctorのprobeはページの日次の指標を1つずつ確かめる():
    dead = "page_views_total"

    def insights(query, form, raw):
        metric = query["metric"][0]
        if metric == dead:
            return 400, {"error": {"message": "(#100) invalid metric", "code": 100}}
        if metric == "page_follows":
            return 200, {"data": []}
        return 200, _metric_rows([metric], value=4)

    routes = {("GET", f"{V}/me"): (200, {"id": PAGE, "name": "こぴちゃ"}),
              ("GET", f"{V}/debug_token"): (200, {"data": {"scopes": list(fb.SCOPES)}}),
              ("GET", f"{V}/{PAGE}/posts"): (200, {"data": []}),
              ("GET", f"{V}/{PAGE}/insights"): insights}
    with fake_graph(routes) as (url, calls):
        rows = adapter(url).probe()
    by = {r["key"]: r for r in rows}
    for name in fb.PAGE_DAILY_METRICS:
        assert "page_insights." + name in by
    assert by["page_insights." + dead]["ok"] is False and "unavailable" in by["page_insights." + dead]["detail"]
    assert by["page_insights.page_follows"]["ok"] is None
    assert by["page_insights.page_media_view"]["ok"] is True
    insight_calls = [c for c in calls if c["path"].endswith("/insights")]
    assert [c["query"]["metric"] for c in insight_calls] == [[n] for n in fb.PAGE_DAILY_METRICS]
    assert {c["method"] for c in calls} == {"GET"}


# ================================================================== 3・4. adapter の書く口

@pytest.mark.parametrize("call,method,path,form", [
    (lambda a: a.edit_post(POST, "直した本文"), "POST", f"/{POST}", {"message": ["直した本文"]}),
    (lambda a: a.hide_reply(COMMENT), "POST", f"/{COMMENT}", {"is_hidden": ["true"]}),
    (lambda a: a.hide_reply(COMMENT, hidden=False), "POST", f"/{COMMENT}", {"is_hidden": ["false"]}),
    (lambda a: a.delete_reply(COMMENT), "DELETE", f"/{COMMENT}", None),
])
def test_書く口は1回だけ投げsuccessが真のときだけ済んだと言う(call, method, path, form):
    with fake_graph({(method, V + path): (200, {"success": True})}) as (url, calls):
        call(adapter(url))
    assert len(calls) == 1 and calls[0]["method"] == method and calls[0]["path"] == V + path
    if form is not None:
        assert {k: v for k, v in calls[0]["form"].items() if k != "access_token"} == form
        assert "access_token" not in calls[0]["query"]
    with fake_graph({(method, V + path): (200, {})}) as (url, _):
        with pytest.raises(base.AdapterError) as e:
            call(adapter(url))
    assert e.value.failure == "ambiguous"


@pytest.mark.parametrize("status,body,expected", [
    (400, {"error": {"message": "bad", "code": 100}}, "definite"),
    (404, {"error": {"message": "gone", "code": 100, "error_subcode": 33}}, "definite"),
    (500, {"error": {"message": "down", "code": 2}}, "ambiguous"),
])
def test_書く口の失敗の種類(status, body, expected):
    with fake_graph({("POST", f"{V}/{POST}"): (status, body)}) as (url, _):
        with pytest.raises(base.AdapterError) as e:
            adapter(url).edit_post(POST, "x")
    assert e.value.failure == expected and not isinstance(e.value, base.PermissionMissing)
    with fake_graph({("POST", f"{V}/{POST}"): (403, {"error": {"message": "(#200) permission", "code": 200}})}) as (url, _):
        with pytest.raises(base.PermissionMissing):
            adapter(url).edit_post(POST, "x")


def test_書く口は形の違うidを投げずに断る():
    a = adapter("http://127.0.0.1:9")
    for call in (lambda: a.edit_post("1001", "x"), lambda: a.edit_post("1_2/../x", "x"),
                 lambda: a.hide_reply("../me"), lambda: a.delete_reply("555?x=1"),
                 lambda: a.edit_post(POST, "  ")):
        with pytest.raises(base.AdapterError):
            call()


def test_facebookのDELETEは投稿の取り下げとコメントの削除の2か所だけ_呼ぶのはedit_cliだけ():
    src = inspect.getsource(fb)
    assert src.count('"DELETE"') == 2
    assert '"DELETE"' in inspect.getsource(fb.FacebookAdapter.delete_post)
    assert '"DELETE"' in inspect.getsource(fb.FacebookAdapter.delete_reply)
    from thth import cli as cli_mod, core, collect as collect_mod, doctor, maintain, retract_cli
    for mod in (cli_mod, core, collect_mod, doctor, maintain, retract_cli):
        text = inspect.getsource(mod)
        for name in (".edit_post(", ".hide_reply(", ".delete_reply("):
            assert name not in text, f"{mod.__name__} が {name} を呼んでいる"
    text = inspect.getsource(edit_cli)
    assert text.count(".edit_post(") == 1 and text.count(".delete_reply(") == 1
    assert text.count(".hide_reply(") == 1


# ================================================================== 止めの柵

NEW_WRITE_METHODS = ("edit_post", "fetch_reply", "hide_reply", "delete_reply")


def test_新しい口はどれもleave_gateのbindが包む名前の集合に入っている():
    src = inspect.getsource(leave_gate.bind)
    for name in NEW_WRITE_METHODS:
        assert f"'{name}'" in src, f"leave_gate.bind が {name} を包んでいない（止めた口座で呼べてしまう）"


def _write_ledger(env, **over):
    path = Path(env["cfg"]["token"]).parent.parent / "accounts" / "alpha.json"
    cfg = json.loads(path.read_text())
    cfg.update(over)
    path.write_text(json.dumps(cfg))


def _stop_alpha():
    from tests.test_v212_leave_gate import stop
    stop("alpha")


@pytest.mark.parametrize("name,args", [("edit_post", (POST, "x")), ("fetch_reply", (COMMENT,)),
                                       ("hide_reply", (COMMENT,)), ("delete_reply", (COMMENT,))])
def test_止めた口座では新しい口を呼んでも要求が1本も出ない(env, name, args):
    write_token(env)
    for method in ("POST", "DELETE", "GET"):
        env["routes"][(method, f"{V}/{POST}")] = (200, {"success": True})
        env["routes"][(method, f"{V}/{COMMENT}")] = (200, {"success": True, "id": COMMENT})
    cfg = accounts.load_account("alpha")
    made = fb.FacebookAdapter.from_account(cfg, accounts.load_token(cfg))
    before = len(env["calls"])
    _stop_alpha()
    with pytest.raises(accounts.AccountStopped):
        getattr(made, name)(*args)
    assert len(env["calls"]) == before


# ================================================================== 3. thth edit（CLI）

def _sent(env, post_id=POST, text="元の本文", **over):
    state = accounts.state_dir_for("alpha")
    sent.write(state, post_id=post_id, text=text, body_hash=approval.compute_body_hash(text),
               sent_at="2026-10-29T10:00:00+09:00")
    if over:
        p = sent.path_for(state, post_id)
        data = json.loads(Path(p).read_text())
        data.update(over)
        Path(p).write_text(json.dumps(data))
    return state


def _edit_args(**kw):
    base_args = dict(account="alpha", post_id=POST, text="直した本文", text_file=None, reason=None,
                     by="operator", confirm=None, json=True, wait=0)
    base_args.update(kw)
    return SimpleNamespace(**base_args)


def _stage1(capsys, func, args):
    assert func(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == 1
    return payload["digest"]


def test_editは一段目で媒体を呼ばず二段目で1回だけ直しsentを直す(env, capsys):
    write_token(env)
    _write_ledger(env, production=True)
    state = _sent(env)
    env["routes"][("POST", f"{V}/{POST}")] = (200, {"success": True})
    before = len(env["calls"])
    digest = _stage1(capsys, edit_cli.cmd_edit, _edit_args())
    assert len(env["calls"]) == before
    assert edit_cli.cmd_edit(_edit_args(confirm=digest)) == 0
    writes = [c for c in env["calls"][before:] if c["method"] != "GET"]
    assert len(writes) == 1 and writes[0]["path"] == f"{V}/{POST}"
    assert writes[0]["form"]["message"] == ["直した本文"]
    row = sent.read(state, POST)
    assert row["text"] == "直した本文" and row["body_hash"] == approval.compute_body_hash("直した本文")
    assert row["edits"][0]["previous_text"] == "元の本文" and row["edits"][0]["result"] == "confirmed"
    assert row["sent_at"] == "2026-10-29T10:00:00+09:00" and row.get("edited_at")


def test_editはTHTHで出していない投稿を断り媒体を呼ばない(env, capsys):
    write_token(env)
    _write_ledger(env, production=True)
    before = len(env["calls"])
    assert edit_cli.cmd_edit(_edit_args()) == 1
    assert "THTH で出していない投稿は直しません" in json.loads(capsys.readouterr().out)["error"]
    _sent(env, retracted_at="2026-10-29T11:00:00+09:00")
    assert edit_cli.cmd_edit(_edit_args()) == 1
    assert "取り下げ済み" in json.loads(capsys.readouterr().out)["error"]
    assert len(env["calls"]) == before


def test_editはdigestが違う_productionが無い_上限を超えるなら媒体を呼ばない(env, capsys):
    write_token(env)
    _sent(env)
    before = len(env["calls"])
    digest = _stage1(capsys, edit_cli.cmd_edit, _edit_args())
    assert edit_cli.cmd_edit(_edit_args(confirm="0" * len(digest))) == 1
    capsys.readouterr()
    assert edit_cli.cmd_edit(_edit_args(confirm=digest)) == 1           # production: false
    assert "production: true" in json.loads(capsys.readouterr().out)["error"]
    assert edit_cli.cmd_edit(_edit_args(text="あ" * 5001)) == 1
    assert "上限" in json.loads(capsys.readouterr().out)["error"]
    assert len(env["calls"]) == before


def test_editは一段目のあとに記録が変われば二段目を通さない(env, capsys):
    write_token(env)
    _write_ledger(env, production=True)
    state = _sent(env)
    digest = _stage1(capsys, edit_cli.cmd_edit, _edit_args())
    sent.mark_post_edit(state, POST, result="confirmed", edited_at=jst.iso(), by="other", text="別の直し")
    before = len(env["calls"])
    assert edit_cli.cmd_edit(_edit_args(confirm=digest)) == 1
    assert len(env["calls"]) == before


def test_editの結果が分からなければ本文を変えずunknownを残す(env, capsys):
    write_token(env)
    _write_ledger(env, production=True)
    state = _sent(env)
    env["routes"][("POST", f"{V}/{POST}")] = (500, {"error": {"message": "down", "code": 2}})
    digest = _stage1(capsys, edit_cli.cmd_edit, _edit_args())
    assert edit_cli.cmd_edit(_edit_args(confirm=digest)) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "unknown"
    row = sent.read(state, POST)
    assert row["text"] == "元の本文" and row["edits"][0]["result"] == "unknown"


def test_editは安全装置で止まった口座では媒体を呼ばない(env, capsys, monkeypatch):
    write_token(env)
    _write_ledger(env, production=True)
    _sent(env)
    from thth import guard
    monkeypatch.setattr(guard, "stop_place_usable", lambda account: True)
    monkeypatch.setattr(guard, "stopped", lambda account: {"reason": "burst"})
    digest = _stage1(capsys, edit_cli.cmd_edit, _edit_args())
    before = len(env["calls"])
    assert edit_cli.cmd_edit(_edit_args(confirm=digest)) == 1
    assert json.loads(capsys.readouterr().out)["guard"] == "stopped"
    assert len(env["calls"]) == before


def test_editは編集の口を持たない媒体を断る(env, capsys, monkeypatch):
    monkeypatch.setattr(fb.FacebookAdapter, "CAPABILITIES",
                        fb.FacebookAdapter.CAPABILITIES - {"edit_post"})
    assert edit_cli.cmd_edit(_edit_args()) == 2
    assert "未対応" in json.loads(capsys.readouterr().out)["error"]


# ================================================================== 4. thth reply（CLI）

def _comment_route(env, **fields):
    row = {"id": COMMENT, "message": "荒れたコメント", "created_time": "2026-10-29T01:00:00+0000",
           "from": {"id": "42", "name": "誰か"}, "is_hidden": False, "can_hide": True, "can_remove": True}
    row.update(fields)
    env["routes"][("GET", f"{V}/{COMMENT}")] = (200, row)
    env["routes"][("POST", f"{V}/{COMMENT}")] = (200, {"success": True})
    env["routes"][("DELETE", f"{V}/{COMMENT}")] = (200, {"success": True})


def _reply_args(action, **kw):
    base_args = dict(action=action, account="alpha", comment_id=COMMENT, reason=None, by="operator",
                     confirm=None, json=True, wait=0)
    base_args.update(kw)
    return SimpleNamespace(**base_args)


def _writes(env, before):
    return [c for c in env["calls"][before:] if c["method"] != "GET"]


def test_reply_hideとunhideは1段で隠し戻し記録を1行ずつ残す(env, capsys):
    write_token(env)
    _write_ledger(env, production=True)
    _comment_route(env)
    before = len(env["calls"])
    assert edit_cli.cmd_reply(_reply_args("hide")) == 0
    first = json.loads(capsys.readouterr().out)
    _comment_route(env, is_hidden=True)
    assert edit_cli.cmd_reply(_reply_args("unhide")) == 0
    writes = _writes(env, before)
    assert [(c["method"], c["form"].get("is_hidden")) for c in writes] == [("POST", ["true"]), ("POST", ["false"])]
    rows = [json.loads(line) for line in Path(first["record"]).read_text().splitlines()]
    assert [(r["action"], r["result"]) for r in rows] == [("hide", "confirmed"), ("unhide", "confirmed")]
    assert all("荒れたコメント" not in json.dumps(r, ensure_ascii=False) for r in rows)   # 本文は残さない


def test_reply_hideは既に隠れていれば何もしない(env, capsys):
    write_token(env)
    _write_ledger(env, production=True)
    _comment_route(env, is_hidden=True)
    before = len(env["calls"])
    assert edit_cli.cmd_reply(_reply_args("hide")) == 0
    assert json.loads(capsys.readouterr().out)["changed"] is False and _writes(env, before) == []


def test_reply_deleteは取り下げと同じ二段確認で投稿の取り下げのdigestは使えない(env, capsys):
    write_token(env)
    _write_ledger(env, production=True)
    _comment_route(env)
    before = len(env["calls"])
    assert edit_cli.cmd_reply(_reply_args("delete")) == 1                 # --reason が無い
    capsys.readouterr()
    digest = _stage1(capsys, edit_cli.cmd_reply, _reply_args("delete", reason="荒らし"))
    retract_digest = approval.compute_retract_digest(post_id=COMMENT, account="alpha", reason="荒らし")
    assert retract_digest != digest
    assert edit_cli.cmd_reply(_reply_args("delete", reason="荒らし", confirm=retract_digest)) == 1
    capsys.readouterr()
    assert _writes(env, before) == []
    assert edit_cli.cmd_reply(_reply_args("delete", reason="荒らし", confirm=digest)) == 0
    writes = _writes(env, before)
    assert [(c["method"], c["path"]) for c in writes] == [("DELETE", f"{V}/{COMMENT}")]


def test_replyはproductionが無い_権限が無い_Metaが隠せないと言うなら書かない(env, capsys):
    write_token(env)
    _comment_route(env)
    before = len(env["calls"])
    assert edit_cli.cmd_reply(_reply_args("hide")) == 1                    # production: false
    assert "production: true" in json.loads(capsys.readouterr().out)["error"]
    _write_ledger(env, production=True)
    _comment_route(env, can_hide=False)
    assert edit_cli.cmd_reply(_reply_args("hide")) == 1
    capsys.readouterr()
    write_token(env, scopes=["pages_show_list"])
    assert edit_cli.cmd_reply(_reply_args("hide")) == 2
    assert "pages_manage_engagement" in json.loads(capsys.readouterr().out)["error"]
    assert _writes(env, before) == []


def test_replyの結果が分からなければunknownを残す(env, capsys):
    write_token(env)
    _write_ledger(env, production=True)
    _comment_route(env)
    env["routes"][("POST", f"{V}/{COMMENT}")] = (500, {"error": {"message": "down", "code": 2}})
    assert edit_cli.cmd_reply(_reply_args("hide")) == 1
    assert json.loads(capsys.readouterr().out)["result"] == "unknown"
    path = edit_cli.moderation_path(accounts.state_dir_for("alpha"))
    assert json.loads(Path(path).read_text().splitlines()[-1])["result"] == "unknown"


def test_thth_editとreplyはhelpに出る():
    from thth import cli as cli_mod
    parser = cli_mod.build_parser()
    args = parser.parse_args(["edit", "alpha", POST, "--text", "x", "--by", "o"])
    assert args.func is edit_cli.cmd_edit
    args = parser.parse_args(["reply", "delete", "alpha", COMMENT, "--reason", "r", "--by", "o"])
    assert args.func is edit_cli.cmd_reply
