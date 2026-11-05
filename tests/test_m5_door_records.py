"""計画 Meta 全部盛り申請: 成功を記録しなかった権限の口が、成功したときに runs へ 1 行だけ残す。

`thth admin appreview check` は `permission_watch.build()` の「最後に成功した日」で「30 日以内に呼んだか」を言う。
本物で 1 回打っても記録に残らない口は赤が消えない。ここで縛るのは次の 3 つ:

  1. 各口を偽の Graph で 1 本打つと、その権限の最後に成功した日が埋まる（口座の媒体の側に出る）。
  2. 失敗したときは数えない（Meta が断った回・権限が乗っていない回）。
  3. runs の行は命令の名前・媒体・結果だけ（検索語・場所・リード・カタログ・数は入れない）。

偽の Graph（loopback）だけを使う。本物の Meta は叩かない。口の無い権限（`pages_utility_messaging`・
`ads_mcp_management`）は記録が無いままであることも縛る。
"""
from __future__ import annotations

import json
import pathlib

import pytest

from thth import accounts, doctor, permission_watch as pw, retract_cli, runs, webhook
from tests.test_m3_public_reads import (ADS, BIO, BUSINESS, CAPTION, CATALOG, FB_ACCOUNT, FORM, IG_ACCOUNT, LEAD_EMAIL,
                                        PAGE, TH_ACCOUNT, V, m3, main)  # noqa: F401
from tests.test_facebook_adapter import fake_graph  # noqa: F401

ADS_DOORS = (("catalog_management", ["ads", "catalog", "list", ADS]),
             ("catalog_management", ["ads", "catalog", "products", ADS, "--catalog", CATALOG]),
             ("leads_retrieval", ["ads", "leads", ADS]),
             ("leads_retrieval", ["ads", "leads", ADS, "--form", FORM]),
             ("read_audience_network_insights", ["ads", "network", ADS]),
             ("commerce_account_read_settings", ["ads", "shop", ADS]))


def stamp(permission):
    """その権限の「最後に成功した日」（無ければ None）。"""
    row = next(row for row in pw.build()["permissions"] if row["permission"] == permission)
    return row["last_success_at"]


def runs_text(*dirs):
    out = ""
    for directory in dirs:
        for path in pathlib.Path(directory).glob("runs-*.ndjson"):
            out += path.read_text(encoding="utf-8")
    return out


def ads_state(name=ADS):
    from thth.ads import ledger
    return ledger.state_dir_for(name)


# ---------------------------------------------------------------- 広告の 4 つの口

@pytest.mark.parametrize("permission, argv", ADS_DOORS)
def test_広告の読む口は成功すると権限の最後に成功した日が埋まる(m3, capsys, permission, argv):
    assert stamp(permission) is None
    rc, _out, err = main(capsys, *argv)
    assert rc == 0, err
    assert stamp(permission) is not None


def test_広告の読む口の記録は名前と結果だけで中身を入れない(m3, capsys):
    for _permission, argv in ADS_DOORS:
        rc, _out, err = main(capsys, *argv)
        assert rc == 0, err
    text = runs_text(ads_state())
    assert text and '"action": "ads_leads"' in text
    for marker in (LEAD_EMAIL, CAPTION, "こぴちゃ", "資料請求", CATALOG, FORM, BUSINESS, "fb_ad_network_imp", "OFFSITE_IOS"):
        assert marker not in text
    for line in text.splitlines():
        assert set(json.loads(line)) <= set(runs.RUNS_FIELDS) | set(runs.OPTIONAL_FIELDS) | set(runs.SPARSE_FIELDS)


@pytest.mark.parametrize("permission, argv, route", [
    ("catalog_management", ["ads", "catalog", "list", ADS], f"/{BUSINESS}/owned_product_catalogs"),
    ("leads_retrieval", ["ads", "leads", ADS], f"/{PAGE}/leadgen_forms"),
    ("read_audience_network_insights", ["ads", "network", ADS], f"/{BUSINESS}/adnetworkanalytics"),
    ("commerce_account_read_settings", ["ads", "shop", ADS], f"/{BUSINESS}/commerce_merchant_settings")])
def test_広告の読む口が失敗したときは数えない(m3, capsys, permission, argv, route):
    m3["routes"][("GET", V + route)] = (403, {"error": {"message": "(#200) denied", "code": 200}})
    rc, _out, _err = main(capsys, *argv)
    assert rc != 0
    assert stamp(permission) is None


# ---------------------------------------------------------------- Threads の場所の検索

def test_場所の検索は成功すると権限の日が埋まり語と場所を残さない(m3):
    place = "場所の目印-渋谷駅"
    m3["routes"][("GET", "/v1.0/location_search")] = (200, {"data": [{"id": "7", "name": place}]})
    assert stamp("threads_location_tagging") is None
    assert retract_cli.location_rows(TH_ACCOUNT, "語の目印-検索")[0]["name"] == place
    assert stamp("threads_location_tagging") is not None
    text = runs_text(accounts.state_dir_for(TH_ACCOUNT))
    assert '"action": "location_search"' in text and place not in text and "語の目印" not in text


def test_場所の検索が断られたときは数えない(m3):
    m3["routes"][("GET", "/v1.0/location_search")] = (400, {"error": {"message": "bad", "code": 100}})
    with pytest.raises(retract_cli.LocationFailed):
        retract_cli.location_rows(TH_ACCOUNT, "渋谷駅")
    assert stamp("threads_location_tagging") is None


# ---------------------------------------------------------------- ページの Webhook の購読

def subscribe_routes(m3, status=200):
    m3["routes"][("GET", f"{V}/{PAGE}/subscribed_apps")] = (200, {"data": [{"id": "4242", "subscribed_fields": []}]})
    m3["routes"][("POST", f"{V}/{PAGE}/subscribed_apps")] = (
        status, {"success": True} if status == 200 else {"error": {"message": "no", "code": 200}})


def test_購読が通ると権限の日がFacebookの側に埋まる(m3):
    subscribe_routes(m3)
    assert stamp("pages_manage_metadata") is None
    webhook.subscribe(FB_ACCOUNT, by="masaru")
    assert stamp("pages_manage_metadata") is not None
    assert '"action": "webhook_subscribe"' in runs_text(accounts.state_dir_for(FB_ACCOUNT))


def test_購読が断られたときは数えない(m3):
    subscribe_routes(m3, status=403)
    with pytest.raises(Exception):
        webhook.subscribe(FB_ACCOUNT, by="masaru")
    assert stamp("pages_manage_metadata") is None


# ---------------------------------------------------------------- Instagram の口座から読む Threads の口座 id

def report(ok):
    return {"error": None, "probes": [{"key": "connected_threads_user", "label": "Threads", "permission":
                                       "threads_business_basic", "ok": ok, "detail": "threads_user_id=31415"}]}


def test_IGのdoctorでThreadsの口座idを読めたら権限の日が埋まる(m3):
    assert stamp("threads_business_basic") is None
    doctor.record_observation(IG_ACCOUNT, report(None))       # 結びついた口座が無い・読めなかった
    assert stamp("threads_business_basic") is None
    doctor.record_observation(IG_ACCOUNT, report(False))
    assert stamp("threads_business_basic") is None
    doctor.record_observation(IG_ACCOUNT, report(True))
    assert stamp("threads_business_basic") is not None
    text = runs_text(accounts.state_dir_for(IG_ACCOUNT))
    assert '"action": "connected_threads_user"' in text and "31415" not in text


# ---------------------------------------------------------------- 口が無い権限

def test_口が無い権限は記録を持たない_作ったふりをしない():
    for name in ("pages_utility_messaging", "ads_mcp_management"):
        assert pw.PERMISSIONS[name]["evidence"] == ()


def test_記録の種類はRUN_ACTIONSに全部ある():
    for name in ("threads_location_tagging", "pages_manage_metadata", "threads_business_basic", "catalog_management",
                 "leads_retrieval", "read_audience_network_insights", "commerce_account_read_settings"):
        kinds = pw.PERMISSIONS[name]["evidence"]
        assert kinds and set(kinds) <= set(pw.RUN_ACTIONS)
