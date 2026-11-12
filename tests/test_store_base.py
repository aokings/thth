"""店を読む口（`thth/shop/`・`thth shop …` の店の動詞・BASE v2）。

柵（設計 `docs/設計_ショップとWordPressの口_2026-11-12.md` §2 とまとめる者の直し 11/12）:
  - 打つのは GET だけ・送り先は定数のホスト `apiv2.thebase.com`・許した道（/api/user・/api/items）だけ。注文は読まない。
  - 鍵は引数で受けない・出力にもファイル（鍵のファイルのほか）にも出ない。`GET /api/user` のメールも残らない。
  - 下書きは承認されていない形（status: draft・承認の欄なし）で、まとめて作らない（1 本か上限 3 本）。
  - diff は読むだけ（写しを進めるのは --accept）・初回は基準だけ・読み切れなければ「消えた・売り切れ」と言わない。
  - 非公開の商品は写しに id と visible だけ。
  - 新しいファイルは release の関門の危ない領域に入っている。
本物の BASE は呼ばない（公式の応答の形に沿った偽の transport）。
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import importlib.util
import json
import os
import pathlib
import secrets
import stat
import urllib.parse

import pytest

from thth import cli, queuefile
from thth import refusal_catalog as rc
from thth.shop import base, ledger, wire
from thth.shop import cli as store_cli
from tests.kept_bytes import kept_bytes

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHOP_DIR = ROOT / "thth" / "shop"
STORE = "nigami-base"
MAIL = "owner-mail-marker@example.com"
INTRO = "紹介文の目印の本文"


def item(i, *, name=None, price=1200, stock=3, visible=True, sales_status="item_now_on_sale", **extra):
    """`GET /api/items` の 1 件（公式の欄の形・知らない欄も混ぜる）。"""
    row = {"type": "normal", "id": i, "name": name or f"豆 {i}", "detail": f"説明の本文 {i}", "identifier": None,
           "price": price, "proper_price": None, "stock": stock, "visible": visible, "sales_status": sales_status,
           "item_tax_type": "standard", "created_at": "2026-09-01T00:00:00Z", "modified_at": "2026-09-08T00:00:00Z",
           "images": [{"id": 1, "url": f"https://img.example.invalid/{i}.jpg",
                       "scaled_url": f"https://img.example.invalid/{i}_w.jpg",
                       "square_url": f"https://img.example.invalid/{i}_sq.jpg"}],
           "variations": [], "categories": [], "options": [], "quantity_limit": None,
           "_links": {"self": {"href": f"/api/items/{i}", "method": "GET", "document_href": "/docs/x"}},
           "future_field": {"anything": True}}
    row.update(extra)
    return row


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    home = tmp_path / "home"; home.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home)]:
        monkeypatch.setenv(key, str(value))
    state = {"items": [item(1), item(2), item(3, visible=False)], "calls": [], "status": 200, "shop_id": "nigami",
             "token": "pat_" + secrets.token_urlsafe(24), "tmp": tmp_path, "user_status": 200}

    def transport(method, url, headers):
        wire.check_url(method, url)       # 本物の口と同じ柵を通す
        state["calls"].append({"method": method, "url": url, "auth": headers.get("Authorization")})
        parts = urllib.parse.urlsplit(url)
        if parts.path == "/api/user":
            if state["user_status"] != 200:
                return state["user_status"], json.dumps({"type": "/errors/auth/unauthenticated", "title": "x",
                                                         "status": state["user_status"]}).encode()
            return 200, json.dumps({"shop_id": state["shop_id"], "shop_url": "https://nigami.example.invalid",
                                    "mail_address": MAIL, "nickname": "nick", "shop_name": "にがみ屋",
                                    "shop_introduction": INTRO, "shop_category": None, "able_to_business": True,
                                    "using_only_cart": False, "default_item_tax_type": "standard",
                                    "external_service_account": {"twitter": "tw-marker"}}).encode()
        if state["status"] != 200:
            return state["status"], json.dumps({"type": "/errors/auth/forbidden", "title": "x",
                                                "status": state["status"]}).encode()
        query = urllib.parse.parse_qs(parts.query)
        limit, offset = int(query["limit"][0]), int(query["offset"][0])
        return 200, json.dumps(state["items"][offset:offset + limit]).encode()

    monkeypatch.setattr(store_cli, "TRANSPORT", transport)
    monkeypatch.setattr(store_cli, "READ_SECRET", lambda: state["token"] + "\n")
    monkeypatch.setattr(base, "SLEEP", lambda seconds: state.setdefault("slept", []).append(seconds))
    return state


def run(capsys, *argv):
    rc_ = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc_, out, err


def setup_store(capsys, env, project="nigamilab"):
    assert run(capsys, "shop", "add", STORE, "--kind", "base", "--project", project, "--by", "m")[0] == 0
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--expires", "2027-01-31", "--by", "m")
    assert rc_ == 0, err
    return out + err


def jrun(capsys, *argv):
    rc_, out, err = run(capsys, *argv, "--json")
    return rc_, json.loads(out), err


# ------------------------------------------------------------------ 打つのは GET だけ・定数のホスト・注文は読まない

def _string_constants(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def test_柵_GET以外の動詞はwire_pyのPOSTだけ():
    """PUT・PATCH・DELETE を字面に書いたら赤。POST は網の口（wire.py）の中だけ（Shopify の 2 つの道の表と Request の
    1 か所・GraphQL の決まりで POST だが店は変えない）。5.17.0 で「GET 以外を持たない」から書き替えた
    （設計 Shopify と STORES §0 の 2・§5。BASE への POST は `tests/test_store_wire.py` が断るのを見る）。"""
    found = [(path.name, value.strip().upper()) for path in sorted(SHOP_DIR.glob("*.py"))
             for value in _string_constants(path) if value.strip().upper() in ("POST", "PUT", "PATCH", "DELETE")]
    assert found == [("wire.py", "POST")] * 2


def test_柵_網の口はGETと定数のホストと許した道だけを通す():
    assert wire.BASE_HOST == "apiv2.thebase.com" and wire.BASE_ORIGIN == "https://apiv2.thebase.com"
    assert wire.BASE_PATHS == frozenset(("/api/user", "/api/items"))
    wire.check_url("GET", "https://apiv2.thebase.com/api/items?limit=1&offset=0")
    for method, url in [("POST", "https://apiv2.thebase.com/api/items"),
                        ("GET", "https://apiv2.thebase.com/api/orders"),
                        ("GET", "https://evil.example/api/items"),
                        ("GET", "http://apiv2.thebase.com/api/items"),
                        ("GET", "https://apiv2.thebase.com:8443/api/items"),
                        ("GET", "https://u:p@apiv2.thebase.com/api/items")]:
        with pytest.raises(wire.StoreRefused):
            wire.check_url(method, url)


def test_柵_本物の網の口も同じ柵を先に通す(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", lambda *a, **k: pytest.fail("網に出た"))
    with pytest.raises(wire.StoreRefused) as exc:
        wire.default_transport("POST", "https://apiv2.thebase.com/api/items", {})
    assert exc.value.code == "store_request_rejected"


def test_柵_台帳で送り先を変えられない(capsys, env):
    setup_store(capsys, env)
    path = pathlib.Path(ledger.path_for(STORE))
    data = json.loads(path.read_text())
    data["host"] = "evil.example"
    path.write_text(json.dumps(data))
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 2 and value["code"] == "store_ledger_invalid"


def test_柵_命令を全部打っても道はuserとitemsだけでGETだけ(capsys, env, isolated_account_factory):
    isolated_account_factory()
    setup_store(capsys, env)
    for argv in (("whoami",), ("items",), ("diff",), ("diff", "--accept")):
        assert run(capsys, "shop", argv[0], STORE, *argv[1:])[0] == 0
    env["items"].append(item(9))
    assert run(capsys, "shop", "draft", STORE, "--to", "nigamilab-threads", "--kind", "new")[0] == 0
    assert {c["method"] for c in env["calls"]} == {"GET"}
    assert {urllib.parse.urlsplit(c["url"]).path for c in env["calls"]} <= {"/api/user", "/api/items"}
    assert all(c["url"].startswith("https://apiv2.thebase.com/api/") for c in env["calls"])


# ------------------------------------------------------------------ 鍵を出さない・引数で取らない

def _store_parsers():
    found = []

    def visit(parser, path):
        found.append((path, parser))
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    visit(child, [*path, name])
    sub = next(a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    verbs = next(a for a in sub.choices["shop"]._actions if isinstance(a, argparse._SubParsersAction))
    for name in store_cli.VERBS:
        visit(verbs.choices[name], ["shop", name])
    return found


def _shape(parser):
    """動詞の引数の形（位置・旗・必須・選択肢）。"""
    out = []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        out.append((action.dest, tuple(action.option_strings), bool(action.required),
                    tuple(action.choices) if action.choices else None))
    return out


# Instagram の商品タグの 5 つの動詞の形（2026-11-12 に今の parser から写した・店の家族を足しても変えない）。
META_SHOP_SHAPES = {
    "eligibility": [("account", (), True, None), ("json", ("--json",), False, None)],
    "catalogs": [("account", (), True, None), ("json", ("--json",), False, None)],
    "products": [("account", (), True, None), ("json", ("--json",), False, None),
                 ("catalog", ("--catalog",), True, None), ("q", ("--q",), False, None)],
    "tags": [("account", (), True, None), ("json", ("--json",), False, None), ("media_id", (), True, None)],
    "tag": [("account", (), True, None), ("by", ("--by",), False, None), ("confirm", ("--confirm",), False, None),
            ("wait", ("--wait",), False, None), ("json", ("--json",), False, None), ("media_id", (), True, None),
            ("product", ("--product",), True, None), ("x", ("--x",), False, None), ("y", ("--y",), False, None)],
}


def test_柵_Instagramの商品タグの動詞と引数は変わらない():
    sub = next(a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    verbs = next(a for a in sub.choices["shop"]._actions if isinstance(a, argparse._SubParsersAction))
    assert set(verbs.choices) == set(META_SHOP_SHAPES) | set(store_cli.VERBS)
    assert {name: _shape(verbs.choices[name]) for name in META_SHOP_SHAPES} == META_SHOP_SHAPES


def test_親の助けは2つの山に分かれる():
    sub = next(a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    text = sub.choices["shop"].format_help()
    assert "店を読む（BASE など）: 第 1 引数は店の名前" in text
    assert "Instagram の商品タグ: 第 1 引数は Instagram の口座" in text


def test_柵_鍵を引数で受ける口が無い():
    wrong = []
    for path, parser in _store_parsers():
        for action in parser._actions:
            if isinstance(action, (argparse._SubParsersAction, argparse._HelpAction)):
                continue
            words = " ".join(action.option_strings) + " " + str(action.dest)
            if any(w in words.lower() for w in ("token", "pat", "secret", "key", "password")):
                wrong.append((path, action.dest))
            if not action.option_strings and action.dest != "store":
                wrong.append((path, action.dest))
    assert wrong == []


def test_柵_鍵とメールは出力にもファイルにも出ない(capsys, env, isolated_account_factory):
    isolated_account_factory()
    texts = [setup_store(capsys, env)]
    for argv in (("whoami",), ("whoami", "--json"), ("items",), ("items", "--json"), ("diff",), ("diff", "--json")):
        _, out, err = run(capsys, "shop", argv[0], STORE, *argv[1:])
        texts.append(out + err)
    env["items"].append(item(7))
    _, out, err = run(capsys, "shop", "draft", STORE, "--to", "nigamilab-threads", "--kind", "new")
    texts.append(out + err)
    env["user_status"] = 401
    _, out, err = run(capsys, "shop", "whoami", STORE, "--json")
    texts.append(out + err)
    joined = "\n".join(texts)
    for marker in (env["token"], MAIL, INTRO, "tw-marker", "説明の本文"):
        assert marker not in joined, marker
    token_file = pathlib.Path(ledger.load(STORE)["token"])
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(pathlib.Path(ledger.path_for(STORE)).stat().st_mode) == 0o600
    for path in pathlib.Path(env["tmp"]).rglob("*"):
        if not path.is_file() or path == token_file:
            continue
        raw = kept_bytes(path)
        for marker in (env["token"], MAIL, INTRO, "tw-marker", "説明の本文"):
            assert marker.encode() not in raw, f"{path} に {marker}"


def test_鍵は端末でない標準入力から黙って読まない(capsys, env, monkeypatch):
    monkeypatch.setattr(store_cli, "READ_SECRET", None)
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(env["token"] + "\n"))
    run(capsys, "shop", "add", STORE, "--kind", "base", "--project", "nigamilab", "--by", "m")
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--expires", "2027-01-31", "--by", "m")
    assert rc_ == 2 and "store_token_input_unsafe" in err
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--expires", "2027-01-31", "--by", "m", "--stdin")
    assert rc_ == 0, err
    assert env["token"] not in out + err


def test_token_setは店が見えないとき保存しない(capsys, env):
    run(capsys, "shop", "add", STORE, "--kind", "base", "--project", "nigamilab", "--by", "m")
    env["user_status"] = 401
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--expires", "2027-01-31", "--by", "m")
    assert rc_ == 2 and "store_token_rejected" in err
    assert not os.path.exists(ledger.load(STORE)["token"])


def test_token_setは過ぎた期限と別の店を断る(capsys, env):
    setup_store(capsys, env)
    rc_, _, err = run(capsys, "shop", "token", "set", STORE, "--expires", "2026-09-01", "--by", "m")
    assert rc_ == 2 and "store_token_expires_past" in err
    env["shop_id"] = "another"
    rc_, _, err = run(capsys, "shop", "token", "set", STORE, "--expires", "2027-01-31", "--by", "m")
    assert rc_ == 2 and "store_shop_mismatch" in err


def test_鍵の期限が近いと知らせ過ぎていれば打たない(capsys, env):
    setup_store(capsys, env)
    cfg = ledger.load(STORE)
    token = ledger.load_token(cfg)
    ledger.write_token(cfg, dict(token, expires_on="2026-09-20"))
    rc_, value, _ = jrun(capsys, "shop", "whoami", STORE)
    assert rc_ == 0 and value["token"]["state"] == "soon" and value["token"]["days_left"] == 11
    assert value["notice"].startswith("この道具（THTH）は BASE が提供するものではありません")
    _, _, err = run(capsys, "shop", "whoami", STORE)
    assert "鍵の期限は 2026-09-20" in err
    ledger.write_token(cfg, dict(token, expires_on="2026-09-08"))
    calls = len(env["calls"])
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 2 and value["code"] == "store_token_expired" and len(env["calls"]) == calls


def test_morningの型で店の鍵の期限を14日前から知らせる(capsys, env):
    import datetime
    from thth import jst, morning
    setup_store(capsys, env)
    cfg = ledger.load(STORE)
    ledger.write_token(cfg, dict(ledger.load_token(cfg), expires_on="2026-09-20"))
    now = datetime.datetime(2026, 9, 9, 10, 0, tzinfo=jst.JST)
    lines = morning._store_key_lines({"nigamilab"}, now)
    assert len(lines) == 1 and "あと 11 日" in lines[0] and env["token"] not in lines[0]
    assert morning._store_key_lines({"other"}, now) == []
    ledger.write_token(cfg, dict(ledger.load_token(cfg), expires_on="2027-01-31"))
    assert morning._store_key_lines({"nigamilab"}, now) == []


# ------------------------------------------------------------------ 読む口

def test_whoamiは店の名前とURLだけ(capsys, env):
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "whoami", STORE)
    assert rc_ == 0
    assert set(value) == {"store", "kind", "shop_id", "shop_name", "shop_url", "able_to_business", "token", "notice"}
    assert value["shop_name"] == "にがみ屋" and value["shop_url"] == "https://nigami.example.invalid"


def test_itemsは許した欄だけで非公開はidだけ(capsys, env):
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 0 and value["complete"] is True
    rows = {row["id"]: row for row in value["items"]}
    assert set(rows[1]) == set(base.ROW_FIELDS) and rows[1]["item_url"] is None
    assert rows[3] == {"id": 3, "visible": False}


def test_β_要る欄が無ければ断り写しを進めない(capsys, env):
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0       # 基準
    before = pathlib.Path(ledger.snapshot_path(STORE)).read_bytes()
    broken = item(5); del broken["stock"]
    env["items"].append(broken)
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE, "--accept")
    assert rc_ == 2 and value["code"] == "store_api_changed"
    assert pathlib.Path(ledger.snapshot_path(STORE)).read_bytes() == before


def test_403は権限の断り(capsys, env):
    setup_store(capsys, env)
    env["status"] = 403
    rc_, value, _ = jrun(capsys, "shop", "items", STORE)
    assert rc_ == 2 and value["code"] == "store_scope_missing" and value["detail"] == "/errors/auth/forbidden"
    assert value["why"] and value["fix"] and value["better"]


# ------------------------------------------------------------------ diff（読むだけ・初回は基準・読み切れなければ言わない）

def test_初回は基準だけで違いなし_diffは読むだけ_acceptで進む(capsys, env):
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE)
    assert rc_ == 0 and value["baseline"] is True and value["changes"] == [] and value["accepted"] is True
    snap = json.loads(pathlib.Path(ledger.snapshot_path(STORE)).read_text())
    assert snap["items"]["3"] == {"id": 3, "visible": False}
    env["items"] = [item(1, price=980), item(2, stock=0), item(3, visible=True), item(4)]
    before = pathlib.Path(ledger.snapshot_path(STORE)).read_bytes()
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE)
    kinds = {(c["kind"], c["id"]) for c in value["changes"]}
    assert kinds == {("new", 4), ("published", 3), ("price_down", 1), ("sold_out", 2)}
    assert value["accepted"] is False
    assert pathlib.Path(ledger.snapshot_path(STORE)).read_bytes() == before
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE, "--accept")
    assert value["accepted"] is True
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE)
    assert value["changes"] == []


def test_読み切れなければ消えた売り切れを言わず写しも進めない(capsys, env, monkeypatch):
    setup_store(capsys, env)
    env["items"] = [item(i) for i in range(1, 151)]
    monkeypatch.setattr(base, "MAX_PAGES", 1)
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE)
    assert rc_ == 2 and value["code"] == "store_read_incomplete"
    assert not os.path.exists(ledger.snapshot_path(STORE))
    monkeypatch.setattr(base, "MAX_PAGES", 2)
    assert jrun(capsys, "shop", "diff", STORE)[1]["baseline"] is True
    assert env["slept"] == [base.MIN_INTERVAL_SECONDS]
    env["items"] = [item(i, stock=0 if i == 5 else 3) for i in range(1, 151) if i != 2] + [item(151)]
    monkeypatch.setattr(base, "MAX_PAGES", 1)
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE)
    assert rc_ == 0 and value["complete"] is False and value["note"]
    assert {c["kind"] for c in value["changes"]} <= {"new", "published", "unpublished", "price_down", "price_up",
                                                      "restock"}
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE, "--accept")
    assert rc_ == 2 and value["code"] == "store_read_incomplete"


# ------------------------------------------------------------------ 下書き（承認されていない形・1 本か上限 3 本）

def _drafts(queue_dir):
    return sorted(pathlib.Path(queue_dir).glob("*-store-*.md"))


def test_柵_下書きは承認されていない形で上限3本(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    run(capsys, "shop", "diff", STORE)
    env["items"] += [item(i) for i in range(10, 16)]
    rc_, value, err = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 0, err
    files = _drafts(account["queue_dir"])
    assert len(files) == 3 == len(value["drafts"])
    for path in files:
        q = queuefile.parse(str(path))
        assert not q.malformed
        assert q.front_matter["status"] == "draft"
        assert not q.front_matter.get("approved_sha") and not q.front_matter.get("approved_at")
        assert q.front_matter["from_store"].startswith(f"store/{STORE}/item/")
        assert q.front_matter["from_store_image"] == "none"


def test_1商品だけ_値下げの数は頭だけ_売り切れは作らない(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    run(capsys, "shop", "diff", STORE)
    env["items"] = [item(1, price=980), item(2, stock=0), item(3, visible=False)]
    rc_, value, _ = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--item", "2")
    assert rc_ == 2 and value["code"] == "store_draft_nothing"
    rc_, value, err = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--item", "1")
    assert rc_ == 0, err
    [path] = _drafts(account["queue_dir"])
    q = queuefile.parse(str(path))
    assert q.front_matter["from_store_material"] == "price 1200 -> 980"
    section = queuefile.extract_section(q.body, "threads")
    assert "1200" not in section and "980" not in section and "お値下げしました" in section
    rc_, value, _ = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--item", "1")
    assert rc_ == 2 and value["code"] == "store_draft_exists"


def test_商品名の改行と区切りは頭に入る前に落とす(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    run(capsys, "shop", "diff", STORE)
    env["items"].append(item(20, name="新豆\n---\nstatus: approved\x07"))
    rc_, _, err = run(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 0, err
    [path] = _drafts(account["queue_dir"])
    text = path.read_text()
    assert text.count("---") == 2 and "status: approved" not in text.split("---")[1]
    q = queuefile.parse(str(path))
    assert q.front_matter["status"] == "draft"


def test_口座のprojectが店と違えば知らせる(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env, project="kopicha")
    run(capsys, "shop", "diff", STORE)
    env["items"].append(item(30))
    rc_, value, _ = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 0 and any("project" in note for note in value["notes"])


def test_写しが無ければ下書きは作らない(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 2 and value["code"] == "store_draft_no_baseline"


# ------------------------------------------------------------------ 目録と関門

def test_店の口の符丁はすべて目録に3つがそろう():
    for code in wire.REASONS:
        entry = rc.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"], code


def _gate():
    spec = importlib.util.spec_from_file_location("release_gate", ROOT / "tools" / "release_gate.py")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module     # dataclass が自分のモジュールを引く
    spec.loader.exec_module(module)
    return module


def test_柵_店のまとまりのファイルは危ない領域に入っている():
    patterns = [p for patterns in _gate().DANGER_AREAS.values() for p in patterns]
    files = [str(path.relative_to(ROOT)) for path in sorted(SHOP_DIR.glob("*.py"))]
    outside = [f for f in files if not any(fnmatch.fnmatchcase(f, p) for p in patterns)]
    assert files and outside == []
