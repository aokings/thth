"""計画 Meta 全部盛り申請 M4: 書く最小の口。

見るのは:
  - `thth page cta show|delete`・`thth live create|end`・`thth dm list|read|send`（FB と IG・コメントへの非公開の返事）・
    `thth like`（Instagram）・`thth send --share-to-instagram`・Facebook の本文の `@[id]` が偽の Graph で 1 本ずつ通る。
  - **二段確認**: 一段目は書く口を呼ばない。digest が違えば書かない。`production: true` が無ければ書かない。
  - **安全装置**（止めの印）と **leave_gate**（止めた口座）で書く口を呼ばない。
  - **24 時間の窓**: 過ぎた会話は `messaging_window_closed`（網に出る前と、Meta が窓の外と答えたときの両方）。
    人の担当者の 7 日は `HUMAN_AGENT` の札だけ。コメントへの非公開の返事は 7 日。
  - **本文・相手・配信の鍵はどこにも残さない**。runs の行が `permission_watch` の「最後に成功した日」に数えられる。
偽の Graph API（loopback）だけを使う。本物の Meta は叩かない。
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import secrets

import pytest

from thth import accounts, cli, guard, jst, leave_gate, meta_mention, meta_write, server_files
from thth import permission_watch as pw
from thth import refusal_catalog
from thth.adapters import facebook as fb
from tests.test_facebook_adapter import fake_graph

V = "/" + fb.GRAPH_VERSION
PAGE, IG_ID = "61596075596464", "17841437318552220"
FB_ACCOUNT, IG_ACCOUNT, TH_ACCOUNT = "m4-fb", "m4-ig", "m4-th"
FB_CONVO, IG_CONVO = "t_100", "aWdfZAG06"
FB_PERSON, IG_PERSON = "5551", "6661"
FB_COMMENT, IG_COMMENT, IG_MEDIA = PAGE + "_55", "17890000000000001", "17895695668004550"
CTA, LIVE = "901", "7001"
# 残ってはいけない目印。
INBOUND, OUTBOUND, STREAM_KEY = "お客さまの問い合わせの目印", "こちらの返事の目印", "FB-" + secrets.token_hex(8)
NAME = "お客の名前の目印"


def ago(**kw):
    return jst.iso(jst.now_jst() - datetime.timedelta(**kw))


@pytest.fixture
def m4(isolated_account_factory, tmp_path, monkeypatch):
    app = tmp_path / "app.env"
    app.write_text("META_APP_ID=4242\nMETA_APP_SECRET=" + secrets.token_urlsafe(32) + "\n")
    app.chmod(0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    for name, extra in ((FB_ACCOUNT, {"media": "facebook", "page_id": PAGE, "handle": PAGE, "user_id": PAGE}),
                        (IG_ACCOUNT, {"media": "instagram", "ig_user_id": IG_ID, "page_id": PAGE, "handle": IG_ID,
                                      "user_id": IG_ID}),
                        (TH_ACCOUNT, {"media": "threads", "user_id": "12345", "instagram_linked": True})):
        token = tmp_path / f"{name}.token"
        token.write_text(json.dumps({"access_token": "tok-" + secrets.token_hex(12), "user_id": extra["user_id"]}))
        token.chmod(0o600)
        isolated_account_factory(name, token=str(token), production=True, **extra)
    state = {"inbound_at": ago(hours=1), "comment_at": ago(days=1), "send": (200, {"recipient_id": FB_PERSON,
                                                                                   "message_id": "m_1"})}

    def conversations(query, form, raw):
        if query.get("platform") == ["instagram"]:
            return 200, {"data": [{"id": IG_CONVO, "updated_time": state["inbound_at"], "participants": {"data": [
                {"id": IG_ID, "username": "me"}, {"id": IG_PERSON, "username": NAME}]}}]}
        return 200, {"data": [{"id": FB_CONVO, "updated_time": state["inbound_at"], "participants": {"data": [
            {"id": PAGE, "name": "ページ"}, {"id": FB_PERSON, "name": NAME}]}}]}

    def convo(me, person):
        def answer(query, form, raw):
            return 200, {"id": "x", "participants": {"data": [{"id": me}, {"id": person, "name": NAME}]},
                         "messages": {"data": [
                             {"id": "m_2", "created_time": state["inbound_at"], "from": {"id": person, "name": NAME},
                              "message": INBOUND},
                             {"id": "m_1", "created_time": ago(days=3), "from": {"id": me}, "message": "前の返事"}]}}
        return answer

    def profile(query, form, raw):
        field = query["fields"][0]
        return 200, {field: {"locale": "ja_JP", "timezone": 9, "gender": "female"}[field]}

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
        ("GET", V + f"/{FB_CONVO}"): convo(PAGE, FB_PERSON),
        ("GET", V + f"/{IG_CONVO}"): convo(IG_ID, IG_PERSON),
        ("POST", V + f"/{PAGE}/messages"): lambda q, f, r: state["send"],
        ("GET", V + f"/{FB_COMMENT}"): lambda q, f, r: (200, {"id": FB_COMMENT, "created_time": state["comment_at"]}),
        ("GET", V + f"/{IG_COMMENT}"): lambda q, f, r: (200, {"id": IG_COMMENT, "timestamp": state["comment_at"]}),
        ("GET", V + f"/{FB_PERSON}"): profile,
        ("POST", V + f"/{IG_MEDIA}/likes"): (200, {"success": True}),
        ("DELETE", V + f"/{IG_MEDIA}/likes"): (200, {"success": True}),
        ("POST", V + f"/{PAGE}/feed"): (200, {"id": PAGE + "_9"}),
    }
    with fake_graph(table) as (url, calls):
        monkeypatch.setenv(fb.BASE_URL_ENV, url)
        yield {"tmp": tmp_path, "routes": table, "calls": calls, "state": state}


def main(capsys, *argv):
    rc = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


def writes(m4):
    return [c for c in m4["calls"] if c["method"] in ("POST", "DELETE")]


def two_steps(capsys, *argv):
    """一段目（digest を読む）→ 二段目。戻りは二段目の `(rc, out, err)` と一段目の JSON。"""
    rc, out, err = main(capsys, *argv, "--json")
    assert rc == 1, err
    first = json.loads(out)
    assert first["stage"] == 1 and re.fullmatch(r"[0-9a-f]{12}", first["digest"])
    return main(capsys, *argv, "--confirm", first["digest"], "--json"), first


def nothing_kept(m4, *markers):
    for path in pathlib.Path(m4["tmp"]).rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            for marker in markers:
                assert marker.encode() not in raw, f"{path} に {marker} が残っている"


def found(name):
    cfg = accounts.load_account(name)
    return pw._account_row(name, cfg, jst.now_jst())["found"]


def stop_leave(account):
    with leave_gate.lease(account, exclusive=True):
        with server_files.directory(leave_gate.location(), create=True, private=True) as fd:
            server_files.replace_at(fd, account + ".json",
                                    server_files.encode({"account": account, "phase": "stopped"}), private=True)


# ================================================================== 1 本ずつ通る

def test_行動ボタンは読んで二段で消す(m4, capsys):
    rc, out, err = main(capsys, "page", "cta", "show", FB_ACCOUNT, "--json")
    assert rc == 0, err
    assert json.loads(out)["call_to_actions"][0]["type"] == "SHOP_NOW"
    (rc, out, err), first = two_steps(capsys, "page", "cta", "delete", FB_ACCOUNT, CTA, "--by", "m")
    assert rc == 0, err
    assert json.loads(out)["changed"] is True
    assert [(c["method"], c["path"]) for c in writes(m4)] == [("DELETE", V + "/" + CTA)]
    assert "page_cta_delete" in found(FB_ACCOUNT)


def test_ライブは宛先を画面に出すだけで残さず二段で終える(m4, capsys):
    (rc, out, err), _ = two_steps(capsys, "live", "create", FB_ACCOUNT, "--title", "試しの配信", "--by", "m")
    assert rc == 0, err
    value = json.loads(out)
    assert value["live_id"] == LIVE and STREAM_KEY in value["stream_url"] and value["storage"] == "display_only"
    post = writes(m4)[0]
    assert post["path"] == V + f"/{PAGE}/live_videos" and post["form"]["status"] == ["LIVE_NOW"]
    (rc, out, err), _ = two_steps(capsys, "live", "end", FB_ACCOUNT, LIVE, "--by", "m")
    assert rc == 0, err
    assert writes(m4)[-1]["form"]["end_live_video"] == ["true"]
    nothing_kept(m4, STREAM_KEY)
    assert {"live_create", "live_end"} <= set(found(FB_ACCOUNT))


@pytest.mark.parametrize("account,convo,person", [(FB_ACCOUNT, FB_CONVO, FB_PERSON), (IG_ACCOUNT, IG_CONVO, IG_PERSON)])
def test_dmは読んで窓の中で二段で返し本文も相手も残さない(m4, capsys, account, convo, person):
    rc, out, err = main(capsys, "dm", "list", account, "--json")
    assert rc == 0, err
    assert json.loads(out)["conversations"][0]["id"] == convo
    listed = next(c for c in m4["calls"] if c["path"].endswith("/conversations"))
    assert listed["query"]["platform"] == ["instagram" if account == IG_ACCOUNT else "messenger"]
    rc, out, err = main(capsys, "dm", "read", account, convo)
    assert rc == 0 and INBOUND in out and "返せる窓:" in out
    (rc, out, err), first = two_steps(capsys, "dm", "send", account, convo, "--text", OUTBOUND, "--by", "m")
    assert rc == 0, err
    assert first["text"] == OUTBOUND
    sent = writes(m4)
    assert len(sent) == 1 and sent[0]["path"] == V + f"/{PAGE}/messages"
    assert json.loads(sent[0]["form"]["recipient"][0]) == {"id": person}
    assert sent[0]["form"]["messaging_type"] == ["RESPONSE"] and "tag" not in sent[0]["form"]
    assert json.loads(sent[0]["form"]["message"][0]) == {"text": OUTBOUND}
    nothing_kept(m4, OUTBOUND, INBOUND, NAME, person)
    assert {"dm_list", "dm_read", "dm_send"} <= set(found(account))


def test_コメントへの非公開の返事は7日の内に1回だけ打つ(m4, capsys):
    (rc, out, err), _ = two_steps(capsys, "dm", "send", IG_ACCOUNT, "--comment", IG_COMMENT, "--text", OUTBOUND,
                                  "--by", "m")
    assert rc == 0, err
    assert json.loads(writes(m4)[0]["form"]["recipient"][0]) == {"comment_id": IG_COMMENT}
    m4["state"]["comment_at"] = ago(days=8)
    rc, out, err = main(capsys, "dm", "send", FB_ACCOUNT, "--comment", FB_COMMENT, "--text", OUTBOUND, "--by", "m")
    assert rc == 1 and "private_reply_window_closed" in err and "通すには:" in err
    assert len(writes(m4)) == 1


def test_相手の属性は1欄ずつ読み画面に出すだけ(m4, capsys):
    rc, out, err = main(capsys, "dm", "read", FB_ACCOUNT, FB_CONVO, "--profile", "--json")
    assert rc == 0, err
    assert json.loads(out)["profile"] == {"locale": "ja_JP", "timezone": 9, "gender": "female"}
    asked = [c["query"]["fields"] for c in m4["calls"] if c["path"] == V + "/" + FB_PERSON]
    assert asked == [["locale"], ["timezone"], ["gender"]]
    assert "dm_profile" in found(FB_ACCOUNT)
    rc, _out, err = main(capsys, "dm", "read", IG_ACCOUNT, IG_CONVO, "--profile")
    assert rc == 2 and "meta_write_unsupported" in err


def test_Instagramのいいねは付けて外せ_最後に成功した日に数える(m4, capsys):
    rc, out, err = main(capsys, "like", IG_ACCOUNT, IG_MEDIA, "--by", "m", "--json")
    assert rc == 0, err
    rc, out, err = main(capsys, "unlike", IG_ACCOUNT, IG_MEDIA, "--by", "m", "--json")
    assert rc == 0, err
    assert [(c["method"], c["path"]) for c in writes(m4)] == [("POST", V + f"/{IG_MEDIA}/likes"),
                                                              ("DELETE", V + f"/{IG_MEDIA}/likes")]
    assert "ig_like" in found(IG_ACCOUNT)
    guard.stop(IG_ACCOUNT, "owner", actor="t")
    rc, out, err = main(capsys, "like", IG_ACCOUNT, IG_MEDIA + "1", "--by", "m")
    assert rc == 1 and len(writes(m4)) == 2


def test_ストーリーズへの共有はThreadsの投稿に付きdigestに入り記録に残る(m4, capsys, monkeypatch):
    from tests.test_fake_api import fake_threads_server
    body = m4["tmp"] / "body.txt"
    body.write_text("ストーリーズにも\n", encoding="utf-8")
    rc, out, _err = main(capsys, "send", TH_ACCOUNT, "--text-file", str(body))
    plain = re.search(r"digest: (\S+)", out).group(1)
    rc, out, _err = main(capsys, "send", TH_ACCOUNT, "--text-file", str(body), "--share-to-instagram")
    assert rc == 0 and "ストーリーズにも出します" in out
    digest = re.search(r"digest: (\S+)", out).group(1)
    assert digest != plain
    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        monkeypatch.setenv("THTH_THREADS_WAIT_SECONDS", "0")
        # 共有なしの digest では通らない（共有の有無は承認の中身）。
        rc, out, err = main(capsys, "send", TH_ACCOUNT, "--text-file", str(body), "--share-to-instagram",
                            "--production", "--confirm", plain)
        assert rc == 1 and base_url.handler_cls.create_params == []
        rc, out, err = main(capsys, "send", TH_ACCOUNT, "--text-file", str(body), "--share-to-instagram",
                            "--production", "--confirm", digest)
        assert rc == 0, out + err
        assert base_url.handler_cls.create_params[0]["crossreshare_to_ig"] == "true"
    assert "share_to_instagram" in found(TH_ACCOUNT)


@pytest.mark.parametrize("account,extra", [(FB_ACCOUNT, ()), (TH_ACCOUNT, ("--reply-to", "123"))])
def test_ストーリーズへの共有はThreadsの返信でない投稿だけ(m4, capsys, account, extra):
    rc, out, err = main(capsys, "send", account, "--text", "共有したい", "--share-to-instagram", *extra)
    assert rc == 2 and "share_to_instagram" in out + err and "通すには:" in out + err
    assert m4["calls"] == []


def test_ページへの言及は数字のidだけを通す(m4, capsys):
    rc, out, err = main(capsys, "send", FB_ACCOUNT, "--text", "お世話になっている @[123456] さん")
    assert rc == 0 and "ページへの言及: 123456" in out
    digest = re.search(r"digest: (\S+)", out).group(1)
    rc, out, err = main(capsys, "send", FB_ACCOUNT, "--text", "お世話になっている @[123456] さん", "--production",
                        "--confirm", digest)
    assert rc == 0, out + err
    assert writes(m4)[0]["form"]["message"] == ["お世話になっている @[123456] さん"]
    for bad in ("@[こぴちゃ] さん", "@[123 さん", "@[] さん"):
        rc, out, err = main(capsys, "send", FB_ACCOUNT, "--text", bad)
        assert rc == 2 and "page_mention_invalid" in out + err and "通すには:" in out + err
    assert meta_mention.problem("メール a@[x] は") is not None and meta_mention.problem("@ [x] は言及でない") is None
    assert len(writes(m4)) == 1


# ================================================================== 二段確認・安全装置・leave_gate

WRITE_COMMANDS = [
    ("page", "cta", "delete", FB_ACCOUNT, CTA, "--by", "m"),
    ("live", "create", FB_ACCOUNT, "--title", "試しの配信", "--by", "m"),
    ("live", "end", FB_ACCOUNT, LIVE, "--by", "m"),
    ("dm", "send", FB_ACCOUNT, FB_CONVO, "--text", OUTBOUND, "--by", "m"),
    ("dm", "send", IG_ACCOUNT, "--comment", IG_COMMENT, "--text", OUTBOUND, "--by", "m"),
]


@pytest.mark.parametrize("argv", WRITE_COMMANDS)
def test_一段目と違うdigestは書く口を呼ばない(m4, capsys, argv):
    rc, out, err = main(capsys, *argv)
    assert rc == 1 and "digest:" in out and "確認の一段目" in out
    rc, out, err = main(capsys, *argv, "--confirm", "0" * 12)
    assert rc == 1 and "meta_write_digest_mismatch" in err and "通すには:" in err
    assert writes(m4) == []


@pytest.mark.parametrize("argv", WRITE_COMMANDS)
def test_productionの無い口座では二段目でも呼ばない(m4, capsys, argv):
    rc, out, _err = main(capsys, *argv, "--json")
    digest = json.loads(out)["digest"]
    name = argv[3] if argv[0] == "page" else argv[2]
    path = pathlib.Path(os.environ["THTH_ACCOUNTS_DIR"]) / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["production"] = False
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rc, out, err = main(capsys, *argv, "--confirm", digest)
    assert rc == 1 and "production_disabled" in err
    assert writes(m4) == []


@pytest.mark.parametrize("argv", WRITE_COMMANDS)
def test_安全装置で止めた口座は二段目でも呼ばない(m4, capsys, argv):
    rc, out, _err = main(capsys, *argv, "--json")
    digest = json.loads(out)["digest"]
    guard.stop(argv[3] if argv[0] == "page" else argv[2], "owner", actor="t")
    rc, out, err = main(capsys, *argv, "--confirm", digest)
    assert rc == 1 and "account_stopped" in err
    assert writes(m4) == []


@pytest.mark.parametrize("argv", WRITE_COMMANDS + [("dm", "list", FB_ACCOUNT), ("page", "cta", "show", FB_ACCOUNT)])
def test_leave_gateで止めた口座は網に出ない(m4, capsys, argv):
    stop_leave(argv[3] if argv[0] == "page" else argv[2])
    rc, out, err = main(capsys, *argv, "--confirm", "0" * 12) if "--by" in argv else main(capsys, *argv)
    assert rc != 0 and "account_stopped" in err and "Traceback" not in err
    assert m4["calls"] == []


def test_止める前に組んだadapterも止めたあとは書く口を呼ばない(m4):
    cfg = accounts.load_account(FB_ACCOUNT)
    adapter = meta_write._adapter(cfg, accounts.load_token(cfg))
    stop_leave(FB_ACCOUNT)
    for call in (lambda: adapter.dm_send(FB_PERSON, OUTBOUND), lambda: adapter.live_create("t"),
                 lambda: adapter.delete_page_cta(CTA), lambda: adapter.private_reply(FB_COMMENT, OUTBOUND)):
        with pytest.raises(accounts.AccountStopped):
            call()
    assert m4["calls"] == []


# ================================================================== 24 時間の窓

def test_24時間を過ぎた窓ではdm_sendがmessaging_window_closedで断られる(m4, capsys):
    m4["state"]["inbound_at"] = ago(hours=30)
    rc, out, err = main(capsys, "dm", "send", FB_ACCOUNT, FB_CONVO, "--text", OUTBOUND, "--by", "m")
    assert rc == 1 and "messaging_window_closed" in err
    assert "なぜ:" not in err.splitlines()[0] and "通すには:" in err and "もっと良く:" in err
    assert "--human-agent" in err
    assert writes(m4) == []


def test_窓の外は人の担当者の札で7日の内だけ送れる(m4, capsys):
    m4["state"]["inbound_at"] = ago(days=2)
    (rc, out, err), _ = two_steps(capsys, "dm", "send", IG_ACCOUNT, IG_CONVO, "--text", OUTBOUND, "--human-agent",
                                  "--by", "m")
    assert rc == 0, err
    form = writes(m4)[0]["form"]
    assert form["messaging_type"] == ["MESSAGE_TAG"] and form["tag"] == ["HUMAN_AGENT"]
    m4["state"]["inbound_at"] = ago(days=8)
    rc, out, err = main(capsys, "dm", "send", IG_ACCOUNT, IG_CONVO, "--text", OUTBOUND, "--human-agent", "--by", "m")
    assert rc == 1 and "messaging_window_closed" in err and len(writes(m4)) == 1


def test_一段目のあとに窓が閉じたら二段目は通らない(m4, capsys):
    rc, out, _err = main(capsys, "dm", "send", FB_ACCOUNT, FB_CONVO, "--text", OUTBOUND, "--by", "m", "--json")
    digest = json.loads(out)["digest"]
    m4["state"]["inbound_at"] = ago(hours=25)
    rc, out, err = main(capsys, "dm", "send", FB_ACCOUNT, FB_CONVO, "--text", OUTBOUND, "--by", "m",
                        "--confirm", digest)
    assert rc == 1 and "messaging_window_closed" in err and writes(m4) == []


# subcode は【推】。本物で違う・無いときも、code 10 と窓の外の文なら窓の断り（権限の不足と取り違えない・確かめ役 11/05）。
@pytest.mark.parametrize("subcode", [2018278, None, 1234567])
def test_Metaが窓の外と答えたらmessaging_window_closedで済んだと言わない(m4, capsys, subcode):
    error = {"message": "(#10) This message is sent outside of allowed window.", "code": 10}
    if subcode is not None:
        error["error_subcode"] = subcode
    m4["state"]["send"] = (400, {"error": error})
    (rc, out, err), _ = two_steps(capsys, "dm", "send", FB_ACCOUNT, FB_CONVO, "--text", OUTBOUND, "--by", "m")
    assert rc == 1 and "messaging_window_closed" in out and "meta_write_permission_missing" not in out
    assert "dm_send" not in found(FB_ACCOUNT)


def test_持ち主の分からないライブは終えない(m4, capsys):
    m4["routes"][("GET", V + f"/{LIVE}")] = (200, {"id": LIVE, "status": "LIVE", "title": "試しの配信"})
    rc, _out, err = main(capsys, "live", "end", FB_ACCOUNT, LIVE, "--by", "m")
    assert rc == 1 and "live_not_this_page" in err
    assert writes(m4) == []


def test_Instagramの相手の属性は網に出る前に断り読んだ記録も残さない(m4, capsys):
    rc, _out, err = main(capsys, "dm", "read", IG_ACCOUNT, IG_CONVO, "--profile")
    assert rc == 2 and "meta_write_unsupported" in err
    assert m4["calls"] == [] and "dm_read" not in found(IG_ACCOUNT)


def test_相手から始まっていない会話には返さない():
    now = jst.now_jst()
    with pytest.raises(meta_write.Refused) as exc:
        meta_write.window([{"inbound": False, "created_time": jst.iso(now)}], now, human_agent=True)
    assert exc.value.code == "messaging_window_closed"
    assert meta_write.window([{"inbound": True, "created_time": jst.iso(now - datetime.timedelta(hours=23))}],
                             now, human_agent=False)["tag"] is None


def test_結果の分からない送信は済んだと言わず打ち直さない(m4, capsys):
    m4["state"]["send"] = (503, {"error": {"message": "temporarily unavailable", "code": 2}})
    (rc, out, err), _ = two_steps(capsys, "dm", "send", FB_ACCOUNT, FB_CONVO, "--text", OUTBOUND, "--by", "m")
    assert rc == 1 and "meta_write_unknown" in out and len(writes(m4)) == 1


@pytest.mark.parametrize("text,limit", [("", ("chars", 2000)), ("a\x07", ("chars", 2000)),
                                        ("あ" * 2001, ("chars", 2000)), ("あ" * 334, ("bytes", 1000))])
def test_本文は切り詰めずに断る(text, limit):
    with pytest.raises(meta_write.Refused) as exc:
        meta_write.check_text(text, limit)
    assert exc.value.code == "dm_text_invalid"
    assert meta_write.check_text("あ" * 333, ("bytes", 1000))


# ================================================================== 柵

def test_書く口を呼ぶのはmeta_writeだけ():
    root = pathlib.Path(__file__).resolve().parent.parent / "thth"
    doors = re.compile(r"\.(dm_send|private_reply|live_create|live_end|delete_page_cta)\(")
    offenders = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                       if doors.search(p.read_text(encoding="utf-8")) and p.name != "meta_write.py")
    assert offenders == []


def test_M4の符丁は目録にあり3つがそろう():
    for code in ("messaging_window_closed", "private_reply_window_closed", "dm_text_invalid", "dm_recipient_unknown",
                 "meta_write_unsupported", "meta_write_permission_missing", "meta_write_digest_mismatch",
                 "meta_write_refused", "meta_write_unknown", "meta_write_busy", "page_cta_not_found",
                 "live_title_invalid", "live_not_this_page", "page_mention_invalid", "share_to_instagram_unavailable"):
        entry = refusal_catalog.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"], code


def test_M4の権限は最後に成功した日の記録の種類を持つ():
    for name, kind in (("pages_manage_cta", "page_cta_delete"), ("publish_video", "live_create"),
                       ("pages_messaging", "dm_send"), ("instagram_manage_messages", "dm_send"),
                       ("instagram_manage_engagement", "ig_like"), ("threads_share_to_instagram", "share_to_instagram"),
                       ("pages_user_timezone", "dm_profile")):
        assert kind in pw.PERMISSIONS[name]["evidence"], name
    assert set(meta_write.RUN_ACTIONS) <= set(pw.RUN_ACTIONS)


def test_止めた口座で書く口はadapterの包みに入っている():
    import inspect
    source = inspect.getsource(leave_gate.bind)
    for name in ("delete_page_cta", "live_create", "live_end", "dm_send", "private_reply", "dm_messages"):
        assert f"'{name}'" in source, name
