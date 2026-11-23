"""11/23 一覧_Meta申請の録画までに要る物 B1: 11/09 以降の Meta の口と webhook を遠くの道（`thth login` のあと）に。

見るのは:
  - `thth dm event`・`thth brand creators|promote|ads-permissions|content`・`thth creators search`・
    `thth shop eligibility|catalogs|products|tags|tag`・`thth webhook subscribe|status <口座>` が、CLI → 偽の Worker →
    VM（`api_requests.handle` の本物の資格の確かめ）→ 偽の Graph で 1 本ずつ通る。
  - **柵は手元の道と同じ**: 書く口は二段（digest は VM が作り VM が照合・一段目と違えば網に出ない）・読む資格だけの
    鍵では書く口は一段目も通らない・止めた口座は網に出ない・`--wait` は黙って捨てずに断る・`--by` は送らない。
  - 相手の id と金額（dm event）を VM の依頼の印に残さない。webhook status は運営者の全体の数を返さない。
  - 審査の区間（`thth/appreview.py` の SEGMENTS）の Threads・Facebook・Instagram の命令は、どれも遠くの道の口を持つ。
偽の Graph と偽の Worker は loopback だけ。本物の Meta・thth.me は叩かない。
"""
from __future__ import annotations

import json
import os
import re
import shlex

import pytest

from thth import appreview, guard, remote, server_meta, server_writes, webhook
from tests.test_remote_meta import (  # noqa: F401
    FB_CONVO, IG_ID, PAGE, V, cli, far, requests_records, two_steps, vm, writes)
from tests.test_v212_server_writes import env, save_config  # noqa: F401
from tests.test_v3140_remote_cli import ACCOUNT, home, run, worker  # noqa: F401

CREATOR, OTHER, BRAND = "17841400000000001", "17841400000000002", "17841400000000009"
MEDIA, BUSINESS, CATALOG, PRODUCT = "17895695668004550", "650157780891832", "960179311066902", "3231775643511089"
ALBUM, CHILD = "17908412406282110", "17953866678213169"
# カルーセルの中の画像 5 枚（断りの理由は 160 字で切れる。5 枚目の id まで届くか）。
CHILDREN = [CHILD] + [f"1795386667821317{i}" for i in range(4)]
DATASET_FB, DATASET_IG = "880011223344", "880055667788"
PSID = "7770123987"           # 相手の id（依頼の印に残ってはいけない）
AMOUNT = "98765"              # 金額（同上）


@pytest.fixture
def b1(far):
    state = {"approved": [CREATOR], "subscribed": ["feed"]}

    def tag_get(query, form, raw):
        asked = query["user_ids"][0].split(",")
        return 200, {"data": [{"id": v} for v in asked if v in state["approved"]]}

    def sub_post(query, form, raw):
        state["subscribed"] = form["subscribed_fields"][0].split(",")
        return 200, {"success": True}

    far["routes"].update({
        ("POST", V + f"/{PAGE}/dataset"): (200, {"id": DATASET_FB}),
        ("POST", V + f"/{IG_ID}/dataset"): (200, {"id": DATASET_IG}),
        ("POST", V + f"/{DATASET_FB}/events"): (200, {"events_received": 1}),
        ("POST", V + f"/{DATASET_IG}/events"): (200, {"events_received": 1}),
        ("GET", V + f"/{IG_ID}/branded_content_tag_approval"): tag_get,
        ("POST", V + f"/{IG_ID}/branded_content_tag_approval"): (200, {"success": True}),
        ("DELETE", V + f"/{IG_ID}/branded_content_tag_approval"): (200, {"success": True}),
        ("GET", V + f"/{MEDIA}/branded_content_partner_promote"): (200, {"data": []}),
        ("POST", V + f"/{MEDIA}/branded_content_partner_promote"): (200, {"success": True}),
        ("GET", V + f"/{IG_ID}/branded_content_ad_permissions"): (200, {"data": [
            {"creator_ig_id": int(CREATOR), "creator_username": "名前の目印", "permission_status": "APPROVED"}]}),
        ("GET", V + f"/{BUSINESS}/partnership-ads-advertisable-content"): (200, {"data": [
            {"content_id": "111", "platform": "INSTAGRAM", "media_type": "IMAGE", "caption": "本文の目印"}]}),
        ("GET", V + f"/{IG_ID}/creator_marketplace_creators"): (200, {"data": [{"id": "9001"}]}),
        ("GET", V + "/creator_marketplace/creators"): (200, {"data": [{"creator_id": "7001"}]}),
        ("GET", V + f"/{IG_ID}"): (200, {"shopping_product_tag_eligibility": True, "id": IG_ID}),
        ("GET", V + f"/{IG_ID}/available_catalogs"): (200, {"available_catalogs": {"data": [
            {"catalog_id": CATALOG, "catalog_name": "カタログ", "shop_name": "店", "product_count": 3}]}}),
        ("GET", V + f"/{IG_ID}/catalog_product_search"): (200, {"data": [
            {"product_id": int(PRODUCT), "product_name": "商品", "review_status": "approved"}]}),
        ("GET", V + f"/{MEDIA}/product_tags"): (200, {"data": []}),
        ("POST", V + f"/{MEDIA}/product_tags"): (200, {"success": True}),
        ("GET", V + f"/{MEDIA}"): (200, {"media_type": "IMAGE", "id": MEDIA}),
        ("GET", V + f"/{ALBUM}"): (200, {"media_type": "CAROUSEL_ALBUM", "id": ALBUM}),
        ("GET", V + f"/{ALBUM}/children"): (200, {"data": [{"id": c, "media_type": "IMAGE"} for c in CHILDREN]}),
        ("GET", V + f"/{PAGE}/subscribed_apps"):
            lambda q, f, r: (200, {"data": [{"id": "4242", "subscribed_fields": state["subscribed"]}]}),
        ("POST", V + f"/{PAGE}/subscribed_apps"): sub_post,
    })
    yield {**far, "b1": state}


def no_body_by(far):
    assert all("by" not in (c["body"] or {}) for c in far["worker"].calls)


# ================================================================== 1 本ずつ通る

def test_dm_eventは二段で送り相手のidと金額をVMの印に残さない(b1, capsys):
    (rc, out, err), first = two_steps(capsys, "dm", "event", "alpha", "--type", "purchase", "--psid", PSID,
                                      "--value", AMOUNT, "--currency", "JPY", "--by", "審査員")
    assert rc == 0, err
    assert first["to"] == PSID and first["event_name"] == "Purchase" and first["by"] == "person"
    assert json.loads(out)["event_name"] == "Purchase"
    assert [c["path"] for c in writes(b1)] == [V + f"/{PAGE}/dataset", V + f"/{DATASET_FB}/events"]
    for record in requests_records(b1):
        text = json.dumps(record, ensure_ascii=False)
        assert PSID not in text and AMOUNT not in text, text
    assert b1["vm_log"].getvalue() == ""
    no_body_by(b1)


def test_dm_eventは会話のidから相手を引き人向けの一段目を出す(b1, capsys):
    rc, out, _err = cli(capsys, "dm", "event", "alpha", "--type", "purchase", "--conversation", FB_CONVO,
                        "--value", "1", "--currency", "JPY", "--remote")
    assert rc == 1 and "確認の一段目" in out and "Purchase" in out
    digest = re.search(r"digest: (\S+)", out).group(1)
    assert f"thth dm event alpha --type purchase --conversation {FB_CONVO}" in out and "--confirm " + digest in out
    rc, out, err = cli(capsys, "dm", "event", "alpha", "--type", "purchase", "--conversation", FB_CONVO,
                       "--value", "1", "--currency", "JPY", "--remote", "--confirm", digest)
    assert rc == 0 and "送りました" in out, err


def test_承認クリエイターは遠くの道で読み二段で足し二段で外す(b1, capsys):
    rc, out, err = cli(capsys, "brand", "creators", "beta", "list", CREATOR, OTHER, "--remote")
    assert rc == 0 and f"承認済み id={CREATOR}" in out and f"載っていない id={OTHER}" in out, err
    (rc, out, err), first = two_steps(capsys, "brand", "creators", "beta", "approve", OTHER, "--by", "m")
    assert rc == 0 and first["ids"] == [OTHER], err
    (rc, out, err), _ = two_steps(capsys, "brand", "creators", "beta", "remove", CREATOR, "--by", "m")
    assert rc == 0, err
    assert [(c["method"], c["path"]) for c in writes(b1)] == [
        ("POST", V + f"/{IG_ID}/branded_content_tag_approval"), ("DELETE", V + f"/{IG_ID}/branded_content_tag_approval")]
    no_body_by(b1)


def test_広告に使う許可は読んで二段で許す(b1, capsys):
    rc, out, err = cli(capsys, "brand", "promote", "beta", MEDIA, "--remote", "--json")
    assert rc == 0 and json.loads(out)["approved"] == [], err
    (rc, out, err), first = two_steps(capsys, "brand", "promote", "beta", MEDIA, "--allow", "--sponsor", BRAND,
                                      "--by", "m")
    assert rc == 0 and first["sponsor_id"] == BRAND and first["allow"] is True, err
    post = writes(b1)[0]
    assert post["path"] == V + f"/{MEDIA}/branded_content_partner_promote" and post["form"]["sponsor_id"] == [BRAND]


@pytest.mark.parametrize("argv,key,value", [
    (("brand", "ads-permissions", "beta"), "count", 1),
    (("brand", "content", "beta", "--business", BUSINESS), "count", 1),
    (("brand", "content", "alpha", "--business", BUSINESS), "count", 1),
    (("creators", "search", "beta", "--country", "JP"), "count", 1),
    (("creators", "search", "alpha", "--country", "JP"), "count", 1),
    (("shop", "eligibility", "beta"), "eligible", True),
    (("shop", "catalogs", "beta"), "count", 1),
    (("shop", "products", "beta", "--catalog", CATALOG), "count", 1),
    (("shop", "tags", "beta", MEDIA), "count", 0),
], ids=lambda a: " ".join(a[:2]) if isinstance(a, tuple) else None)
def test_読む口は遠くの道で読み網に書かない(b1, capsys, argv, key, value):
    rc, out, err = cli(capsys, *argv, "--remote", "--json")
    assert rc == 0, out + err
    assert json.loads(out)[key] == value
    assert writes(b1) == []
    rc, out, err = cli(capsys, *argv, "--remote")
    assert rc == 0 and "保存しません" in out, out + err


def test_商品タグは二段で付けカルーセルの親は中の画像のidを添えて断る(b1, capsys):
    (rc, out, err), first = two_steps(capsys, "shop", "tag", "beta", MEDIA, "--product", PRODUCT, "--x", "0.5",
                                      "--y", "0.5", "--by", "m")
    assert rc == 0 and first["product_id"] == PRODUCT, err
    assert [c["path"] for c in writes(b1)] == [V + f"/{MEDIA}/product_tags"]
    rc, out, err = cli(capsys, "shop", "tag", "beta", ALBUM, "--product", PRODUCT, "--remote")
    assert rc != 0 and err.startswith("shop_tag_carousel") and "通すには:" in err, err
    assert all(child in err for child in CHILDREN), err  # 中の画像の id は 5 枚目まで切れずに届く
    assert len(writes(b1)) == 1


def test_webhookは1段で購読しstatusはその口座の分だけ返す(b1, capsys):
    rc, out, err = cli(capsys, "webhook", "subscribe", "alpha", "--by", "審査員", "--remote", "--json")
    assert rc == 0, err
    value = json.loads(out)
    assert value["added"] == ["mention"] and value["fields"] == ["feed", "mention"]
    assert webhook.subscriptions()["alpha"]["by"] == "person"  # 記録は鍵の持ち主（--by は送らない）
    no_body_by(b1)
    # ほかの口座の購読と、運営者の全体の数（Worker・知らない id）は返さない。
    webhook.subscribe("beta", by="masaru")
    rc, out, err = cli(capsys, "webhook", "status", "alpha", "--remote", "--json")
    assert rc == 0, err
    value = json.loads(out)
    assert value["account"] == "alpha" and value["subscribed"] is True
    assert "beta" not in out and "masaru" not in out
    for word in ("worker", "unrouted", "subscriptions", "outages"):
        assert word not in value
    rc, out, err = cli(capsys, "webhook", "status", "alpha", "--remote")
    assert rc == 0 and "届いている:" in out, err


def test_手元のwebhook_statusも口座を受ける(b1, capsys):
    webhook.subscribe("alpha", by="masaru")
    rc, out, err = cli(capsys, "webhook", "status", "alpha", "--json")
    assert rc == 0 and json.loads(out)["subscribed"] is True, err
    assert b1["worker"].calls == []  # 台帳が手元にあれば手元の道
    rc, out, err = cli(capsys, "webhook", "status", "../x")
    assert rc == 2 and err.startswith("invalid_account")


# ================================================================== 柵（わざと壊して赤を見た所）

B1_WRITES = [
    ("dm", "event", "alpha", "--type", "lead", "--psid", PSID),
    ("brand", "creators", "beta", "approve", OTHER),
    ("brand", "creators", "beta", "remove", CREATOR),
    ("brand", "promote", "beta", MEDIA, "--allow", "--sponsor", BRAND),
    ("shop", "tag", "beta", MEDIA, "--product", PRODUCT),
    ("webhook", "subscribe", "alpha"),
]


@pytest.mark.parametrize("argv", B1_WRITES, ids=lambda a: " ".join(a[:2]))
def test_読む資格だけの鍵ではB1の書く口は一段目も通らない(b1, capsys, argv):
    b1["value"]["credentials"][0]["writes"] = False
    save_config(b1)
    rc, out, err = cli(capsys, *argv, "--by", "m", "--remote", "--json")
    assert rc != 0 and "writes_not_allowed" in out + err, out + err
    assert writes(b1) == []


def test_読む資格だけの鍵でもB1の読む口は通る(b1, capsys):
    b1["value"]["credentials"][0]["writes"] = False
    save_config(b1)
    rc, out, err = cli(capsys, "shop", "eligibility", "beta", "--remote", "--json")
    assert rc == 0, out + err


@pytest.mark.parametrize("argv", [a for a in B1_WRITES if a[0] != "webhook"], ids=lambda a: " ".join(a[:2]))
def test_B1の書く口は一段目と違うdigestで断られ網に出ない(b1, capsys, argv):
    rc, out, _err = cli(capsys, *argv, "--by", "m", "--remote", "--json")
    assert rc == 1 and json.loads(out)["stage"] == 1, out
    digest = json.loads(out)["digest"]
    wrong = ("0" if digest[0] != "0" else "1") + digest[1:]
    rc, out, err = cli(capsys, *argv, "--by", "m", "--remote", "--confirm", wrong)
    assert rc != 0 and err.startswith("meta_write_digest_mismatch"), err
    assert writes(b1) == []


def test_止めた口座でdm_eventの二段目は網に出ない(b1, capsys):
    argv = ("dm", "event", "alpha", "--type", "lead", "--psid", PSID, "--remote")
    rc, out, _err = cli(capsys, *argv, "--json")
    digest = json.loads(out)["digest"]
    guard.stop("alpha", "owner", actor="t")
    rc, out, err = cli(capsys, *argv, "--confirm", digest)
    assert rc != 0 and err.startswith("account_stopped") and "Traceback" not in err, err
    assert writes(b1) == []


def test_productionの無い口座は商品タグの二段目で呼ばず一段目でそう言う(b1, capsys):
    path = b1["root"] / "accounts" / "beta.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "production": False}))
    argv = ("shop", "tag", "beta", MEDIA, "--product", PRODUCT, "--remote")
    rc, out, _err = cli(capsys, *argv)
    assert rc == 1 and "production: true が無い" in out
    digest = re.search(r"digest: (\S+)", out).group(1)
    rc, out, err = cli(capsys, *argv, "--confirm", digest)
    assert rc != 0 and err.startswith("production_disabled")
    assert writes(b1) == []


def test_待つ旗はB1の書く口でも黙って捨てずに断る(b1, capsys):
    rc, _out, err = cli(capsys, "brand", "creators", "beta", "approve", OTHER, "--wait", "5", "--remote")
    assert rc == 2 and err.startswith("remote_unsupported")
    rc, _out, err = cli(capsys, "shop", "tag", "beta", MEDIA, "--product", PRODUCT, "--wait", "5", "--remote")
    assert rc == 2 and err.startswith("remote_unsupported")
    assert b1["worker"].calls == []


def test_B1の口もMCPの表に足さず符丁は遠くの道で言える():
    assert not set(server_meta.OPERATIONS) & set(server_writes.WRITE_OPERATIONS)
    assert set(server_meta.REASONS) <= set(server_writes.SAFE_ERRORS)
    for op in ("dm_event", "brand_creators_approve", "brand_promote_allow", "shop_tag", "webhook_subscribe"):
        assert op in server_meta.OPERATIONS and op not in server_meta.READS
    for name in ("value", "currency", "to"):
        assert name in server_meta.SECRET_FIELDS


def test_出さない子の命令には旗を足さない(capsys):
    from thth import cli as cli_mod
    with pytest.raises(SystemExit):
        cli_mod.build_parser().parse_args(["webhook", "pull", "--remote"])
    capsys.readouterr()


# ================================================================== 台帳が無く login 済みなら遠くの道

ROUTES = [
    (("dm", "event", ACCOUNT, "--type", "purchase", "--conversation", "t_1", "--value", "1", "--currency", "JPY",
      "--nth", "2", "--by", "m", "--json"), "dm_event",
     {"type": "purchase", "conversation": "t_1", "value": "1", "currency": "JPY", "nth": 2}),
    (("brand", "creators", ACCOUNT, "list", CREATOR, OTHER, "--json"), "brand_creators_list",
     {"ids": f"{CREATOR},{OTHER}"}),
    (("brand", "creators", ACCOUNT, "approve", CREATOR, "--by", "m", "--json"), "brand_creators_approve",
     {"ids": CREATOR}),
    (("brand", "creators", ACCOUNT, "remove", CREATOR, "--by", "m", "--confirm", "a" * 12, "--json"),
     "brand_creators_remove", {"ids": CREATOR, "confirm": "a" * 12}),
    (("brand", "promote", ACCOUNT, MEDIA, "--json"), "brand_promote_show", {"media_id": MEDIA}),
    (("brand", "promote", ACCOUNT, MEDIA, "--deny", "--sponsor", BRAND, "--by", "m", "--json"), "brand_promote_deny",
     {"media_id": MEDIA, "sponsor": BRAND}),
    (("brand", "ads-permissions", ACCOUNT, "--creator", "someone", "--json"), "brand_ads_permissions",
     {"creator": "someone"}),
    (("brand", "content", ACCOUNT, "--business", BUSINESS, "--limit", "5", "--json"), "brand_content",
     {"business": BUSINESS, "limit": 5}),
    (("creators", "search", ACCOUNT, "--country", "JP", "--q", "cafe", "--json"), "creators_search",
     {"country": "JP", "q": "cafe"}),
    (("shop", "eligibility", ACCOUNT, "--json"), "shop_eligibility", {}),
    (("shop", "catalogs", ACCOUNT, "--json"), "shop_catalogs", {}),
    (("shop", "products", ACCOUNT, "--catalog", CATALOG, "--json"), "shop_products", {"catalog": CATALOG}),
    (("shop", "tags", ACCOUNT, MEDIA, "--json"), "shop_tags", {"media_id": MEDIA}),
    (("shop", "tag", ACCOUNT, MEDIA, "--product", PRODUCT, "--x", "0.25", "--y", "0.75", "--by", "m", "--json"),
     "shop_tag", {"media_id": MEDIA, "product": PRODUCT, "x": 0.25, "y": 0.75}),
    (("webhook", "subscribe", ACCOUNT, "--by", "m", "--json"), "webhook_subscribe", {}),
    (("webhook", "status", ACCOUNT, "--json"), "webhook_status", {}),
]


@pytest.mark.parametrize("argv,operation,fields", ROUTES, ids=lambda v: v if isinstance(v, str) else None)
def test_台帳が無くloginしていればB1の命令が遠くの道に届く(worker, capsys, argv, operation, fields):  # noqa: F811
    rc, _out, err = run(capsys, *argv)
    assert "台帳" not in err
    call = worker.calls[-1]
    assert call["path"] == "/api/v1/" + operation
    assert call["body"] == {"account": ACCOUNT, **fields}


def test_店を読む口の動詞はB1に巻き込まない(worker, capsys):  # noqa: F811
    # `thth shop add <店>` は店の台帳を書く手元の口（遠くの道に出していない・旗も無い）。
    assert "shop add" not in remote.COMMANDS and "webhook pull" not in remote.COMMANDS
    assert worker.calls == []


def _segment_keys():
    from thth import cli as cli_mod
    parser = cli_mod.build_parser()
    for segment in appreview.SEGMENTS:
        if segment["medium"] not in ("threads", "facebook", "instagram"):
            continue
        for command in appreview.segment_commands(segment):
            argv = shlex.split(re.sub(r"\{[A-Z_]+\}", "123", command))[1:]
            args = parser.parse_args(argv)
            yield segment["id"], command, remote.command_key(argv[0], args)


def test_審査の区間のMetaの命令はどれも遠くの道の口を持つ():
    """`tools/appreview_rec.sh` は login の道で打つ。区間の命令が遠くの道に無いと審査員も録画も打てない（B1）。"""
    missing = [(seg, command) for seg, command, key in _segment_keys() if key not in remote.COMMANDS]
    assert missing == []
    keys = {key for _seg, _command, key in _segment_keys()}
    for key in ("dm event", "brand creators", "brand promote", "brand ads-permissions", "brand content",
                "creators search", "shop eligibility", "shop tag", "webhook subscribe", "webhook status"):
        assert key in keys, key


def test_知らない欄と型の違う欄と幅の外のnthは断る(b1):
    from thth.report_service import ReportServiceError
    for request in ({"operation": "dm_event", "account": "alpha", "type": "lead", "psid": PSID, "nth": 101},
                    {"operation": "dm_event", "account": "alpha", "type": "lead", "nth": "2"},
                    {"operation": "shop_tag", "account": "beta", "media_id": MEDIA, "product": PRODUCT, "x": "0.5"},
                    {"operation": "brand_content", "account": "alpha", "business": BUSINESS, "limit": True},
                    {"operation": "webhook_subscribe", "account": "alpha", "by": "誰か"},
                    {"operation": "webhook_status", "account": "alpha", "all": True}):
        with pytest.raises(ReportServiceError) as caught:
            server_writes.serve(b1["context"], request, via="api")
        assert str(caught.value) == "invalid_request", request
    assert writes(b1) == []


# 審査員は login の道で打つ（貼り紙 §9）。手順に書いた命令が遠くの道の口を持ち、旗も通ること（11/23 C-4・retract の --production）。
_REVIEWER_LOCAL_ONLY = {"login", "logout", "account leave", "ads leave", "admin invite create"}


def test_審査員の手順の命令はどれも遠くの道で打てる形():
    from thth import cli as cli_mod
    text = open(os.path.join(os.path.dirname(__file__), "..", "docs", "貼り紙_Meta全部盛り申請_2026-11-05.md"),
                encoding="utf-8").read()
    section = text.split("## 9. ", 1)[1].split("\n## ", 1)[0]
    parser = cli_mod.build_parser()
    bad = []
    for command in sorted(set(re.findall(r"`(thth [^`]+)`", section))):
        tokens = command.split()
        if "…" in tokens[1:3] or "|" in command or command.endswith("--help"):
            continue
        if not re.search(r"\b[A-Z][A-Z_]+\b|<[^>]+>", command):
            continue                       # 口座の無い名前だけの言及（例 `thth dm event`）は命令ではない
        argv = shlex.split(re.sub(r"\b[A-Z][A-Z_]+\b", "123", re.sub(r"<[^>]+>", "123", command)))[1:]
        try:
            args = parser.parse_args(argv)
        except SystemExit:
            bad.append(("parse", command))
            continue
        key = remote.command_key(argv[0], args)
        if key not in remote.COMMANDS and key not in _REVIEWER_LOCAL_ONLY:
            bad.append(("remote", command))
    assert bad == []
