"""計画 Meta 全部盛り申請: 会話から起きた出来事を Meta に送る口（`thth dm event`）と、認可のときに 1 回だけ読むメール。

見るのは:
  - `thth dm event` が Messenger・Instagram の偽の Graph で通る（dataset を作り、記録して使い回す・`data[]` の欄は
    公式の頁の例の名前）。WhatsApp は口の形（`user_data`）と試験だけで、`--channel whatsapp` は前向きに断る。
  - 二段確認・production・安全装置・leave_gate（`thth dm send` と同じ型）。相手の id・金額はどこにも残らない。
  - 権限の綴りの揺れ（`pages_events`／`page_events`）はどちらでも通す。結果の分からない送信は済んだと言わない。
  - メール: 認可のとき 1 回だけ読み、画面にだけ出す。`.token` には読めた印だけ・値は無い。doctor の 1 行。
偽の Graph API（loopback）だけを使う。本物の Meta は叩かない。
"""
from __future__ import annotations

import json
import os
import pathlib
import re

import pytest

from thth import accounts, guard, jst, meta_events, meta_write, refusal_catalog
from thth import permission_watch as pw
from thth.adapters import facebook as fb
from thth.adapters import instagram as ig
from tests.test_m4_meta_writes import (CTA, FB_ACCOUNT, FB_CONVO, FB_PERSON, IG_ACCOUNT, IG_CONVO, IG_ID,  # noqa: F401
                                        IG_PERSON, PAGE, V, found, m4, main, nothing_kept, stop_leave,
                                        two_steps, writes)

DATASET_FB, DATASET_IG = "880011223344", "880055667788"
PSID = "7770123"                       # 残ってはいけない相手の id の目印
AMOUNT = "987654"                      # 残ってはいけない金額の目印


@pytest.fixture
def ev(m4):
    m4["routes"].update({
        ("POST", V + f"/{PAGE}/dataset"): (200, {"id": DATASET_FB}),
        ("POST", V + f"/{IG_ID}/dataset"): (200, {"id": DATASET_IG}),
        ("POST", V + f"/{DATASET_FB}/events"): (200, {"events_received": 1, "messages": [], "fbtrace_id": "x"}),
        ("POST", V + f"/{DATASET_IG}/events"): (200, {"events_received": 1, "messages": [], "fbtrace_id": "x"}),
    })
    return m4


def sent_event(call):
    body = json.loads(call["form"]["data"][0])
    assert len(body) == 1
    return body[0]


FB_ARGV = ("dm", "event", FB_ACCOUNT, "--type", "purchase", "--psid", PSID, "--value", AMOUNT, "--currency", "JPY",
           "--by", "m")
IG_ARGV = ("dm", "event", IG_ACCOUNT, "--type", "lead", "--conversation", IG_CONVO, "--by", "m")


# ================================================================== 1 件ずつ通る

def test_Messengerの購入を送る_datasetを作って記録し次は使い回す(ev, capsys):
    (rc, out, err), first = two_steps(capsys, *FB_ARGV)
    assert rc == 0, err
    assert first["dataset"] == "will_create" and first["event_name"] == "Purchase" and first["channel"] == "messenger"
    made = writes(ev)
    assert [(c["method"], c["path"]) for c in made] == [("POST", V + f"/{PAGE}/dataset"),
                                                        ("POST", V + f"/{DATASET_FB}/events")]
    event = sent_event(made[1])
    assert event["event_name"] == "Purchase" and event["action_source"] == "business_messaging"
    assert event["messaging_channel"] == "messenger"
    assert event["user_data"] == {"page_id": PAGE, "page_scoped_user_id": PSID}
    assert event["custom_data"] == {"currency": "JPY", "value": int(AMOUNT)}
    assert isinstance(event["event_time"], int) and abs(event["event_time"] - jst.now_jst().timestamp()) < 60
    assert made[1]["form"]["partner_agent"] == ["thth"]
    assert meta_events.stored_dataset(FB_ACCOUNT, PAGE, "messenger") == DATASET_FB
    # 2 回目は dataset を作り直さない（記録を使う）。同じ日の同じ出来事は柵が断るので、別の出来事として --nth 2。
    (rc, out, err), again = two_steps(capsys, *FB_ARGV, "--nth", "2")
    assert rc == 0, err
    assert again["dataset"] == "saved"
    assert [c["path"] for c in writes(ev)[2:]] == [V + f"/{DATASET_FB}/events"]
    nothing_kept(ev, PSID, AMOUNT)
    assert "dm_event" in found(FB_ACCOUNT)


def test_runsの1行は種類と件数だけで相手も金額も入らない(ev, capsys):
    (rc, _out, err), _ = two_steps(capsys, *FB_ARGV)
    assert rc == 0, err
    state = accounts.state_dir_for(FB_ACCOUNT)
    rows = [json.loads(line) for path in pathlib.Path(state).glob("runs-*.ndjson")
            for line in path.read_text(encoding="utf-8").splitlines() if '"dm_event"' in line]
    assert len(rows) == 1
    row = rows[0]
    assert (row["action"], row["status"], row["event_type"], row["n"], row["permission"]) == \
        ("dm_event", "ok", "Purchase", 1, "pages_events")
    assert PSID not in json.dumps(row) and AMOUNT not in json.dumps(row)


def test_Instagramの問い合わせは会話から相手を引きdatasetはIGのuserに結ぶ(ev, capsys):
    (rc, out, err), first = two_steps(capsys, *IG_ARGV)
    assert rc == 0, err
    made = writes(ev)
    assert [c["path"] for c in made] == [V + f"/{IG_ID}/dataset", V + f"/{DATASET_IG}/events"]
    event = sent_event(made[1])
    assert event["event_name"] == "LeadSubmitted" and event["messaging_channel"] == "instagram"
    assert event["user_data"] == {"instagram_business_account_id": IG_ID, "ig_sid": IG_PERSON}
    assert "custom_data" not in event
    assert "dm_event" in found(IG_ACCOUNT)
    nothing_kept(ev, IG_PERSON)


def test_WhatsAppは口の形だけ_user_dataは公式の例の欄(ev, capsys):
    assert meta_events.user_data("whatsapp", waba_id="W1", ctwa_clid="C1") == {
        "whatsapp_business_account_id": "W1", "ctwa_clid": "C1"}
    assert meta_events.user_data("messenger", page_id=PAGE, person_id=PSID) == {
        "page_id": PAGE, "page_scoped_user_id": PSID}
    assert meta_events.user_data("instagram", ig_user_id=IG_ID, person_id=PSID) == {
        "instagram_business_account_id": IG_ID, "ig_sid": PSID}
    body = meta_events.event_body("Purchase", "whatsapp", {"x": 1}, event_time=1675999999.7, value=123, currency="USD")
    assert body == {"event_name": "Purchase", "event_time": 1675999999, "action_source": "business_messaging",
                    "messaging_channel": "whatsapp", "user_data": {"x": 1},
                    "custom_data": {"currency": "USD", "value": 123}}
    rc, out, err = main(capsys, "dm", "event", FB_ACCOUNT, "--type", "lead", "--psid", PSID, "--channel", "whatsapp",
                        "--by", "m")
    assert rc == 2 and "meta_event_whatsapp_unavailable" in err
    assert "通すには:" in err and "もっと良く:" in err
    assert ev["calls"] == []


# ================================================================== 断り

@pytest.mark.parametrize("extra,code", [
    (("--type", "bogus", "--psid", PSID), "meta_event_type_unknown"),
    (("--type", "purchase", "--psid", PSID), "meta_event_value_invalid"),                  # purchase は金額が要る
    (("--type", "lead", "--psid", PSID, "--value", "10"), "meta_event_value_invalid"),     # 一方だけ
    (("--type", "lead", "--psid", PSID, "--value", "-1", "--currency", "JPY"), "meta_event_value_invalid"),
    (("--type", "lead", "--psid", PSID, "--value", "10", "--currency", "yen!"), "meta_event_value_invalid"),
    (("--type", "lead"), "meta_event_recipient_unknown"),                                  # 相手が無い
    (("--type", "lead", "--psid", PSID, "--conversation", FB_CONVO), "meta_event_recipient_unknown"),
    (("--type", "lead", "--psid", "abc"), "meta_event_recipient_unknown"),
    (("--type", "lead", "--psid", PSID, "--channel", "sms"), "meta_event_channel_unknown"),
    (("--type", "lead", "--psid", PSID, "--channel", "instagram"), "meta_write_unsupported"),  # 口座の媒体と違う
])
def test_断りは符丁と前向きな3つで返り網に出ない(ev, capsys, extra, code):
    rc, out, err = main(capsys, "dm", "event", FB_ACCOUNT, *extra, "--by", "m")
    assert rc != 0 and code in err and "通すには:" in err and "もっと良く:" in err
    assert writes(ev) == []


def test_Metaの名前そのものも通る():
    assert meta_events.event_name("LeadSubmitted") == "LeadSubmitted"
    assert meta_events.event_name("add-to-cart") == "AddToCart"       # 区切りは _ と - の両方
    assert meta_events.event_name("add_to_cart") == "AddToCart"
    assert meta_events.money("1200.5", "jpy", "Lead") == (1200.5, "JPY")
    assert meta_events.money(None, None, "Lead") == (None, None)


def test_権限の綴りの揺れはどちらでも通る(ev, capsys):
    token_path = pathlib.Path(accounts.load_account(FB_ACCOUNT)["token"])
    raw = json.loads(token_path.read_text())
    for spelling, ok in (("page_events", True), ("pages_events", True), ("pages_show_list", False)):
        raw.update(scopes=["pages_show_list", spelling] if ok else ["pages_show_list"], scopes_source="response")
        token_path.write_text(json.dumps(raw))
        rc, out, err = main(capsys, *FB_ARGV, "--json")
        if ok:
            assert rc == 1 and json.loads(out)["stage"] == 1
        else:
            assert rc == 2 and "meta_write_permission_missing" in out and "pages_events" in out
    assert writes(ev) == []


def test_Metaが0件と答えたら済んだと言わず記録に成功を残さない(ev, capsys):
    ev["routes"][("POST", V + f"/{DATASET_FB}/events")] = (200, {"events_received": 0})
    (rc, out, err), _ = two_steps(capsys, *FB_ARGV)
    assert rc == 1 and "meta_write_refused" in out
    assert "dm_event" not in found(FB_ACCOUNT)


def test_結果の分からない送信は打ち直さない(ev, capsys):
    ev["routes"][("POST", V + f"/{DATASET_FB}/events")] = (503, {"error": {"message": "unavailable", "code": 2}})
    (rc, out, err), _ = two_steps(capsys, *FB_ARGV)
    assert rc == 1 and "meta_write_unknown" in out
    assert len([c for c in writes(ev) if c["path"].endswith("/events")]) == 1


def test_権限の無い鍵はMetaの断りを権限の不足として返す(ev, capsys):
    ev["routes"][("POST", V + f"/{PAGE}/dataset")] = (403, {"error": {"message": "(#200) Requires page_events permission",
                                                                      "code": 200}})
    (rc, out, err), _ = two_steps(capsys, *FB_ARGV)
    assert rc == 2 and "meta_write_permission_missing" in out


def test_datasetの応答に_idが無ければ作れたと言わない(ev, capsys):
    ev["routes"][("POST", V + f"/{PAGE}/dataset")] = (200, {"ok": True})
    (rc, out, err), _ = two_steps(capsys, *FB_ARGV)
    assert rc == 1 and "meta_write_unknown" in out
    assert meta_events.stored_dataset(FB_ACCOUNT, PAGE, "messenger") is None


# ================================================================== 同じ日の打ち直しの柵（masaru 11/10）

def _event_posts(ev):
    return [c for c in writes(ev) if c["path"].endswith("/events")]


def test_同じ日の同じ出来事は二度送らない_nthで別の出来事として送れる(ev, capsys):
    (rc, _out, err), _ = two_steps(capsys, *FB_ARGV)
    assert rc == 0, err
    rc, out, err = main(capsys, *FB_ARGV, "--json")              # 一段目は通る（digest は同じ）
    digest = json.loads(out)["digest"]
    rc, out, err = main(capsys, *FB_ARGV, "--confirm", digest, "--json")
    assert rc == 1 and "meta_event_already_sent" in out
    assert len(_event_posts(ev)) == 1
    (rc, _out, err), first = two_steps(capsys, *FB_ARGV, "--nth", "2")
    assert rc == 0, err
    assert first["digest"] != digest
    assert len(_event_posts(ev)) == 2
    nothing_kept(ev, PSID, AMOUNT)


def test_はっきり断られたら印を外し_結果が分からなければ印を残す(ev, capsys):
    route = ("POST", V + f"/{DATASET_FB}/events")
    ev["routes"][route] = (200, {"events_received": 0})          # Meta が数えなかった: 送り直してよい
    (rc, out, _err), _ = two_steps(capsys, *FB_ARGV)
    assert rc == 1 and "meta_write_refused" in out
    ev["routes"][route] = (200, {"events_received": 1})
    (rc, _out, err), _ = two_steps(capsys, *FB_ARGV)
    assert rc == 0, err
    ev["routes"][route] = (503, {"error": {"message": "unavailable", "code": 2}})
    (rc, out, _err), _ = two_steps(capsys, *FB_ARGV, "--nth", "3")
    assert rc == 1 and "meta_write_unknown" in out
    ev["routes"][route] = (200, {"events_received": 1})
    (rc, out, _err), _ = two_steps(capsys, *FB_ARGV, "--nth", "3")   # 分からなかったものは打ち直さない
    assert rc == 1 and "meta_event_already_sent" in out
    assert len(_event_posts(ev)) == 3


def test_前の日の印は数えない(ev, capsys):
    rc, out, _err = main(capsys, *FB_ARGV, "--json")
    digest = json.loads(out)["digest"]
    path = pathlib.Path(accounts.state_dir_for(FB_ACCOUNT)) / meta_events.SENT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": "2000-01-01", "marks": [meta_events.sent_mark(digest, "2000-01-01")]}))
    (rc, _out, err), _ = two_steps(capsys, *FB_ARGV)
    assert rc == 0, err
    kept = json.loads(path.read_text())
    assert kept["date"] == jst.now_jst().date().isoformat() and len(kept["marks"]) == 1


@pytest.mark.parametrize("broken", ["dir", "garbage"])
def test_印が読めなければ確かめられないので送らない(ev, capsys, broken):
    path = pathlib.Path(accounts.state_dir_for(FB_ACCOUNT)) / meta_events.SENT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if broken == "dir":
        path.mkdir()
    else:
        path.write_text("{not json")
    (rc, out, _err), _ = two_steps(capsys, *FB_ARGV)
    assert rc == 1 and "meta_event_already_sent" in out and "確かめられません" in out
    assert _event_posts(ev) == []


def test_印には相手のidも金額も入らない():
    mark = meta_events.sent_mark("abcdef012345", "2026-11-10")
    assert re.fullmatch(r"[0-9a-f]{24}", mark)
    assert meta_events.sent_mark("abcdef012345", "2026-11-11") != mark


# ================================================================== 二段確認・安全装置・leave_gate

@pytest.mark.parametrize("argv", [FB_ARGV, IG_ARGV])
def test_一段目と違うdigestは書く口を呼ばない(ev, capsys, argv):
    rc, out, err = main(capsys, *argv)
    assert rc == 1 and "digest:" in out and "確認の一段目" in out
    rc, out, err = main(capsys, *argv, "--confirm", "0" * 12)
    assert rc == 1 and "meta_write_digest_mismatch" in err and "通すには:" in err
    assert writes(ev) == []


def test_相手と金額が変わればdigestも変わる(ev, capsys):
    digests = set()
    for extra in (("--psid", PSID, "--value", "100"), ("--psid", PSID, "--value", "101"), ("--psid", "7770124", "--value", "100")):
        rc, out, _err = main(capsys, "dm", "event", FB_ACCOUNT, "--type", "purchase", *extra, "--currency", "JPY",
                             "--by", "m", "--json")
        digests.add(json.loads(out)["digest"])
    assert len(digests) == 3


@pytest.mark.parametrize("argv,name", [(FB_ARGV, FB_ACCOUNT), (IG_ARGV, IG_ACCOUNT)])
def test_productionの無い口座では二段目でも呼ばない(ev, capsys, argv, name):
    rc, out, _err = main(capsys, *argv, "--json")
    digest = json.loads(out)["digest"]
    path = pathlib.Path(os.environ["THTH_ACCOUNTS_DIR"]) / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["production"] = False
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rc, out, err = main(capsys, *argv, "--confirm", digest)
    assert rc == 1 and "production_disabled" in err
    assert writes(ev) == []


@pytest.mark.parametrize("argv,name", [(FB_ARGV, FB_ACCOUNT), (IG_ARGV, IG_ACCOUNT)])
def test_安全装置で止めた口座は二段目でも呼ばない(ev, capsys, argv, name):
    rc, out, _err = main(capsys, *argv, "--json")
    digest = json.loads(out)["digest"]
    guard.stop(name, "owner", actor="t")
    rc, out, err = main(capsys, *argv, "--confirm", digest)
    assert rc == 1 and "account_stopped" in err
    assert writes(ev) == []


@pytest.mark.parametrize("argv,name", [(FB_ARGV, FB_ACCOUNT), (IG_ARGV, IG_ACCOUNT)])
def test_leave_gateで止めた口座は網に出ない(ev, capsys, argv, name):
    stop_leave(name)
    rc, out, err = main(capsys, *argv, "--confirm", "0" * 12)
    assert rc != 0 and "account_stopped" in err and "Traceback" not in err
    assert ev["calls"] == []


def test_止める前に組んだadapterも止めたあとはイベントの口を呼ばない(ev):
    cfg = accounts.load_account(FB_ACCOUNT)
    adapter = meta_write._adapter(cfg, accounts.load_token(cfg))
    stop_leave(FB_ACCOUNT)
    for call in (lambda: adapter.event_dataset(), lambda: adapter.event_send(DATASET_FB, {})):
        with pytest.raises(accounts.AccountStopped):
            call()
    assert ev["calls"] == []


# ================================================================== 柵

def test_イベントの書く口を呼ぶのはmeta_eventsだけ():
    root = pathlib.Path(__file__).resolve().parent.parent / "thth"
    doors = re.compile(r"\.(event_send|event_dataset)\(")
    offenders = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                       if doors.search(p.read_text(encoding="utf-8")) and p.name != "meta_events.py")
    assert offenders == []


def test_イベントの符丁は目録にあり3つがそろう():
    for code in ("meta_event_type_unknown", "meta_event_value_invalid", "meta_event_recipient_unknown",
                 "meta_event_channel_unknown", "meta_event_whatsapp_unavailable"):
        entry = refusal_catalog.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"], code


def test_権限は最後に成功した日の記録の種類を持つ():
    for name, kind in (("pages_events", "dm_event"), ("instagram_manage_events", "dm_event"), ("email", "auth_email")):
        assert kind in pw.PERMISSIONS[name]["evidence"], name
    assert pw.PERMISSIONS["whatsapp_business_manage_events"]["evidence"] == ()   # 口座の種類が無く、記録を作れない
    assert "dm_event" in pw.RUN_ACTIONS and "dm_event" in meta_write.RUN_ACTIONS


# ================================================================== メール（認可のとき 1 回・画面にだけ・値は保存しない）

from tests.test_facebook_auth import env as auth_env  # noqa: E402,F401
from tests.test_facebook_auth import run as auth_run, saved as auth_saved  # noqa: E402

EMAIL = "owner-of-page@example.test"


def _email_routes(auth_env, email=EMAIL):
    original = auth_env["routes"][("GET", V + "/me")]

    def me(query, form, raw):
        if query.get("fields") == ["email"]:
            if query.get("access_token") != [auth_env["long"]]:
                return 400, {"error": {"code": 190}}
            return 200, ({"id": "111", "email": email} if email else {"id": "111"})
        return original(query, form, raw)
    auth_env["routes"][("GET", V + "/me")] = me


def test_認可のとき1回だけメールを読み画面にだけ出し印だけを残す(auth_env, capsys):
    _email_routes(auth_env)
    assert auth_run(auth_env) == 0
    out = capsys.readouterr().out
    assert f"認可したユーザーのメール: {EMAIL}" in out and "保存していません" in out
    token = auth_saved(auth_env)
    assert token["email_readable"] is True and re.fullmatch(r"20\d\d-\d\d-\d\dT.*", token["email_read_at"])
    assert [c for c in auth_env["calls"] if c["query"].get("fields") == ["email"]].__len__() == 1
    # 値は .token にも log にも、tmp の下のどのファイルにも入らない。
    assert EMAIL not in "\n".join(auth_env["lines"])
    root = pathlib.Path(os.environ["THTH_ROOT"]).parent
    for path in root.rglob("*"):
        if path.is_file():
            assert EMAIL.encode() not in path.read_bytes(), path


def test_メールが返らなければ印も残さない(auth_env, capsys):
    _email_routes(auth_env, email=None)
    assert auth_run(auth_env) == 0
    assert "認可したユーザーのメール" not in capsys.readouterr().out
    token = auth_saved(auth_env)
    assert "email_readable" not in token and "email_read_at" not in token


def test_doctorの1行は読めた印だけを言う():
    adapter = fb.FacebookAdapter(access_token="t", page_id=PAGE)
    rows = []

    def row(key, label, permission, ok, detail, **extra):
        rows.append((key, ok, detail))
    adapter._email_probe(row, None)
    assert rows == []                                   # email に触れない口座は行を足さない
    adapter.expected_scopes = [*fb.SCOPES, "email"]
    adapter._email_probe(row, None)
    adapter._email_probe(row, {"public_profile"})
    adapter.expected_scopes = list(fb.SCOPES)           # 招待の一覧に無くても、読んだ印があれば言う
    adapter.email_read_at = "2026-11-09T10:00:00+09:00"
    adapter._email_probe(row, {"public_profile", "email"})
    assert [(k, ok) for k, ok, _ in rows] == [("email", None), ("email", False), ("email", True)]
    assert "2026-11-09T10:00:00" in rows[2][2] and "保存していません" in rows[2][2]
    assert "@" not in "".join(detail for _, _, detail in rows)
    ig_adapter = ig.InstagramAdapter(access_token="t", ig_user_id=IG_ID, page_id=PAGE)
    ig_adapter.expected_scopes = [*ig.SCOPES, "email"]
    ig_adapter.email_read_at = "2026-11-09T10:00:00+09:00"
    ig_rows = []
    ig_adapter._email_probe(lambda *a, **k: ig_rows.append(a), {"email"})
    assert ig_rows and ig_rows[0][0] == "email"


def test_メールを読めた日は権限の最後に成功した日に数える(ev):
    cfg = accounts.load_account(FB_ACCOUNT)
    path = pathlib.Path(cfg["token"])
    raw = json.loads(path.read_text())
    raw.update(email_readable=True, email_read_at=jst.iso())
    path.write_text(json.dumps(raw))
    assert "auth_email" in found(FB_ACCOUNT)
    # IG の口座でも同じ印を数える。
    ig_cfg = accounts.load_account(IG_ACCOUNT)
    ig_path = pathlib.Path(ig_cfg["token"])
    ig_raw = json.loads(ig_path.read_text())
    ig_raw.update(email_readable=True, email_read_at=jst.iso())
    ig_path.write_text(json.dumps(ig_raw))
    view = pw.build()
    mail = next(row for row in view["permissions"] if row["permission"] == "email")
    assert mail["last_success_at"] is not None


def test_招待の権限にはメールとイベントをまだ載せない():
    """綴り（pages_events／page_events）と構成を M0 で Dashboard に見るまで、外の人の招待の scope に足さない（11/09 合流）。

    綴りが違うと Meta の認可の画面が権限の名を断り、招待がまるごと通らない（`pages_user_*` と同じ扱い）。
    """
    from thth import scopes
    assert "pages_events" not in scopes.META_INVITE_EXTRA["facebook"]
    assert "page_events" not in scopes.META_INVITE_EXTRA["facebook"]
    assert "instagram_manage_events" not in scopes.META_INVITE_EXTRA["instagram"]
    # email は Worker の形の検査（<頭>_<名前>）を通らないので招待には足さない（Dashboard の構成に足す）。
    assert all("email" not in extra for extra in scopes.META_INVITE_EXTRA.values())
