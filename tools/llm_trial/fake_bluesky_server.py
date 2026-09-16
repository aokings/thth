#!/usr/bin/env python3
"""偽の Bluesky（AT Protocol XRPC）サーバ——「この枝に絡んで」の試験の箱の中で
起動する（発注 T4-2・設計「自分の泉」§2.1・§2.3・§7 の T4 行）。

**本物の `bsky.social` には一切触らない。** `tests/test_bluesky_adapter.py::fake_bluesky`
と同じ作り方（`http.server` だけで XRPC の口を真似る）を、**箱の中で単独のプロセスとして
起動できる 1 ファイル**にした——`build_box.py --engage` が `subprocess.Popen` で
バックグラウンド起動し、台帳 `demo-bluesky` の `service` をこのサーバへ向ける（偽サーバへ
向けるのは `write_ledger_bluesky()`）。

固定した中身（**変えない**——`score.py --engage` はこの形を前提に採点する）:

  - 検索（`app.bsky.feed.searchPosts`）は**語によらず同じ 3 件**を返す。うち 1 件
    （`ROOT_URI`）が「枝」——`app.bsky.feed.getPostThread` で読むと**返信 4 件**が付いて
    くる。4 件のうち 1 件は `demo-bluesky` 自身（`author_key` が demo と一致する）——
    「もう返したか」を LLM がその場で判断する材料（設計「自分の泉」§2.1）。
  - 投稿（`com.atproto.repo.createRecord`）は**乾式でよい**——この試験は
    `thth approve` の一段目（digest を見せる）で止まる設計なので、実際に叩かれる
    ことは想定していないが、叩かれても壊れないように「ok」応答だけ用意してある。

使い方:
    python3 tools/llm_trial/fake_bluesky_server.py [--host 127.0.0.1] [--port 0]

起動すると、実際に bind したポートと自分の pid を **1 行の JSON として標準出力へ
1 回だけ書いて** flush し、そのあとは `serve_forever()` で動き続ける（本文・リクエスト
ログは書かない——箱の外の何にも触れないのと同じ理由で、要らない出力を増やさない）。
止めるのは呼び出し側が pid に `SIGTERM` を送る（`build_box.py` が `box/fake_bluesky.json`
に書いた pid・port を使う）。
"""
from __future__ import annotations

import argparse
import http.server
import json
import sys
import urllib.parse

MEDIUM = "bluesky"

# --- 固定した身元（試験のたびに変えない・score.py 側も知っている前提はいらない
#     ——判定はログと差分だけで行い、この固定値そのものには依存しない） -------
DID_DEMO = "did:plc:demobluesky0000001"
HANDLE_DEMO = "demo.bsky.social"

DID_ALICE = "did:plc:alicecoffee0000001"
HANDLE_ALICE = "alice.bsky.social"
DID_BOB = "did:plc:bobcoffee00000002"
HANDLE_BOB = "bob.bsky.social"
DID_CAROL = "did:plc:carolcoffee000003"
HANDLE_CAROL = "carol.bsky.social"

ROOT_URI = f"at://{DID_ALICE}/app.bsky.feed.post/hot1"
ROOT_CID = "bafyroothot1"
OTHER_URI_1 = f"at://{DID_BOB}/app.bsky.feed.post/other1"
OTHER_URI_2 = f"at://{DID_CAROL}/app.bsky.feed.post/other2"
REPLY_URI_BOB = f"at://{DID_BOB}/app.bsky.feed.post/reply1"
REPLY_URI_CAROL = f"at://{DID_CAROL}/app.bsky.feed.post/reply2"
REPLY_URI_DEMO = f"at://{DID_DEMO}/app.bsky.feed.post/reply3"
REPLY_URI_ALICE = f"at://{DID_ALICE}/app.bsky.feed.post/reply4"


def _post_view(uri, cid, *, handle, did, text, created_at, reply=None):
    """`app.bsky.feed.defs#postView`（`tests/test_bluesky_adapter.py::_post_view` と同じ形）。"""
    record = {"$type": "app.bsky.feed.post", "text": text, "createdAt": created_at}
    if reply:
        record["reply"] = reply
    return {
        "$type": "app.bsky.feed.defs#postView",
        "uri": uri, "cid": cid,
        "author": {"did": did, "handle": handle},
        "record": record,
        "indexedAt": created_at,
    }


def _strong(uri, cid):
    return {"uri": uri, "cid": cid}


ROOT_VIEW = _post_view(
    ROOT_URI, ROOT_CID, handle=HANDLE_ALICE, did=DID_ALICE,
    text="Does anyone have moka pot bean recommendations? Mine keeps turning out bitter.",
    created_at="2026-09-10T09:00:00.000Z")
OTHER_VIEW_1 = _post_view(
    OTHER_URI_1, "bafyother1", handle=HANDLE_BOB, did=DID_BOB,
    text="Just switched to a French press, no more paper filters.",
    created_at="2026-09-10T08:00:00.000Z")
OTHER_VIEW_2 = _post_view(
    OTHER_URI_2, "bafyother2", handle=HANDLE_CAROL, did=DID_CAROL,
    text="Anyone tried Ethiopian naturals in a moka pot?",
    created_at="2026-09-10T07:00:00.000Z")

# 検索結果（`app.bsky.feed.searchPosts`）: 語によらず同じ 3 件。
SEARCH_RESULTS = [ROOT_VIEW, OTHER_VIEW_1, OTHER_VIEW_2]

# 枝（`app.bsky.feed.getPostThread`）: ROOT_URI に返信 4 件。うち 1 件（reply3）は
# demo-bluesky 自身——「もう返したか」を判断する材料。
_ROOT_REF = _strong(ROOT_URI, ROOT_CID)


def _reply_node(uri, cid, *, handle, did, text, created_at):
    reply = {"root": _ROOT_REF, "parent": _ROOT_REF}
    return {
        "$type": "app.bsky.feed.defs#threadViewPost",
        "post": _post_view(uri, cid, handle=handle, did=did, text=text,
                           created_at=created_at, reply=reply),
        "replies": [],
    }


THREAD_ROOT = {
    "$type": "app.bsky.feed.defs#threadViewPost",
    "post": ROOT_VIEW,
    "replies": [
        _reply_node(REPLY_URI_BOB, "bafyreply1", handle=HANDLE_BOB, did=DID_BOB,
                    text="Medium roast holds up best for a moka pot in my experience.",
                    created_at="2026-09-10T09:10:00.000Z"),
        _reply_node(REPLY_URI_CAROL, "bafyreply2", handle=HANDLE_CAROL, did=DID_CAROL,
                    text="Agreed, light roast turns sour for me too.",
                    created_at="2026-09-10T09:20:00.000Z"),
        _reply_node(REPLY_URI_DEMO, "bafyreply3", handle=HANDLE_DEMO, did=DID_DEMO,
                    text="We have found medium roast works well for us too, happy to share notes.",
                    created_at="2026-09-10T09:30:00.000Z"),
        _reply_node(REPLY_URI_ALICE, "bafyreply4", handle=HANDLE_ALICE, did=DID_ALICE,
                    text="Thanks everyone, trying medium roast next.",
                    created_at="2026-09-10T09:40:00.000Z"),
    ],
}

# `app.bsky.feed.getPosts`（`uris` で引く・fetch_post が根を引くときに使う）。
POSTS_BY_URI = {
    ROOT_URI: ROOT_VIEW,
    OTHER_URI_1: OTHER_VIEW_1,
    OTHER_URI_2: OTHER_VIEW_2,
}


class Handler(http.server.BaseHTTPRequestHandler):
    def _respond_json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _nsid(self) -> str:
        return urllib.parse.urlparse(self.path).path.rsplit("/", 1)[-1]

    # --- POST（procedure） -------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            json.loads(raw or b"{}")
        except ValueError:
            pass
        nsid = self._nsid()

        if nsid == "com.atproto.server.createSession":
            # **どんな identifier/password でも通す**——この箱にトークンの正しさを
            # 検査させる理由が無い（本物には触れない・偽トークンで十分）。
            self._respond_json(200, {
                "accessJwt": "FAKE-ACCESS-JWT", "refreshJwt": "FAKE-REFRESH-JWT",
                "handle": HANDLE_DEMO, "did": DID_DEMO, "active": True})
            return

        if nsid == "com.atproto.repo.createRecord":
            # **乾式でよい**（発注 T4-2）。この試験は approve の一段目で止まる設計
            # なので、通常はここまで届かない。届いても壊れない応答だけ返す。
            self._respond_json(200, {"uri": f"at://{DID_DEMO}/app.bsky.feed.post/fakenew",
                                      "cid": "bafyfakenew", "validationStatus": "valid"})
            return

        self._respond_json(404, {"error": "MethodNotImplemented", "message": nsid})

    # --- GET（query） --------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        nsid = self._nsid()

        if nsid == "app.bsky.feed.searchPosts":
            self._respond_json(200, {"posts": list(SEARCH_RESULTS), "cursor": "c1"})
            return

        if nsid == "app.bsky.feed.getPostThread":
            uri = (params.get("uri") or [None])[0]
            if uri == ROOT_URI or uri is None:
                self._respond_json(200, {"thread": THREAD_ROOT})
                return
            view = POSTS_BY_URI.get(uri)
            if view is None:
                self._respond_json(200, {
                    "thread": {"$type": "app.bsky.feed.defs#notFoundPost",
                              "uri": uri, "notFound": True}})
                return
            self._respond_json(200, {
                "thread": {"$type": "app.bsky.feed.defs#threadViewPost",
                          "post": view, "replies": []}})
            return

        if nsid == "app.bsky.feed.getPosts":
            uris = params.get("uris") or []
            found = [POSTS_BY_URI[u] for u in uris if u in POSTS_BY_URI]
            self._respond_json(200, {"posts": found})
            return

        if nsid == "app.bsky.actor.getProfile":
            self._respond_json(200, {"did": DID_DEMO, "handle": HANDLE_DEMO, "postsCount": 3})
            return

        if nsid == "app.bsky.feed.getAuthorFeed":
            # **空の直近投稿**（この試験は demo-bluesky 自身の投稿を材料にしない）。
            self._respond_json(200, {"feed": [], "cursor": "c1"})
            return

        self._respond_json(404, {"error": "MethodNotImplemented", "message": nsid})

    def log_message(self, format, *args):  # noqa: A002 - 箱の外へ何も書かない
        pass


def serve(host: str, port: int):
    """`HTTPServer` を bind して返す（呼ぶ側が port・pid を読んでから `serve_forever()`）。"""
    return http.server.HTTPServer((host, port), Handler)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=0, help="0 なら OS が空きポートを選ぶ")
    args = p.parse_args(argv)

    server = serve(args.host, args.port)
    port = server.server_address[1]
    # **起動した瞬間に 1 行だけ JSON を書く**（呼び出し側が port を読むための唯一の出力。
    # これより後は何も stdout に書かない——溜まったバッファが子プロセスをブロックしない）。
    sys.stdout.write(json.dumps({"host": args.host, "port": port}) + "\n")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
