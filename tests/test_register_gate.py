"""台帳の器 段 7 の後半（指示 `docs/指示_台帳の器_段6と段7_書く口と出口の残り_2026-11-14.md` §7.5・7.6・7.7 と「済み」の線の 1）。

- §7.5 `used`: 使った先（2 つの出し口・2 つの世代・留保・差し込み・来歴の控え・材料が古い）。
- §7.6 `published`: 出した物の控えと関門。重い穴の定め「出した URL が消えたのに関門が緑」を、消え方ごとに試す
  （転送も例外も無い消え・転送先が一覧に無い・許していない番号の転送・二重・控えの行を消して戻す）。
- §7.7 読者に見える欄の点検（`reader_check.check`）: 内部の語・載らない種類の番号・宣言の攻め。
- どの命令も、`--record` を付けない限り台帳に書かない（前後で台帳のフォルダの全ファイルの sha256 が同じ）。

見本の台帳は試験ごとに一時の場所へ組む。種類は店の表（CSV）・覚えの枚（YAML）・品の枚（JSON）・札（内側の単位）と、
読者の出し口に載らない仮説の表（CSV。「から作った」で店を指す）。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil

import pytest

from thth import cli
from thth.register import published as P
from thth.register import reader_check as RC
from thth.register import route as R
from thth.register import seal as S
from thth.register import vessel as V
from thth.register.errors import RegisterRefused

AT = "2027-03-20T10:00:00+09:00"
AT2 = "2027-03-25T10:00:00+09:00"
NOW = "2027-03-26T09:00:00+09:00"

SHOPS = (
    "id,name,price,secret,body,status\n"
    "s1,Alpha,100,SECRET-A,Alpha sells {{note:n1.title}},open\n"
    's2,"Beta, Ltd",200,SECRET-B,plain body,open\n'
    "s3,Gamma,300,SECRET-C,uses {{shop:s2.name}} here,open\n"
)
NOTE = "title: Note One\nsecret: SECRET-N\nbody: see {{shop:s2.name}}\n"
ITEM = '{\n  "name": "Widget",\n  "secret": "SECRET-I",\n  "price": 5\n}\n'
CARD = {"rows": [{"seed": "a", "role": "mix", "memo": "m1", "status": "ok"},
                 {"seed": "b", "role": "master", "memo": "m2", "status": "ok"}]}
HYPS = "id,text,from\nhyp-0001,maybe cheap,s2\nhyp-0002,maybe old,s1\nh9,short one,s3\n"

KINDS = {
    "shop": {"schema": "thth.kind/1", "kind": "shop", "place": "data/shops.csv", "format": "csv",
             "id": {"field": "id"},
             "questions": [{"name": "fresh", "depends": ["name", "price", "secret", "body"]}]},
    "note": {"schema": "thth.kind/1", "kind": "note", "place": "notes/*.yaml", "format": "yaml", "records": "file",
             "id": {"name": True}},
    "item": {"schema": "thth.kind/1", "kind": "item", "place": "items/*.json", "format": "json", "records": "file",
             "id": {"name": True}},
    "card": {"schema": "thth.kind/1", "kind": "card", "place": "cards/*.json", "format": "json", "records": "file",
             "id": {"name": True}, "questions": [{"name": "card_ok", "level": "unit", "state": "status"}],
             "units": {"groups": [], "items": ["rows", "*"], "key": ["seed"]}},
    "hyp": {"schema": "thth.kind/1", "kind": "hyp", "place": "hyps/hyps.csv", "format": "csv", "id": {"field": "id"},
            "pointers": [{"field": "from", "to": "shop", "meaning": "derived_from"}]},
}

WORDS = ["社内メモ", ".*", "(?i)secret", "TODO"]


def outlets(**over) -> dict:
    site = {"audience": "reader", "dest": "site", "fact": [{"question": "may_publish@site"}],
            "kinds": {"shop": {"private_fields": ["secret"], "inserts": ["body"], "reader_fields": ["name", "body"]},
                      "note": {"private_fields": ["secret"], "inserts": ["body"], "reader_fields": ["title", "body"]},
                      "item": {"private_fields": ["secret"], "reader_fields": ["name"]}},
            "published": {"redirects": [{"from": "/old/", "to": "/new/", "status": 301}],
                          "gone": [{"url": "/bye/", "reason_kind": "retired"}]}}
    site2 = {"audience": "reader", "dest": "site2", "kinds": ["shop"], "fact": [{"question": "fresh"}],
             "reservation": {"question": "fresh", "conclusion": "pending", "reason_kinds": ["thin"]}}
    data = {"site": site, "site2": site2,
            "tools": {"audience": "internal", "kinds": ["shop", "note", "item", "hyp"], "dest": "tools"},
            "cards": {"audience": "reader", "level": "unit", "dest": "cards",
                      "kinds": {"card": {"reader_fields": ["role"]}},
                      "fact": [{"question": "card_ok", "conclusion": "good"}]}}
    for name, value in over.items():
        if value is None:
            data.pop(name, None)
        else:
            data[name] = value
    return {"schema": "thth.outlets/1", "internal_words": list(WORDS), "outlets": data}


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key, sub in (("THTH_ROOT", "root"), ("HOME", "home"), ("XDG_CONFIG_HOME", "config")):
        (tmp_path / sub).mkdir()
        monkeypatch.setenv(key, str(tmp_path / sub))
    monkeypatch.delenv(S.ENV_PUBLIC, raising=False)
    return tmp_path


@pytest.fixture
def keyed(env):
    try:
        S.openssl()
    except RegisterRefused:
        pytest.skip("OpenSSL 3 か 4 が決まった場所に無い")
    S.init_key()
    return env


def write_outlets(led, decl) -> None:
    (led / ".thth/outlets.json").write_text(json.dumps(decl, ensure_ascii=False, indent=1))


def _ledger(env, decl=None, *, shops=SHOPS, note=NOTE, card=CARD, hyps=HYPS) -> pathlib.Path:
    led = env / "led"
    (led / ".thth/kinds").mkdir(parents=True)
    for name, body in KINDS.items():
        (led / f".thth/kinds/{name}.json").write_text(json.dumps(body, ensure_ascii=False))
    write_outlets(led, decl or outlets())
    for rel, body in (("data/shops.csv", shops), ("notes/n1.yaml", note), ("items/i1.json", ITEM),
                      ("cards/c1.json", json.dumps(card, ensure_ascii=False, indent=1) + "\n"),
                      ("hyps/hyps.csv", hyps)):
        (led / rel).parent.mkdir(parents=True, exist_ok=True)
        (led / rel).write_text(body, encoding="utf-8", newline="")
    return led


def tree_sha(led) -> dict:
    """台帳のフォルダの全部のファイル（`.` で始まる物も）の sha256。"""
    out = {}
    for directory, _dirs, files in os.walk(led):
        for name in files:
            p = pathlib.Path(directory, name)
            out[str(p.relative_to(led))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def judge(led, ref, question, conclusion="good", **kw):
    R.judge(str(led), ref, question, by="masaru", at="2027-03-01T00:00:00+09:00", conclusion=conclusion, **kw)


def accept(led, outlet, to, as_of=AT):
    plan = R.accept_plan(str(led), outlet, str(to), as_of)
    return R.accept(str(led), outlet, str(to), as_of, confirm=plan["digest"], at=as_of)


def run(capsys, *argv):
    rc = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return rc, out, err


def jrun(capsys, *argv):
    rc, out, err = run(capsys, *argv, "--json")
    return rc, (json.loads(out) if out.strip() else None), err


# ------------------------------------------------------------------ §7.5 使った先

def _sealed_two(keyed):
    """site（差し込みあり）と site2（留保あり）と cards（内側の単位）に封をした台帳。"""
    led = _ledger(keyed)
    for ref in ("shop:s1", "shop:s2", "shop:s3", "note:n1", "item:i1"):
        judge(led, ref, "may_publish@site")
    judge(led, "shop:s1", "fresh", "pending", reason_kind="thin")
    for ref in ("shop:s2", "shop:s3"):
        judge(led, ref, "fresh")
    vessel = V.open_vessel(str(led))
    for unit in vessel.get("card:c1").units:
        R.judge(str(led), "card:c1", "card_ok", unit=unit.pointer, by="m", at=AT, conclusion="good")
    assert accept(led, "site", keyed / "site")["generation"] == 1
    assert accept(led, "site2", keyed / "site2")["generation"] == 1
    assert accept(led, "cards", keyed / "cards")["generation"] == 1
    return led


def _row(value, outlet):
    got = [r for r in value["outlets"] if r["outlet"] == outlet]
    assert len(got) == 1, value
    return got[0]


def test_7_5_二つの出し口と差し込みと来歴の控えを引ける(keyed):
    led = _sealed_two(keyed)
    before = tree_sha(led)
    v = R.used(str(led), "shop:s2")
    site, site2 = _row(v, "site"), _row(v, "site2")
    assert site["generation"] == 1 and site["loaded"] == [{"as": "fact"}]
    assert site["inserted_into"] == [{"record": "note:n1", "fields": ["body"]},
                                     {"record": "shop:s3", "fields": ["body"]}]
    assert site2["loaded"] == [{"as": "fact"}] and site2["inserted_into"] == []   # site2 は差し込みを宣言していない
    assert not site["stale"] and not site2["stale"] and v["stale"] == []
    assert site["seal"] == "verified" and site["version"] == v["version"]
    assert v["derived_by"] == [{"record": "hyp:hyp-0001", "field": "from"}]
    assert all(r["outlet"] != "tools" for r in v["outlets"])     # 内の道具の出し口は封をしていない
    # 留保として載った記録・差し込みで使われた記録（種類が出し口に載らない物）
    s1 = R.used(str(led), "shop:s1")
    assert _row(s1, "site2")["loaded"] == [{"as": "reservation"}] and _row(s1, "site")["loaded"] == [{"as": "fact"}]
    n1 = R.used(str(led), "note:n1")
    assert _row(n1, "site")["inserted_into"] == [{"record": "shop:s1", "fields": ["body"]}]
    assert all(r["outlet"] != "site2" for r in n1["outlets"])
    # 内側の単位として載った記録
    c1 = _row(R.used(str(led), "card:c1"), "cards")
    assert sorted(x["as"] for x in c1["loaded"]) == ["fact", "fact"] and all(x["unit"] for x in c1["loaded"])
    assert tree_sha(led) == before


def test_7_5_二つめの世代と材料が古い(keyed):
    led = _sealed_two(keyed)
    shops = led / "data/shops.csv"
    shops.write_bytes(shops.read_bytes().replace(b'"Beta, Ltd",200', b'"Beta, Ltd",250'))
    judge(led, "shop:s2", "may_publish@site")
    judge(led, "shop:s2", "fresh")
    v = R.used(str(led), "shop:s2")
    assert set(v["stale"]) == {"site", "site2"}               # 直したが、まだどちらにも封をし直していない
    assert accept(led, "site", keyed / "site", AT2)["generation"] == 2
    v = R.used(str(led), "shop:s2")
    site, site2 = _row(v, "site"), _row(v, "site2")
    assert site["generation"] == 2 and site["version"] == v["version"] and not site["stale"]
    assert site2["generation"] == 1 and site2["version"] != v["version"] and site2["stale"]
    assert v["stale"] == ["site2"]


def test_7_5_差し込んだ側が封のあとに変わると差し込みは確かめられないと出す(keyed):
    led = _sealed_two(keyed)
    shops = led / "data/shops.csv"
    shops.write_bytes(shops.read_bytes().replace(b"uses {{shop:s2.name}} here", b"uses {{shop:s2.name}} now"))
    site = _row(R.used(str(led), "shop:s2"), "site")
    assert site["inserted_into"] == [{"record": "note:n1", "fields": ["body"]}]
    assert site["unconfirmed"] is True


def test_7_5_封の無い記録と無い記録(env, capsys):
    led = _ledger(env)
    before = tree_sha(led)
    v = R.used(str(led), "shop:s2")
    assert v["outlets"] == [] and v["derived_by"] == [{"record": "hyp:hyp-0001", "field": "from"}]
    with pytest.raises(RegisterRefused) as exc:
        R.used(str(led), "shop:s99")
    assert exc.value.code == "register_record_missing"
    rc, out, _ = run(capsys, "register", "used", led, "shop:s2")
    assert rc == 0 and "どの出し口の封にも載っていません" in out and "hyp:hyp-0001" in out
    rc, v, _ = jrun(capsys, "register", "used", led, "shop:s2")
    assert rc == 0 and v["record"] == "shop:s2"
    assert tree_sha(led) == before


# ------------------------------------------------------------------ §7.6 出した物の控えと関門

def lst(env, *urls, name="list.txt") -> pathlib.Path:
    path = env / name
    path.write_text("".join(u + "\n" for u in urls), encoding="utf-8")
    return path


FIRST = ("/", "/a/", "/old/", "/bye/", "/日本/")


def seeded(env, decl=None):
    """初めの配信（/old/ と /bye/ がまだ頁だった時）を控えに積み、そのあとで転送と例外を宣言する。"""
    decl = decl or outlets()
    first = json.loads(json.dumps(decl))
    first["outlets"]["site"]["published"] = {}
    led = _ledger(env, first)
    v = P.published(str(led), "site", str(lst(env, *FIRST, name="first.txt")), AT, record=True)
    assert v["ok"] and v["recorded"] == len(FIRST), v
    write_outlets(led, decl)
    return led


def words(v) -> set:
    return {p["word"] for p in v["problems"]}


def test_7_6_転送と例外を宣言どおりに通し新しい物は記録のときだけ足す(env, capsys):
    led = seeded(env)
    log = (led / ".thth/published/site.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["url"] for x in log] == sorted(FIRST)
    assert all(json.loads(x)["state"] == "live" and json.loads(x)["first_at"] == AT for x in log)
    # 消えた /old/ は宣言した転送（301・先が一覧に在る）・/bye/ は宣言した例外。日本語はパーセントの字でも同じ
    second = lst(env, "https://example.com/", "/a", "/new/", "/%E6%97%A5%E6%9C%AC/", "/c/?q=1")
    before = tree_sha(led)
    v = P.published(str(led), "site", str(second), NOW)
    assert v["ok"], v
    assert v["counts"]["redirected"] == 1 and v["counts"]["gone"] == 1 and v["new"] == ["/c/", "/new/"]
    assert tree_sha(led) == before                                  # --record なしは書かない
    rc, out, _ = run(capsys, "register", "published", led, "--outlet", "site", "--list", second, "--now", NOW)
    assert rc == 0 and "--record で足す" in out and tree_sha(led) == before
    rc, v, _ = jrun(capsys, "register", "published", led, "--outlet", "site", "--list", second, "--now", NOW,
                    "--record")
    assert rc == 0 and v["recorded"] == 4                          # 新しい 2・扱いの変わった 2（転送・消した）
    rows = [json.loads(x) for x in (led / ".thth/published/site.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[:len(FIRST)] == [json.loads(x) for x in log]        # 前の行はそのまま（追記だけ）
    last = {r["url"]: r for r in rows}
    assert last["/old/"]["state"] == "redirect" and last["/old/"]["to"] == "/new/"
    assert last["/old/"]["first_at"] == AT and last["/bye/"]["state"] == "gone"
    assert last["/c/"]["state"] == "live" and last["/c/"]["first_at"] == NOW
    # 同じ一覧でもう一度回しても、足す物は無い
    v = P.published(str(led), "site", str(second), NOW, record=True)
    assert v["ok"] and v["recorded"] == 0


def test_重い穴_転送も例外も無い消えは赤(env):
    led = seeded(env)
    before = tree_sha(led)
    v = P.published(str(led), "site", str(lst(env, "/", "/new/", "/日本/")), NOW, record=True)
    assert not v["ok"] and words(v) == {"missing"} and [p["url"] for p in v["problems"]] == ["/a/"]
    assert v["problems"][0]["first_at"] == AT
    assert tree_sha(led) == before                                  # 赤なら --record でも書かない


def test_重い穴_転送先が一覧に無いと赤_鎖も赤(env):
    led = seeded(env)
    v = P.published(str(led), "site", str(lst(env, "/", "/a/", "/日本/")), NOW)
    assert words(v) == {"redirect_target_missing"}
    decl = outlets()
    decl["outlets"]["site"]["published"]["redirects"].append({"from": "/new/", "to": "/newer/", "status": 301})
    write_outlets(led, decl)
    v = P.published(str(led), "site", str(lst(env, "/", "/a/", "/newer/", "/日本/")), NOW)
    assert words(v) == {"redirect_chain"}


def test_重い穴_許していない番号の転送は赤_例外の一時の転送だけ宣言で通す(env):
    decl = outlets()
    decl["outlets"]["site"]["published"]["redirects"] = [{"from": "/old/", "to": "/new/", "status": 302}]
    led = seeded(env, decl)
    listed = lst(env, "/", "/a/", "/new/", "/日本/")
    v = P.published(str(led), "site", str(listed), NOW)
    assert words(v) == {"redirect_status"} and v["problems"][0]["status"] == 302
    decl["outlets"]["site"]["published"]["gone_redirect_status"] = [302]
    write_outlets(led, decl)
    assert words(P.published(str(led), "site", str(listed), NOW)) == {"redirect_status"}   # 例外に無い URL は通さない
    decl["outlets"]["site"]["published"]["gone"].append({"url": "/old/", "reason_kind": "paused"})
    write_outlets(led, decl)
    assert P.published(str(led), "site", str(listed), NOW)["ok"]
    # 許す番号を宣言で足せば 307 も通る（厳しさは出し口の宣言）
    decl = outlets()
    decl["outlets"]["site"]["published"]["redirects"][0]["status"] = 307
    decl["outlets"]["site"]["published"]["redirect_status"] = [301, 307, 308]
    write_outlets(led, decl)
    assert P.published(str(led), "site", str(listed), NOW)["ok"]


def test_重い穴_二重は赤_転送元に頁があるのも既定で赤(env):
    led = seeded(env)
    v = P.published(str(led), "site", str(lst(env, "/", "/a/", "/a", "/new/", "/日本/")), NOW)
    assert words(v) == {"duplicate"}
    v = P.published(str(led), "site", str(lst(env, "/", "/a/", "/old/", "/new/", "/日本/")), NOW)
    assert words(v) == {"redirect_with_page"}
    decl = outlets()
    decl["outlets"]["site"]["published"].update(duplicate="warn", redirect_with_page="warn")
    write_outlets(led, decl)
    v = P.published(str(led), "site", str(lst(env, "/", "/a/", "/a", "/old/", "/new/", "/日本/")), NOW)
    assert v["ok"] and {w["word"] for w in v["warnings"]} == {"duplicate", "redirect_with_page"}


def test_重い穴_控えの行を消して戻すと赤(env):
    led = seeded(env)
    log = led / ".thth/published/site.jsonl"
    head = led / ".thth/published/site.head.json"
    without_a = lst(env, "/", "/new/", "/日本/")
    lines = log.read_text(encoding="utf-8").splitlines(keepends=True)
    # 行を消す（/a/ を控えから外せば、消えた /a/ が赤にならない）
    log.write_text("".join(x for x in lines if '"/a/"' not in x), encoding="utf-8")
    v = P.published(str(led), "site", str(without_a), NOW)
    assert not v["ok"] and "log_rewritten" in words(v) and "missing" not in words(v)
    # 行を書き換える
    log.write_text("".join(x.replace('"/a/"', '"/z/"') for x in lines), encoding="utf-8")
    assert "log_rewritten" in words(P.published(str(led), "site", str(without_a), NOW))
    # 最後の行を消す（頭より短い）
    log.write_text("".join(lines[:-1]), encoding="utf-8")
    assert "log_rewritten" in words(P.published(str(led), "site", str(without_a), NOW))
    # 頭ごと消す
    log.write_text("".join(lines), encoding="utf-8")
    saved = head.read_bytes()
    head.rename(env / "head.moved")
    assert "head_missing" in words(P.published(str(led), "site", str(without_a), NOW))
    (env / "head.moved").rename(head)
    assert head.read_bytes() == saved
    # 前に配った控えの頭（--publish が緑のときに書く物）: 行を消して頭も書き直しても、前に配った頭の続きでなければ赤
    published_head = env / "pub/site.head.json"
    full = lst(env, "/", "/a/", "/new/", "/日本/", name="full.txt")
    assert P.published(str(led), "site", str(full), NOW, publish=str(published_head))["ok"]
    assert json.loads(published_head.read_text())["bytes"] == len("".join(lines).encode())
    log.write_text("".join(x for x in lines if '"/a/"' not in x), encoding="utf-8")
    P._write_head(str(head), "site", log.read_bytes())          # 頭も一緒に書き直す
    assert P.published(str(led), "site", str(without_a), NOW)["ok"]   # 台帳の中だけでは見抜けない
    v = P.published(str(led), "site", str(without_a), NOW, previous=str(published_head))
    assert not v["ok"] and "previous_not_continued" in words(v)
    # 前に配った頭が無いのに --previous を言えば赤（黙って通さない）
    v = P.published(str(led), "site", str(without_a), NOW, previous=str(env / "nothing.json"))
    assert "previous_not_continued" in words(v)


def test_7_6_読めない控えの行は赤で例外を漏らさない(env):
    led = seeded(env)
    log = led / ".thth/published/site.jsonl"
    for junk in (b"{not json}\n", b'{"url": 5}\n', b"\xff\xfe\n", b"[]\n", b"null\n"):
        led2 = env / f"copy{len(junk)}{junk[:2].hex()}"
        shutil.copytree(led, led2)
        (led2 / ".thth/published/site.jsonl").write_bytes(log.read_bytes() + junk)
        P._write_head(str(led2 / ".thth/published/site.head.json"), "site",
                      (led2 / ".thth/published/site.jsonl").read_bytes())
        v = P.published(str(led2), "site", str(lst(env, *FIRST)), NOW)
        assert not v["ok"] and "log_invalid" in words(v), junk


@pytest.mark.parametrize("bad", [
    None, [], "x", {"redirects": None}, {"redirects": [{"from": "/a/", "to": "/b/", "status": "301"}]},
    {"redirects": [{"from": "/a/", "to": "/b/", "status": True}]}, {"redirects": [{"from": None, "to": "/b/"}]},
    {"redirects": [{"from": "/a/"}]}, {"gone": [{"url": "/a/"}]}, {"gone": [{"url": "/a/", "reason_kind": ""}]},
    {"gone": [{"url": "/a/", "reason_kind": None}]}, {"redirect_status": ["301"]}, {"redirect_status": None},
    {"duplicate": "ignore"}, {"normalize": "regex"}, {"redirects": [{"from": "/a/\n", "to": "/b/"}]},
    {"redirects": [{"from": "/" + "a" * 3000, "to": "/b/"}]}, {"extra": 1},
    {"redirects": [{"from": "/a/", "to": "/b/"}] * (P.REDIRECTS_MAX + 1)},
])
def test_7_6_宣言の攻めは宣言の崩れとして断る(env, bad):
    decl = outlets()
    decl["outlets"]["site"]["published"] = bad
    led = _ledger(env, decl)
    with pytest.raises(RegisterRefused) as exc:
        R.load_outlets(str(led), V.open_vessel(str(led)))
    assert exc.value.code == "register_outlet_unknown"


def test_7_6_一覧の形(env):
    led = seeded(env)
    with pytest.raises(RegisterRefused) as exc:
        P.published(str(led), "site", str(env / "nothing.txt"), NOW)
    assert exc.value.code == "register_args_invalid"
    bad = env / "bad.txt"
    bad.write_bytes(b"/\xff/\n")
    with pytest.raises(RegisterRefused):
        P.published(str(led), "site", str(bad), NOW)
    # 頭の注と空の行は読まない・タブのあとは読まない（今の控えの形）
    ok = env / "ok.txt"
    ok.write_text("# 注\n\n/\t2026-09-24\n/a/\n/new/\n/日本/\n", encoding="utf-8")
    assert P.published(str(led), "site", str(ok), NOW)["ok"]


def test_7_6_宣言の無い出し口は既定の厳しさで消えを赤にする(env):
    led = _ledger(env)
    first = lst(env, "/", "/x/", name="f.txt")
    assert P.published(str(led), "site2", str(first), AT, record=True)["recorded"] == 2
    v = P.published(str(led), "site2", str(lst(env, "/")), NOW)
    assert words(v) == {"missing"} and v["declared"] is False


def test_7_6_CLIは赤でrc1と直し方(env, capsys):
    led = seeded(env)
    before = tree_sha(led)
    rc, out, _ = run(capsys, "register", "published", led, "--outlet", "site", "--list", lst(env, "/"), "--now", NOW,
                     "--record")
    assert rc == 1 and "赤 missing: /a/" in out and "published.redirects" in out
    rc, v, _ = jrun(capsys, "register", "published", led, "--outlet", "site", "--list", lst(env, "/"), "--now", NOW)
    assert rc == 1 and not v["ok"]
    assert tree_sha(led) == before


# ------------------------------------------------------------------ §7.7 読者に見える欄の点検

def red_of(led) -> dict:
    return {(c["outlet"], c["kind"], c["id"]): c for c in RC.red(RC.check(str(led)))}


def test_7_7_今の見本は違反0で検査の部品と同じ形(env):
    led = _ledger(env)
    before = tree_sha(led)
    got = RC.check(str(led))
    assert RC.red(got) == []
    assert {(c["outlet"], c["kind"]) for c in got} == {("site", "shop"), ("site", "note"), ("site", "item"),
                                                       ("cards", "card")}
    keys = {"kind", "id", "check", "level", "note", "checked", "not_when", "not_inspected", "unverifiable",
            "unresolved", "violations", "records", "more"}
    assert all(keys <= set(c) for c in got)
    ids = {c["id"]: c for c in got if c["kind"] == "shop"}
    assert ids["reader_hidden_ids"]["short_ids_skipped"] == 1        # h9 は短いので拾わない（数だけ出す）
    assert ids["reader_internal_words"]["checked"] == 3
    assert tree_sha(led) == before


def test_7_7_内部の語を1つ足すと赤_読者に見えない欄なら赤にならない(env):
    led = _ledger(env, shops=SHOPS.replace("plain body", "plain 社内メモ body"))
    got = red_of(led)
    c = got[("site", "shop", "reader_internal_words")]
    assert c["violations"] == 1 and c["records"][0] == {"kind": "shop", "record": "shop:s2", "check":
                                                        "reader_internal_words", "reason": "internal_word",
                                                        "field": "body", "word": "社内メモ"}
    led2 = env / "two"
    led2.mkdir()
    led2 = _ledger(led2, shops=SHOPS.replace("SECRET-B", "社内メモ TODO"))   # 出さない欄（読者に見えない欄）
    assert red_of(led2) == {}
    # 内側の単位の出し口
    card = {"rows": [{"seed": "a", "role": "TODO mix", "memo": "TODO", "status": "ok"}]}
    led3 = env / "three"
    led3.mkdir()
    c = red_of(_ledger(led3, card=card))[("cards", "card", "reader_internal_words")]
    assert c["violations"] == 1 and c["records"][0]["unit"] and c["records"][0]["field"] == "role"


def test_7_7_載らない種類の番号を1つ足すと赤_境目と短い番号(env):
    led = _ledger(env, note=NOTE.replace("Note One", "Note (hyp-0001)"))
    c = red_of(led)[("site", "note", "reader_hidden_ids")]
    assert c["violations"] == 1 and c["records"][0]["found"] == "hyp:hyp-0001" and c["records"][0]["field"] == "title"
    for text, hit in (("see hyp-00012", False), ("x_hyp-0001", False), ("hyp-0001.", True), ("a-hyp-0001-b", True),
                      ("h9 only", False), ("(hyp-0002)", True)):
        place = env / f"t{abs(hash(text))}"
        place.mkdir()
        got = red_of(_ledger(place, note=NOTE.replace("Note One", text)))
        assert (("site", "note", "reader_hidden_ids") in got) is hit, text
    # 読者に見えない欄（出さない欄）にあっても赤にならない
    place = env / "secret"
    place.mkdir()
    assert red_of(_ledger(place, note=NOTE.replace("SECRET-N", "hyp-0001"))) == {}
    # 読者の出し口に載る種類の番号（店の s1 など）は拾わない
    place = env / "shopid"
    place.mkdir()
    assert red_of(_ledger(place, note=NOTE.replace("Note One", "about shop s1 and item i1"))) == {}


def test_7_7_語は字のまま比べる_正規表現らしい字も字として(env):
    led = _ledger(env, shops=SHOPS.replace("plain body", "secret plain"))     # (?i)secret は正規表現として読まない
    assert red_of(led) == {}
    place = env / "lit"
    place.mkdir()
    got = red_of(_ledger(place, shops=SHOPS.replace("plain body", "literal .* here")))
    assert got[("site", "shop", "reader_internal_words")]["records"][0]["word"] == ".*"


@pytest.mark.parametrize("bad", [
    ["x"] * (R.INTERNAL_WORDS_MAX + 1), ["a" * (R.INTERNAL_WORD_LEN + 1)], [None], [""], ["  "], ["a\nb"], "TODO",
    {"w": 1}, [["nested"]], [1],
])
def test_7_7_内部の語の宣言の攻めは断る(env, bad):
    decl = outlets()
    decl["internal_words"] = bad
    led = _ledger(env, decl)
    for go in (lambda: RC.check(str(led)), lambda: R.load_outlets(str(led), V.open_vessel(str(led)))):
        with pytest.raises(RegisterRefused) as exc:
            go()
        assert exc.value.code == "register_outlet_unknown"


def test_7_7_上限ちょうどの語と正規表現の字は受ける_nullは無いのと同じ(env):
    decl = outlets()
    decl["internal_words"] = [f"w{i}" for i in range(R.INTERNAL_WORDS_MAX - 3)] + ["[", "(", "a" * 64]
    led = _ledger(env, decl)
    assert RC.red(RC.check(str(led))) == []
    decl["internal_words"] = None
    write_outlets(led, decl)
    assert RC.red(RC.check(str(led))) == []


@pytest.mark.parametrize("bad", [None, "name", [None], ["bad-name"], [1], {"a": 1}, ["name", "name"]])
def test_7_7_読者に見える欄の宣言の攻めは断る(env, bad):
    decl = outlets()
    decl["outlets"]["site"]["kinds"]["shop"]["reader_fields"] = bad
    led = _ledger(env, decl)
    with pytest.raises(RegisterRefused) as exc:
        RC.check(str(led))
    assert exc.value.code == "register_outlet_unknown"


def test_7_7_内の道具の出し口は読者に見える欄を持てない_巨大な並びも例外を漏らさない(env):
    decl = outlets()
    decl["outlets"]["tools"]["kinds"] = {"shop": {"reader_fields": ["name"]}}
    led = _ledger(env, decl)
    with pytest.raises(RegisterRefused):
        RC.check(str(led))
    decl = outlets()
    decl["outlets"]["site"]["kinds"]["shop"]["reader_fields"] = [f"f{i}" for i in range(5000)]
    write_outlets(led, decl)
    got = RC.check(str(led))
    assert RC.red(got) == []


def test_7_7_載る記録は渡した集まりか最後の封の項目_封が無ければ全部(keyed):
    led = _ledger(keyed, shops=SHOPS.replace("Gamma", "Gamma 社内メモ"))
    assert red_of(led)[("site", "shop", "reader_internal_words")]["scope"] == "all"     # 封が無い: 全部を見る
    got = RC.check(str(led), loaded={"site": {"shop:s1", "shop:s2"}})
    assert RC.red(got) == [] and {c["scope"] for c in got if c["outlet"] == "site"} == {"given"}
    assert next(c for c in got if c["outlet"] == "site" and c["kind"] == "shop")["not_when"] == 1
    for ref in ("shop:s1", "shop:s2", "note:n1", "item:i1"):        # s3 は判断が無く、載らない
        judge(led, ref, "may_publish@site")
    accept(led, "site", keyed / "site")
    got = RC.check(str(led))
    assert RC.red(got) == [] and {c["scope"] for c in got if c["outlet"] == "site"} == {"seal"}
    judge(led, "shop:s3", "may_publish@site")
    accept(led, "site", keyed / "site", AT2)                       # 載ると赤
    c = red_of(led)[("site", "shop", "reader_internal_words")]
    assert c["scope"] == "seal" and c["records"][0]["record"] == "shop:s3"
    # 内側の単位: 封に載った単位だけを見る
    card = {"rows": [{"seed": "a", "role": "TODO", "memo": "x", "status": "ok"},
                     {"seed": "b", "role": "fine", "memo": "y", "status": "ok"}]}
    (led / "cards/c1.json").write_text(json.dumps(card, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    units = {u.key["seed"]: u.pointer for u in V.open_vessel(str(led)).get("card:c1").units}
    R.judge(str(led), "card:c1", "card_ok", unit=units["b"], by="m", at=AT, conclusion="good")
    accept(led, "cards", keyed / "cards")
    assert ("cards", "card", "reader_internal_words") not in red_of(led)
    R.judge(str(led), "card:c1", "card_ok", unit=units["a"], by="m", at=AT, conclusion="good")
    accept(led, "cards", keyed / "cards", AT2)
    assert red_of(led)[("cards", "card", "reader_internal_words")]["records"][0]["unit"] == units["a"]


def test_どの命令も記録を付けない限り台帳に書かない(keyed, capsys):
    led = _sealed_two(keyed)
    now_list = ("/", "/a/", "/new/", "/日本/")
    assert P.published(str(led), "site", str(lst(keyed, *now_list, name="n.txt")), AT, record=True)["recorded"] == 4
    before = tree_sha(led)
    assert R.used(str(led), "shop:s2")["outlets"]
    RC.check(str(led))
    assert not P.published(str(led), "site", str(lst(keyed, "/")), NOW)["ok"]                      # 赤
    v = P.published(str(led), "site", str(lst(keyed, *now_list, "/more/", name="m.txt")), NOW)    # 緑・新しい物あり
    assert v["ok"] and v["new"] == ["/more/"]
    for argv in (("used", led, "shop:s2"),
                 ("published", led, "--outlet", "site", "--list", keyed / "m.txt", "--now", NOW)):
        rc, _out, _err = run(capsys, "register", *argv)
        assert rc == 0
    assert tree_sha(led) == before
    # 控えの頭の写しを配る（--publish）のも、台帳の外にだけ書く
    assert P.published(str(led), "site", str(keyed / "m.txt"), NOW, publish=str(keyed / "pub/h.json"))["ok"]
    assert tree_sha(led) == before
