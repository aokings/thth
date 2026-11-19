"""店の変化の連動（設計 `docs/設計_店の変化の連動_2026-11-19.md` 段 3〜7）: 見本の店で 変化 → 束 → 出る。

見本の店は `registers.example/cafe-sample`（架空の喫茶「喫茶 みほん」）を一時の置き場に写し、IG の口座 `mihon-ig`（偽）と
Google マップの店 `locations/222`（偽）の両方を持たせる。**本物の Meta・Google には繋がない**（偽の Graph API は
loopback、偽の Google は `tests/test_gbp.py` の `FakeGoogle`）。時刻は 2026-12-08 10:00 JST に止める
（臨時 12/12 の 7 日前・催し 12/14 の 14 日前の窓の中）。

柵:
  - 同意のある物は確認なしで出て、同意の無い物は束ごとの二段確認で出る（一段目は何も変えない）。
  - 同意の範囲の外（店・出し先・きっかけ・期限のどれかが外れる）は自動で出ない。
  - 同意を取り消すと次の回から止まる。
  - Google 側の値が、THTH が前に出した値から変わっていたら自動では出さずに止める。
  - 同じ変化を二度出さない（束も、出た物も）。
  - ig-story は画像が無ければ前向きな断り（なぜ・通る直し方・もっと良く）。
  - 休みの理由は既定で外に出さない（IG の本文にも Google にも入らない）。
"""
from __future__ import annotations

import datetime
import json
import pathlib
import secrets
import time

import pytest

from tests.test_facebook_adapter import fake_graph
from tests.test_gbp import ACC, LOC, FakeGoogle, _location, google  # noqa: F401
from tests.test_register_base import add, jrun, run  # noqa: F401
from tests.test_register_store import confirm
from tests.test_v213_media_foundation import jpeg
from thth import accounts, core, jst, media_relay, refusal_catalog
from thth.adapters import facebook as fb
from thth.adapters import instagram as ig
from thth.gbp import api as gbp_api
from thth.register import bundle as bundle_mod, linkage

NOW = datetime.datetime(2026, 12, 8, 10, 0, tzinfo=jst.JST)
STORE = "cafe-sample"
IG_ACCOUNT = "mihon-ig"
IG_USER = "17841400000000001"
PAGE = "1361492233719900"
TOKEN = "ig-page-token-do-not-leak-link-0123456789"
V = "/" + fb.GRAPH_VERSION
GBP = f"gbp:{LOC}"
SPECIAL, EVENT = "cafe-special-2026-12-12", "cafe-event-2026-12-14"
REASON = "設備の点検"


# ------------------------------------------------------------------ 偽の IG（Graph API と relay）

class _Relay:
    def __init__(self, store):
        self.store = store

    def upload_prepared(self, item):
        self.store["uploads"] += 1
        return secrets.token_urlsafe(16)

    def grant(self, item, source):
        return {"url": media_relay.origin() + "/m/" + secrets.token_urlsafe(16),
                "expires_at": int(time.time() * 1000) + 600000}

    def result(self, grant, *, published):
        return {"status": "acknowledged" if published else "retired"}


class _LiveRoutes(dict):
    """偽の Google を、書いた臨時の時間が次の読みに出る形にする（本物と同じく、PATCH のあとの GET は書いた値）。"""

    def __init__(self, fake):
        super().__init__(fake.routes)
        self.fake = fake

    def __getitem__(self, key):
        if key == ("PATCH", gbp_api.INFO_BASE + LOC) and dict.__getitem__(self, key)[0] == 200:
            body = self.fake.calls[-1]["body"]
            status, location = dict.__getitem__(self, ("GET", gbp_api.INFO_BASE + LOC))
            self[("GET", gbp_api.INFO_BASE + LOC)] = (status, dict(json.loads(json.dumps(location)), **body))
        return dict.__getitem__(self, key)


@pytest.fixture
def world(tmp_path, monkeypatch, google):  # noqa: F811
    """THTH の置き場・見本の店の台帳・偽の IG の口座・偽の Google（`google`）。"""
    root = tmp_path.resolve() / "root"
    root.mkdir(mode=0o700)
    home = tmp_path / "home"
    home.mkdir()
    for key, value in (("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home)):
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv("THTH_ACCOUNTS_DIR", raising=False)
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    repo = root / "ig-repo"
    repo.mkdir()
    cfg = {"account": IG_ACCOUNT, "media": "instagram", "repo_dir": str(repo), "production": True,
           "hashtags": False, "ig_user_id": IG_USER, "page_id": PAGE, "project": "sample"}
    original = accounts.load_account
    monkeypatch.setattr(accounts, "load_account", lambda name: dict(cfg) if name == IG_ACCOUNT else original(name))
    monkeypatch.setattr(accounts, "load_token", lambda c: {"access_token": TOKEN})
    monkeypatch.setattr(core, "_append_run", lambda *a, **k: None)
    state = {"uploads": 0, "published": 0}
    monkeypatch.setattr(media_relay, "MediaRelay", lambda account, actor: _Relay(state))

    def create(query, form, raw):
        state["created"] = state.get("created", 0) + 1
        return 200, {"id": str(9000 + state["created"])}

    def publish(query, form, raw):
        state["published"] += 1
        return 200, {"id": f"1800000000000000{state['published']}"}

    routes = {("GET", f"{V}/{IG_USER}/content_publishing_limit"): (200, {"data": [
        {"quota_usage": 0, "config": {"quota_total": 100, "quota_duration": 86400}}]}),
        ("POST", f"{V}/{IG_USER}/media"): create, ("POST", f"{V}/{IG_USER}/media_publish"): publish}
    for n in range(1, 10):
        cid = str(9000 + n)
        routes[("GET", f"{V}/{cid}")] = (lambda q, f, r, cid=cid: (200, {"id": cid, "status_code": "FINISHED"}))
        routes[("GET", f"{V}/1800000000000000{n}")] = (200, {"permalink": f"https://www.instagram.com/p/{n}/"})
    google.routes = _LiveRoutes(google)
    with fake_graph(routes) as (url, calls):
        def factory(c, token):
            a = ig.InstagramAdapter(base_url=url, access_token=TOKEN, ig_user_id=IG_USER, page_id=PAGE)
            a.poll_interval, a.poll_total = 0, 5
            return a
        monkeypatch.setattr(core, "_default_adapter_factory", factory)
        state.update(calls=calls, cfg=cfg, root=root, google=google)
        yield state


def ig_creates(world) -> list:
    return [c["form"] for c in world["calls"] if c["method"] == "POST" and c["path"].endswith(f"/{IG_USER}/media")]


def gbp_writes(world) -> list:
    return world["google"].writes()


def consent(**over) -> dict:
    entry = {"register": STORE, "to": GBP, "triggers": ["special_soon"], "until": "2027-03-31", "by": "店主 みほん",
             "at": "2026-11-19T12:00:00+09:00"}
    entry.update(over)
    return entry


def declare(store: pathlib.Path, consents=None, *, image=True, outlets=None, keep=True) -> None:
    outlets = outlets or [
        {"to": GBP, "triggers": ["special_soon", "event_soon"]},
        {"to": f"ig:{IG_ACCOUNT}", "triggers": ["special_soon", "event_soon"]},
        {"to": f"ig-story:{IG_ACCOUNT}", "triggers": ["special_soon"]}]
    if image:
        for outlet in outlets:
            if not outlet["to"].startswith("gbp:"):
                outlet.update(image="画像/告知.jpg", image_alt="店の入口の写真")
        (store / "画像").mkdir(exist_ok=True)
        (store / "画像" / "告知.jpg").write_bytes(jpeg())
    data = {"schema": linkage.SCHEMA, "outlets": outlets, "consent": [consent()] if consents is None else consents}
    (store / ".thth").mkdir(exist_ok=True)
    (store / ".thth" / "linkage.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if keep:
        keep_consent()


def keep_consent():
    """持ち主が二段確認で同意を控える（宣言が崩れていれば控えない）。"""
    import io
    import contextlib
    from thth import cli
    for extra in ([], None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            argv = ["link", "consent", STORE, "--by", "店主 みほん", "--json"]
            rc_ = cli.main(argv + (extra if extra is not None else ["--confirm", value["digest"]]))
        value = json.loads(out.getvalue() or "{}")
        if rc_ != 1:
            return value
    return value


@pytest.fixture
def store(world, capsys, tmp_path):
    """見本の店を控え、臨時 12/12 と催し 12/14 を認め、連動の宣言を置く（同意は gbp の special_soon だけ）。"""
    shop = tmp_path / STORE
    import shutil
    shutil.copytree(pathlib.Path(__file__).resolve().parents[1] / "registers.example" / STORE, shop)
    rc_, out, err = run(capsys, "register", "add", STORE, "--path", str(shop), "--project", "sample", "--kind", "store",
                        "--by", "m")
    assert rc_ == 0, err
    # 店の行に Google マップの店を結ぶ（認める操作は二段確認）。
    shop_row = shop / "店.md"
    shop_row.write_text(shop_row.read_text(encoding="utf-8").replace("site_url:", f"gbp_location: {LOC}\nsite_url:"),
                        encoding="utf-8")
    for row in (SPECIAL, EVENT, "cafe-store"):
        confirm(capsys, STORE, row)
    declare(shop)
    return shop


def link(capsys, *argv):
    return jrun(capsys, "link", *argv)


def bundles_by_trigger() -> dict:
    return {b["trigger"]: b for b in bundle_mod.read(STORE)}


def states(b) -> dict:
    return {i["to"] + "|" + i["kind"]: i["state"] for i in b["items"]}


# ------------------------------------------------------------------ 通しの道

def test_見本の店で_変化から束_同意ありは確認なしで_同意なしは束ごとの二段確認で出る(store, world, capsys):
    rc_, value, err = link(capsys, "check", STORE)
    assert rc_ == 0, err
    assert [c["state"] for c in value["consent"]] == ["valid"]

    rc_, value, err = link(capsys, "make", STORE, "--by", "m")
    assert rc_ == 0, err
    made = bundles_by_trigger()
    assert set(made) == {"special_soon", "event_soon"}
    assert states(made["special_soon"]) == {f"{GBP}|gbp_special": "ready", f"ig:{IG_ACCOUNT}|ig": "waiting",
                                            f"ig-story:{IG_ACCOUNT}|ig_story": "waiting"}
    assert states(made["event_soon"]) == {f"{GBP}|gbp_event": "waiting", f"ig:{IG_ACCOUNT}|ig": "waiting"}

    # 自動: 同意のある 1 つ（Google マップの臨時の時間）だけが、確認なしで出る。IG には何も投げない。
    rc_, value, err = link(capsys, "send", STORE, "--auto", "--by", "timer")
    assert rc_ == 0, err
    [patch] = gbp_writes(world)
    periods = patch["body"]["specialHours"]["specialHourPeriods"]
    assert {"startDate": {"year": 2026, "month": 12, "day": 12}, "openTime": {"hours": 14, "minutes": 0},
            "closeTime": {"hours": 18, "minutes": 0}, "endDate": {"year": 2026, "month": 12, "day": 12},
            "closed": False} in periods
    assert len(periods) == 3            # Google にあったほかの日（12/24・12/29）はそのまま送り直す
    assert ig_creates(world) == []
    assert states(bundles_by_trigger()["special_soon"])[f"{GBP}|gbp_special"] == "sent"

    # 同意の無い物: 束ごとの二段確認。一段目は何も変えない。
    special = bundles_by_trigger()["special_soon"]["id"]
    rc_, first, err = link(capsys, "send", STORE, "--bundle", special, "--by", "店主")
    assert rc_ == 1 and first["stage"] == 1 and first["digest"]
    assert sorted(i["kind"] for i in first["items"]) == ["ig", "ig_story"]
    assert len(gbp_writes(world)) == 1 and ig_creates(world) == []
    rc_, value, err = link(capsys, "send", STORE, "--bundle", special, "--by", "店主", "--confirm", first["digest"])
    assert rc_ == 0, err
    creates = ig_creates(world)
    assert [c.get("media_type") for c in creates] == [None, ["STORIES"]]
    assert "12月12日（土）は 14:00〜18:00 の営業です" in creates[0]["caption"][0]
    assert "caption" not in creates[1]
    assert len(gbp_writes(world)) == 1  # 出た Google マップの物は出し直さない
    assert set(states(bundles_by_trigger()["special_soon"]).values()) == {"sent"}

    # 催しの束: Google マップのイベントの投稿と IG のフィード。
    event = bundles_by_trigger()["event_soon"]["id"]
    rc_, first, err = link(capsys, "send", STORE, "--bundle", event, "--by", "店主")
    assert rc_ == 1
    rc_, value, err = link(capsys, "send", STORE, "--bundle", event, "--by", "店主", "--confirm", first["digest"])
    assert rc_ == 0, err
    post = gbp_writes(world)[-1]
    assert post["method"] == "POST" and post["body"]["topicType"] == "EVENT"
    assert post["body"]["event"]["title"] == "冬のブレンドの試食会"
    assert post["body"]["event"]["schedule"]["startTime"] == {"hours": 13, "minutes": 0}
    assert len(ig_creates(world)) == 3
    assert set(states(bundles_by_trigger()["event_soon"]).values()) == {"sent"}

    # 現在地（thth scenario の links）と hub が読む 1 行（thth events の link_published）。
    from thth import events_cli
    from thth.link import status
    links = status.links_for("sample")
    assert {row["trigger"]: row["states"] for row in links} == {"special_soon": {"sent": 3}, "event_soon": {"sent": 2}}
    rows, _cannot = status.events_for("sample", lambda kind, *a, **k: kind, now=NOW)
    assert rows.count("link_published") == 5 and "link_waiting" not in rows
    assert {"link_published", "link_waiting"} <= set(events_cli.KINDS)


def test_自動の道は確認なしで出し_同意の無い物はhubに待ちの1行が出る(store, world, capsys):
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    assert len(gbp_writes(world)) == 1 and ig_creates(world) == []
    from thth.link import status
    rows, _ = status.events_for("sample", lambda kind, *a, **k: (kind, k.get("count")), now=NOW)
    waiting = sorted(c for k, c in rows if k == "link_waiting")
    assert waiting == [2, 2]           # 臨時の束の IG 2 つ・催しの束の 2 つ
    assert ("link_published", None) in rows


# ------------------------------------------------------------------ 柵: 同意の範囲

@pytest.mark.parametrize("over, why, allowed", [
    ({"to": f"ig:{IG_ACCOUNT}"}, "出し先が外れる", {("special_soon", "ig")}),
    ({"triggers": ["event_soon"]}, "きっかけが外れる", {("event_soon", "gbp_event")}),
    ({"until": "2026-12-07"}, "期限を過ぎた", set()),
    ({"at": "2026-12-09T09:00:00+09:00", "until": "2027-03-31"}, "まだ始まらない同意", set()),
    ({"revoked_at": "2026-12-01T00:00:00+09:00"}, "取り消した同意", set()),
])
def test_柵_同意の範囲の外は自動で出ない(store, world, capsys, over, why, allowed):
    """臨時 12/12 の Google マップの臨時の時間は、どの例でも同意の範囲の外。出るのは同意を書いた範囲（allowed）だけ。"""
    declare(store, [consent(**over)])
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, (why, err)
    assert [w for w in gbp_writes(world) if w["method"] == "PATCH"] == [], why
    sent = {(b["trigger"], i["kind"]) for b in bundle_mod.read(STORE) for i in b["items"] if i["state"] == "sent"}
    assert sent == allowed, why
    assert len(ig_creates(world)) == sum(1 for _t, kind in sent if kind.startswith("ig")), why


def test_柵_店の外の同意は宣言ごと断り_何も出さない(store, world, capsys):
    declare(store, [consent(register="other-store")])
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 2 and value["code"] == "register_linkage_invalid" and value["detail"] == "consent_register:0"
    assert gbp_writes(world) == [] and ig_creates(world) == []
    # 台帳の点検の柵にも入る。
    rc_, value, err = jrun(capsys, "register", "check", STORE)
    assert rc_ == 2 and value["code"] == "register_linkage_invalid"


@pytest.mark.parametrize("over, word", [
    ({"until": "2028-01-01"}, "consent_too_long:0"),
    ({"by": ""}, "consent_by:0"),
    ({"at": "2026-11-19 12:00"}, "consent_at:0"),
])
def test_柵_具体的でない同意は宣言の点検で断る(store, world, capsys, over, word):
    declare(store, [consent(**over)])
    rc_, value, err = link(capsys, "check", STORE)
    assert rc_ == 2 and value["detail"] == word
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 2 and gbp_writes(world) == []


def test_柵_同意を取り消すと次の回から止まる(store, world, capsys):
    rc_, value, err = link(capsys, "make", STORE, "--by", "m")
    assert states(bundles_by_trigger()["special_soon"])[f"{GBP}|gbp_special"] == "ready"
    declare(store, [consent(revoked_at="2026-12-08T09:00:00+09:00")])   # 束を作ったあとに取り消した
    rc_, value, err = link(capsys, "send", STORE, "--auto", "--by", "timer")
    assert rc_ == 0, err
    assert gbp_writes(world) == []
    assert states(bundles_by_trigger()["special_soon"])[f"{GBP}|gbp_special"] == "waiting"
    # 同じ文を戻しても生き返らない（ファイルから消えた控えは、その回に取り消しとして記録した・2 回目の B）。
    declare(store, [consent()])
    rc_, value, err = link(capsys, "send", STORE, "--auto", "--by", "timer")
    assert gbp_writes(world) == []
    rc_, value, err = link(capsys, "check", STORE)
    assert value["consent"][0]["kept"] is False and value["unkept"] == 0   # 取り消した指紋は控え直せない
    # 続けるなら、新しく書いた同意を控え直す。
    declare(store, [consent(at="2026-12-08T09:30:00+09:00")])
    rc_, value, err = link(capsys, "send", STORE, "--auto", "--by", "timer")
    assert len(gbp_writes(world)) == 1


# ------------------------------------------------------------------ 柵: Google 側の変わり

def _google_has(world, day: int, open_h: int, close_h: int):
    location = _location()
    location["specialHours"]["specialHourPeriods"].append(
        {"startDate": {"year": 2026, "month": 12, "day": day}, "openTime": {"hours": open_h},
         "endDate": {"year": 2026, "month": 12, "day": day}, "closeTime": {"hours": close_h}, "closed": False})
    world["google"].routes[("GET", gbp_api.INFO_BASE + LOC)] = (200, location)


def test_柵_Googleの値がTHTHの前に出した値から変わっていたら自動では出さずに止める(store, world, capsys):
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert len(gbp_writes(world)) == 1
    # 持ち主が行を直して認め直した（出し直しの束）。そのあいだに、人がマップの画面で 12/12 を 15〜18 時に直した。
    row = store / "臨時" / "2026-12-12_設備の点検.md"
    row.write_text(row.read_text(encoding="utf-8").replace("close: 18:00", "close: 17:00"), encoding="utf-8")
    confirm(capsys, STORE, SPECIAL)
    _google_has(world, 12, 15, 18)
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    assert len(gbp_writes(world)) == 1                                  # 自動では書かない
    newest = [b for b in bundle_mod.read(STORE) if b["trigger"] == "special_soon"][-1]
    [item] = [i for i in newest["items"] if i["kind"] == "gbp_special"]
    assert item["state"] == "stopped" and item["code"] == "register_link_google_changed"
    assert newest["replaces"] is not None
    # 持ち主の二段確認なら、置き換えることを一段目に出して通す。
    rc_, first, err = link(capsys, "send", STORE, "--bundle", newest["id"], "--by", "店主")
    assert rc_ == 1 and any("置き換えます" in n for n in first["notes"])
    rc_, value, err = link(capsys, "send", STORE, "--bundle", newest["id"], "--by", "店主", "--confirm", first["digest"])
    assert rc_ == 0, err
    sent = gbp_writes(world)[-1]["body"]["specialHours"]["specialHourPeriods"]
    assert [p for p in sent if p["startDate"]["day"] == 12] == [
        {"startDate": {"year": 2026, "month": 12, "day": 12}, "openTime": {"hours": 14, "minutes": 0},
         "closeTime": {"hours": 17, "minutes": 0}, "endDate": {"year": 2026, "month": 12, "day": 12}, "closed": False}]


def test_柵_前に出していない日に違う値がGoogleにあれば自動では出さない(store, world, capsys):
    _google_has(world, 12, 9, 12)
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0 and gbp_writes(world) == []
    [item] = [i for i in bundles_by_trigger()["special_soon"]["items"] if i["kind"] == "gbp_special"]
    assert item["code"] == "register_link_google_changed"


def test_Googleに同じ値が既にあれば止めずに出す(store, world, capsys):
    _google_has(world, 12, 14, 18)
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert len(gbp_writes(world)) == 1


# ------------------------------------------------------------------ 柵: 二度出さない

def test_柵_同じ変化を二度出さない(store, world, capsys):
    declare(store, [consent(to=GBP, triggers=["special_soon", "event_soon"]),
                    consent(to=f"ig:{IG_ACCOUNT}", triggers=["special_soon", "event_soon"]),
                    consent(to=f"ig-story:{IG_ACCOUNT}")])
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    writes, creates = len(gbp_writes(world)), len(ig_creates(world))
    assert (writes, creates) == (2, 3)
    count = len(bundle_mod.read(STORE))
    for _ in range(2):
        rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
        assert rc_ == 0 and value["made"] == [] and value["items"] == []
    assert (len(gbp_writes(world)), len(ig_creates(world))) == (writes, creates)
    assert len(bundle_mod.read(STORE)) == count
    # 出た束を二段確認で通そうとしても、出す物が無い（出た物は選ばない）。
    special = bundles_by_trigger()["special_soon"]["id"]
    rc_, first, err = link(capsys, "send", STORE, "--bundle", special, "--by", "店主")
    assert first["items"] == []
    rc_, value, err = link(capsys, "send", STORE, "--bundle", special, "--by", "店主", "--confirm", first["digest"])
    assert rc_ == 0 and (len(gbp_writes(world)), len(ig_creates(world))) == (writes, creates)


def test_柵_一段目のあとに中身が変われば二段目は断る(store, world, capsys):
    link(capsys, "make", STORE, "--by", "m")
    special = bundles_by_trigger()["special_soon"]["id"]
    rc_, first, err = link(capsys, "send", STORE, "--bundle", special, "--by", "店主")
    (store / "画像" / "告知.jpg").write_bytes(jpeg() + b"\0")
    rc_, value, err = link(capsys, "send", STORE, "--bundle", special, "--by", "店主", "--confirm", first["digest"])
    assert rc_ == 2 and value["code"] == "register_link_confirm_mismatch"
    assert gbp_writes(world) == [] and ig_creates(world) == []


def test_柵_束を作ったあとに行が変われば出さない(store, world, capsys):
    link(capsys, "make", STORE, "--by", "m")
    row = store / "臨時" / "2026-12-12_設備の点検.md"
    row.write_text(row.read_text(encoding="utf-8").replace("close: 18:00", "close: 17:00"), encoding="utf-8")
    rc_, value, err = link(capsys, "send", STORE, "--auto", "--by", "timer")
    assert rc_ == 0 and gbp_writes(world) == []
    [item] = [i for i in bundles_by_trigger()["special_soon"]["items"] if i["kind"] == "gbp_special"]
    assert item["state"] == "stopped" and item["code"] == "register_link_row_changed"


# ------------------------------------------------------------------ 柵: 画像・休みの理由

def test_柵_ストーリーは画像が無ければ前向きに断る(store, world, capsys):
    declare(store, [consent(to=f"ig-story:{IG_ACCOUNT}")], image=False)
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    [item] = [i for i in bundles_by_trigger()["special_soon"]["items"] if i["kind"] == "ig_story"]
    assert item["state"] == "refused" and item["code"] == "register_link_image_missing" and item["detail"] == "no_image"
    assert ig_creates(world) == []
    entry = refusal_catalog.CATALOG["register_link_image_missing"]
    assert entry["why"] and entry["fix"]["text"] and entry["better"]
    assert "image:" in entry["fix"]["text"]
    # 人の目にも 3 つが出る（show の 1 行に符丁・断りの文は目録）。
    rc_, out, err = run(capsys, "link", "show", STORE)
    assert "断った" in out and "register_link_image_missing" in out


def test_柵_休みの理由は既定で外に出さない(store, world, capsys):
    declare(store, [consent(to=GBP), consent(to=f"ig:{IG_ACCOUNT}"), consent(to=f"ig-story:{IG_ACCOUNT}")])
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    sent = json.dumps([w["body"] for w in gbp_writes(world)] + ig_creates(world), ensure_ascii=False)
    assert ig_creates(world) and REASON not in sent
    assert REASON not in json.dumps(bundle_mod.read(STORE), ensure_ascii=False)


def test_理由はpublic_fieldsに書いたときだけ本文に入る(store, world, capsys):
    row = store / "臨時" / "2026-12-12_設備の点検.md"
    row.write_text(row.read_text(encoding="utf-8").replace("reason: 設備の点検", "reason: 設備の点検\npublic_fields: reason"),
                   encoding="utf-8")
    confirm(capsys, STORE, SPECIAL)
    link(capsys, "make", STORE, "--by", "m")
    [item] = [i for i in bundles_by_trigger()["special_soon"]["items"] if i["kind"] == "ig"]
    assert REASON in item["text"]


def test_目録_連動の断りは3つがそろい_命令が実在する():
    from thth.register import errors
    for code in [c for c in errors.REASONS if c.startswith("register_link")]:
        entry = refusal_catalog.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"], code


def test_連動の口は危ない領域に入っている():
    from tests.test_release_gate import gate
    assert "link_write" in gate.areas_of("thth/link/send.py")
    for path in ("thth/register/linkage.py", "thth/register/bundle.py"):
        assert "register" in gate.areas_of(path), path


# ------------------------------------------------------------------ 監査の直し（11/19）

SPECIAL_FILE = "臨時/2026-12-12_設備の点検.md"


def edit_row(store, old, new, *, again=True, capsys=None):
    row = store / SPECIAL_FILE
    text = row.read_text(encoding="utf-8")
    assert old in text
    row.write_text(text.replace(old, new), encoding="utf-8")
    if again:
        confirm(capsys, STORE, SPECIAL)


def special_patches(world) -> list:
    """PATCH ごとの 12 月の臨時の日（日 → (開け, 閉め) か closed）。"""
    out = []
    for w in gbp_writes(world):
        if w["method"] == "PATCH":
            out.append({p["startDate"]["day"]: ("closed" if p.get("closed") else
                                                (p.get("openTime", {}).get("hours"), p.get("closeTime", {}).get("hours")))
                        for p in w["body"]["specialHours"]["specialHourPeriods"]})
    return out


def item_of(trigger, kind, *, last=True):
    rows = [b for b in bundle_mod.read(STORE) if b["trigger"] == trigger]
    b = rows[-1] if last else rows[0]
    return next(i for i in b["items"] if i["kind"] == kind)


def test_柵_出す途中で落ちても_出た物は記録に残り_次の回に二度出さない(store, world, capsys, monkeypatch):
    """監査の 1: 1 件出すたびに記録し、出す前の印（sending）が残っていれば次の回は unknown にして出し直さない。"""
    from thth.link import send as send_mod
    declare(store, [consent(to=GBP), consent(to=f"ig:{IG_ACCOUNT}"), consent(to=f"ig-story:{IG_ACCOUNT}")])
    send_mod.make(STORE, by="timer", now=NOW)
    real = send_mod._send_ig

    def crash(*a, **k):
        raise RuntimeError("電源が落ちた")
    monkeypatch.setattr(send_mod, "_send_ig", crash)
    with pytest.raises(RuntimeError):
        send_mod.send(STORE, by="timer", now=NOW, auto=True)
    assert len(gbp_writes(world)) == 1
    assert item_of("special_soon", "gbp_special")["state"] == "sent"          # 出た物は落ちる前に残っている
    assert item_of("special_soon", "ig")["state"] == "sending"
    monkeypatch.setattr(send_mod, "_send_ig", real)
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    assert len(gbp_writes(world)) == 1                                          # Google マップは出し直さない
    assert item_of("special_soon", "ig")["state"] == "unknown"                  # 出たか分からない物は自動で出さない
    assert item_of("special_soon", "ig")["detail"] == "interrupted"
    assert [c.get("media_type") for c in ig_creates(world)] == [["STORIES"]]    # まだ出ていなかった物だけ出る


def test_柵_IGの口座のprojectが台帳と違えば出さない(store, world, capsys):
    """監査の 2: 同意の「店」は自己申告なので、口座の project を台帳の project と比べる。"""
    declare(store, [consent(to=f"ig:{IG_ACCOUNT}")])
    world["cfg"]["project"] = "other-shop"
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    assert ig_creates(world) == []
    item = item_of("special_soon", "ig")
    assert (item["state"], item["code"], item["detail"]) == ("blocked", "register_link_account", "project_mismatch")
    rc_, out, err = run(capsys, "link", "show", STORE)
    assert "直す所がある" in out


def test_柵_店の行に結んでいないGoogleマップの店には出さない(store, world, capsys):
    """監査の 2: gbp: の店は、台帳の店の行の gbp_location（二段確認で認めた）と一致するものだけ。"""
    other = "gbp:locations/333"
    declare(store, [consent(to=other)], outlets=[{"to": other, "triggers": ["special_soon"]}])
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    assert gbp_writes(world) == []
    item = item_of("special_soon", "gbp_special")
    assert (item["state"], item["code"]) == ("blocked", "register_link_store_unbound")


def test_店の行のgbp_locationは二段確認で認める(store, world, capsys):
    shop_row = store / "店.md"
    shop_row.write_text(shop_row.read_text(encoding="utf-8").replace(f"gbp_location: {LOC}", "gbp_location: locations/444"),
                        encoding="utf-8")
    rc_, value, err = jrun(capsys, "register", "confirm", STORE, "cafe-store", "--by", "m")
    assert rc_ == 1 and value["stage"] == 1 and "locations/444" in value["text"]


def test_柵_ファイルだけに書いた同意は控えるまで自動に使わない(store, world, capsys):
    """監査の 3: 素のファイルの同意は持ち主の手と言えない。控え（二段確認）の指紋と合う同意だけが効く。"""
    declare(store, [consent(until="2027-03-30")], keep=False)       # 書き換えた（期限を 1 日縮めた）が、控えていない
    rc_, value, err = link(capsys, "check", STORE)
    assert rc_ == 0 and value["unkept"] == 1 and value["consent"][0]["kept"] is False
    assert value["note"] == "register_link_consent_unkept"
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0 and gbp_writes(world) == []
    assert item_of("special_soon", "gbp_special")["state"] == "waiting"
    # 一段目は何も控えない。
    rc_, first, err = link(capsys, "consent", STORE, "--by", "店主 みほん")
    assert rc_ == 1 and first["stage"] == 1 and first["consent"][0]["until"] == "2027-03-30"
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert gbp_writes(world) == []
    rc_, value, err = link(capsys, "consent", STORE, "--by", "店主 みほん", "--confirm", first["digest"])
    assert rc_ == 0, err
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert len(gbp_writes(world)) == 1


def test_柵_行を元に戻せば_外に今出ている値と違うので出し直す(store, world, capsys):
    """監査の 4: A → B → A と戻したとき、A の束の id が前と同じで作られず、外が B のまま残らないこと。"""
    link(capsys, "run", STORE, "--by", "timer")
    edit_row(store, "close: 18:00", "close: 17:00", capsys=capsys)
    link(capsys, "run", STORE, "--by", "timer")
    edit_row(store, "close: 17:00", "close: 18:00", capsys=capsys)
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    assert [p[12] for p in special_patches(world)] == [(14, 18), (14, 17), (14, 18)]


def test_柵_日を動かせば前に書いた日を片づける(store, world, capsys):
    """監査の 5: 12/12 を 12/11 に動かしたら、12/12 は Google から外す（片づけも同意の範囲で）。"""
    link(capsys, "run", STORE, "--by", "timer")
    edit_row(store, "date: 2026-12-12", "date: 2026-12-11", capsys=capsys)
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    last = special_patches(world)[-1]
    assert 11 in last and 12 not in last and {24, 29} <= set(last)


def test_柵_行を退役すれば前に書いた日を片づける(store, world, capsys):
    link(capsys, "run", STORE, "--by", "timer")
    edit_row(store, "status: 本採用", "status: 退役", again=False)
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    patches = special_patches(world)
    assert len(patches) == 2 and 12 not in patches[-1] and {24, 29} <= set(patches[-1])
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")        # 片づけも二度しない
    assert len(special_patches(world)) == 2


def test_片づけは同意の範囲の外なら自動でしない(store, world, capsys):
    link(capsys, "run", STORE, "--by", "timer")
    declare(store, [consent(to=f"ig:{IG_ACCOUNT}")])
    edit_row(store, "status: 本採用", "status: 退役", again=False)
    link(capsys, "run", STORE, "--by", "timer")
    assert len(special_patches(world)) == 1
    [b] = [b for b in bundle_mod.read(STORE) if b.get("clear_only")]
    assert b["items"][0]["state"] == "waiting" and b["items"][0]["clear"] == ["2026-12-12"]


@pytest.mark.parametrize("method, status, word", [
    ("PATCH", 429, "gbp_quota_exceeded"),      # 書く口の 429（受け付けていない）
    ("GET", 503, "gbp_unavailable"),           # 読む口の 503（書く口の 5xx は出たか分からない＝unknown のまま）
])
def test_一時の断りは止めずに次の回へ回す(store, world, capsys, method, status, word):
    """監査の 6: Google の 429・503 などは stopped にせず、同意のある物は次の回に自動で出る（RETRY_MAX 回まで）。"""
    key = (method, gbp_api.INFO_BASE + LOC)
    good = dict.get(world["google"].routes, key)
    world["google"].routes[key] = (status, {"error": {"code": status, "status": "RESOURCE_EXHAUSTED"
                                                      if status == 429 else "UNAVAILABLE"}})
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    item = item_of("special_soon", "gbp_special")
    assert item["state"] == "ready" and item["retry_since"] and item["detail"] == word
    world["google"].routes[key] = good
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert item_of("special_soon", "gbp_special")["state"] == "sent"


def test_一時の断りは回数でなく時間で止める(store, world, capsys, monkeypatch):
    """2 回目の E: 何回断られても出さない期限までは待ち続け、期限を過ぎたら出さない。"""
    key = ("PATCH", gbp_api.INFO_BASE + LOC)
    world["google"].routes[key] = (429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}})
    for _ in range(8):
        link(capsys, "run", STORE, "--by", "timer")
    assert item_of("special_soon", "gbp_special")["state"] == "ready"
    monkeypatch.setattr(jst, "now_jst", lambda: datetime.datetime(2026, 12, 13, 0, 30, tzinfo=jst.JST))
    link(capsys, "run", STORE, "--by", "timer")
    assert item_of("special_soon", "gbp_special")["state"] == "expired"


def test_画像の無いストーリーは画像を置けば作り直して出る(store, world, capsys):
    declare(store, [consent(to=GBP), consent(to=f"ig-story:{IG_ACCOUNT}")], image=False)
    link(capsys, "run", STORE, "--by", "timer")
    assert item_of("special_soon", "ig_story")["state"] == "refused"
    declare(store, [consent(to=GBP), consent(to=f"ig-story:{IG_ACCOUNT}")], image=True)
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    assert [c.get("media_type") for c in ig_creates(world)] == [["STORIES"]]
    assert len(gbp_writes(world)) == 1                                   # 出たマップは出し直さない
    assert item_of("special_soon", "gbp_special")["state"] == "unchanged"


def test_外に出る中身が同じなら行の字が変わっても出し直さない(store, world, capsys):
    """監査の 7: 本文（外に出さない）だけを直して認め直しても、束を作らない。"""
    link(capsys, "run", STORE, "--by", "timer")
    count = len(bundle_mod.read(STORE))
    edit_row(store, "午前は閉め、", "午前は閉めて、", capsys=capsys)
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0 and value["made"] == [] and len(bundle_mod.read(STORE)) == count
    assert len(gbp_writes(world)) == 1
    # 承認待ちの IG は、外に出る中身が同じなので止めずに通せる。
    special = bundles_by_trigger()["special_soon"]["id"]
    rc_, first, err = link(capsys, "send", STORE, "--bundle", special, "--by", "店主")
    assert rc_ == 1 and first["stops"] == [] and len(first["items"]) == 2


def test_書く直前にGoogleが変わっていれば書かない(store, world, capsys, monkeypatch):
    """監査の 8: 計画のための読みと、書く直前の読みのあいだに人がマップで変えたら書かない。"""
    reads = {"n": 0}
    real = gbp_api.Client.location

    def location(self, location_id, **k):
        value = real(self, location_id, **k)
        reads["n"] += 1
        if reads["n"] >= 2:
            value["specialHours"]["specialHourPeriods"].append(
                {"startDate": {"year": 2026, "month": 12, "day": 31}, "closed": True})
        return value
    monkeypatch.setattr(gbp_api.Client, "location", location)
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    assert gbp_writes(world) == []
    item = item_of("special_soon", "gbp_special")
    assert (item["state"], item["code"], item["detail"]) == ("stopped", "register_link_google_changed",
                                                             "between_read_and_write")


def test_出たか分からない物を通すときは一段目に印が出る_書けていたなら止めない(store, world, capsys):
    """監査の 9 と 6: unknown を --bundle で通すとき、先に確かめる注意を出す。Google に既に今の値があれば置き換えと言わない。"""
    declare(store, [])
    link(capsys, "make", STORE, "--by", "m")
    bundles = bundle_mod.read(STORE)
    for b in bundles:
        for i in b["items"]:
            if i["kind"] == "gbp_special":
                i.update(state="unknown", code="register_link_unknown", detail="gbp_write_unknown")
    bundle_mod.write(STORE, bundles)
    _google_has(world, 12, 14, 18)                                       # 実は書けていた
    special = bundles_by_trigger()["special_soon"]["id"]
    rc_, first, err = link(capsys, "send", STORE, "--bundle", special, "--by", "店主")
    assert rc_ == 1
    assert any("出たか分からなかった" in n and "thth gbp show" in n for n in first["notes"])
    assert not any("置き換えます" in n for n in first["notes"])
    assert [i["was_unknown"] for i in first["items"] if i["kind"] == "gbp_special"] == [True]
    rc_, out, err = run(capsys, "link", "send", STORE, "--bundle", special, "--by", "店主")
    assert "出たか分からなかった" in out


def test_束が多くなっても出た束と書いた値は落とさない():
    """監査の 10: 古い束を落とすとき、出た物を持つ束（二度出さない・前に書いた値と比べる材料）は残す。"""
    sent = {"id": "bsent", "row": "r0", "trigger": "special_soon", "items": [
        {"to": GBP, "kind": "gbp_special", "state": "sent", "sent_value": [{"date": "2026-12-12", "period": {}}],
         "outward": "x", "text": "長い本文"}]}
    closed = [{"id": f"b{n}", "row": f"r{n}", "trigger": "special_soon", "items": [{"state": "expired"}]}
              for n in range(1, bundle_mod.BUNDLES_MAX + 50)]
    later = [{"id": f"c{n}", "row": f"r{n}", "trigger": "special_soon", "items": [{"state": "superseded"}]}
             for n in range(1, bundle_mod.BUNDLES_MAX + 50)]
    out = bundle_mod.trim([sent] + closed + later)
    assert out[0]["id"] == "bsent" and out[0]["items"][0]["sent_value"]
    assert not [b for b in out if b["id"].startswith("b") and b["id"] != "bsent"]   # 閉じた古い束だけ落ちた
    assert {b["id"] for b in later} <= {b["id"] for b in out}                       # 行ごとの最後の束は残る


# ------------------------------------------------------------------ 監査 2 回目（11/19）

OTHER = "cafe-special-2026-12-12b"


def add_other_row(store, capsys, *, head="closed: false\nopen: 10:00\nclose: 15:00"):
    """同じ 12/12 を持つ別の臨時の行（R2）を足して認める。"""
    (store / "臨時" / "2026-12-12_別の知らせ.md").write_text(
        f"---\nid: {OTHER}\nkind: special\nstatus: 本採用\nsource: owner\nconfirmed: 推測\nreview_by: 2026-12-13\n"
        f"public: true\ndate: 2026-12-12\n{head}\n---\n同じ日の別の行。\n", encoding="utf-8")
    confirm(capsys, STORE, OTHER)


def test_柵_片づけは同じ日を持つ別の行の値を消さない(store, world, capsys):
    """2 回目の A: R1（12/12 14〜18 時）が出たあと、R2（12/12 10〜15 時）を作って R1 を退役しても、12/12 は R2 の値。"""
    link(capsys, "run", STORE, "--by", "timer")
    add_other_row(store, capsys)
    edit_row(store, "status: 本採用", "status: 退役", again=False)
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    assert special_patches(world)[-1].get(12) == (10, 15)
    assert not [b for b in bundle_mod.read(STORE) if b.get("clear_only")]


def test_片づけはその日に最後に書いたのが別の行なら外さない(store, world, capsys):
    link(capsys, "run", STORE, "--by", "timer")                          # R1 が 12/12 を書いた
    add_other_row(store, capsys)
    link(capsys, "run", STORE, "--by", "timer")                          # R2 が 12/12 を書き替えた
    edit_row(store, "status: 本採用", "status: 退役", again=False)       # R1 を退役
    (store / "臨時" / "2026-12-12_別の知らせ.md").write_text(
        (store / "臨時" / "2026-12-12_別の知らせ.md").read_text(encoding="utf-8").replace("public: true", "public: false"),
        encoding="utf-8")                                               # R2 も今は使えない（が、最後に書いたのは R2）
    link(capsys, "run", STORE, "--by", "timer")
    assert special_patches(world)[-1].get(12) == (10, 15)


def test_柵_行が見つからないだけの片づけは同意があっても承認待ち(store, world, capsys):
    """2 回目の C: 行が無い（消した・読めない）は決められないので、自動では外さない。持ち主の二段確認なら外す。"""
    link(capsys, "run", STORE, "--by", "timer")
    (store / SPECIAL_FILE).rename(store / "臨時.bak")
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    assert len(special_patches(world)) == 1
    [b] = [b for b in bundle_mod.read(STORE) if b.get("clear_only")]
    assert b["items"][0]["hold"] == "row_missing" and b["items"][0]["state"] == "waiting"
    rc_, first, err = link(capsys, "send", STORE, "--bundle", b["id"], "--by", "店主")
    assert rc_ == 1 and any("外す日: 2026-12-12" in n for n in first["notes"])
    rc_, value, err = link(capsys, "send", STORE, "--bundle", b["id"], "--by", "店主", "--confirm", first["digest"])
    assert rc_ == 0, err
    assert 12 not in special_patches(world)[-1]


def test_入れ替えの途中は束を作るのも出すのも断る(store, world, capsys, monkeypatch):
    from thth.register import keep
    monkeypatch.setattr(keep, "pending", lambda root: {"id": "k1", "what": "batch", "entries": [], "by": "m", "at": "x"})
    for verb in (("make", STORE, "--by", "m"), ("run", STORE, "--by", "timer")):
        rc_, value, err = link(capsys, *verb)
        assert rc_ == 2 and value["code"] == "register_batch_pending", verb
    assert gbp_writes(world) == []


def test_片づける日は一段目と一覧とdigestに出る(store, world, capsys):
    """2 回目の D。"""
    declare(store, [])
    link(capsys, "make", STORE, "--by", "m")
    special = bundles_by_trigger()["special_soon"]["id"]
    rc_, first, err = link(capsys, "send", STORE, "--bundle", special, "--by", "店主", "--confirm", "x" * 12)
    assert rc_ == 2
    rc_, first, err = link(capsys, "send", STORE, "--bundle", special, "--by", "店主")
    link(capsys, "send", STORE, "--bundle", special, "--by", "店主", "--confirm", first["digest"])
    edit_row(store, "status: 本採用", "status: 退役", again=False)
    link(capsys, "make", STORE, "--by", "m")
    [b] = [b for b in bundle_mod.read(STORE) if b.get("clear_only")]
    rc_, first, err = link(capsys, "send", STORE, "--bundle", b["id"], "--by", "店主")
    assert [i["clear"] for i in first["items"]] == [["2026-12-12"]]
    assert any("外す日: 2026-12-12" in n for n in first["notes"])
    rc_, out, err = run(capsys, "link", "send", STORE, "--bundle", b["id"], "--by", "店主")
    assert "外す日: 2026-12-12" in out
    rc_, out, err = run(capsys, "link", "show", STORE)
    assert "片づけの束" in out and "外す日: 2026-12-12" in out


def test_consentの一段目は確認なしで出ることと片づけを含むことを言う(store, world, capsys):
    declare(store, [consent(until="2027-03-30")], keep=False)
    rc_, out, err = run(capsys, "link", "consent", STORE, "--by", "店主")
    assert rc_ == 1 and "確認なしで出ます" in out and "外す" in out


def test_consentはbyが要る_取り消しは二段確認で二度と効かない(store, world, capsys):
    """2 回目の G と B。"""
    rc_, value, err = link(capsys, "consent", STORE)
    assert rc_ == 2 and value["code"] == "register_args_invalid"
    rc_, first, err = link(capsys, "consent", STORE, "--revoke", "--to", GBP, "--by", "店主")
    assert rc_ == 1 and first["consent"][0]["to"] == GBP and "二度と" in first["note"]
    rc_, value, err = link(capsys, "check", STORE)
    assert value["consent"][0]["kept"] is True                            # 一段目は何も変えない
    rc_, value, err = link(capsys, "consent", STORE, "--revoke", "--to", GBP, "--by", "店主", "--confirm", first["digest"])
    assert rc_ == 0 and value["revoked"][0]["to"] == GBP
    rc_, value, err = link(capsys, "check", STORE)
    assert value["consent"][0]["kept"] is False and value["unkept"] == 0  # 同じ同意は控え直せない
    rc_, value, err = link(capsys, "consent", STORE, "--by", "店主")
    assert rc_ == 0 and value["kept"] == []
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0 and gbp_writes(world) == []
    assert item_of("special_soon", "gbp_special")["state"] == "waiting"


def test_壊れた控えは別の名前に退けて空から始める(store, world, capsys):
    path = pathlib.Path(linkage.kept_path(STORE))
    path.write_text("{壊れた", encoding="utf-8")
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 2 and value["code"] == "register_ledger_invalid"
    rc_, first, err = link(capsys, "consent", STORE, "--reset", "--by", "店主")
    assert rc_ == 1 and first["exists"] is True
    assert path.read_text(encoding="utf-8") == "{壊れた"                   # 一段目は何も変えない
    rc_, value, err = link(capsys, "consent", STORE, "--reset", "--by", "店主", "--confirm", first["digest"])
    assert rc_ == 0, err
    assert not path.exists() and (path.parent / value["aside"]).read_text(encoding="utf-8") == "{壊れた"
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0 and gbp_writes(world) == []                         # 控え直すまで自動に効かない
    keep_consent()
    link(capsys, "run", STORE, "--by", "timer")
    assert len(gbp_writes(world)) == 1


def test_台帳を外せば控えと束も外す(store, world, capsys):
    link(capsys, "run", STORE, "--by", "timer")
    assert pathlib.Path(bundle_mod.path_for(STORE)).exists() and pathlib.Path(linkage.kept_path(STORE)).exists()
    bundles_path, kept_path = pathlib.Path(bundle_mod.path_for(STORE)), pathlib.Path(linkage.kept_path(STORE))
    rc_, first, err = jrun(capsys, "register", "remove", STORE, "--by", "m")
    if rc_ == 1:
        rc_, value, err = jrun(capsys, "register", "remove", STORE, "--by", "m", "--confirm", first["digest"])
    assert rc_ == 0, err
    assert not bundles_path.exists() and not kept_path.exists()


@pytest.mark.parametrize("code", ["too_soon", "quiet_hours"])
def test_IGの安全装置の断りは次の時刻まで数えずに待つ(store, world, capsys, monkeypatch, code):
    """2 回目の E: 安全装置（本物の core.send_once の中の guard.check）が次の時刻を返せば、その時刻まで出さずに待つ。"""
    from thth import guard
    later = NOW + datetime.timedelta(hours=2)
    real = guard.check
    blocked = {"on": True}

    def check(account, cfg, kind, **k):
        if blocked["on"] and kind == "publish" and account == IG_ACCOUNT:
            raise guard.GuardRefused(code, next_at=later)
        return real(account, cfg, kind, **k)
    monkeypatch.setattr(guard, "check", check)
    declare(store, [consent(to=f"ig:{IG_ACCOUNT}")])
    link(capsys, "run", STORE, "--by", "timer")
    item = item_of("special_soon", "ig")
    assert item["state"] == "ready" and item["detail"] == code and item["retry_after"]
    assert ig_creates(world) == []
    blocked["on"] = False
    link(capsys, "run", STORE, "--by", "timer")                           # 次の時刻の前は試しもしない
    assert item_of("special_soon", "ig")["state"] == "ready" and ig_creates(world) == []
    monkeypatch.setattr(jst, "now_jst", lambda: later + datetime.timedelta(minutes=1))
    link(capsys, "run", STORE, "--by", "timer")
    assert item_of("special_soon", "ig")["state"] == "sent" and len(ig_creates(world)) == 1


def test_片づける日が過ぎても同じ中身の束を作り直さない(store, world, capsys, monkeypatch):
    """2 回目の F。"""
    link(capsys, "run", STORE, "--by", "timer")
    edit_row(store, "date: 2026-12-12", "date: 2026-12-14", capsys=capsys)
    edit_row(store, "review_by: 2026-12-13", "review_by: 2026-12-20", capsys=capsys)
    link(capsys, "make", STORE, "--by", "m")
    count = len(bundle_mod.read(STORE))
    monkeypatch.setattr(jst, "now_jst", lambda: datetime.datetime(2026, 12, 13, 9, 0, tzinfo=jst.JST))
    rc_, value, err = link(capsys, "make", STORE, "--by", "m")
    assert rc_ == 0 and value["made"] == [] and len(bundle_mod.read(STORE)) == count


def test_束が500を超えても作り直しと送る道が通る(store, world, capsys):
    """2 回目の H: 古い束を落としても id が重ならず、開いた物を持つ束は要約にしない。"""
    filler = [{"id": f"bfill{n:04d}", "row": f"old-{n}", "trigger": "event_soon", "generation": 0,
               "items": [{"to": GBP, "kind": "gbp_event", "state": "expired"}]} for n in range(bundle_mod.BUNDLES_MAX + 20)]
    bundle_mod.write(STORE, filler)
    link(capsys, "run", STORE, "--by", "timer")
    edit_row(store, "close: 18:00", "close: 17:00", capsys=capsys)
    link(capsys, "run", STORE, "--by", "timer")
    edit_row(store, "close: 17:00", "close: 18:00", capsys=capsys)
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    rows = bundle_mod.read(STORE)
    ids = [b["id"] for b in rows]
    assert len(ids) == len(set(ids))
    assert [p[12] for p in special_patches(world)] == [(14, 18), (14, 17), (14, 18)]
    line = [b for b in rows if b["row"] == SPECIAL and b["trigger"] == "special_soon"]
    assert [b["generation"] for b in line] == [0, 1, 2]
    assert not [b for b in rows if b.get("summary") and any(i["state"] in bundle_mod.OPEN_STATES for i in b["items"])]
    # 承認待ちの IG は、要約にされずに二段確認で出せる。
    newest = line[-1]["id"]
    rc_, first, err = link(capsys, "send", STORE, "--bundle", newest, "--by", "店主")
    rc_, value, err = link(capsys, "send", STORE, "--bundle", newest, "--by", "店主", "--confirm", first["digest"])
    assert rc_ == 0, err
    assert len(ig_creates(world)) == 2 and item_of("special_soon", "ig")["state"] == "sent"


def test_代は最大の代に1を足す():
    line = [{"id": "x2", "row": "r", "trigger": "special_soon", "generation": 5, "outward": "o1", "items": []}]
    made = bundle_mod._new_bundle("s", "/nonexistent", line, "r", "special_soon", None, None,
                                  [{"to": GBP, "kind": "gbp_special", "days": [], "clear": [], "state": "waiting"}], NOW)
    assert made["generation"] == 6


def test_手元の読み書きの失敗は止めて続ける(store, world, capsys):
    """2 回目の I: 画像を口座の repo に写せない（OSError）は網に出る前なので stopped・ほかの物は続ける。"""
    declare(store, [consent(to=GBP), consent(to=f"ig:{IG_ACCOUNT}")])
    repo = pathlib.Path(world["cfg"]["repo_dir"])
    repo.chmod(0o500)
    try:
        rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    finally:
        repo.chmod(0o700)
    assert rc_ == 0, err
    item = item_of("special_soon", "ig")
    assert (item["state"], item["detail"]) == ("stopped", "local_io")
    assert len(gbp_writes(world)) == 1 and ig_creates(world) == []


def test_本物のsend_onceの途中_入れ物を作ったあとで落ちても二度出さない(store, world, capsys, monkeypatch):
    """1 件ずつの記録: `_send_ig` の差し替えでなく、本物の core.send_once の中（IG の入れ物を作ったあと）で落ちる形。"""
    declare(store, [consent(to=GBP), consent(to=f"ig:{IG_ACCOUNT}")])
    real = ig.InstagramAdapter.publish

    def publish(self, post, *, dry_run, on_container_created=None, **k):
        def created(*a, **kw):
            if on_container_created is not None:
                on_container_created(*a, **kw)
            raise KeyboardInterrupt("入れ物を作ったあとで落ちた")
        return real(self, post, dry_run=dry_run, on_container_created=None if dry_run else created, **k)
    monkeypatch.setattr(ig.InstagramAdapter, "publish", publish)
    from thth.link import send as send_mod
    send_mod.make(STORE, by="timer", now=NOW)
    with pytest.raises(BaseException):
        send_mod.send(STORE, by="timer", now=NOW, auto=True)
    assert item_of("special_soon", "gbp_special")["state"] == "sent"
    assert item_of("special_soon", "ig")["state"] == "sending"
    created = len(ig_creates(world))
    assert created == 1
    monkeypatch.setattr(ig.InstagramAdapter, "publish", real)
    rc_, value, err = link(capsys, "run", STORE, "--by", "timer")
    assert rc_ == 0, err
    assert item_of("special_soon", "ig")["state"] == "unknown"
    assert len(ig_creates(world)) == created and len(gbp_writes(world)) == 1
