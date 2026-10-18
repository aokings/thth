"""4.2.2（設計 S4 §24・kopicha の本番 10/18）: はてなブックマークの 1 件の断りでまとめ全体を止めない。

見るもの:
  A. 半角「%」の柵: `add`・`edit`・`batch` の 1 段目と `thth lint <札のファイル>` で予告して断る
     （`bookmark_percent_unsupported`・どこに何個・全角の ％ へ）。全角は通す。台帳に観測の項目。
     4.2.2 より前に並べた札も、付けるときの読み直しで断り、まとめは進む。
  B. POST が 401/403 なら同じ run の中で `/rest/1/my` を GET して token を確かめる。通ればその札だけ
     `hatena_rejected_item`（詳細に HTTP の番号）でまとめは進む。GET も断られたら `hatena_auth_rejected`。
     1 件ずつの `add`・`edit` でも同じ見分けで、人向けの文を分ける。
  C. はてなに打って止まった（blocked）ら `BLOCKED_HOLD_MINUTES`（60 分）は次の run も HTTP を打たない。
     `thth bookmark batch-resume <口座> --by` で今すぐ見直させる。打たずに止まる止め（主体の止めなど）は今どおり。

HTTP は偽物（`hatena_s4_helpers.Bookmarks`）。はてなには出ない。秘密の値は偽物だけ。
"""
from __future__ import annotations

import datetime
import json

from tests.hatena_s1_flow_helpers import call, lane  # noqa: F401
from tests.hatena_s4_helpers import ACCOUNT, EXTERNAL, EXTERNAL_2, bm, bookmark, calls, cfg, fake_http, world  # noqa: F401
from tests.test_v410_bookmark_batch import (URL_A1, URL_B1, approve, batch_file, bb, item, later, run,  # noqa: F401
                                            states, write)
from thth import bookmark_batch, hatena_bookmark, lint, media_traits, sent

ME = "https://bookmark.hatenaapis.com/rest/1/my"


def hatena_bookmark_api(target):
    import urllib.parse
    return "https://bookmark.hatenaapis.com/rest/1/my/bookmark?" + urllib.parse.urlencode([("url", target)])


def me_calls(world):
    return [u for m, u in calls(world, "GET") if u == ME]


# ================================================================ A. 半角「%」の柵

def test_A_addの1段目で半角パーセントを予告して断り_どこに何個かと全角への案内(bm):
    """柵: `check_percent` を外すと赤（「約 85%」の札が 1 段目を通る）。"""
    rc, out, _ = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "約 85% が一次資料",
                       "--tags", "割合%,統計", "--json"])
    body = json.loads(out)
    assert rc == 2 and body["error"] == "bookmark_percent_unsupported"
    assert "コメント 1 個" in body["message"] and "タグ「割合%」 1 個" in body["message"]
    assert "全角の「％」" in body["message"] and "統計" not in body["message"]
    rc, out, _ = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "約 85％ が一次資料", "--json"])
    assert rc == 1 and json.loads(out)["stage"] == 1
    assert calls(bm, "POST") == []


def test_A_editは直したあとの値だけを見る(bm):
    bookmark(ACCOUNT, url=EXTERNAL, comment="旧", tags=["旧名"])
    rc, out, _ = call(["bookmark", "edit", ACCOUNT, "--url", EXTERNAL, "--comment", "100%", "--json"])
    assert rc == 2 and json.loads(out)["error"] == "bookmark_percent_unsupported"
    rc, out, _ = call(["bookmark", "edit", ACCOUNT, "--url", EXTERNAL, "--comment", "100％", "--json"])
    assert rc == 1 and json.loads(out)["stage"] == 1


def test_A_batchはその札だけ除き理由と詳細を見せる(bb):
    path = write(bb, [item(URL_A1, comment="約 85%"), item(URL_B1, comment="約 85％")])
    rc, out, _ = call(["bookmark", "batch", ACCOUNT, path, "--json"])
    items = json.loads(out)["preview"]["items"]
    assert [r["reason"] for r in items] == ["bookmark_percent_unsupported", None]
    assert "コメント 1 個" in items[0]["detail"] and "全角" in items[0]["detail"]
    rc, out, _ = call(["bookmark", "batch", ACCOUNT, path])
    assert "除く: bookmark_percent_unsupported" in out


def test_A_lintは札のファイルの半角パーセントをerrorで言う(tmp_path):
    path = tmp_path / "day.jsonl"
    path.write_text("\n".join([json.dumps({"url": EXTERNAL, "comment": "約 85%", "tags": "a%b"}, ensure_ascii=False),
                               json.dumps({"url": EXTERNAL_2, "comment": "約 85％"}, ensure_ascii=False)]) + "\n",
                    encoding="utf-8")
    problems = lint.lint_file(str(path))
    assert len(problems) == 1 and problems[0].startswith("bookmark_percent_unsupported: 札 1（1 行目）")
    assert "コメント 1 個" in problems[0] and "タグ「a%b」 1 個" in problems[0]
    assert lint.reason_code(problems) == "bookmark_percent_unsupported"
    rc, out, _ = call(["lint", str(path)])
    assert rc == 1 and "bookmark_percent_unsupported" in out


def test_A_前に並べた札も付けるときに断り_まとめは進む(bb, monkeypatch):
    """本番の形: 4.2.1 で並べた「約 85%」の札。付けるときの読み直しで断り、次の札へ進む（打たない）。"""
    with monkeypatch.context() as m:
        m.setattr(hatena_bookmark, "check_percent", lambda comment, tags: None)
        approve(write(bb, [item(URL_A1, comment="約 85%"), item(URL_B1)]))
    out, _ = run(bb)
    b = batch_file(bb)
    assert [(i["state"], i["reason"]) for i in b["items"]] == [
        ("refused", "bookmark_percent_unsupported"), ("placed", None)]
    assert "コメント 1 個" in b["items"][0]["detail"]
    assert [u for _m, u in calls(bb, "POST")] == [hatena_bookmark_api(URL_B1)]
    assert out["status"] == "placed" and not out.get("blocked")


def test_A_媒体の台帳に観測の項目がある():
    doc = media_traits.load("hatenabookmark")
    found = next(t for t in doc["traits"] if t["id"] == "hatenabookmark.percent_in_body_rejected_observed")
    assert found["kind"] == "observation" and found["confidence"] in ("medium", "low")
    assert found["evidence"]["type"] == "observed" and found["evidence"]["observed_at"] == "2026-10-18"
    assert "kopicha" in found["evidence"]["record"]
    assert "401" in found["statement"] and "%25" in found["statement"] and "推測" in found["statement"]


# ================================================================ B. 1 件だけの 401/403

def test_B_addでPOSTが401でもtokenが生きていればその札だけの断り(bm):
    """柵: `_rejection` の GET を外すと赤（token の生きている 401 を認可の問題と言う）。"""
    bm.bookmarks.post = (401, b"Unauthorized")
    rc, out, _ = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--json"])
    digest = json.loads(out)["digest"]
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--confirm", digest,
                         "--by", "masaru", "--production", "--json"])
    body = json.loads(out)
    assert rc == 2 and body["error"] == "hatena_rejected_item", (out, err)
    assert "この札だけはてなに断られました" in body["message"] and "token は生きています" in body["message"]
    assert "HTTP 401" in body["message"] and "principal auth" not in body["message"]
    assert len(me_calls(bm)) == 1           # POST のあとに 1 回だけ確かめる
    assert [r for r in sent.records(bm.state(ACCOUNT)) if r.get("kind") == "bookmark_add"] == []


def test_B_addでtokenも断られたら認可の問題(bm):
    bm.bookmarks.post = (403, b"Forbidden")
    bm.bookmarks.me = (401, b"Unauthorized")
    rc, out, _ = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--json"])
    digest = json.loads(out)["digest"]
    rc, out, _ = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--confirm", digest,
                       "--by", "masaru", "--production", "--json"])
    body = json.loads(out)
    assert rc == 2 and body["error"] == "hatena_auth_rejected"
    assert "thth principal auth" in body["message"] and "--force" in body["message"]


def test_B_editでも同じ見分け(bm):
    bookmark(ACCOUNT, url=EXTERNAL, comment="旧", tags=["旧名"])
    bm.bookmarks.post = (401, b"Unauthorized")
    rc, out, _ = call(["bookmark", "edit", ACCOUNT, "--url", EXTERNAL, "--comment", "新", "--json"])
    digest = json.loads(out)["digest"]
    args = ["bookmark", "edit", ACCOUNT, "--url", EXTERNAL, "--comment", "新", "--confirm", digest, "--by", "masaru",
            "--production", "--json"]
    rc, out, _ = call(args)
    assert rc == 2 and json.loads(out)["error"] == "hatena_rejected_item"
    bm.bookmarks.me = (401, b"Unauthorized")
    rc, out, _ = call(args)
    assert rc == 2 and json.loads(out)["error"] == "hatena_auth_rejected"
    row = sent.read(bm.state(ACCOUNT), sent.bookmark_post_id(EXTERNAL))
    assert row["text"] == "旧" and not row.get("attempts")


def test_B_まとめは断られた札だけrefusedにして進む(bb):
    """柵: `hatena_rejected_item` を `_FINAL_BOOKMARK` から外すと赤（まとめ全体が止まる）。"""
    bb.bookmarks.post = lambda target, form: (401, b"no") if target == URL_A1 else _place_default(bb, target, form)
    approve(write(bb, [item(URL_A1), item(URL_B1)]))
    out, _ = run(bb)
    b = batch_file(bb)
    assert [(i["state"], i["reason"], i["detail"]) for i in b["items"]] == [
        ("refused", "hatena_rejected_item", "HTTP 401"), ("placed", None, None)]
    assert out["status"] == "placed" and not out.get("blocked")
    assert bookmark_batch.status(ACCOUNT)["blocked"] is None


def test_B_まとめでtokenも断られたら止まる(bb):
    bb.bookmarks.post = (401, b"no")
    bb.bookmarks.me = (401, b"no")
    approve(write(bb, [item(URL_A1), item(URL_B1)]))
    out, _ = run(bb)
    assert out["status"] == "blocked" and out["blocked"]["code"] == "hatena_auth_rejected"
    assert states(bb) == ["pending", "pending"]


def _place_default(world, target, form):
    """偽物のはてなの既定の POST（付ける）。"""
    from tests.hatena_s4_helpers import bookmark_object
    mark = bookmark_object(target, comment=form.get("comment", [""])[0], tags=form.get("tags", []))
    world.bookmarks.marks[("lane-owner", target)] = mark
    return 200, json.dumps(mark).encode()


# ================================================================ C. 止まったら打ち直し続けない

def _blocked(bb):
    bb.bookmarks.post = (401, b"no")
    bb.bookmarks.me = (401, b"no")
    approve(write(bb, [item(URL_A1), item(URL_B1)]))
    now = later(0)
    out, _ = run(bb, now=now)
    assert out["status"] == "blocked" and out["blocked"]["code"] == "hatena_auth_rejected"
    return now


def test_C_はてなに打って止まったら次のrunはHTTPを打たない_60分あとに試す(bb):
    """柵: blocked に `next_at` を置かないと赤（10 分ごとに同じ札を打ち直す・kopicha 10/18 に 57 回）。"""
    now = _blocked(bb)
    before = len(bb.http.calls)
    for minutes in (10, 20, 30, 59):
        out, _ = run(bb, now=now + datetime.timedelta(minutes=minutes))
        assert out["status"] == "waiting" and out["blocked"]["code"] == "hatena_auth_rejected"
    assert len(bb.http.calls) == before
    s = bookmark_batch.status(ACCOUNT)
    assert s["blocked"]["until"] == s["next_at"]
    assert bookmark_batch.jst.parse(s["next_at"]) - now == datetime.timedelta(
        minutes=bookmark_batch.BLOCKED_HOLD_MINUTES)
    rc, text, _ = call(["bookmark", "batch-status", ACCOUNT])
    assert "まではてなに打ちません" in text and f"thth bookmark batch-resume {ACCOUNT} --by" in text
    # 60 分あとは今の柵で見直す（直っていなければまた止まる）
    run(bb, now=now + datetime.timedelta(minutes=61))
    assert len(bb.http.calls) > before


def test_C_batch_resumeで今すぐ見直させ_札と指紋は変えない(bb):
    now = _blocked(bb)
    before_items = batch_file(bb)["items"]
    rc, out, _ = call(["bookmark", "batch-resume", ACCOUNT, "--json"])
    assert rc == 1 and json.loads(out)["error"] == "by_required"
    bb.bookmarks.post = None
    bb.bookmarks.me = None
    rc, out, err = call(["bookmark", "batch-resume", ACCOUNT, "--by", "masaru", "--json"])
    body = json.loads(out)
    assert rc == 0 and body["was_blocked"]["code"] == "hatena_auth_rejected" and body["pending"] == 2, (out, err)
    s = bookmark_batch.status(ACCOUNT)
    assert s["blocked"] is None and s["next_at"] is None
    assert [i["approved_sha"] for i in batch_file(bb)["items"]] == [i["approved_sha"] for i in before_items]
    out, _ = run(bb, now=now + datetime.timedelta(minutes=5))
    assert out["status"] == "placed" and states(bb)[0] == "placed"
    from thth import admin_log
    assert any(r["event"] == "bookmark_batch_resumed" and r["by"] == "masaru" for r in admin_log.read()[0])


def test_C_打たずに止まる止めは今どおり次の刻みで見直す(bb):
    """主体の止め（HTTP を打たない）は 60 分待たせない（止めを解けば次の刻みで付く）。"""
    from thth import principals
    approve(write(bb, [item(URL_A1)]))
    principals.stop("hatena-main", "owner", actor="tester")
    out, _ = run(bb)
    assert out["blocked"]["code"] == "principal_stopped" and "until" not in out["blocked"]
    principals.resume("hatena-main", by="tester")
    run(bb, now=later(10))
    assert states(bb) == ["placed"]
