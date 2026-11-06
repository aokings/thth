"""Threads を広告の出し先に（masaru 11/06「スレッズ広告 api 対応して欲しい」・11/07）。偽の Meta だけ。

公式（調べ_Threads広告のAPI_2026-11-06）で決まっていること:

- Threads は単独で出せない。`publisher_platforms` に instagram と threads、`instagram_positions` に stream、
  `threads_positions` に threads_stream。instagram 無しの threads は**要求を出す前に**前向きに断る。
- FB の投稿を広告にするときは creative に `object_story_id`＋`instagram_user_id`＋`threads_user_id`。
- `threads_user_id` は台帳の欄が先。無ければ `GET /<IG>/connected_threads_user`（答えは data の並び）。
  ads の鍵の SCOPES に threads_business_basic を足した（masaru 11/07）。一覧が分かる鍵に乗っていなければ
  要求の前に `ads_threads_scope_missing`（再認可を案内）。一覧が分からない鍵は通し、Meta の権限の断りを
  `ads_threads_not_granted` で言い換える。
- Threads の投稿そのものは広告にできない（`ads_threads_post_unsupported`・道は作らない）。
"""
from __future__ import annotations

import json

import pytest

from thth import refusal_catalog, server_ads
from thth.ads import digest, graphread, graphwrite, ledger, ops
from tests.test_meta_stage5 import ACT, NAME, STORY, V, end_in, env, posts, setup_ledger  # noqa: F401

IG = "17841400000000001"
THREADS = "31415926535"
ROUTE = ("GET", V + f"/{IG}/connected_threads_user")


@pytest.fixture
def ig(monkeypatch):
    """台帳に結んだ IG の口座（`_instagram_user` は口座の台帳の handle を読む・ここでは値だけ差し替える）。"""
    monkeypatch.setattr(ops, "_instagram_user", lambda cfg: IG)
    return IG


def setup(env, *, scopes=("ads_read", "ads_management", "business_management", "threads_business_basic"),
          scopes_source="response"):
    """`setup_ledger` の鍵に、再認可したあとの権限の一覧を乗せる（`scopes=None` は一覧の無い古い鍵）。"""
    cfg = setup_ledger(env)
    token = ledger.load_token(cfg)
    if scopes is None:
        token.pop("scopes", None)
    else:
        token["scopes"] = list(scopes)
    if scopes_source is None:
        token.pop("scopes_source", None)
    else:
        token["scopes_source"] = scopes_source
    ledger.write_token(cfg, token)
    return cfg


def set_threads_user(value):
    path = ledger.path_for(NAME)
    raw = json.loads(open(path, encoding="utf-8").read())
    raw["threads_user_id"] = value
    open(path, "w", encoding="utf-8").write(json.dumps(raw))


def boost(**kw):
    args = dict(by="masaru", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1),
                platforms=("facebook", "instagram", "threads"))
    args.update(kw)
    return ops.boost(NAME, **args)


def gets(env, part):
    return [c for c in env["calls"] if c["method"] == "GET" and part in c["path"]]


def test_threadsはinstagramのstreamと組で出し_creativeに3つの口座を入れる(env, ig):
    setup(env)
    set_threads_user(THREADS)
    out = boost()
    targeting = json.loads(posts(env, "/adsets")[0]["form"]["targeting"][0])
    assert targeting["publisher_platforms"] == ["facebook", "instagram", "threads"]
    assert targeting["instagram_positions"] == ["stream"] and targeting["threads_positions"] == ["threads_stream"]
    assert "facebook_positions" not in targeting, "facebook の配置は今と同じく書かない"
    creative = posts(env, "/adcreatives")[0]["form"]
    assert creative["object_story_id"] == [STORY] and creative["instagram_user_id"] == [IG]
    assert creative["threads_user_id"] == [THREADS]
    assert not gets(env, "connected_threads_user"), "台帳に値があれば読みに行かない"
    plan = out["plan"]
    assert plan["threads_user_id"] == THREADS and plan["threads_user_source"] == "ledger"
    assert plan["threads_verified"] is False
    assert [s["kind"] for s in out["next_steps"]][:2] == ["skill", "threads_unverified"]
    rows = [r for r in ops.read_ops(NAME) if r["op"] == "boost"]
    assert rows[-1]["result"] == "ok" and rows[-1]["plan"]["threads_user_id"] == THREADS


def test_threadsを含まない広告は今と同じ形のまま(env, ig):
    setup(env)
    set_threads_user(THREADS)
    out = boost(platforms=("facebook", "instagram"))
    targeting = json.loads(posts(env, "/adsets")[0]["form"]["targeting"][0])
    assert "threads_positions" not in targeting and "instagram_positions" not in targeting
    assert "threads_user_id" not in posts(env, "/adcreatives")[0]["form"]
    assert out["plan"]["threads_user_id"] is None and out["plan"]["threads_verified"] is None
    assert [s["kind"] for s in out["next_steps"]] == ["skill"]


@pytest.mark.parametrize("platforms", [("threads",), ("facebook", "threads")])
def test_instagram無しのthreadsは網に出る前に前向きに断る(env, platforms):
    setup(env)
    with pytest.raises(ops.OpsRefused) as exc:
        boost(platforms=platforms)
    assert exc.value.code == "ads_threads_needs_instagram"
    assert "instagram,threads" in exc.value.message
    assert env["calls"] == [], "要求を 1 本も出さない"
    entry = refusal_catalog.CATALOG["ads_threads_needs_instagram"]
    assert entry["why"] and entry["fix"]["text"] and entry["better"] and "--platforms instagram,threads" in entry["fix"]["command"]


def test_台帳に無ければconnected_threads_userで引いてcreativeに入れる(env, ig):
    setup(env)
    env["meta"].fail[ROUTE] = (200, {"data": [{"threads_user_id": THREADS, "id": THREADS}]})
    out = boost(platforms=("instagram", "threads"))
    call = gets(env, "connected_threads_user")[0]
    assert call["query"]["fields"] == ["threads_user_id"]
    assert posts(env, "/adcreatives")[0]["form"]["threads_user_id"] == [THREADS]
    assert out["plan"]["threads_user_source"] == "connected_threads_user"


def test_結びついたThreadsの口座が無ければ何も作らずads_threads_unlinked(env, ig):
    setup(env)
    env["meta"].fail[ROUTE] = (200, {"data": []})
    with pytest.raises(ops.OpsRefused) as exc:
        boost()
    assert exc.value.code == "ads_threads_unlinked" and IG in exc.value.message
    assert not posts(env) and not ops.read_ops(NAME)


def test_鍵の一覧にthreads_business_basicが無ければ要求の前に再認可を案内する(env, ig):
    """11/07 に SCOPES に足す前に作った鍵（一覧が分かる）。台帳に threads_user_id があっても同じ。"""
    setup(env, scopes=("ads_read", "ads_management", "business_management"))
    set_threads_user(THREADS)
    with pytest.raises(ops.OpsRefused) as exc:
        boost()
    assert exc.value.code == "ads_threads_scope_missing"
    assert f"thth ads auth {NAME} --by" in exc.value.message
    assert env["calls"] == [], "要求を 1 本も出さない"
    entry = refusal_catalog.CATALOG["ads_threads_scope_missing"]
    assert entry["fix"]["command"].startswith("thth ads auth") and entry["why"] and entry["better"]
    # threads を含まなければ今までどおり作れる
    assert boost(platforms=("facebook",))["status"] == "PAUSED"


@pytest.mark.parametrize("scopes,source", [(None, None), (("ads_read",), "unknown")])
def test_一覧の分からない鍵は通し_Metaの権限の断りを再認可の案内に言い換える(env, ig, scopes, source):
    setup(env, scopes=scopes, scopes_source=source)
    env["meta"].fail[ROUTE] = (403, {"error": {"code": 10, "message": "Application does not have permission"}})
    with pytest.raises(ops.OpsRefused) as exc:
        boost()
    assert exc.value.code == "ads_threads_not_granted"
    assert gets(env, "connected_threads_user"), "断らずに読みに行った"
    assert f"thth ads auth {NAME} --by" in exc.value.hint and "threads_user_id" in exc.value.hint
    assert not posts(env)


def test_読めないだけならads_threads_unreadableで何も作らない(env, ig):
    setup(env)
    env["meta"].fail[ROUTE] = (500, {"error": {"code": 2, "message": "temporary"}})
    with pytest.raises(ops.OpsRefused) as exc:
        boost()
    assert exc.value.code == "ads_threads_unreadable" and not posts(env)


def test_threadsのcreativeが権限で断られたら鍵の権限の見込みを先に案内する(env, ig, capsys):
    setup(env)
    set_threads_user(THREADS)
    env["meta"].fail[("POST", V + f"/act_{ACT}/adcreatives")] = (
        403, {"error": {"code": 200, "message": "Permissions error"}})
    with pytest.raises(ops.OpsRefused) as exc:
        boost()
    assert exc.value.code == "ads_boost_failed"
    assert exc.value.hint.startswith("Threads を含むクリエイティブが権限で断られました")
    assert "threads_business_basic" in exc.value.hint and f"thth ads auth {NAME} --by" in exc.value.hint


def test_Threadsの投稿そのものはboostの道を作らず_代わりを言って断る(env, monkeypatch):
    setup(env)
    monkeypatch.setattr(ops, "_threads_sent_account", lambda cfg, post: "kopicha-threads")
    with pytest.raises(ops.OpsRefused) as exc:
        boost(object_story_id=None, post="18000000000000001")
    assert exc.value.code == "ads_threads_post_unsupported"
    assert "kopicha-threads" in exc.value.message and "facebook,instagram,threads" in exc.value.message
    assert env["calls"] == []
    entry = refusal_catalog.CATALOG["ads_threads_post_unsupported"]
    assert entry["why"] and entry["fix"]["text"] and entry["better"]


def test_utm_sourceは出し先の1つ目のまま_threadsを先に書けばthreads(env, ig):
    setup(env)
    set_threads_user(THREADS)
    first = boost(platforms=("instagram", "threads"), utm_campaign="2026-11-07-fb-sakura")
    assert first["plan"]["url_tags"].startswith("utm_source=instagram&utm_medium=paid")
    second = boost(platforms=("threads", "instagram"), utm_campaign="2026-11-07-fb-sakura")
    assert second["plan"]["url_tags"] == "utm_source=threads&utm_medium=paid&utm_campaign=2026-11-07-fb-sakura"
    assert posts(env, "/adcreatives")[-1]["form"]["url_tags"] == [second["plan"]["url_tags"]]


def test_creativeはIGの口座無しのthreads_user_idを送らない():
    writer = graphwrite.Writer.__new__(graphwrite.Writer)
    writer.act = "act_" + ACT
    writer._post = lambda *a, **k: pytest.fail("要求を出してはいけない")
    with pytest.raises(graphwrite.AdsWriteError) as exc:
        writer.create_creative(name="x", object_story_id=STORY, threads_user_id=THREADS)
    assert exc.value.code == "ads_threads_needs_instagram"


def test_activateのdigestにthreads_user_idが入り_木の読みでも頼む(env, ig):
    setup(env)
    set_threads_user(THREADS)
    cid = boost()["created"]["campaign_id"]
    assert "threads_user_id" in graphread.AD_TREE_FIELDS and "instagram_user_id" in graphread.AD_TREE_FIELDS
    first = ops.activate(NAME, cid, by="masaru")
    assert first["stage"] == 1
    tree_call = [c for c in gets(env, f"/{cid}/ads")][-1]
    assert "threads_user_id" in tree_call["query"]["fields"][0]
    reader = ops._reader(NAME, ledger.load(NAME), ops._token(ledger.load(NAME)))
    tree = reader.campaign_tree(cid)
    ads = digest.components(op="activate", act_id=ACT, currency="JPY", tree=tree)["ads"]
    assert ads[0]["threads_user_id"] == THREADS and ads[0]["instagram_user_id"] == IG
    assert digest.VERSION == "thth-ads-2"


def test_遠くの道の符丁に新しい断りがそろう():
    for code in ("ads_threads_needs_instagram", "ads_threads_unlinked", "ads_threads_not_granted",
                 "ads_threads_unreadable"):
        assert code in server_ads.REASONS
        assert "remote" in refusal_catalog.CATALOG[code]["doors"]
    # activate を遠くの道に出さない今の決まりは変えない
    assert "ads_activate" not in server_ads.OPERATIONS


def test_台帳のthreads_user_idは数字の文字列だけ(env):
    setup(env)
    set_threads_user("abc")
    with pytest.raises(ledger.AdAccountError) as exc:
        ledger.load(NAME)
    assert exc.value.code == "threads_user_id_invalid"


def test_Threadsの口座の送った記録にある投稿を本物の道で見分ける(env, tmp_path):
    """`_threads_sent_account` を差し替えずに: 同じ project の Threads の口座の sent/ にあれば断る。"""
    import os

    from thth import accounts, sent
    from tests.conftest import make_account_json
    setup(env)
    repo = tmp_path / "repo"
    repo.mkdir()
    make_account_json(os.environ["THTH_ACCOUNTS_DIR"], "kopicha-threads", repo_dir=str(repo), project="kopicha",
                      handle="kopicha")
    post = "18000000000000001"
    path = sent.path_for(accounts.state_dir_for("kopicha-threads"), post)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(json.dumps({"post_id": post}))
    with pytest.raises(ops.OpsRefused) as exc:
        boost(object_story_id=None, post=post)
    assert exc.value.code == "ads_threads_post_unsupported" and "kopicha-threads" in exc.value.message
    # 記録に無い投稿は今までどおり ads_post_not_found
    with pytest.raises(ops.OpsRefused) as exc:
        boost(object_story_id=None, post="18000000000000002")
    assert exc.value.code == "ads_post_not_found"
