"""4.0 S6 §5・§6・§10: 記事の名で反応を束ねる口と見せ方（T-S6-4a〜6a）。

はてなの配信 1 本（`lane-a-hatenablog`）に、同じ project の SNS の告知 2 本（Threads・Bluesky・repo なし）と
自分のブックマーク（`sent/bm:`）を結び、反応の台帳を偽物の公開の読む口（`FakeHatena`）で刻みごとに採る。
**はてなには一切つながない。**
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import secrets
import sys

import pytest

from tests.conftest import make_account_json
from tests.hatena_s4_helpers import record_row
from tests.hatena_s6_helpers import (ACCOUNT, ARTICLE, NOW, SELF, URL1, URL2, bm, call, collect,  # noqa: F401
                                     fake_http, lane, ledger_rows, put_delivery, s6, snapshot, world)
from thth import (accounts, approval, article_reactions, click_attribution, deliveries_cli, events_cli,
                  hatena_reactions, jst, morning, postid, report, sent)
from thth.report_service import ReportServiceError

THREADS = "lane-a-threads"
BSKY = "lane-a-bsky"
T_POST = "17890001"
B_POST = "at://did:plc:abc/app.bsky.feed.post/3kxyz"


def _at(hours_ago):
    return NOW - datetime.timedelta(hours=hours_ago)


def _sns_account(world, name, media, handle):  # noqa: F811
    make_account_json(str(world.accounts_dir), name, repo_dir="$THTH_ROOT/repos/_none", project="lane-a",
                      media=media, handle=handle, production=True)


def _announce(world, name, post_id, delivery, *, hours_ago, metrics_by_mark):  # noqa: F811
    """告知の `sent/` の行と、実測の台帳（刻み → 指標）を置く。"""
    posted = _at(hours_ago)
    sent.write(world.state(name), post_id=post_id, text="記事を書きました", body_hash=approval.compute_body_hash("x"),
               sent_at=jst.iso(posted), announces=delivery, article=ARTICLE, article_url=URL1)
    cfg = accounts.load_account(name)
    base = accounts.data_dirs(cfg, name)["insights_posts"]
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, postid.to_filename(post_id) + ".ndjson"), "w", encoding="utf-8") as f:
        for mark, metrics in metrics_by_mark:
            at = posted + datetime.timedelta(hours=mark, minutes=5)
            f.write(json.dumps({"account": name, "post_id": post_id, "posted_at": jst.iso(posted),
                                "collected_at": jst.iso(at), "age_hours": round(mark + 5 / 60, 2), "marks": [mark],
                                "metrics": metrics}) + "\n")


@pytest.fixture
def article(s6):
    """配信 1 本（80 時間前に公開）・刻み 1・6・24・72 の反応・告知 2 本・自分のブックマーク 1。"""
    world = s6.world
    dl = put_delivery(URL1, hours_ago=80)
    h = s6.hatena
    h.entries[URL1] = [bm(SELF, "運営です")]
    h.stars[URL1] = {"stars": [{"name": SELF}]}
    steps = [(79, 1, []), (74, 2, [bm("alice", "いい記事", ["コーヒー"])]),
             (56, 3, [bm("alice", "いい記事", ["コーヒー"]), bm("bob")]),
             (8, 4, [bm("alice", "いい記事", ["コーヒー"]), bm("bob"), bm("carol", "参考に")])]
    for hours_ago, total, third in steps:
        h.counts[URL1] = total
        h.entries[URL1] = [bm(SELF, "運営です")] + third
        h.stars[URL1] = {"stars": [{"name": SELF}] + [{"name": b["user"]} for b in third]}
        assert collect(_at(hours_ago)) == 0
    h.calls.clear()
    record_row(world, ACCOUNT, URL1, target="own", delivery=dl)
    _sns_account(world, THREADS, "threads", "lane_a")
    _sns_account(world, BSKY, "bluesky", "lane-a.bsky.social")
    _announce(world, THREADS, T_POST, dl, hours_ago=79, metrics_by_mark=[
        (1, {"views": 10, "likes": 1}), (24, {"views": 120, "likes": 3, "replies": 1}),
        (72, {"views": 140, "likes": 4, "replies": 1})])
    _announce(world, BSKY, B_POST, dl, hours_ago=78, metrics_by_mark=[(24, {"likes": 2, "reposts": 1})])
    s6.delivery = dl
    return s6


def _payload(*argv):
    rc, out, err = call(["article", "reactions", ACCOUNT, "--article", ARTICLE, "--json", *argv])
    assert rc == 0, (out, err)
    return json.loads(out)


def _rows(payload, **match):
    return [r for r in payload["reactions"] if all(
        (r["object"].get(k[4:]) if k.startswith("obj_") else (r["window"].get(k[4:]) if k.startswith("win_")
                                                              else r.get(k))) == v for k, v in match.items())]


# ---------------------------------------------------------------- T-S6-4a 束ね

def test_4a_配信と告知の行が出所ごとに並び合計の鍵もsiteの行も無い(article):
    """柵: 和の鍵（`total`）を足すと赤。"""
    p = _payload()
    assert p["schema"] == "thth.article_reactions/1" and p["report_type"] == "article_reactions"
    sources = {(r["source"], r["metric"]) for r in p["reactions"]}
    assert {("hatena_bookmark", m) for m in ("bookmark_total", "bookmark_others", "bookmark_comments_n")} <= sources
    assert ("hatena_star", "star_others") in sources
    assert {r["object"]["account"] for r in _rows(p, source="sns")} == {THREADS, BSKY}
    assert {r["object"]["account"] for r in _rows(p, source="sns_clicks")} == {THREADS}
    assert not [r for r in p["reactions"] if r["source"] == "site"]
    assert "site_not_in_thth" in p["cannot_say"]
    assert "total" not in p and "totals" not in p
    for r in p["reactions"]:
        assert set(r) == {"source", "metric", "value", "object", "attribution", "attribution_basis", "window"}
    (others72,) = _rows(p, metric="bookmark_others", win_mark=72)
    assert (others72["value"], others72["attribution"], others72["attribution_basis"]) == (3, "inferred",
                                                                                         "total_minus_self")
    assert others72["window"]["cached"] is True and others72["window"]["origin_at"] == jst.iso(_at(80))
    (total24,) = _rows(p, metric="bookmark_total", win_mark=24)
    assert (total24["value"], total24["attribution"], total24["window"]["cached"]) == (3, "direct", False)
    (replies,) = _rows(p, metric="replies_received", obj_account=THREADS)
    assert replies["value"] == 0 and replies["object"]["kind"] == "announcement"
    assert p["announcements"][0]["post_id"] in (T_POST, B_POST)
    assert s6_no_calls(article)


def s6_no_calls(lane_):
    return lane_.hatena.calls == []


# ---------------------------------------------------------------- T-S6-4b 24h と最新

def test_4b_刻み1_6_24_72の配信は24と72の2行(article):
    p = _payload()
    marks = sorted({r["window"]["mark"] for r in _rows(p, source="hatena_bookmark", metric="bookmark_total")})
    assert marks == [24, 72]
    (star24,) = _rows(p, metric="star_others", win_mark=24)
    assert star24["value"] == 2 and star24["window"]["missing"] is None


def test_4b_24が無ければ公開から24時間前はnot_yet_過ぎていればnot_fetched(s6):
    """柵: `not_yet` と `not_fetched` を混ぜると赤。"""
    young = put_delivery(URL1, hours_ago=7, article=ARTICLE)
    s6.hatena.counts[URL1] = 1
    assert collect() == 0                      # 刻み 1・6
    old = put_delivery(URL2, hours_ago=30, article=ARTICLE)
    p = _payload()
    (y24,) = _rows(p, metric="bookmark_total", obj_id=young, win_mark=24)
    (o24,) = _rows(p, metric="bookmark_total", obj_id=old, win_mark=24)
    assert (y24["value"], y24["window"]["missing"]) == (None, "not_yet")
    assert (o24["value"], o24["window"]["missing"]) == (None, "not_fetched")
    (y6,) = _rows(p, metric="bookmark_total", obj_id=young, win_mark=6)
    assert y6["value"] == 1 and y6["window"]["missing"] is None


def test_4b_取れなかった刻みは理由の符丁を添える(s6):
    dl = put_delivery(URL1, hours_ago=25, article=ARTICLE)
    s6.hatena.counts[URL1] = 1
    s6.hatena.fail["stars"] = 500
    collect()
    p = _payload()
    (star,) = _rows(p, metric="star_others", obj_id=dl, win_mark=24)
    assert star["window"]["missing"] == "not_fetched:hatena_http_500"


# ---------------------------------------------------------------- T-S6-4c 登録の成立・自分の分

def test_4c_登録の成立はregistrationに出てreactionsに入らずselfのスターも入らない(article):
    """柵: 自分のスターを `star_others` に入れると赤。"""
    p = _payload()
    (entry,) = p["deliveries"]
    assert entry["registration"]["result"] == "confirmed"
    assert not [r for r in p["reactions"] if "registration" in r["metric"] or r["metric"].startswith("bookmark_self")]
    (star72,) = _rows(p, metric="star_others", win_mark=72)
    assert star72["value"] == 3          # alice・bob・carol（自分の 1 つは入れない）
    assert entry["rows"][-1]["star"]["self"] == 1 and entry["rows"][-1]["bookmark"]["self"] == [SELF]


# ---------------------------------------------------------------- T-S6-4d 本文

def test_4d_既定ではコメントの本文とuserが出ず_commentsで出る(article):
    """柵: 既定の --json に `bookmarks[]` を残すと赤。"""
    rc, out, err = call(["article", "reactions", ACCOUNT, "--article", ARTICLE, "--json"])
    rc2, text, err2 = call(["article", "reactions", ACCOUNT, "--article", ARTICLE])
    assert rc == rc2 == 0, (err, err2)
    for body in (out, text):
        for needle in ("いい記事", "参考に", "alice", "carol", "bookmarks\""):
            assert needle not in body, needle
    assert "comments" not in json.loads(out)["deliveries"][0]
    p = _payload("--comments")
    comments = p["deliveries"][0]["comments"]
    assert [c["user"] for c in comments] == ["alice", "bob", "carol"]
    assert comments[0]["comment"] == "いい記事" and comments[0]["tags"] == ["コーヒー"]
    assert SELF not in [c["user"] for c in comments]


def test_4d_1行の表示(article):
    rc, out, err = call(["article", "reactions", ACCOUNT, "--article", ARTICLE])
    assert rc == 0, err
    assert f"配信 {article.delivery}" in out
    assert "24h: ブックマーク: 全体 3・第三者 2（コメント 1）  スター: 2" in out
    assert "最新: ブックマーク: 全体 4・第三者 3（コメント 2）  スター: 3" in out
    assert "登録の成立: 自分の分 confirmed" in out
    assert f"告知 {THREADS}" in out and "24h: views 120・likes 3・replies 1" in out
    assert "クリック: 言えない（no_link）" in out
    assert "サイトの訪問は THTH にありません" in out


def test_4d_articleは必須(s6):
    rc, out, err = call(["article", "reactions", ACCOUNT, "--json"])
    assert rc == 2 and "article_required" in err


# ---------------------------------------------------------------- T-S6-4e --fetch

def test_4e_fetchは今の行を添えて台帳は1バイトも変わらない_SNSは呼ばない(article):
    """柵: `--fetch` の行を台帳に書くと赤。"""
    article.hatena.counts[URL1] = 9
    article.hatena.entries[URL1] = [bm(SELF), bm("dave", "今のコメント")]
    before = snapshot(os.path.join(article.repo, "data"))
    p = _payload("--fetch")
    assert snapshot(os.path.join(article.repo, "data")) == before
    assert sorted(article.hatena.kinds()) == ["counts", "entry", "stars"]
    (now_total,) = _rows(p, metric="bookmark_total", win_kind="now")
    (now_others,) = _rows(p, metric="bookmark_others", win_kind="now")
    assert now_total["value"] == 9 and now_others["value"] == 8
    assert now_total["window"]["at"] == jst.iso(NOW) and now_total["window"]["mark"] is None
    assert "dave" not in json.dumps(p, ensure_ascii=False)
    assert p["fetched"] is True


def test_4e_MCPと遠くの道はfetchを受けない(article, key):
    server = _mcp_server()
    out = server.call_tool("thth_article_reactions", {"account": ACCOUNT, "article": ARTICLE, "fetch": True})
    assert out["isError"] is True and out["content"][0]["text"] == "invalid_request"
    tool = next(t for t in server.SERVER_TOOLS if t["name"] == "thth_article_reactions")
    assert "fetch" not in tool["inputSchema"]["properties"]
    assert _refused(key({ACCOUNT: "lane-a"}), operation="article_reactions", article=ARTICLE,
                    fetch=True) == "invalid_request"
    assert article.hatena.calls == []


# ---------------------------------------------------------------- T-S6-4f クリック

def test_4f_クリックの言えない符丁はそのままでThreads以外にsns_clicksは無い(article, monkeypatch):
    def shared(self, post_id, posted, views_24h=None):
        return {"post_id": post_id, "basis": None, "clicks_72h": None, "clicks_missing": None,
                "cannot_say": click_attribution.URL_SHARED}
    monkeypatch.setattr(click_attribution.Index, "attribute", shared)
    p = _payload()
    (clicks,) = _rows(p, source="sns_clicks")
    assert clicks["object"]["account"] == THREADS and clicks["window"]["missing"] == "url_shared_72h"
    assert clicks["window"]["kind"] == "post_day_plus_2" and clicks["attribution_basis"] == "unique_url_72h"
    assert not _rows(p, source="sns_clicks", obj_account=BSKY)


def test_4f_言える投稿はclicks_72hの値(article, monkeypatch):
    def unique(self, post_id, posted, views_24h=None):
        return {"post_id": post_id, "basis": click_attribution.BASIS, "clicks_72h": 4, "clicks_missing": None,
                "cannot_say": None, "views_24h": views_24h}
    monkeypatch.setattr(click_attribution.Index, "attribute", unique)
    p = _payload()
    (clicks,) = _rows(p, source="sns_clicks")
    assert clicks["value"] == 4 and clicks["window"]["missing"] is None
    assert p["announcements"][[a["account"] for a in p["announcements"]].index(THREADS)]["clicks"]["views_24h"] == 120


# ---------------------------------------------------------------- T-S6-5a deliveries の 1 行

def test_5a_deliveriesの1行に台帳のいちばん新しい行から反応_無ければ未採取(article):
    """柵: deliveries から媒体へ GET を足すと赤（呼び出しが 0 でない）。"""
    put_delivery(URL2, hours_ago=0.5)
    rc, out, err = call(["deliveries", ACCOUNT])
    assert rc == 0, err
    lines = out.splitlines()
    assert any(article.delivery in line and "反応: ブクマ 他 3・スター 3（72h・9/9）" in line for line in lines)
    assert any("反応: 未採取" in line for line in lines)
    payload = json.loads(call(["deliveries", ACCOUNT, "--json"])[1])
    latest = next(r for r in payload["deliveries"] if r["delivery"] == article.delivery)["reactions_latest"]
    assert (latest["bookmark_others"], latest["star_others"], latest["marks"]) == (3, 3, [72])
    assert article.hatena.calls == []


# ---------------------------------------------------------------- T-S6-5b events

def test_5b_bookmark_receivedは第三者のuserごとに1度で本文もuserも無い(article):
    """柵: summary にコメントの本文を入れると赤。"""
    payload = events_cli.build(ACCOUNT, since="30d", now=NOW)
    got = [e for e in payload["events"] if e["kind"] == "bookmark_received"]
    assert len(got) == 3                                   # alice・bob・carol（刻みを跨いでも 1 度）
    assert payload["by_kind"]["bookmark_received"] == 3
    ids = {e["event_id"] for e in got}
    assert events_cli.reaction_event_id(article.delivery, "alice") in ids
    for e in got:
        assert e["ref"] == article.delivery and e["article"] == ARTICLE
        for needle in ("いい記事", "参考に", "alice", "bob", "carol", SELF):
            assert needle not in e["summary"]
        assert "user" not in e and "comment" not in e
    alice = next(e for e in got if e["event_id"] == events_cli.reaction_event_id(article.delivery, "alice"))
    assert alice["has_comment"] is True and alice["occurred_at"] == "2026-09-08T21:03:00+09:00"
    assert alice["recorded_at"] == jst.iso(_at(74))
    # 読み直しても id は同じ（時刻を入れない）。
    again = events_cli.build(ACCOUNT, since="30d", now=NOW + datetime.timedelta(hours=1))
    assert ids == {e["event_id"] for e in again["events"] if e["kind"] == "bookmark_received"}


# ---------------------------------------------------------------- T-S6-5c morning・board

def test_5c_morningのyesterday_articlesは数だけ(article):
    # 窓は前日 JST（栞が無い）。最後の採取（9/9 02:00）が入る翌日の朝に読む。
    payload = morning.build(ACCOUNT, now=NOW + datetime.timedelta(days=1), mark=False, no_world=True)
    node = payload["sections_by_name"]["yesterday"]["value"]["by_account"][ACCOUNT]["value"]
    (row,) = node["articles"]
    assert row == {"article": ARTICLE, "delivery": article.delivery, "bookmark_others": 3, "star_others": 3,
                   "announcements_n": 2, "mark": 72, "age_hours": row["age_hours"],
                   "collected_at": jst.iso(_at(8))}
    text = json.dumps(payload, ensure_ascii=False)
    assert "いい記事" not in text and "alice" not in text


def test_5c_boardのaccountsに2つの鍵(article):
    collect(NOW)   # runs に hatena_collect の行（跨いだ刻みは無い）
    acc = _board_row(ACCOUNT)
    assert acc["hatena_collect_last_at"] == jst.iso(NOW) and acc["hatena_collect_errors_n"] == 0


def _board_row(name):
    rc, out, err = call(["board", "--json"])
    assert rc == 0, err
    return next(r for r in json.loads(out)["accounts"] if r["account"] == name)


# ---------------------------------------------------------------- T-S6-6a 遠くの道・MCP

@pytest.fixture
def key(lane, tmp_path, monkeypatch):  # noqa: F811
    from thth import report_http, report_isolation
    monkeypatch.setattr(report_isolation, "validate_environment", lambda *a, **k: None)

    def make(accounts_):
        bearer = secrets.token_urlsafe(32)
        config = tmp_path / f"credentials-{secrets.token_hex(2)}.json"
        value = dict(schema_version=1, root=str(lane.world.root), credentials=[dict(
            sha256=hashlib.sha256(bearer.encode()).hexdigest(),
            expires_at=(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat(),
            revoked=False, accounts=accounts_, writes=False, actor="person")])
        config.write_text(json.dumps(value))
        config.chmod(0o600)
        return report_http.load_credentials(config)[1][0][3]
    return make


def _serve(context, **body):
    from thth import server_writes
    return server_writes.serve(context, dict(account=ACCOUNT, **body), via="mcp")


def _refused(context, account=ACCOUNT, **body):
    from thth import server_writes
    with pytest.raises(ReportServiceError) as caught:
        server_writes.serve(context, dict(account=account, **body), via="mcp")
    return str(caught.value)


def _mcp_server():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
    import server
    return server


def test_6a_遠くの道は鍵の範囲の全部を読み範囲外の告知は行が無い(article, key):
    """柵: 範囲外の告知の口座を読むと赤。"""
    context = key({ACCOUNT: "lane-a", THREADS: "lane-a"})
    p = _serve(context, operation="article_reactions", article=ARTICLE)
    assert p["schema"] == "thth.article_reactions/1"
    assert {a["account"] for a in p["announcements"]} == {THREADS}
    assert not _rows(p, obj_account=BSKY)
    assert "scope_unavailable" in p["cannot_say"]
    # 既定では本文なし・comments で出る（手元と同じ鍵）。
    assert "いい記事" not in json.dumps(p, ensure_ascii=False)
    q = _serve(context, operation="article_reactions", article=ARTICLE, comments=True)
    assert [c["user"] for c in q["deliveries"][0]["comments"]] == ["alice", "bob", "carol"]
    # 鍵の口座だけなら告知は 1 本も読まない。
    only = _serve(key({ACCOUNT: "lane-a"}), operation="article_reactions", article=ARTICLE)
    assert only["announcements"] == [] and "scope_unavailable" in only["cannot_say"]
    assert _refused(key({ACCOUNT: "lane-a"}), operation="article_reactions") == "invalid_request"
    assert _refused(key({"lane-b-hatenablog": "lane-b"}), operation="article_reactions",
                    article=ARTICLE) == "scope_unavailable"
    assert article.hatena.calls == []


def test_6a_大きい答えはrowsを古い方から落とす(article, key, monkeypatch):
    from thth import server_reads
    context = key({ACCOUNT: "lane-a"})
    full = _serve(context, operation="article_reactions", article=ARTICLE)
    monkeypatch.setattr(server_reads, "RESULT_BUDGET", server_reads._size(full) - 400)
    small = _serve(context, operation="article_reactions", article=ARTICLE)
    assert small["trimmed"] >= 1
    kept = small["deliveries"][0]["rows"]
    assert kept == full["deliveries"][0]["rows"][small["trimmed"]:]   # 古い方から落ちた
    assert any("落としました" in c for c in small["cannot_say"])


def test_6a_MCPの道具の一覧にthth_article_reactionsがありthth_hatena_collectは無い():
    server = _mcp_server()
    for names in ({t["name"] for t in server.TOOLS}, {t["name"] for t in server.SERVER_TOOLS}):
        assert "thth_article_reactions" in names
        assert not [n for n in names if "hatena_collect" in n or n == "thth_hatena"]


# ---------------------------------------------------------------- doctor（§6.1）

def test_doctorは反応の台帳の数と件数の口への1回を出し終了コードを変えない(article, fake_http):  # noqa: F811
    from tests.hatena_s2_http import blog_handler, put_secrets
    put_secrets(article.world)
    blog = blog_handler()

    def both(method, url, headers, body):
        if url.startswith("https://bookmark.hatenaapis.com/count/"):
            return article.hatena(method, url, headers, body)
        if url.startswith("https://bookmark.hatenaapis.com/rest/1/my"):
            return 200, {}, json.dumps({"name": SELF}).encode()
        return blog(method, url, headers, body)
    http = fake_http(both)
    rc, out, err = call(["doctor", ACCOUNT, "--json"])
    report_ = json.loads(out)
    react = report_["reactions"]
    assert (react["deliveries"], react["unreadable"], react["count_ok"]) == (1, 0, True)
    assert react["last_collected_at"] == jst.iso(_at(8))
    (count_call,) = [c for c in http.calls if "/count/" in c["url"]]
    assert "authorization" not in count_call["headers"]
    assert "a.hatenablog.com" in count_call["url"]
    assert [p["key"] for p in report_["probes"]] == ["atom_entries"]     # probes には入れない
    article.hatena.fail["counts"] = 500
    rc2, out2, _err = call(["doctor", ACCOUNT, "--json"])
    assert rc2 == rc and json.loads(out2)["reactions"]["count_error"] == "hatena_http_500"
