"""4.0 S3: timer の画像の段（3c）・画像の台帳・画像だけ成功・読み戻しと `sent/`・見せる口（T-S3-2c・4a〜8c）。

HTTP は偽物（`httpsafe.urlopen` の差し替え）。adapter は本物（ブログもフォトライフも）。
**本物のはてなには 1 回も出ない。** 秘密の値は明らかな偽物だけ。
"""
from __future__ import annotations

import json
import os
import urllib.error

import pytest

from tests.hatena_s3_helpers import (ACCOUNT, ARTICLE, FOTOLIFE, HATENA_ID, JPEG, NOW, PNG, Fotolife,  # noqa: F401
                                     approve, approved, blog_calls, call, fake_http, fm_of, lane, ledger,
                                     nothing_written, photo_calls, photo_feed, photo_lane, photo_xml, posted,
                                     sha_of, states, throw, world)
from tests.hatena_s2_http import COLLECTION
from tests.test_v400_s1_remote import SOON, draft_id, key, request  # noqa: F401
from thth import core, deliveries, hatena_fotolife, hatena_publish, httpsafe, inflight, lock, principals, sent

JPEG_SHA = sha_of(JPEG)
PNG_SHA = sha_of(PNG)


def _syntax(record):
    return record["syntax"].split(":")


# ---------------------------------------------------------------- 通し

def test_画像2枚の記事は画像を上げてから記事をPOSTし_本文はf_id記法(photo_lane):
    path, delivery = approved(photo_lane)
    logs = []
    result = throw(logs=logs)
    assert (result.action, result.exit_code) == ("post", 0), (result, logs)
    # 順: ブログの記法の GET（3b）→ 画像 2 枚の POST（3c）→ 記事の POST（5）
    calls = [(c["method"], c["url"]) for c in photo_lane.http.calls]
    assert calls == [("GET", COLLECTION), ("POST", f"{FOTOLIFE}/atom/post"),
                     ("POST", f"{FOTOLIFE}/atom/post"), ("POST", COLLECTION)]
    # 送った本文: 画像の参照がフォトライフ記法に置き換わっている
    sent_body = photo_lane.blog.entries["99"]
    rows = {r["sha256"]: r for r in ledger(photo_lane.world)}
    jpeg, png = rows[JPEG_SHA], rows[PNG_SHA]
    assert f"[f:id:{HATENA_ID}:{_syntax(jpeg)[3]}:plain]" in sent_body
    assert f"[f:id:{HATENA_ID}:{_syntax(png)[3]}:plain]" in sent_body
    assert "../images/" not in sent_body
    assert jpeg["state"] == png["state"] == "uploaded"
    assert jpeg["content_type"] == "image/jpeg" and png["content_type"] == "image/png"
    assert jpeg["title"] == f"bean.jpg thth:{JPEG_SHA[:12]}" and jpeg["folder"] == "thth"
    assert jpeg["first_seen"] == {"account": ACCOUNT, "delivery": delivery, "path": "images/bean.jpg"}
    assert jpeg["edit_uri"] == f"{FOTOLIFE}/atom/edit/{jpeg['fotolife_id']}"
    # 配信の台帳の images[] に写す（記録）
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "published"
    by_path = {r["path"]: r for r in record["images"]}
    assert by_path["images/bean.jpg"]["fotolife_id"] == jpeg["fotolife_id"]
    assert by_path["images/bean.jpg"]["syntax"] == jpeg["syntax"]
    assert by_path["images/leaf.png"]["image_url"] == png["image_url"]
    assert any("画像 2 件をそろえました（上げた 2 件・使い回し 0 件）" in line for line in logs)


# ---------------------------------------------------------------- T-S3-2c §6.4 の表

def test_T_S3_2c_201でuserが違えばfotolife_user_mismatch_台帳に書かずexit2(photo_lane):
    photo_lane.photo.post = lambda body: (201, photo_xml("777", title=posted(body)["title"], hatena_id="other"))
    path, delivery = approved(photo_lane)
    result = throw()
    assert (result.action, result.error, result.exit_code) == ("refused", "fotolife_user_mismatch", 2)
    assert ledger(photo_lane.world) == []
    nothing_written(photo_lane, delivery)
    assert [m for m, _u in blog_calls(photo_lane.http)] == ["GET"]   # 記事は送らない


@pytest.mark.parametrize("status, error, exit_code", [
    (400, "fotolife_rejected_400", 2), (413, "fotolife_too_large", 2), (415, "fotolife_rejected_415", 2),
    (429, "provider_rate_limited", 1), (401, "hatena_auth_rejected", 2), (404, "fotolife_http_404", 2)])
def test_T_S3_2c_4xxは書かず止まり_inflightも配信の台帳も書かない(photo_lane, status, error, exit_code):
    photo_lane.photo.post = (status, b"no")
    path, delivery = approved(photo_lane)
    result = throw()
    assert (result.error, result.exit_code) == (error, exit_code), result
    assert ledger(photo_lane.world) == []
    nothing_written(photo_lane, delivery)
    assert len([1 for m, _u in photo_calls(photo_lane.http) if m == "POST"]) == 1


@pytest.mark.parametrize("answer, error", [
    ((500, b"oops"), "fotolife_http_500"),
    ((503, b"oops"), "fotolife_http_503"),
    (TimeoutError("timed out"), "fotolife_transport_failed"),
    (urllib.error.URLError("down"), "fotolife_transport_failed"),
    (httpsafe.RedirectBlocked(f"{FOTOLIFE}/atom/post", 302, "redirect", {}, None), "redirect_blocked"),
    ((201, b"<" * (4 * 1024 * 1024 + 10)), "response_too_large"),
    ((201, b"<entry"), "response_invalid"),
    ((200, b"<entry/>"), "fotolife_http_200"),
])
def test_T_S3_2c_5xxと時間切れとRedirectBlockedと大きすぎはunknown_止まりinflight無し(photo_lane, answer, error):
    """柵: 5xx を definite（ambiguous=False）にすると unknown の行が無くなって赤。"""
    photo_lane.photo.post = answer
    path, delivery = approved(photo_lane)
    result = throw()
    assert (result.action, result.error, result.exit_code) == ("fotolife_ambiguous", "fotolife_ambiguous", 1)
    (row,) = ledger(photo_lane.world)
    assert row["state"] == "unknown" and row["sha256"] == JPEG_SHA and row["error"] == error
    assert row["title"] == f"bean.jpg thth:{JPEG_SHA[:12]}" and row["sent_at"]
    nothing_written(photo_lane, delivery)


def test_T_S3_2e_5xxのあと同じrunで2回目のPOSTが無い(photo_lane):
    photo_lane.photo.post = (500, b"oops")
    approved(photo_lane)
    throw()
    assert [m for m, _u in photo_calls(photo_lane.http)] == ["POST"]


def test_T_S3_2e_上げの時間切れは120秒_確かめのGETは30秒(photo_lane):
    path, delivery = approved(photo_lane)
    throw()
    ups = [c for c in photo_lane.http.calls if c["url"] == f"{FOTOLIFE}/atom/post"]
    assert ups and all(c["timeout"] == 120 for c in ups)   # 口座の timeout は 300
    approved(photo_lane, "b.md")
    photo_lane.http.calls.clear()
    throw()
    gets = [c for c in photo_lane.http.calls if "/atom/edit/" in c["url"]]
    assert gets and all(c["timeout"] == 30 for c in gets)
    data = photo_lane.world.account(ACCOUNT)
    data["timeout"] = 45
    photo_lane.world.write_account(data)
    photo_lane.write("images/bean.jpg", JPEG + b"x")
    approved(photo_lane, "c.md")
    photo_lane.http.calls.clear()
    throw()
    ups = [c for c in photo_lane.http.calls if c["url"] == f"{FOTOLIFE}/atom/post"]
    assert ups and all(c["timeout"] == 45 for c in ups)


# ---------------------------------------------------------------- T-S3-4 台帳

def test_T_S3_4a_同じ画像を2つの記事で使うとPOSTは1回_2回目はGETだけ(photo_lane):
    """柵: 使い回しの前の GET を消すと、外で消された画像（404）の形で赤。"""
    approved(photo_lane)
    assert throw().exit_code == 0
    first = {r["sha256"]: r["fotolife_id"] for r in ledger(photo_lane.world)}
    # 別の記事（同じ画像）
    photo_lane.write("articles/b.md", "# 別の記事\n\n![豆](../images/bean.jpg)\n")
    path, delivery = approved(photo_lane, "b.md", article_source="articles/b.md", title="別の記事")
    photo_lane.http.calls.clear()
    logs = []
    assert throw(logs=logs).exit_code == 0
    assert photo_calls(photo_lane.http) == [("GET", f"/atom/edit/{first[JPEG_SHA]}")]
    assert any("上げた 0 件・使い回し 1 件" in line for line in logs)
    # 外で消す → 次の記事は 404 を見て台帳を消し、上げ直す
    del photo_lane.photo.photos[first[JPEG_SHA]]
    photo_lane.write("articles/c.md", "# 三つ目\n\n![豆](../images/bean.jpg)\n")
    approved(photo_lane, "c.md", article_source="articles/c.md", title="三つ目")
    photo_lane.http.calls.clear()
    assert throw().exit_code == 0
    assert photo_calls(photo_lane.http) == [("GET", f"/atom/edit/{first[JPEG_SHA]}"), ("POST", "/atom/post")]
    again = {r["sha256"]: r["fotolife_id"] for r in ledger(photo_lane.world)}
    assert again[JPEG_SHA] != first[JPEG_SHA]


def test_T_S3_4a_確かめのGETが5xxならfotolife_check_failed_上げず出ない(photo_lane):
    approved(photo_lane)
    assert throw().exit_code == 0
    photo_lane.photo.get = (502, b"bad")
    photo_lane.write("articles/b.md", "# 別\n\n![豆](../images/bean.jpg)\n")
    path, delivery = approved(photo_lane, "b.md", article_source="articles/b.md", title="別")
    photo_lane.http.calls.clear()
    result = throw()
    assert (result.action, result.error, result.exit_code) == ("fotolife_check_failed", "fotolife_check_failed", 1)
    assert [m for m, _u in photo_calls(photo_lane.http)] == ["GET"]
    assert states(delivery) == ["wanted"] and inflight.read(photo_lane.state) is None


def test_T_S3_4a_GETのidが台帳と違えばfotolife_check_failed(photo_lane):
    approved(photo_lane)
    assert throw().exit_code == 0
    photo_lane.photo.get = (200, photo_xml("1"))
    photo_lane.write("articles/b.md", "# 別\n\n![豆](../images/bean.jpg)\n")
    approved(photo_lane, "b.md", article_source="articles/b.md", title="別")
    result = throw()
    assert result.error == "fotolife_check_failed"


def test_T_S3_4b_台帳が読めなければfotolife_ledger_unreadable_上げず出ない(photo_lane):
    """柵: 読めない台帳を「無い」とみなして上げ直すと赤（POST が走る）。"""
    directory = hatena_fotolife.dir_for("hatena-main")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    target = os.path.join(directory, f"{JPEG_SHA}.json")
    with open(target, "w", encoding="utf-8") as f:
        f.write("{broken")
    os.chmod(target, 0o600)
    path, delivery = approved(photo_lane)
    result = throw()
    assert (result.action, result.error, result.exit_code) == ("refused", "fotolife_ledger_unreadable", 2)
    assert photo_calls(photo_lane.http) == []
    nothing_written(photo_lane, delivery)


def test_T_S3_4c_台帳は主体のロックの中でしか書かれない(photo_lane, monkeypatch):
    held = []
    real_acquire, real_release = lock.acquire, lock.AccountLock.release

    def acquire(lk, *args, **kwargs):
        out = real_acquire(lk, *args, **kwargs)
        held.append(str(lk.lock_path))
        return out

    def release(self):
        name = str(self.lock_path)
        if name in held:
            held.remove(name)
        return real_release(self)

    monkeypatch.setattr(lock, "acquire", acquire)
    monkeypatch.setattr(lock.AccountLock, "release", release)
    principal_lock = principals.lock_path_for("hatena-main")
    writes = []
    real_write = hatena_fotolife._write

    def write(principal, record, **kwargs):
        writes.append(any(h == principal_lock for h in held))
        return real_write(principal, record, **kwargs)

    monkeypatch.setattr(hatena_fotolife, "_write", write)
    approved(photo_lane)
    assert throw().exit_code == 0
    assert writes and all(writes), (writes, held)


def test_T_S3_4d_1日の上限のあとfotolife_daily_limit_次は翌日0時(photo_lane):
    photo_lane.world.edit_principal("hatena-main", fotolife={"daily_max_uploads": 1})
    path, delivery = approved(photo_lane)
    logs = []
    result = throw(logs=logs)
    assert (result.action, result.error) == ("guard_refused", "fotolife_daily_limit")
    # 時刻が来れば解ける（WAITING_REFUSALS・timer の失敗にしない＝exit 0）。設計 §3.2 の「exit 1」とは
    # 食い違う（報告に書いた）。
    assert result.exit_code == 0 and "fotolife_daily_limit" in hatena_publish.WAITING_REFUSALS
    assert "2026-09-10T00:00:00+09:00" in result.message
    assert any("2026-09-10T00:00:00+09:00" in line for line in logs)
    assert [r["state"] for r in ledger(photo_lane.world)] == ["uploaded"]   # 1 枚目は上がって残る
    nothing_written(photo_lane, delivery)


# ---------------------------------------------------------------- T-S3-5 画像だけ成功

def test_T_S3_5a_記事が4xxなら台帳はwantedに戻り_次のrunは画像を上げ直さない(photo_lane):
    """柵: 画像の台帳を消して上げ直すと、2 回目の POST の数で赤。"""
    blog = photo_lane.blog

    def refuse(method, url, headers, body):
        if method == "POST" and not url.startswith(FOTOLIFE):
            return 400, {}, b"bad"
        return route_default(photo_lane)(method, url, headers, body)
    photo_lane.http.handler = refuse
    path, delivery = approved(photo_lane)
    result = throw()
    assert result.error == "hatena_http_400"
    assert states(delivery)[-1] == "wanted" and inflight.read(photo_lane.state) is None
    assert {r["state"] for r in ledger(photo_lane.world)} == {"uploaded"}
    photo_lane.http.handler = route_default(photo_lane)
    photo_lane.http.calls.clear()
    assert throw().exit_code == 0
    assert [m for m, _u in photo_calls(photo_lane.http)] == ["GET", "GET"]
    assert blog.entries   # 記事は 2 回目で出た


def route_default(lane):
    from tests.hatena_s3_helpers import route
    return route(lane.blog, lane.photo)


def _resolve(*flags):
    rc, out, err = call(["inflight", ACCOUNT, "resolve", *flags, "--by", "tester"])
    digest = [line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("digest: ")][0]
    return call(["inflight", ACCOUNT, "resolve", *flags, "--by", "tester", "--confirm", digest])


def _article_500(lane):
    def handler(method, url, headers, body):
        if method == "POST" and not url.startswith(FOTOLIFE):
            lane.blog(method, url, headers, body)   # 記事は作られたが 500 を返した
            return 500, {}, b""
        return route_default(lane)(method, url, headers, body)
    lane.http.handler = handler


def test_T_S3_5b_記事が5xxならunknown_not_publishedで解くと次のrunは画像を上げ直さない(photo_lane):
    _article_500(photo_lane)
    path, delivery = approved(photo_lane)
    assert throw().error == "publish_ambiguous"
    assert states(delivery)[-1] == "unknown" and inflight.read(photo_lane.state) is not None
    rc, out, err = _resolve("--not-published")
    assert rc == 0, (out, err)
    photo_lane.http.handler = route_default(photo_lane)
    photo_lane.http.calls.clear()
    assert throw().exit_code == 0
    assert [m for m, _u in photo_calls(photo_lane.http)] == ["GET", "GET"]


def test_T_S3_5b_publishedで解くとimagesにfotolife_idが写り_読み戻しはbody_sentと比べる(photo_lane):
    _article_500(photo_lane)
    path, delivery = approved(photo_lane)
    assert throw().error == "publish_ambiguous"
    photo_lane.http.handler = route_default(photo_lane)
    from tests.hatena_s2_http import entry_id
    rc, out, err = _resolve("--published", entry_id("99"))
    assert rc == 0, (out, err)
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "published"
    rows = {r["sha256"]: r for r in ledger(photo_lane.world)}
    by_path = {r["path"]: r for r in record["images"]}
    assert by_path["images/bean.jpg"]["fotolife_id"] == rows[JPEG_SHA]["fotolife_id"]
    # 読み戻した本文（はてなが覚えている＝置き換えたあと）と送った本文が同じなので印は付かない
    assert "readback_differs" not in record["history"][-1]
    (row,) = sent.records(photo_lane.state)
    assert "[f:id:" in row["text"] and "../images/" not in row["text"]


def test_T_S3_5b_publishedで解くとき台帳に無い画像があれば比べを飛ばしfotolife_idなしで写す(photo_lane):
    _article_500(photo_lane)
    path, delivery = approved(photo_lane)
    assert throw().error == "publish_ambiguous"
    os.unlink(os.path.join(hatena_fotolife.dir_for("hatena-main"), f"{PNG_SHA}.json"))
    photo_lane.http.handler = route_default(photo_lane)
    from tests.hatena_s2_http import entry_id
    rc, out, err = _resolve("--published", entry_id("99"))
    assert rc == 0, (out, err)
    assert "画像の台帳に無い画像があるので" in out + err
    record = deliveries.read(ACCOUNT, delivery)
    assert "readback_differs" not in record["history"][-1]
    assert all("fotolife_id" not in r for r in record["images"])


def test_T_S3_5c_画像のunknownは次のrunがfeedで題を照合し_あればPOSTしない(photo_lane):
    """柵: 照合を消すと、feed に在る形で POST が走って赤。"""
    state = {"n": 0}

    def first_times_out(body):
        state["n"] += 1
        fid = photo_lane.photo.create(posted(body)["title"])   # 上がったのに
        if state["n"] == 1:
            raise TimeoutError("timed out")                      # 答えが返らない
        return 201, photo_lane.photo.photos[fid]
    photo_lane.photo.post = first_times_out
    path, delivery = approved(photo_lane)
    assert throw().error == "fotolife_ambiguous"
    (row,) = ledger(photo_lane.world)
    assert row["state"] == "unknown"
    photo_lane.http.calls.clear()
    logs = []
    assert throw(logs=logs).exit_code == 0
    calls = photo_calls(photo_lane.http)
    assert calls[0] == ("GET", "/atom/feed")
    assert calls.count(("POST", "/atom/post")) == 1   # 2 枚目（PNG）だけ
    rows = {r["sha256"]: r for r in ledger(photo_lane.world)}
    assert rows[JPEG_SHA]["state"] == "uploaded" and rows[JPEG_SHA]["fotolife_id"]
    assert any("一覧にあったので使い回します" in line for line in logs)


def test_T_S3_5c_feedに無ければPOSTし直す_feedが読めなければcheck_failed(photo_lane):
    photo_lane.photo.post = TimeoutError("timed out")
    path, delivery = approved(photo_lane)
    assert throw().error == "fotolife_ambiguous"
    photo_lane.photo.post = None
    photo_lane.photo.feed = (503, b"")
    assert throw().error == "fotolife_check_failed"
    photo_lane.photo.feed = None
    photo_lane.http.calls.clear()
    assert throw().exit_code == 0
    assert photo_calls(photo_lane.http)[:2] == [("GET", "/atom/feed"), ("POST", "/atom/post")]


def test_T_S3_5d_画像の段で止まったrunはsentもinflightも台帳のhistoryも増やさない(photo_lane):
    photo_lane.photo.post = (503, b"")
    path, delivery = approved(photo_lane)
    before = deliveries.read(ACCOUNT, delivery)
    throw()
    nothing_written(photo_lane, delivery)
    assert deliveries.read(ACCOUNT, delivery)["history"] == before["history"]


# ---------------------------------------------------------------- T-S3-6 読み戻し・sent・差分

def test_T_S3_6a_sentとreadback_shaは置き換えたあとの本文(photo_lane):
    """柵: `_record_outcome` が `m["body"]`（元の形）と比べると、readback_differs が付いて赤。"""
    from thth import approval
    path, delivery = approved(photo_lane)
    assert throw().exit_code == 0
    (row,) = sent.records(photo_lane.state)
    record = deliveries.read(ACCOUNT, delivery)
    assert row["text"] == photo_lane.blog.entries["99"]
    assert record["readback_sha"] == approval.hatena_body_sha256(row["text"])
    assert "readback_differs" not in record["history"][-1]


def test_T_S3_6b_updateの差分は承認の控えと比べ画像の行は変わったと出ない(photo_lane):
    path, delivery = approved(photo_lane)
    assert throw().exit_code == 0
    photo_lane.write("articles/a.md", ARTICLE.replace("本文。", "直した本文。"))
    upath = photo_lane.queue("u.md", op="update", delivery=delivery)
    rc, out, err = call(["approve", upath])
    assert "-本文。" in out and "+直した本文。" in out
    assert "-![豆]" not in out and "+![豆]" not in out and "[f:id:" not in out
    assert "上がっている（f:id:" in out
    # 控えが無ければ sent に倒し、1 行の断り
    for name in os.listdir(deliveries.dir_for(ACCOUNT)):
        if name.endswith(".body.md"):
            os.unlink(os.path.join(deliveries.dir_for(ACCOUNT), name))
    rc, out, err = call(["approve", upath])
    assert "画像の行は置き換えのため差が出ます" in out


# ---------------------------------------------------------------- T-S3-7a 更新で差し替え

def test_T_S3_7a_画像を差し替えると承認が古くなり_承認し直すと新しい画像だけ上がり古い台帳は残る(photo_lane):
    path, delivery = approved(photo_lane)
    assert throw().exit_code == 0
    upath, _ = approved(photo_lane, "u.md", op="update", delivery=delivery)
    photo_lane.write("images/bean.jpg", JPEG + b" v2")
    photo_lane.http.calls.clear()
    result = throw()
    assert result.action == "none" and {"file": "u.md", "reason": "approval_stale"} in result.rejections
    assert photo_calls(photo_lane.http) == []
    approve(upath)
    photo_lane.http.calls.clear()
    assert throw().exit_code == 0
    calls = photo_calls(photo_lane.http)
    assert calls.count(("POST", "/atom/post")) == 1 and ("GET", "/atom/edit/20260909100001") in calls
    assert not [c for c in photo_lane.http.calls if c["method"] in ("DELETE", "PUT") and FOTOLIFE in c["url"]]
    shas = {r["sha256"] for r in ledger(photo_lane.world)}
    assert {JPEG_SHA, PNG_SHA, sha_of(JPEG + b" v2")} <= shas   # 古い画像の台帳は残る
    assert photo_lane.blog.entries["99"].count("[f:id:") == 2


# ---------------------------------------------------------------- T-S3-8 見せる口

def test_T_S3_8a_dry_runはPOSTもGETも打たずログに上げるはずの数(photo_lane):
    path, delivery = approved(photo_lane)
    logs = []
    result = core.throw_once(ACCOUNT, production_flag=False, adapter_factory=None, now=NOW, log=logs.append)
    assert result.action == "skip"
    assert photo_calls(photo_lane.http) == []
    assert any("画像 2 件をフォトライフに上げるはず（うち 0 件は上がっている）" in line for line in logs)


def test_T_S3_8b_1段目に画像ごとの状態(photo_lane):
    path = photo_lane.queue()
    rc, out, err = call(["approve", path])
    assert out.count("未（公開の直前に上げる）") == 2
    approve(path)
    photo_lane.photo.post = lambda body: (
        (201, photo_lane.photo.photos[photo_lane.photo.create(posted(body)["title"])])
        if "bean" in posted(body)["title"] else (500, b""))
    assert throw().error == "fotolife_ambiguous"
    path2 = photo_lane.queue("b.md")
    rc, out, err = call(["approve", path2])
    assert "上がっている（f:id:lane-owner:" in out and "結果不明（次の run が照合する）" in out


def test_T_S3_8b_schedule_requestのpreviewに画像の行_2段目で上げない(photo_lane, key):
    context = key({ACCOUNT: "lane-a"})
    path = photo_lane.queue(publish_at=SOON)
    first = request(context, operation="schedule_request", draft_id=draft_id(path))
    assert first["stage"] == 1
    assert "images/bean.jpg" in first["preview"] and "未（公開の直前に上げる）" in first["preview"]
    done = request(context, operation="schedule_request", draft_id=draft_id(path), confirm=first["digest"])
    assert done["status"] == "approved"
    assert photo_lane.http.calls == [] and ledger(photo_lane.world) == []   # 2 段目では上げない


def test_T_S3_8b_MCPの道具とthth_meの操作にfotolifeが無い():
    """柵: 道具に足すと赤。"""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    server = (root / "mcp" / "server.py").read_text(encoding="utf-8")
    assert "fotolife" not in server
    for path in (root / "callback").rglob("*"):
        if path.is_file() and path.suffix in (".js", ".ts", ".mjs", ".json") and "node_modules" not in path.parts:
            assert "fotolife" not in path.read_text(encoding="utf-8", errors="ignore"), path


def test_deliveriesの1行に画像の数と状態(photo_lane):
    path, delivery = approved(photo_lane)
    assert throw().exit_code == 0
    rc, out, err = call(["deliveries", ACCOUNT])
    assert "画像 2 件（上がっている 2）" in out
    rc, out, err = call(["deliveries", ACCOUNT, "--json"])
    assert json.loads(out)["deliveries"][0]["images"][0]["fotolife_id"]


def test_thth_fotolifeは台帳を読むだけ(photo_lane):
    approved(photo_lane)
    assert throw().exit_code == 0
    photo_lane.http.calls.clear()
    rc, out, err = call(["fotolife", "hatena-main"])
    assert rc == 0 and out.count("uploaded") == 2 and JPEG_SHA[:12] in out
    rc, out, err = call(["fotolife", "hatena-main", "show", JPEG_SHA])
    assert rc == 0 and json.loads(out)["sha256"] == JPEG_SHA
    rc, out, err = call(["fotolife", "hatena-main", "--json"])
    assert json.loads(out)["n"] == 2
    assert photo_lane.http.calls == []
    rc, out, err = call(["fotolife", "no-such-principal"])
    assert rc == 2


def test_doctorはフォトライフの一覧と台帳の数を見せ_終了コードは変えない(photo_lane):
    rc, out, err = call(["doctor", ACCOUNT, "--json"])
    report = json.loads(out)
    assert report["fotolife"]["ok"] is True and report["fotolife"]["entries"] == 0
    assert report["fotolife"]["unknown"] == 0
    photo_lane.photo.feed = (401, b"")
    rc2, out, err = call(["doctor", ACCOUNT])
    assert "フォトライフ: 認可が通りません（401）" in out and rc2 == rc


# ---------------------------------------------------------------- T-S3-8c 主体の台帳の欄

@pytest.mark.parametrize("value", [
    {"max_image_bytes": 1024}, {"max_image_bytes": 65 * 1024 * 1024}, {"max_images_per_entry": 0},
    {"max_images_per_entry": 101}, {"daily_max_uploads": 501}, {"daily_max_uploads": "30"},
    {"feed_pages": 0}, {"unknown_key": 1}, "not an object"])
def test_T_S3_8c_fotolifeの欄の範囲外はAccountError(world, value):
    from thth import accounts
    world.edit_principal("hatena-main", fotolife=value)
    with pytest.raises(accounts.AccountError):
        principals.load_principal("hatena-main")


@pytest.mark.parametrize("folder", ["a/b", "x\x01", "a" * 65, " lead", "trail ", ""])
def test_T_S3_8c_fotolife_folderは断る(world, folder):
    from thth import accounts
    data = world.principal("hatena-main")
    data["blogs"][0]["fotolife_folder"] = folder
    world.write_principal(data)
    with pytest.raises(accounts.AccountError):
        principals.load_principal("hatena-main")


def test_T_S3_8c_既定値とブログごとのフォルダ(world):
    p = principals.load_principal("hatena-main")
    p.pop("fotolife", None)
    assert hatena_fotolife.limits(p) == {"max_image_bytes": 8 * 1024 * 1024, "max_images_per_entry": 20,
                                         "max_total_bytes": 64 * 1024 * 1024, "daily_max_uploads": 30,
                                         "feed_pages": 1}
    assert hatena_fotolife.folder_for(p, "a.hatenablog.com") == "thth"
    data = world.principal("hatena-main")
    data["blogs"][0]["fotolife_folder"] = "コーヒーの写真"
    world.write_principal(data)
    p = principals.load_principal("hatena-main")
    assert hatena_fotolife.folder_for(p, "a.hatenablog.com") == "コーヒーの写真"
    assert hatena_fotolife.folder_for(p, "b.hatenablog.com") == "thth"


def test_T_S3_8c_principal_setでfotolifeの欄を変える(world):
    rc, out, err = call(["principal", "set", "hatena-main", "fotolife.daily_max_uploads", "5", "--by", "tester"])
    assert rc == 0, (out, err)
    assert principals.load_principal("hatena-main")["fotolife"]["daily_max_uploads"] == 5
    rc, out, err = call(["principal", "set", "hatena-main", "fotolife.daily_max_uploads", "9999", "--by", "tester"])
    assert rc != 0 and "invalid_setting" in out + err


def test_フォルダはブログのfotolife_folderで送る(photo_lane):
    data = photo_lane.world.principal("hatena-main")
    data["blogs"][0]["fotolife_folder"] = "lane-a 写真"
    photo_lane.world.write_principal(data)
    approved(photo_lane)
    assert throw().exit_code == 0
    ups = [c for c in photo_lane.http.calls if c["url"] == f"{FOTOLIFE}/atom/post"]
    assert {posted(c["body"])["subject"] for c in ups} == {"lane-a 写真"}
