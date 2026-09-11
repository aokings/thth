"""200 で返ってきた `error` を「取れた」ことにしない（監査 2026-09-11）。

**本物の Threads API には触らない。** 偽の HTTP サーバを立てて確かめる。

なぜ要るか: 採取側（`collect.py`）は「例外なら記録を書かない・成功なら
`kind: fetch` の行を書く」で**成功と失敗を分けている。** だが 200 のまま
`error` が入っていると `body.get("data") or []` が静かに 0 件になり、
**「取れて 0 件」として台帳に残る。** 返信が何回 0 件でも、それが事実なのか
取れていないのかを、あとから区別できなくなる。
"""
from __future__ import annotations

import contextlib
import http.server
import json
import threading

import pytest

from thth.adapters import threads as threads_mod


class _Handler(http.server.BaseHTTPRequestHandler):
    payload = {"data": []}

    def do_GET(self) -> None:  # noqa: N802（http.server の命名規則）
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002
        pass


@contextlib.contextmanager
def _server(payload: dict):
    handler = type("Handler", (_Handler,), {"payload": payload})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _adapter(base_url):
    return threads_mod.ThreadsAdapter(base_url=base_url, access_token="fake-token",
                                       user_id="12345", timeout=2.0)


def test_200でもerrorが入っていれば失敗として上げる():
    """**静かに 0 件にしない。** 上げれば採取側が `errors` に残して記録を書かない。"""
    payload = {"error": {"message": "(#10) Application does not have permission",
                          "code": 10}}
    with _server(payload) as base_url:
        with pytest.raises(RuntimeError) as e:
            _adapter(base_url).replies("POST1")
    assert "error を返しました" in str(e.value)


def test_本当に0件のときは0件として返す():
    """**取れて 0 件**は成功。ここまで止めると、今度は事実が記録できない。"""
    with _server({"data": []}) as base_url:
        assert _adapter(base_url).replies("POST1") == []


def test_数の取得でも同じ():
    with _server({"error": {"message": "token expired"}}) as base_url:
        with pytest.raises(RuntimeError):
            _adapter(base_url).insights("POST1")


def test_失敗したら台帳に取得の行を書かない(tmp_path, isolated_account_factory):
    """**これが本題。** 失敗と「取れて 0 件」が台帳で区別できることを確かめる。

    運用セッションから「`replies: 0` が成功なのか無言の失敗なのか区別できない
    のでは」と問われた（2026-09-11）。**台帳の作りとしては区別できている**——
    失敗すると `kind: fetch` の行そのものが書かれず、`errors` に残る。
    この test はその区別を固定する。
    """
    import datetime
    from tests.test_collect import NOW, FakeAdapter, _rows, _setup
    from thth import collect as collect_mod

    pair, account = _setup(tmp_path, isolated_account_factory,
                            posted_at="2026-09-03T12:00:00+09:00")   # 168 時間前
    failing = FakeAdapter(replies_rows=[], fail={"replies"})
    result = collect_mod.run_collect(account["name"], adapter=failing, now=NOW,
                                      log=lambda _l: None)
    assert result == 1, "失敗を終了コードに出していない"
    assert _rows(pair["work"], "data/sns/replies/POST1.ndjson") == [], \
        "失敗したのに取得の行を書いている（0 件の成功と区別できなくなる）"

    healthy = FakeAdapter(replies_rows=[])
    later = NOW + datetime.timedelta(minutes=10)
    assert collect_mod.run_collect(account["name"], adapter=healthy, now=later,
                                    log=lambda _l: None) == 0
    fetched = [r for r in _rows(pair["work"], "data/sns/replies/POST1.ndjson")
               if r.get("kind") == "fetch"]
    assert len(fetched) == 1 and fetched[0]["replies"] == 0, fetched


# --- 逆監査で出た穴（2026-09-11）: 形が違うものを 0 件と読まない ---------------

@pytest.mark.parametrize("payload,なぜ", [
    ({}, "data がそもそも無い"),
    ({"data": None}, "data が null"),
    ({"ページ": "壊れた応答"}, "別の形の object"),
])
def test_dataが取れない応答を0件と読まない(payload, なぜ):
    """**「取れて 0 件」と区別できないものを、0 件にしない。**

    逆監査（別セッションに反証を取りに行かせた・2026-09-11）で出た。
    `body.get("data") or []` と書いていたので、上の 3 つがどれも静かに 0 件に
    なっていた。200 が返っていても、**中身が期待した形でなければ取れていない。**
    """
    with _server(payload) as base_url:
        with pytest.raises(RuntimeError) as e:
            _adapter(base_url).replies("POST1")
    assert "data" in str(e.value), なぜ


def test_dataが配列でなければ件数として数えない():
    """`{"data": "文字列"}` が**そのまま返っていた。**

    採取側は `for row in replies` で回すので 1 文字ずつになり、`len()` が
    そのまま件数になる筋もあった。**形が違うものを件数にしない。**
    """
    with _server({"data": "これは配列ではない"}) as base_url:
        with pytest.raises(RuntimeError) as e:
            _adapter(base_url).replies("POST1")
    assert "配列ではありません" in str(e.value)


def test_本当の0件はこれまでどおり通る():
    """**止めすぎない。** `{"data": []}` は取れて 0 件。"""
    with _server({"data": []}) as base_url:
        assert _adapter(base_url).replies("POST1") == []


# --- clicks の形（外部調査 2026-09-11 Codex・公式の例そのまま） ---------------

# 公式ドキュメントの応答例（`developers.facebook.com/documentation/threads/insights`・
# 2026-09-12 取得）。**この形を取り出せず、clicks が None になって捨てられていた。**
CLICKS_EXAMPLE = {"data": [{
    "name": "clicks", "period": "day",
    "link_total_values": [{"value": 11, "link_url": "https://ai.meta.com/blog/"}],
    "title": "clicks", "description": "The number of times users clicked on a link.",
    "id": "37602215421583/insights/clicks/day"}]}


def test_clicksを取りこぼさない():
    """**`link_total_values` を見ていなかった。**

    `_metric_value()` は `total_value` と `values` しか見ておらず、公式の例の
    clicks=11 に対して `None` を返していた。`account_insights()` は None の指標を
    結果に入れないので、**clicks は一度も記録されていなかった可能性が高い**
    （本番で何件失われたかは未確認）。外部調査（Codex）の指摘を、こちらでも
    同じ入力で再現して直した。
    """
    with _server(CLICKS_EXAMPLE) as base_url:
        got = _adapter(base_url).account_insights(
            "12345", since="2026-09-10", until="2026-09-11")
    assert got["clicks"] == 11


def test_URLごとの内訳を捨てない():
    """**投稿ごとの clicks は取れないが、URL ごとの内訳は取れる。**

    こちらの資料に「どの URL かも分からない」と書いていたのは誤りだった
    （外部調査で判明）。ただし**URL と投稿は一対一とは限らない**ので、
    投稿へ割り当てはしない——内訳として持つだけ。
    """
    with _server(CLICKS_EXAMPLE) as base_url:
        got = _adapter(base_url).account_insights(
            "12345", since="2026-09-10", until="2026-09-11")
    assert got["clicks_by_url"] == [
        {"link_url": "https://ai.meta.com/blog/", "value": 11}]


def test_これまでの2つの形も引き続き取れる():
    """**止めすぎない。** `total_value` と `values` の形は変わらず取れる。"""
    for row, expected in (
            ({"name": "views", "total_value": {"value": 323}}, 323),
            ({"name": "views", "values": [{"value": 164}]}, 164)):
        with _server({"data": [row]}) as base_url:
            got = _adapter(base_url).account_insights(
                "12345", since="2026-09-10", until="2026-09-11")
        assert got["views"] == expected
        assert "views_by_url" not in got
