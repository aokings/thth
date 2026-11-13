"""店を読む口の監査 5.15.0 の直し（`docs/監査_5.15.0_shop_2026-11-12.md` の直したほうがよい 1〜6・8 と気づき）。

柵:
  - 1: `thth shop remove` は二段確認で、鍵 → 写し → 台帳を消す。既定の置き場でない鍵は消さない。
  - 2: 店の下書きの承認（確定の段）で商品を 1 回読み直し、値段・公開・在庫切れが変わっていれば断る。
  - 3: 写しを進めていない間は、日をまたいでも同じ違いから 2 本目の下書きを作らない。
  - 4: 別の店の鍵に替えたら写しを捨てて基準を取り直す（全部が new・removed にならない）。
  - 5: 網に出るのは `wire.py` の 1 か所（ほかのファイルは網の部品を import しない・`Request` に data を渡さない）。
  - 6: 公開で買える商品だけ下書き。店が公開前なら作らない。
  - 8: add・token set・remove・draft を `shopread_…` の名前で記録に残す（--by・鍵は入れない）。
本物の BASE は呼ばない（`tests/test_store_base.py` の偽の transport）。
"""
from __future__ import annotations

import ast
import datetime
import json
import os
import pathlib

import pytest

from tests.conftest import commit_and_push_path
from tests.test_store_base import STORE, SHOP_DIR, _drafts, env, item, jrun, run, setup_store  # noqa: F401
from thth import admin_log, cli, jst, queuefile
from thth import refusal_catalog as rc
from thth.shop import ACTION_PREFIX, base, ledger, wire
from thth.shop import cli as store_cli
from thth.shop import draft as draft_mod


def _events(prefix=ACTION_PREFIX):
    rows, broken = admin_log.read()
    assert broken == 0
    return [row for row in rows if row["event"].startswith(prefix)]


# ------------------------------------------------------------------ 1: 店を外す

def test_removeは二段確認で鍵と写しと台帳を消す(capsys, env):
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    token = pathlib.Path(ledger.load(STORE)["token"])
    state = pathlib.Path(ledger.state_dir_for(STORE))
    path = pathlib.Path(ledger.path_for(STORE))
    assert token.exists() and state.exists() and path.exists()
    rc_, value, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m")
    assert rc_ == 1 and value["stage"] == 1
    assert [t["category"] for t in value["targets"]] == ["token", "state", "ledger"]
    assert token.exists() and state.exists() and path.exists()       # 一段目は何も消さない
    rc_, wrong, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m", "--confirm", "000000000000")
    assert rc_ == 2 and wrong["code"] == "store_remove_unconfirmed" and token.exists()
    rc_, done, err = jrun(capsys, "shop", "remove", STORE, "--by", "m", "--confirm", value["digest"])
    assert rc_ == 0, err
    assert done["deleted"] == ["token", "state", "ledger"]
    assert not token.exists() and not state.exists() and not path.exists()
    assert env["token"] not in json.dumps(done)
    # もう何も無い店は「無い」と断る（何度打っても同じ結果）。
    rc_, again, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m")
    assert rc_ == 2 and again["code"] == "store_missing"


def test_removeは既定の置き場でない鍵を消さずに残す(capsys, env):
    setup_store(capsys, env)
    path = pathlib.Path(ledger.path_for(STORE))
    data = json.loads(path.read_text())
    other = env["tmp"] / "elsewhere.token"
    other.write_text("{}")
    data["token"] = str(other)
    path.write_text(json.dumps(data))
    rc_, value, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m")
    assert rc_ == 1 and [k["category"] for k in value["kept"]] == ["token"]
    rc_, done, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m", "--confirm", value["digest"])
    assert rc_ == 0 and other.exists() and not path.exists()


def test_removeはsymlinkの写しを消さずに断る(capsys, env):
    setup_store(capsys, env)
    state = pathlib.Path(ledger.state_dir_for(STORE))
    elsewhere = env["tmp"] / "precious"
    elsewhere.mkdir()
    (elsewhere / "keep").write_text("keep")
    state.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(elsewhere, state)
    rc_, value, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m")
    assert rc_ == 2 and value["code"] == "store_remove_unsafe"
    assert (elsewhere / "keep").read_text() == "keep" and pathlib.Path(ledger.path_for(STORE)).exists()


# ------------------------------------------------------------------ 2: 承認のときの読み直し

def _store_draft(capsys, env, account):
    setup_store(capsys, env)
    run(capsys, "shop", "diff", STORE)
    env["items"].append(item(40, price=1500))
    rc_, value, err = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--item", "40")
    assert rc_ == 0, err
    [path] = _drafts(account["queue_dir"])
    assert queuefile.parse(str(path)).front_matter["from_store_price"] in ("1500", 1500)
    commit_and_push_path(str(path), message="test: store draft")
    return str(path)


def _approve(capsys, path):
    """承認の 1 段目（見せるだけ）。digest を返す。"""
    first = run(capsys, "approve", path)
    digests = [line.split(": ", 1)[1].strip() for line in first[1].splitlines() if line.startswith("digest: ")]
    assert first[0] == 1 and digests, first
    return digests[0]


@pytest.mark.parametrize("changed, detail", [
    ({"stock": 0}, "not_buyable"),
    ({"visible": False}, "unpublished"),
    ({"price": 1800}, "price 1500 -> 1800"),
    ({"sales_status": "item_end_of_sale"}, "not_buyable"),
    (None, "item_gone"),
])
def test_柵_店の下書きは確定の段で読み直し変わっていれば承認しない(capsys, env, isolated_account_factory, changed, detail):
    account = isolated_account_factory()
    path = _store_draft(capsys, env, account)
    digest = _approve(capsys, path)
    reads_before = len(env["calls"])
    if changed is None:
        env["items"] = [row for row in env["items"] if row["id"] != 40]
    else:
        env["items"] = [item(40, **{"price": 1500, **changed}) if row["id"] == 40 else row for row in env["items"]]
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 1 and "store_item_changed" in err and detail in err, err
    assert "承認しました" not in out
    assert len(env["calls"]) > reads_before                      # 確定の段で読み直した
    assert all(c["method"] == "GET" for c in env["calls"])


def test_店の下書きは変わっていなければ承認でき_1段目は網に出ない(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    path = _store_draft(capsys, env, account)
    reads = len(env["calls"])
    digest = _approve(capsys, path)
    assert len(env["calls"]) == reads                           # 見せる段は読まない
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 0, err
    assert "承認しました" in out
    assert len(env["calls"]) > reads


def test_店の台帳が無い機械では読み直さずに知らせて通す(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    path = _store_draft(capsys, env, account)
    os.unlink(ledger.path_for(STORE))
    digest = _approve(capsys, path)
    reads = len(env["calls"])
    rc_, out, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 0, err
    assert "読み直していません" in err and len(env["calls"]) == reads


def test_店の下書きでない原稿は網に出ない(capsys, env, isolated_account_factory, monkeypatch):
    from tests.conftest import write_queue_file
    account = isolated_account_factory()
    path = write_queue_file(account["queue_dir"], "plain.md", fm_overrides={"status": "draft", "approved_sha": None})
    setup_store(capsys, env)
    # 控えは見る（頭に from_store が無くても店の下書きか知るため・監査 5.19.0 の 1）が、店の口は開かない。
    from thth.shop import kinds
    monkeypatch.setattr(kinds, "client", lambda *a, **k: pytest.fail("店の下書きでないのに読み直した"))
    reads = len(env["calls"])
    digest = _approve(capsys, path)
    rc_, _, err = run(capsys, "approve", path, "--confirm", digest, "--by", "m")
    assert rc_ == 0, err
    assert len(env["calls"]) == reads


# ------------------------------------------------------------------ 3: 日をまたいでも同じ違いから 2 本目を作らない

def test_柵_写しを進めない間は日をまたいでも同じ違いの下書きは1本だけ(capsys, env, isolated_account_factory, monkeypatch):
    account = isolated_account_factory()
    setup_store(capsys, env)
    run(capsys, "shop", "diff", STORE)
    env["items"].append(item(50))
    day1 = datetime.datetime(2026, 11, 13, 9, 0, tzinfo=jst.JST)
    monkeypatch.setattr(jst, "now_jst", lambda: day1)
    assert jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")[0] == 0
    monkeypatch.setattr(jst, "now_jst", lambda: day1 + datetime.timedelta(days=1))
    rc_, value, _ = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 2 and value["code"] == "store_draft_exists" and value["detail"] == "same_change"
    assert len(_drafts(account["queue_dir"])) == 1
    # 写しを進めたあとの、同じ商品の別の違い（値下げ）は作れる。
    assert run(capsys, "shop", "diff", STORE, "--accept")[0] == 0
    env["items"] = [item(50, price=900) if row["id"] == 50 else row for row in env["items"]]
    rc_, value, err = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--item", "50")
    assert rc_ == 0, err
    assert len(_drafts(account["queue_dir"])) == 2


# ------------------------------------------------------------------ 4: 別の店の鍵に替えたら基準を取り直す

def test_柵_別の店の鍵に替えたら写しを捨てて基準を取り直す(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    assert run(capsys, "shop", "diff", STORE)[0] == 0
    env["shop_id"] = "another-shop"
    env["items"] = [item(i) for i in range(70, 75)]
    rc_, out, err = run(capsys, "shop", "token", "set", STORE, "--expires", "2027-01-31", "--by", "m", "--force")
    assert rc_ == 0, err
    assert "写しを捨てました" in out
    rc_, value, _ = jrun(capsys, "shop", "diff", STORE)
    assert rc_ == 0 and value["baseline"] is True and value["changes"] == []
    snapshot = ledger.read_snapshot(STORE)
    assert snapshot["shop_id"] == "another-shop"


def test_写しの店が鍵の店と違えばdraftは作らずdiffは取り直す(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    run(capsys, "shop", "diff", STORE)
    path = pathlib.Path(ledger.snapshot_path(STORE))
    snap = json.loads(path.read_text())
    snap["shop_id"] = "someone-else"
    path.write_text(json.dumps(snap))
    env["items"].append(item(80))
    rc_, value, _ = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 2 and value["code"] == "store_draft_no_baseline"
    assert _drafts(account["queue_dir"]) == []


# ------------------------------------------------------------------ 5: 網に出るのは wire.py の 1 か所

NET_MODULES = ("urllib.request", "urllib.error", "http", "http.client", "socket", "ssl", "requests", "httpx",
               "aiohttp", "urllib3", "thth.httpsafe", "httpsafe", "subprocess")
NET_NAMES = ("urlopen", "Request", "HTTPConnection", "HTTPSConnection", "create_connection", "build_opener",
             "OpenerDirector")


def _net_uses(path: pathlib.Path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name in NET_MODULES or a.name.split(".")[0] in
                      ("socket", "ssl", "requests", "httpx", "aiohttp", "urllib3", "http", "subprocess")
                      or a.name in ("urllib.request", "urllib.error")]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [a.name for a in node.names]
            if module in NET_MODULES or module.split(".")[0] in ("socket", "ssl", "requests", "httpx", "http"):
                found.append(module)
            if module in ("", "..", "thth") or node.level:
                found += [n for n in names if n == "httpsafe"]
            if module == "urllib":
                found += [n for n in names if n in ("request", "error")]
        elif isinstance(node, ast.Attribute) and node.attr in NET_NAMES:
            found.append(node.attr)
        elif isinstance(node, ast.Name) and node.id in NET_NAMES:
            found.append(node.id)
    return found


def test_柵_網の部品を使うのはwire_pyだけ():
    others = {path.name: _net_uses(path) for path in sorted(SHOP_DIR.glob("*.py")) if path.name != "wire.py"}
    assert others and {name: uses for name, uses in others.items() if uses} == {}
    # wire.py の中は使っている（柵の検査そのものが空回りしていない）。
    assert "urlopen" in _net_uses(SHOP_DIR / "wire.py")


def test_柵_wire_pyのRequestはGETとPOSTの2か所でurlopenは1か所():
    """5.17.0 で「Request は GET だけ」から書き替えた（設計 Shopify と STORES §5 の柵 2・書き替えるのはこの 1 本だけ）。

    Request は 2 か所: 1 つは method="GET" の定数で data なし、もう 1 つは method="POST" の定数（本文は打つ直前に
    `check_request` が表と照らす）。urlopen に当たる呼び出しは 1 か所で、渡すのは組んだ Request と timeout と
    リダイレクトを追わない opener だけ。
    """
    tree = ast.parse((SHOP_DIR / "wire.py").read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and (getattr(node.func, "attr", None) or getattr(node.func, "id", None)) in ("Request", "urlopen")]
    requests_ = [c for c in calls if (getattr(c.func, "attr", None) or getattr(c.func, "id", None)) == "Request"]
    opens = [c for c in calls if (getattr(c.func, "attr", None) or getattr(c.func, "id", None)) == "urlopen"]
    assert len(requests_) == 2 and len(opens) == 1
    methods = {}
    for request in requests_:
        keywords = {k.arg: k.value for k in request.keywords}
        assert len(request.args) == 1
        assert isinstance(keywords.get("method"), ast.Constant), "method は定数で書く"
        methods[keywords["method"].value] = keywords
    assert set(methods) == {"GET", "POST"}
    assert "data" not in methods["GET"] and "data" in methods["POST"]
    [opened] = opens
    assert len(opened.args) == 1 and {k.arg for k in opened.keywords} <= {"timeout", "via"}


def test_網の途中で切れても前向きな断り(monkeypatch):
    import http.client
    from thth import httpsafe

    class Broken:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        status = 200

        def read(self, size=None):
            raise http.client.IncompleteRead(b"[", 10)

    monkeypatch.setattr(httpsafe, "urlopen", lambda *a, **k: Broken())
    with pytest.raises(wire.StoreRefused) as caught:
        wire.default_transport("GET", "https://apiv2.thebase.com/api/items?limit=1&offset=0", {})
    assert caught.value.code == "store_unavailable"


# ------------------------------------------------------------------ 6: 公開で買える商品だけ

@pytest.mark.parametrize("kind, before, after", [
    ("new", None, {"stock": 0}),
    ("new", None, {"sales_status": "item_waiting_for_sale"}),
    ("new", None, {"sales_status": "item_end_of_sale"}),
    ("price_down", {}, {"price": 900, "stock": 0}),
    ("price_down", {}, {"price": 900, "sales_status": "item_end_of_sale"}),
    ("restock", {"stock": 0}, {"stock": 2, "sales_status": "item_waiting_for_sale"}),
])
def test_柵_買えない商品からは下書きを作らない(capsys, env, isolated_account_factory, kind, before, after):
    account = isolated_account_factory()
    if before is not None:
        env["items"].append(item(60, **before))
    setup_store(capsys, env)
    run(capsys, "shop", "diff", STORE)
    env["items"] = [row for row in env["items"] if row["id"] != 60] + [item(60, **after)]
    rc_, value, _ = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", kind)
    assert rc_ == 2 and value["code"] == "store_draft_nothing", value
    assert _drafts(account["queue_dir"]) == []
    # 同じ形で買える商品なら作る（試験が空回りしていない）。
    env["items"] = [row for row in env["items"] if row["id"] != 60] + [
        item(60, **{**after, "stock": 3, "sales_status": "item_now_on_sale"})]
    rc_, value, err = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", kind)
    assert rc_ == 0, err


def test_店が公開前なら下書きを作らない(capsys, env, isolated_account_factory, monkeypatch):
    account = isolated_account_factory()
    setup_store(capsys, env)
    run(capsys, "shop", "diff", STORE)
    env["items"].append(item(61))
    real = store_cli.TRANSPORT

    def closed(method, url, headers):
        status, body = real(method, url, headers)
        if url.endswith("/api/user"):
            value = json.loads(body)
            value["able_to_business"] = False
            body = json.dumps(value).encode()
        return status, body

    monkeypatch.setattr(store_cli, "TRANSPORT", closed)
    rc_, value, _ = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 2 and value["code"] == "store_not_open"
    assert _drafts(account["queue_dir"]) == []


# ------------------------------------------------------------------ 商品名（気づき 4）

def test_商品名の改行は本文の1行にも残らない(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    run(capsys, "shop", "diff", STORE)
    env["items"].append(item(20, name="新豆\nstatus: approved\r\n## bluesky"))
    rc_, _, err = run(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 0, err
    [path] = _drafts(account["queue_dir"])
    section = queuefile.extract_section(queuefile.parse(str(path)).body, "threads").strip().split("\n")
    assert section[0] == "新豆 status: approved ## bluesky 新しく入りました"


def test_頭が井桁の商品名は落として下書きにし空白だけの名前は作らない(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    run(capsys, "shop", "diff", STORE)
    env["items"] += [item(21, name="## 新豆"), item(22, name="   ")]
    rc_, value, err = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new")
    assert rc_ == 0, err
    assert [row["id"] for row in value["drafts"]] == [21]
    [path] = _drafts(account["queue_dir"])
    section = queuefile.extract_section(queuefile.parse(str(path)).body, "threads")
    assert section.strip().startswith("新豆 新しく入りました")
    rc_, value, _ = jrun(capsys, "shop", "draft", STORE, "--to", account["name"], "--item", "22")
    assert rc_ == 2 and value["code"] == "store_draft_nothing"


# ------------------------------------------------------------------ 8: 記録

def test_柵_足し_鍵_下書き_外すは名前つきで記録に残り鍵は入らない(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    setup_store(capsys, env)
    run(capsys, "shop", "diff", STORE)
    env["items"].append(item(90))
    assert run(capsys, "shop", "draft", STORE, "--to", account["name"], "--kind", "new", "--by", "下書きの人")[0] == 0
    rc_, value, _ = jrun(capsys, "shop", "remove", STORE, "--by", "外す人")
    assert jrun(capsys, "shop", "remove", STORE, "--by", "外す人", "--confirm", value["digest"])[0] == 0
    rows = _events()
    assert [row["event"] for row in rows] == [ACTION_PREFIX + e for e in ("added", "token_set", "drafted", "removed")]
    assert [row["by"] for row in rows] == ["m", "m", "下書きの人", "外す人"]
    assert all(row["account"] == STORE for row in rows)
    assert rows[2]["diff"]["items"] == [[], [90]]
    raw = (pathlib.Path(os.environ["THTH_ROOT"]) / "state" / "_admin" / "accounts.ndjson").read_text()
    assert env["token"] not in raw


def test_記録の動きの名前はshopread_で始まり変更ログが受ける():
    names = [ACTION_PREFIX + e for e in ("added", "token_set", "removed", "drafted")]
    assert ACTION_PREFIX == "shopread_" and set(names) <= admin_log.EVENTS
    assert not [e for e in admin_log.EVENTS if e.startswith("shop_")]   # Meta の見張りの名前と混ぜない


def test_記録の置き場に書けなければ台帳を書かない(capsys, env, monkeypatch):
    def broken(*a, **k):
        raise admin_log.AdminLogError("admin_log_write_failed")
    monkeypatch.setattr(admin_log, "transaction", broken)
    rc_, _, err = run(capsys, "shop", "add", STORE, "--kind", "base", "--project", "nigamilab", "--by", "m")
    assert rc_ == 2 and "store_log_unavailable" in err
    assert not pathlib.Path(ledger.path_for(STORE)).exists()


def test_新しい符丁も目録に3つがそろう():
    for code in ("store_token_expires_past", "store_draft_no_baseline", "store_not_open", "store_item_changed",
                 "store_remove_unconfirmed", "store_remove_unsafe", "store_log_unavailable"):
        assert code in wire.REASONS
        entry = rc.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"], code


def test_confirm_fileの確定でも店の下書きを読み直して断る(capsys, env, isolated_account_factory):
    account = isolated_account_factory()
    path = _store_draft(capsys, env, account)
    digest = _approve(capsys, path)
    env["items"] = [item(40, price=1500, stock=0) if row["id"] == 40 else row for row in env["items"]]
    listing = env["tmp"] / "confirm.txt"
    listing.write_text(f"{path} {digest}\n")
    rc_, out, err = run(capsys, "approve", "--confirm-file", str(listing), "--by", "m")
    assert rc_ != 0 and "store_item_changed" in out + err, (out, err)
    assert "not_buyable" in out + err


def test_removeの文字の表示は一段目のあと鎖の中で消した物も言う(capsys, env, monkeypatch):
    """監査 5.17.0 残っていること 1 の直し（c8e7cf8）で、鎖の中で一覧に無い短い鍵も消すようになった。
    JSON の deleted には入るが、文字の表示が一段目の一覧の物だけを言うと、消した物を黙る。"""
    from thth.shop import remove as remove_mod
    setup_store(capsys, env)
    rc_, value, _ = jrun(capsys, "shop", "remove", STORE, "--by", "m")
    real = remove_mod.execute
    monkeypatch.setattr(remove_mod, "execute", lambda found: real(found) + ["access"])
    rc_, out, err = run(capsys, "shop", "remove", STORE, "--by", "m", "--confirm", value["digest"])
    assert rc_ == 0, err
    assert "消しました: 短い鍵（一段目のあと、取り直しの途中に書かれた物）" in out
    assert out.count("取り直しの途中に書かれた物") == 1
