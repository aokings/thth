"""計画 Meta 全部盛り申請: 遠くの道（`thth login` のあと・thth.me の鍵で VM に頼む道）の Meta の口。

見るのは:
  - `thth doctor`・`page cta show|delete`・`live create|end`・`dm list|read|send`・`like|unlike`（IG）・`edit`・
    `reply hide` が、CLI → 偽の Worker → VM（`api_requests.handle` の本物の資格の確かめ）→ 偽の Graph で 1 本ずつ通る。
  - **柵は手元の道と同じ**: 二段確認の digest は VM が作り VM が照合する（一段目と違う digest は網に出ない）・
    安全装置の止めの印と leave_gate で止めた口座は網に出ない・24 時間の窓・`production: true`。
  - **本文・相手・配信の宛先を VM に残さない**: 依頼の印（`requests/<id>.json`）・runs・VM の置き場の全部の
    ファイルに目印が無く、捕まえた VM の stdout/stderr にも出ない。stream_url は答えで 1 回だけ端末に届く。
  - 台帳が手元に無く `thth login` 済みなら、各命令が `/api/v1/<operation>` に届く（`--by` は送らない）。
偽の Graph と偽の Worker は loopback だけ。本物の Meta・thth.me は叩かない。
"""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import pathlib
import re
import secrets
import socket
import sys

import pytest

from thth import accounts, api_requests, guard, jst, leave_gate, remote, server_files, server_meta, server_writes
from thth import approval, refusal_catalog, sent
from thth.adapters import facebook as fb
from tests.test_facebook_adapter import fake_graph
from tests.test_v212_server_writes import env, save_config  # noqa: F401
from tests.test_v3140_remote_cli import ACCOUNT, KEY, FakeWorker, home, run, worker  # noqa: F401
from tests.kept_bytes import kept_bytes

V = "/" + fb.GRAPH_VERSION
PAGE, IG_ID = "61596075596464", "17841437318552220"
FB_CONVO, FB_PERSON = "t_100", "5551"
CTA, LIVE, POST, COMMENT, IG_MEDIA = "901", "7001", PAGE + "_9", PAGE + "_55", "17895695668004550"
# 残ってはいけない目印。
INBOUND, OUTBOUND, NAME = "お客さまの問い合わせの目印", "こちらの返事の目印", "お客の名前の目印"
STREAM_KEY = "FB-" + secrets.token_hex(8)
EDITED = "直した本文の目印"


def ago(**kw):
    return jst.iso(jst.now_jst() - datetime.timedelta(**kw))


def _ledger(env, name, **extra):
    path = env["root"] / "accounts" / (name + ".json")
    cfg = json.loads(path.read_text())
    cfg.update(extra)
    path.write_text(json.dumps(cfg))
    token = pathlib.Path(cfg["token"])
    token.write_text(json.dumps({"access_token": "tok-" + secrets.token_hex(12), "user_id": extra["user_id"]}))
    token.chmod(0o600)


@pytest.fixture
def vm(env, monkeypatch, tmp_path):  # noqa: F811
    """VM の側: alpha は Facebook ページ・beta は Instagram（同じ鍵で両方）。偽の Graph。"""
    monkeypatch.setattr(socket.socket, "connect", env["connect"])  # loopback の偽物だけ（env は網を断っている）
    app = tmp_path / "app.env"
    app.write_text("META_APP_ID=4242\nMETA_APP_SECRET=" + secrets.token_urlsafe(32) + "\n")
    app.chmod(0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app))
    _ledger(env, "alpha", media="facebook", page_id=PAGE, handle=PAGE, user_id=PAGE, char_limit=63206)
    _ledger(env, "beta", media="instagram", ig_user_id=IG_ID, page_id=PAGE, handle=IG_ID, user_id=IG_ID)
    env["value"]["credentials"][0]["accounts"] = {"alpha": "alpha", "beta": "beta"}
    save_config(env)
    state = {"inbound_at": ago(hours=1)}

    def conversations(query, form, raw):
        return 200, {"data": [{"id": FB_CONVO, "updated_time": state["inbound_at"], "participants": {"data": [
            {"id": PAGE, "name": "ページ"}, {"id": FB_PERSON, "name": NAME}]}}]}

    def convo(query, form, raw):
        return 200, {"id": FB_CONVO, "participants": {"data": [{"id": PAGE}, {"id": FB_PERSON, "name": NAME}]},
                     "messages": {"data": [
                         {"id": "m_2", "created_time": state["inbound_at"], "from": {"id": FB_PERSON, "name": NAME},
                          "message": INBOUND},
                         {"id": "m_1", "created_time": ago(days=3), "from": {"id": PAGE}, "message": "前の返事"}]}}

    table = {
        ("GET", V + f"/{PAGE}/call_to_actions"): (200, {"data": [{"id": CTA, "type": "SHOP_NOW",
                                                                  "web_url": "https://example.test/shop",
                                                                  "status": "ACTIVE"}]}),
        ("DELETE", V + f"/{CTA}"): (200, {"success": True}),
        ("POST", V + f"/{PAGE}/live_videos"): (200, {"id": LIVE, "stream_url": "rtmp://live.test/" + STREAM_KEY,
                                                     "secure_stream_url": "rtmps://live.test:443/rtmp/" + STREAM_KEY}),
        ("GET", V + f"/{LIVE}"): (200, {"id": LIVE, "status": "LIVE", "title": "試しの配信", "from": {"id": PAGE}}),
        ("POST", V + f"/{LIVE}"): (200, {"id": LIVE}),
        ("GET", V + f"/{PAGE}/conversations"): conversations,
        ("GET", V + f"/{FB_CONVO}"): convo,
        ("POST", V + f"/{PAGE}/messages"): (200, {"recipient_id": FB_PERSON, "message_id": "m_9"}),
        ("POST", V + f"/{IG_ID}/likes"): (200, {"success": True}),
        ("DELETE", V + f"/{IG_ID}/likes"): (200, {"success": True}),
        ("POST", V + f"/{POST}"): (200, {"success": True}),
        ("GET", V + f"/{COMMENT}"): (200, {"id": COMMENT, "message": "コメント", "created_time": ago(hours=2),
                                           "from": {"id": "77", "name": "誰か"}, "is_hidden": False,
                                           "can_hide": True, "can_remove": True}),
        ("POST", V + f"/{COMMENT}"): (200, {"success": True}),
    }
    with fake_graph(table) as (url, calls):
        monkeypatch.setenv(fb.BASE_URL_ENV, url)
        yield {**env, "calls": calls, "state": state, "routes": table}


class Captured(io.StringIO):
    """VM の側で捕まえた文（VM のログに当たる）。"""


@pytest.fixture
def far(vm, tmp_path, monkeypatch):
    """CLI の側: `thth login` 済みの remote.json と、届いた依頼を VM の `api_requests.handle` で行う偽の Worker。"""
    sha = hashlib.sha256(vm["bearer"].encode()).hexdigest()
    fake = FakeWorker()
    seen = {"entries": [], "vm_log": io.StringIO()}

    def answer(call):
        op = call["path"].rsplit("/", 1)[-1]
        item = {"request_id": secrets.token_urlsafe(32) + "." + call["body"]["account"],
                "account": call["body"]["account"], "operation": op, "key_sha256": sha, "body": call["body"]}
        real_err = sys.stderr
        sys.stderr = seen["vm_log"]  # VM の常駐の stderr（journald）に当たる
        try:
            entry = api_requests.handle("person", item, str(vm["path"]))
        finally:
            sys.stderr = real_err
        seen["entries"].append(entry)
        return 200, entry["result"], None

    fake.default = answer
    config = tmp_path / "cfg" / "remote.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"url": fake.url, "key": "k3y_" + "B" * 39}))
    config.chmod(0o600)
    monkeypatch.setenv("THTH_REMOTE_CONFIG", str(config))
    monkeypatch.setattr(remote, "_sleep", lambda seconds: None)
    yield {**vm, "worker": fake, **seen}
    fake.close()


def cli(capsys, *argv):
    from thth import cli as cli_mod
    rc = cli_mod.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


def writes(far):
    return [c for c in far["calls"] if c["method"] in ("POST", "DELETE")]


def two_steps(capsys, *argv):
    """遠くの道の一段目（VM の digest を読む）→ 二段目。"""
    rc, out, err = cli(capsys, *argv, "--remote", "--json")
    assert rc == 1, out + err
    first = json.loads(out)
    assert first["stage"] == 1 and re.fullmatch(r"[0-9a-f]{12}", first["digest"])
    return cli(capsys, *argv, "--remote", "--confirm", first["digest"], "--json"), first


def nothing_kept(far, *markers):
    """VM の置き場の全部のファイル（依頼の印・runs・記録）と VM の stderr に目印が無い。"""
    for path in pathlib.Path(far["root"]).rglob("*"):
        if path.is_file():
            raw = kept_bytes(path)
            for marker in markers:
                assert marker.encode() not in raw, f"{path} に {marker} が残っている"
    for marker in markers:
        assert marker not in far["vm_log"].getvalue()


def requests_records(far, account="alpha"):
    folder = pathlib.Path(accounts.state_dir_for(account)) / "requests"
    return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))]


def stop_leave(account):
    with leave_gate.lease(account, exclusive=True):
        with server_files.directory(leave_gate.location(), create=True, private=True) as fd:
            server_files.replace_at(fd, account + ".json",
                                    server_files.encode({"account": account, "phase": "stopped"}), private=True)


# ================================================================== 1 本ずつ通る

def test_doctorは読み取りのprobeだけでVMの置き場を返さない(far, capsys, monkeypatch):
    rows = [{"key": "page", "label": "ページ", "permission": "pages_show_list", "ok": True,
             "detail": "読めました /srv/thth/state/alpha/secret.json", "body": "本文"},
            {"key": "posts", "label": "投稿", "permission": "pages_read_engagement", "ok": False, "detail": "×"}]
    monkeypatch.setattr(fb.FacebookAdapter, "probe", lambda self, get=None: [dict(r) for r in rows])
    rc, out, err = cli(capsys, "doctor", "alpha", "--remote", "--json")
    assert rc == 1, err  # × が 1 つあるので手元の doctor と同じ rc 1
    value = json.loads(out)
    assert [p["key"] for p in value["probes"]] == ["page", "posts"]
    assert "body" not in value["probes"][0] and "/srv/thth" not in out
    for key in ("directory_checks", "accounts_dir", "notices", "app_env", "media_cleanup"):
        assert key not in value
    assert far["worker"].calls[-1]["path"] == "/api/v1/doctor"
    rc, out, err = cli(capsys, "doctor", "alpha", "--remote")
    assert rc == 1 and "○ ページ（pages_show_list）" in out and "× 投稿" in out


def test_行動ボタンは読んで二段で消す(far, capsys):
    rc, out, err = cli(capsys, "page", "cta", "show", "alpha", "--remote", "--json")
    assert rc == 0, err
    assert json.loads(out)["call_to_actions"][0]["type"] == "SHOP_NOW"
    (rc, out, err), first = two_steps(capsys, "page", "cta", "delete", "alpha", CTA, "--by", "審査員")
    assert rc == 0, err
    assert json.loads(out)["changed"] is True and first["by"] == "person"  # by は鍵の持ち主（--by は送らない）
    assert all("by" not in (c["body"] or {}) for c in far["worker"].calls)
    assert [(c["method"], c["path"]) for c in writes(far)] == [("DELETE", V + "/" + CTA)]


def test_ライブは宛先を1回だけ端末に返しVMに残さず二段で終える(far, capsys):
    (rc, out, err), _ = two_steps(capsys, "live", "create", "alpha", "--title", "試しの配信", "--by", "m")
    assert rc == 0, err
    value = json.loads(out)
    assert value["live_id"] == LIVE and STREAM_KEY in value["stream_url"] and value["storage"] == "display_only"
    assert sum(STREAM_KEY in json.dumps(e["result"]) for e in far["entries"]) == 1  # 答えの 1 回だけ
    kept = [r for r in requests_records(far) if r.get("operation") == "live_create"]
    assert kept and all(r["result"] is None or r["result"].get("stream_url") is None for r in kept)
    (rc, out, err), _ = two_steps(capsys, "live", "end", "alpha", LIVE, "--by", "m")
    assert rc == 0, err
    assert writes(far)[-1]["form"]["end_live_video"] == ["true"]
    nothing_kept(far, STREAM_KEY)


def test_人向けのライブの画面は宛先を出し終える命令を添える(far, capsys):
    rc, out, _err = cli(capsys, "live", "create", "alpha", "--title", "試しの配信", "--remote")
    assert rc == 1 and "確認の一段目" in out
    digest = re.search(r"digest: (\S+)", out).group(1)
    rc, out, err = cli(capsys, "live", "create", "alpha", "--title", "試しの配信", "--remote", "--confirm", digest)
    assert rc == 0, err
    assert STREAM_KEY in out and f"thth live end alpha {LIVE}" in out
    nothing_kept(far, STREAM_KEY)


def test_dmは読んで窓の中で二段で返し本文も相手もVMに残さない(far, capsys):
    rc, out, err = cli(capsys, "dm", "list", "alpha", "--remote", "--json")
    assert rc == 0, err
    assert json.loads(out)["conversations"][0]["id"] == FB_CONVO
    rc, out, err = cli(capsys, "dm", "read", "alpha", FB_CONVO, "--remote")
    assert rc == 0 and INBOUND in out and "返せる窓:" in out, err
    (rc, out, err), first = two_steps(capsys, "dm", "send", "alpha", FB_CONVO, "--text", OUTBOUND, "--by", "m")
    assert rc == 0, err
    assert first["text"] == OUTBOUND
    sent_calls = writes(far)
    assert len(sent_calls) == 1 and sent_calls[0]["path"] == V + f"/{PAGE}/messages"
    assert sent_calls[0]["form"]["messaging_type"] == ["RESPONSE"]
    nothing_kept(far, OUTBOUND, INBOUND, NAME)


def test_dmの窓を過ぎた会話は遠くの道でも断る(far, capsys):
    far["state"]["inbound_at"] = ago(days=2)
    rc, out, err = cli(capsys, "dm", "send", "alpha", FB_CONVO, "--text", OUTBOUND, "--remote")
    assert rc != 0 and err.startswith("messaging_window_closed") and "通すには:" in err
    assert writes(far) == []


def test_Instagramのいいねは付けて外せる(far, capsys):
    rc, out, err = cli(capsys, "like", "beta", IG_MEDIA, "--by", "m", "--remote", "--json")
    assert rc == 0, err
    rc, out, err = cli(capsys, "unlike", "beta", IG_MEDIA, "--by", "m", "--remote", "--json")
    assert rc == 0, err
    assert json.loads(out)["record"]["via"] == "api"
    assert [(c["method"], c["path"]) for c in writes(far)] == [("POST", V + f"/{IG_ID}/likes"),
                                                               ("DELETE", V + f"/{IG_ID}/likes")]


def test_editは二段で直しVMのpathを返さない(far, capsys):
    state = accounts.state_dir_for("alpha")
    sent.write(state, post_id=POST, text="元の本文", body_hash=approval.compute_body_hash("元の本文"),
               sent_at="2026-11-05T10:00:00+09:00")
    (rc, out, err), first = two_steps(capsys, "edit", "alpha", POST, "--text", EDITED, "--by", "m")
    assert rc == 0, err
    assert first["previous_text"] == "元の本文" and "record" not in json.loads(out)
    assert str(far["root"]) not in out
    assert writes(far)[0]["form"]["message"] == [EDITED]
    # 印には直したあとの本文を書かない（記録の正本は sent/ の 1 か所）。
    assert all(EDITED not in json.dumps(r, ensure_ascii=False) for r in requests_records(far))


def test_reply_hideは1段で隠しdeleteは遠くの道に出さない(far, capsys):
    rc, out, err = cli(capsys, "reply", "hide", "alpha", COMMENT, "--by", "m", "--remote", "--json")
    assert rc == 0, err
    assert json.loads(out)["changed"] is True and writes(far)[0]["form"]["is_hidden"] == ["true"]
    rc, out, err = cli(capsys, "reply", "delete", "alpha", COMMENT, "--reason", "r", "--by", "m", "--remote")
    assert rc == 2 and err.startswith("remote_unsupported")
    assert len(writes(far)) == 1


# ================================================================== 柵（わざと壊して赤を見た所）

def test_遠くの道のdm_sendは一段目と違うdigestで断られ網に出ない(far, capsys):
    rc, out, _err = cli(capsys, "dm", "send", "alpha", FB_CONVO, "--text", OUTBOUND, "--remote", "--json")
    digest = json.loads(out)["digest"]
    wrong = ("0" if digest[0] != "0" else "1") + digest[1:]
    rc, out, err = cli(capsys, "dm", "send", "alpha", FB_CONVO, "--text", OUTBOUND, "--remote", "--confirm", wrong)
    assert rc != 0 and err.startswith("meta_write_digest_mismatch") and "通すには:" in err
    # 本文を変えても、一段目の digest では通らない（digest は VM がその場で作り直す）。
    rc, out, err = cli(capsys, "dm", "send", "alpha", FB_CONVO, "--text", OUTBOUND + "!", "--remote",
                       "--confirm", digest)
    assert rc != 0 and err.startswith("meta_write_digest_mismatch")
    assert writes(far) == []


def test_Metaの断りの文は依頼の印に残さない(far, capsys):
    # Meta の error.message は相手の id や本文を反響しうる。印（最長 150 秒）には符丁だけ（確かめ役 11/06）。
    marker = "反響した目印 5551"
    far["routes"][("POST", V + f"/{PAGE}/messages")] = (400, {"error": {"message": f"(#100) bad {marker}",
                                                                      "code": 100}})
    (rc, out, err), _ = two_steps(capsys, "dm", "send", "alpha", FB_CONVO, "--text", OUTBOUND, "--by", "m")
    assert rc != 0
    records = [r for r in requests_records(far) if isinstance(r.get("result"), dict) and r["result"].get("error")]
    assert records and all(r["result"].get("reason") is None and "reason" in r["result"]["not_kept"]
                           for r in records)
    nothing_kept(far, marker)


WRITE_ARGV = [
    ("dm", "send", "alpha", FB_CONVO, "--text", "x"),
    ("live", "create", "alpha", "--title", "t"),
    ("live", "end", "alpha", LIVE),
    ("page", "cta", "delete", "alpha", CTA),
    ("like", "beta", IG_MEDIA),
    ("unlike", "beta", IG_MEDIA),
    ("edit", "alpha", POST, "--text", "x"),
    ("reply", "hide", "alpha", COMMENT),
    ("reply", "unhide", "alpha", COMMENT),
]


@pytest.mark.parametrize("argv", WRITE_ARGV, ids=lambda a: " ".join(a[:2]))
def test_読む資格だけの鍵では書く口は一段目も通らない(far, capsys, argv):
    # 確かめ役 11/06: execute を write=False 固定に壊しても緑だった穴を塞ぐ。
    far["value"]["credentials"][0]["writes"] = False
    save_config(far)
    rc, out, err = cli(capsys, *argv, "--remote", "--json")
    assert rc != 0 and "writes_not_allowed" in out + err, out + err
    assert writes(far) == []


def test_CLIはdigestを作らずVMの一段目のdigestを渡すだけ(far, capsys):
    rc, out, _err = cli(capsys, "dm", "send", "alpha", FB_CONVO, "--text", OUTBOUND, "--remote", "--json")
    digest = json.loads(out)["digest"]
    first = far["worker"].calls[-1]["body"]
    assert "confirm" not in first and first["text"] == OUTBOUND
    cli(capsys, "dm", "send", "alpha", FB_CONVO, "--text", OUTBOUND, "--remote", "--confirm", digest, "--json")
    assert far["worker"].calls[-1]["body"]["confirm"] == digest


@pytest.mark.parametrize("how", ["guard", "leave"])
def test_止めた口座で遠くの道のlive_createは網に出ない(far, capsys, how):
    rc, out, _err = cli(capsys, "live", "create", "alpha", "--title", "試しの配信", "--remote", "--json")
    digest = json.loads(out)["digest"]
    if how == "guard":
        guard.stop("alpha", "owner", actor="t")
    else:
        stop_leave("alpha")
    rc, out, err = cli(capsys, "live", "create", "alpha", "--title", "試しの配信", "--remote", "--confirm", digest)
    # 退出（leave_gate）で止めた口座は、資格を読む所で鍵の範囲から外れる（今の遠くの道の読む口と同じ符丁）。
    first = err.splitlines()[0].split(":")[0]
    assert rc != 0 and first == ("account_stopped" if how == "guard" else "scope_unavailable"), err
    assert "Traceback" not in err
    assert writes(far) == []


def test_productionの無い口座は遠くの道でも二段目で呼ばない(far, capsys):
    rc, out, _err = cli(capsys, "page", "cta", "delete", "alpha", CTA, "--remote", "--json")
    digest = json.loads(out)["digest"]
    path = far["root"] / "accounts" / "alpha.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "production": False}))
    rc, out, err = cli(capsys, "page", "cta", "delete", "alpha", CTA, "--remote", "--confirm", digest)
    assert rc != 0 and err.startswith("production_disabled")
    assert writes(far) == []


def test_遠くの道の答えのstream_urlはVMの記録に残らない(far, capsys):
    """`api_requests` の印は送り直しの間（150 秒）残る。そこに宛先を書かない（送り直しは null と not_kept）。"""
    (rc, out, err), _ = two_steps(capsys, "live", "create", "alpha", "--title", "試しの配信")
    assert rc == 0 and STREAM_KEY in out
    for record in requests_records(far):
        assert STREAM_KEY not in json.dumps(record)
    resent = api_requests.undelivered(["alpha"])
    for entry in resent:
        assert STREAM_KEY not in json.dumps(entry)
    nothing_kept(far, STREAM_KEY)


def test_送り直しの答えは宛先がnullで人向けの画面がそう言う(far, capsys):
    record = server_meta.for_record("live_create", {"ok": True, "live_id": LIVE, "stream_url": "rtmps://x/" + STREAM_KEY})
    assert record["stream_url"] is None and record["not_kept"] == ["stream_url"]
    far["worker"].push(200, record)
    rc, out, err = cli(capsys, "live", "create", "alpha", "--title", "t", "--remote", "--confirm", "0" * 12)
    assert rc == 0 and "届けられませんでした" in out and f"thth live end alpha {LIVE}" in out


def test_VMの捕まえた文はVMのログに出ない(far, capsys):
    cli(capsys, "dm", "read", "alpha", FB_CONVO, "--remote", "--json")
    cli(capsys, "dm", "send", "alpha", FB_CONVO, "--text", OUTBOUND, "--remote", "--confirm", "0" * 12)
    assert far["vm_log"].getvalue() == ""


def test_MCPの書く口の表には足さない():
    assert not set(server_meta.OPERATIONS) & set(server_writes.WRITE_OPERATIONS)
    assert set(server_meta.REASONS) <= set(server_writes.SAFE_ERRORS)


def test_知らない欄と型の違う欄は断る(far):
    from thth.report_service import ReportServiceError
    context = far["context"]
    for request in ({"operation": "dm_send", "account": "alpha", "by": "誰か"},
                    {"operation": "dm_read", "account": "alpha", "target": FB_CONVO, "profile": "yes"},
                    {"operation": "live_create", "account": "alpha"}):
        with pytest.raises(ReportServiceError) as caught:
            server_writes.serve(context, request, via="api")
        assert str(caught.value) == "invalid_request"


# ================================================================== 台帳が無く login 済みなら遠くの道

ROUTES = [
    (("doctor", ACCOUNT, "--json"), "doctor", {}),
    (("page", "cta", "show", ACCOUNT, "--json"), "page_cta_show", {}),
    (("page", "cta", "delete", ACCOUNT, CTA, "--by", "m", "--json"), "page_cta_delete", {"cta_id": CTA}),
    (("live", "create", ACCOUNT, "--title", "t", "--by", "m", "--json"), "live_create", {"title": "t"}),
    (("live", "end", ACCOUNT, LIVE, "--by", "m", "--confirm", "a" * 12, "--json"), "live_end",
     {"live_id": LIVE, "confirm": "a" * 12}),
    (("dm", "list", ACCOUNT, "--json"), "dm_list", {}),
    (("dm", "read", ACCOUNT, FB_CONVO, "--profile", "--json"), "dm_read", {"target": FB_CONVO, "profile": True}),
    (("dm", "send", ACCOUNT, FB_CONVO, "--text", "返事", "--human-agent", "--by", "m", "--json"), "dm_send",
     {"target": FB_CONVO, "text": "返事", "human_agent": True}),
    (("like", ACCOUNT, IG_MEDIA, "--by", "m", "--json"), "like", {"post": IG_MEDIA}),
    (("unlike", ACCOUNT, IG_MEDIA, "--by", "m", "--json"), "unlike", {"post": IG_MEDIA}),
    (("edit", ACCOUNT, POST, "--text", "直す", "--by", "m", "--json"), "edit_post", {"post_id": POST, "text": "直す"}),
    (("reply", "hide", ACCOUNT, COMMENT, "--by", "m", "--json"), "reply_hide", {"comment_id": COMMENT}),
    (("reply", "unhide", ACCOUNT, COMMENT, "--by", "m", "--json"), "reply_unhide", {"comment_id": COMMENT}),
]


@pytest.mark.parametrize("argv,operation,fields", ROUTES)
def test_台帳が無くloginしていれば各命令が遠くの道に届く(worker, capsys, argv, operation, fields):  # noqa: F811
    rc, _out, err = run(capsys, *argv)
    assert "台帳" not in err
    call = worker.calls[-1]
    assert call["path"] == "/api/v1/" + operation
    assert call["body"] == {"account": ACCOUNT, **fields}


def test_待つ旗は遠くの道で黙って捨てずに断る(worker, capsys):  # noqa: F811
    rc, _out, err = run(capsys, "dm", "list", ACCOUNT, "--wait", "5")
    assert rc == 2 and err.startswith("remote_unsupported") and worker.calls == []


def test_新しい符丁は3つがそろい遠くの道の口を持つ():
    for code in sorted(server_meta.REASONS):
        entry = refusal_catalog.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"] and "remote" in entry["doors"], code
    assert refusal_catalog.CATALOG["remote_command_refused"]["since"] == "5.13.0"
