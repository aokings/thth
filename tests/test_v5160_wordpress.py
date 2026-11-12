"""5.16.0: WordPress（自分で置く型）に記事を出す口の柵（設計 ショップとWordPressの口 §4・設計の直し 1〜9）。

はてなブログの配信の道（queue の原稿 → 承認の指紋 → 二段の柵 → timer が 1 回出す → 記録）に乗せた 2 つめの
出し先。送る先は手元の偽の WordPress（127.0.0.1 の http.server・`THTH_TEST_ALLOW_HTTP=1` の柵の内）で、本物の
サイトには何も送らない。鍵は試験の値（本物ではない）。

柵:
- 承認を通らない公開は無い（未承認・承認のあとに状態を下書きから公開に替えた・サイトを替えた → 送らない）
- 鍵を出力・ファイル（台帳・ログ・記録）に出さない／引数で鍵を取らない
- 本文は狭い部分集合だけ HTML にし、外れる記法・手元の画像・生の HTML は前向きに断る
- 新しいファイルは release の関門の危ない領域にある
"""
from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.conftest import init_git_pair, restore_real_hatena_adapters, run_git
from thth import approval, cli, core, deliveries, hatena_entry, jst, principals, sent, wp_html
from thth.adapters import wordpress

NOW = datetime.datetime(2026, 9, 9, 10, 0, 0, tzinfo=jst.JST)
ACCOUNT = "lane-w-wordpress"
PRINCIPAL = "wp-main"
WP_USER = "editor1"
# 試験の鍵（本物ではない・アプリケーションパスワードの形だけ合わせた値）。
PASSWORD = "abcd EFGH ijkl MNOP qrst UVWX"
ARTICLE = "# アラビカ\n\n値動きの **まとめ**。\n次の行。\n\n- 一つ\n- 二つ\n\n[出典](https://example.com/a)\n"


# ------------------------------------------------------------------ 偽の WordPress

class FakeWordPress:
    """REST の 1 部分だけを真似る（posts・categories・tags・users/me）。呼ばれた要求を `calls` に残す。"""

    def __init__(self):
        self.calls = []
        self.posts = {}
        self.next_id = 41
        self.terms = {"categories": {1: "未分類", 5: "コーヒー"}, "tags": {9: "価格"}}
        self.mode = None
        self.roles = ["editor"]
        self.kses = None   # 本文を削る（WordPress の kses の真似）
        self.home = None   # WordPress の「サイトアドレス」（home）。入口と違うホストの構えの真似（5.18.2）
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _post_json(self, pid):
        p = self.posts[pid]
        return {"id": pid, "link": f"{self.home or self.base}/?p={pid}", "status": p["status"],
                "title": {"raw": p["title"], "rendered": p["title"]},
                "content": {"raw": p["content"], "rendered": "<div>" + p["content"] + "</div>"},
                "categories": p["categories"], "tags": p["tags"],
                "date_gmt": "2026-09-09T01:00:00", "modified_gmt": "2026-09-09T01:00:00"}

    def _handler(self):
        wp = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, data, *, raw=None):
                body = raw if raw is not None else json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html" if raw is not None else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _route(self, method):
                url = urlsplit(self.path)
                query = parse_qs(url.query)
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else None
                wp.calls.append((method, url.path, payload, self.headers.get("Authorization")))
                if wp.mode == "rest_closed":
                    return self._send(404, None, raw=b"<html>Not Found</html>")
                if wp.mode == "security":
                    return self._send(403, None, raw=b"<html>Blocked by firewall</html>")
                auth = self.headers.get("Authorization")
                if wp.mode == "drop_auth" or not auth:
                    return self._send(401, {"code": "rest_not_logged_in", "message": "x"})
                import base64
                user, _, password = base64.b64decode(auth.split(" ", 1)[1]).decode().partition(":")
                if user != WP_USER or password != PASSWORD:
                    return self._send(401, {"code": "incorrect_password", "message": "x"})
                path = url.path
                if path == "/wp-json/wp/v2/users/me":
                    me = {"id": 2, "username": WP_USER, "roles": wp.roles}
                    if wp.home:
                        me["link"] = f"{wp.home}/author/{WP_USER}/"   # 著者の頁は home の上に組まれる
                    return self._send(200, me)
                for kind in ("categories", "tags"):
                    if path == f"/wp-json/wp/v2/{kind}":
                        rows = wp.terms[kind]
                        if "include" in query:
                            ids = [int(i) for i in query["include"][0].split(",")]
                            return self._send(200, [{"id": i, "name": rows[i]} for i in ids if i in rows])
                        name = query.get("search", [""])[0]
                        return self._send(200, [{"id": i, "name": n} for i, n in rows.items() if name in n])
                if path == "/wp-json/wp/v2/posts" and method == "POST":
                    if wp.mode == "no_permission":
                        return self._send(403, {"code": "rest_cannot_create", "message": "x"})
                    pid = wp.next_id
                    wp.next_id += 1
                    content = payload["content"] if wp.kses is None else wp.kses(payload["content"])
                    wp.posts[pid] = {"status": payload.get("status", "draft"), "title": payload["title"],
                                     "content": content, "categories": payload.get("categories") or [1],
                                     "tags": payload.get("tags") or []}
                    return self._send(201, wp._post_json(pid))
                if path.startswith("/wp-json/wp/v2/posts/"):
                    pid = int(path.rsplit("/", 1)[1])
                    if pid not in wp.posts:
                        return self._send(404, {"code": "rest_post_invalid_id", "message": "x"})
                    if method == "POST":
                        post = wp.posts[pid]
                        post.update({"title": payload["title"], "content": payload["content"],
                                     "categories": payload.get("categories") or [1],
                                     "tags": payload.get("tags") or []})
                        if "status" in payload:
                            post["status"] = payload["status"]
                    return self._send(200, wp._post_json(pid))
                return self._send(404, {"code": "rest_no_route", "message": "x"})

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

        return Handler


# ------------------------------------------------------------------ 世界

def manuscript(*, op="create", title="アラビカの値動き", categories="[コーヒー, \"#価格\"]", delivery=None,
               publish_at="2026-09-09T09:00:00+09:00", extra=None):
    lines = ["---", "thth: 1", f"account: {ACCOUNT}", "status: draft", f"publish_at: {publish_at}",
             "article: lane-w/arabica", "article_source: articles/a.md", "form: full",
             f"title: {title}", f"categories: {categories}", f"op: {op}"]
    if delivery:
        lines.append(f"delivery: {delivery}")
    for key, value in (extra or {}).items():
        lines.append(f"{key}: {value}")
    lines += ["---", "", "（本文は article_source から）", ""]
    return "\n".join(lines)


class Lane:
    def __init__(self, root, repo, wp, token):
        self.root, self.repo, self.wp, self.token = root, repo, wp, token

    def write(self, rel, text, *, commit=True):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        if commit:
            run_git(self.repo, ["add", "--", rel])
            run_git(self.repo, ["commit", "-m", "test"])
            run_git(self.repo, ["push"])
        return path

    def queue(self, name="a.md", **kwargs):
        return self.write(f"docs/sns/queue/{name}", manuscript(**kwargs))

    @property
    def state(self):
        return str(self.root / "state" / ACCOUNT)

    def principal_path(self):
        return self.root / "principals" / f"{PRINCIPAL}.json"

    def edit_principal(self, **values):
        data = json.loads(self.principal_path().read_text(encoding="utf-8"))
        data.update(values)
        self.principal_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def wp():
    server = FakeWordPress()
    yield server
    server.close()


@pytest.fixture
def lane(tmp_path, monkeypatch, wp):
    root = tmp_path / "root"
    (root / "accounts").mkdir(parents=True)
    (root / "principals").mkdir(mode=0o700)
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(root / "accounts"))
    monkeypatch.delenv("THTH_PRINCIPALS_DIR", raising=False)
    restore_real_hatena_adapters(monkeypatch)   # 本物の WordPress の adapter（送る先は偽のサーバ）
    token = tmp_path / "secrets" / f"{PRINCIPAL}.token"
    token.parent.mkdir(mode=0o700)
    examples = os.path.join(os.path.dirname(__file__), "..", "accounts.example")
    with open(os.path.join(examples, "principal-wordpress.json"), encoding="utf-8") as f:
        p = json.load(f)
    p.update({"principal": PRINCIPAL, "wp_user": WP_USER, "token": str(token),
              "blogs": [{"blog_id": "wp.test", "lanes": ["lane-w"], "site_url": wp.base}]})
    (root / "principals" / f"{PRINCIPAL}.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    os.chmod(root / "principals" / f"{PRINCIPAL}.json", 0o600)
    pair = init_git_pair(root / "repos" / "lane-w", seed_content="", seed_name=".gitkeep")
    with open(os.path.join(examples, "wordpress.json"), encoding="utf-8") as f:
        a = json.load(f)
    a.update({"account": ACCOUNT, "project": "lane-w", "principal": PRINCIPAL, "blog_id": "wp.test",
              "handle": WP_USER, "token": str(token), "env": str(token) + ".env", "repo_dir": pair["work"],
              "production": True, "scheduled": True})
    (root / "accounts" / f"{ACCOUNT}.json").write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    w = Lane(root, pair["work"], wp, token)
    w.write("articles/a.md", ARTICLE)
    return w


def call(argv, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        old = None
        if stdin is not None:
            import sys
            old, sys.stdin = sys.stdin, io.StringIO(stdin)
        try:
            rc = cli.main(argv)
        except SystemExit as exc:
            rc = exc.code
        finally:
            if old is not None:
                import sys
                sys.stdin = old
    return rc, out.getvalue(), err.getvalue()


def put_key(lane):
    rc, out, err = call(["principal", "auth", PRINCIPAL, "--by", "tester", "--stdin"], stdin=PASSWORD + "\n")
    assert rc == 0, (out, err)
    return out + err


def approve(path):
    rc, out, err = call(["approve", path])
    assert rc == 1, (out, err)
    digest = [line.split(": ", 1)[1].strip() for line in out.splitlines() if line.startswith("digest: ")][-1]
    rc, out2, err2 = call(["approve", path, "--confirm", digest, "--by", "tester"])
    assert rc == 0, (out2, err2)
    return out


def throw():
    return core.throw_once(ACCOUNT, production_flag=True, now=NOW)


def fm_of(path):
    from thth import queuefile
    return queuefile.parse(path).front_matter


def posts_sent(wp):
    return [c for c in wp.calls if c[0] == "POST" and c[1].startswith("/wp-json/wp/v2/posts")]


# ------------------------------------------------------------------ 出す道

def test_承認からtimerで公開して記事のIDとURLを記録する(lane):
    put_key(lane)
    path = lane.queue()
    shown = approve(path)
    assert "wp.test" in shown and lane.wp.base in shown   # 1 段目は送る先のサイトを見せる
    delivery = fm_of(path)["delivery"]
    result = throw()
    assert (result.action, result.exit_code) == ("post", 0), result
    (post,) = posts_sent(lane.wp)
    assert post[2]["status"] == "publish"
    assert post[2]["categories"] == [5] and post[2]["tags"] == [9]
    assert "<p>値動きの <strong>まとめ</strong>。<br />\n次の行。</p>" in post[2]["content"]
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "published"
    assert record["entry_id"] == "41" and record["published_url"] == f"{lane.wp.base}/?p=41"
    assert record["edit_uri"] == f"{lane.wp.base}/wp-json/wp/v2/posts/41"
    assert record["readback_categories"] == ["コーヒー", "#価格"]
    fm = fm_of(path)
    assert (fm["status"], fm["post_id"], fm["url"]) == ("posted", "41", f"{lane.wp.base}/?p=41")
    (row,) = sent.records(lane.state)
    assert (row["kind"], row["blog_id"], row["delivery"]) == ("entry_create", "wp.test", delivery)
    assert throw().action == "none"   # 2 度は出さない


def test_下書きで出せる(lane):
    put_key(lane)
    path = lane.queue(extra={"send_as": "draft"})
    approve(path)
    assert throw().action == "post"
    (post,) = posts_sent(lane.wp)
    assert post[2]["status"] == "draft"
    assert deliveries.read(ACCOUNT, fm_of(path)["delivery"])["send_as"] == "draft"


def test_直すは読み戻しが基準と同じときだけ送り状態は変えない(lane):
    put_key(lane)
    path = lane.queue(extra={"send_as": "draft"})
    approve(path)
    throw()
    delivery = fm_of(path)["delivery"]
    lane.write("articles/a.md", ARTICLE + "\n追記。\n")
    upd = lane.queue("b.md", op="update", delivery=delivery)
    approve(upd)
    assert throw().action == "post"
    last = posts_sent(lane.wp)[-1]
    assert last[1] == "/wp-json/wp/v2/posts/41" and "status" not in last[2]
    assert lane.wp.posts[41]["status"] == "draft"
    assert "追記" in lane.wp.posts[41]["content"]


def test_外で手直しされていれば上書きしない(lane):
    put_key(lane)
    path = lane.queue()
    approve(path)
    throw()
    delivery = fm_of(path)["delivery"]
    lane.wp.posts[41]["content"] += "<p>画面で足した</p>"
    lane.write("articles/a.md", ARTICLE + "\n追記。\n")
    upd = lane.queue("b.md", op="update", delivery=delivery)
    approve(upd)
    result = throw()
    assert result.action == "conflict"
    assert len(posts_sent(lane.wp)) == 1   # 直しは送っていない


def test_kses_で削られても初回の読み戻しが台帳の基準になる(lane):
    put_key(lane)
    lane.wp.kses = lambda html: html.replace("<strong>", "").replace("</strong>", "")
    path = lane.queue()
    approve(path)
    throw()
    record = deliveries.read(ACCOUNT, fm_of(path)["delivery"])
    assert record["readback_sha"] == approval.hatena_body_sha256(lane.wp.posts[41]["content"])
    assert record["history"][-1].get("readback_differs") is True


# ------------------------------------------------------------------ 柵: 承認を通らない公開は無い

def test_柵_承認していない原稿は送らない(lane):
    put_key(lane)
    lane.queue()
    assert throw().action == "none"
    assert posts_sent(lane.wp) == []


def test_柵_承認のあとに下書きを公開に替えると送らない(lane):
    put_key(lane)
    path = lane.queue(extra={"send_as": "draft"})
    approve(path)
    text = open(path, encoding="utf-8").read().replace("send_as: draft\n", "send_as: publish\n")
    lane.write("docs/sns/queue/a.md", text)
    result = throw()
    assert result.action != "post"
    assert posts_sent(lane.wp) == []


def test_柵_承認のあとにサイトを替えると送らない(lane):
    put_key(lane)
    path = lane.queue()
    approve(path)
    lane.edit_principal(blogs=[{"blog_id": "wp.test", "lanes": ["lane-w"],
                                "site_url": lane.wp.base.replace("127.0.0.1", "localhost")}])
    assert throw().action != "post"
    assert posts_sent(lane.wp) == []


def test_柵_指紋は状態とサイトを含みはてなの指紋と別():
    base = dict(account="a", site_url="https://s.example", blog_id="s.example", op="create", entry_id=None,
                base_readback_sha=None, title="t", categories=[], body="<p>x</p>", article="x", form="full",
                status="draft", publish_at="2026-09-09T09:00:00+09:00")
    draft = approval.compute_wp_entry_sha(**base)
    assert draft != approval.compute_wp_entry_sha(**{**base, "status": "publish"})
    assert draft != approval.compute_wp_entry_sha(**{**base, "site_url": "https://t.example"})
    assert approval.HATENA_ENTRY_VERSION == "thth-hatena-entry-1"   # はてなの指紋は変えない
    from thth import fingerprint_golden
    assert fingerprint_golden.hatena_current("wp_entry") == fingerprint_golden.GOLDEN["wp_entry"][1]


# ------------------------------------------------------------------ 柵: 鍵

def test_柵_鍵は出力と台帳とログと記録に出さない(lane):
    said = put_key(lane)
    path = lane.queue()
    approve(path)
    throw()
    secrets = (PASSWORD, PASSWORD.replace(" ", ""))
    assert not any(s in said for s in secrets)
    assert oct(os.stat(lane.token).st_mode & 0o777) == "0o600"
    found = []
    for base, _dirs, files in os.walk(lane.root):
        for name in files:
            full = os.path.join(base, name)
            if os.path.realpath(full) == os.path.realpath(lane.token) or "/.git/" in full:
                continue
            try:
                text = open(full, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            if any(s in text for s in secrets):
                found.append(full)
    assert found == []


def test_柵_鍵は引数で取らない():
    parser = cli.build_parser()
    for argv in (["principal", "auth", PRINCIPAL, "--by", "t", PASSWORD],
                 ["principal", "auth", PRINCIPAL, "--by", "t", "--password", PASSWORD],
                 ["principal", "auth", PRINCIPAL, "--by", "t", "--stdin", PASSWORD]):
        with pytest.raises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(argv)
    # `principal auth` の旗は値を取らない（名前と --by だけが値）。
    sub = next(a for a in parser._actions if getattr(a, "choices", None) and "principal" in a.choices)
    auth = next(a for a in sub.choices["principal"]._actions if getattr(a, "choices", None)
                and "auth" in a.choices).choices["auth"]
    takes_value = sorted(a.dest for a in auth._actions if a.option_strings and a.nargs != 0)
    assert takes_value == ["by"]


def test_鍵が違う_落とされる_RESTが閉じている_セキュリティの部品を分けて返す(lane):
    put_key(lane)
    adapter = wordpress.factory({"blog_id": "wp.test", "token": str(lane.token), "media": "wordpress",
                                 "principal": PRINCIPAL}, principals.load_principal(PRINCIPAL))
    for mode, code in (("drop_auth", "wordpress_auth_header_dropped"), ("rest_closed", "wordpress_rest_unavailable"),
                       ("security", "wordpress_blocked_by_security")):
        lane.wp.mode = mode
        assert adapter.whoami()["error"] == code, mode
    lane.wp.mode = "no_permission"
    result = adapter.create_entry(title="t", body="<p>x</p>", categories=[], images=[])
    assert (result.ok, result.ambiguous, result.error) == (False, False, "wordpress_permission_missing")
    lane.wp.mode = None
    lane.token.write_text(json.dumps({"schema": wordpress.TOKEN_SCHEMA, "wp_user": WP_USER,
                                      "application_password": "zzzz zzzz zzzz zzzz zzzz zzzz"}))
    bad = wordpress.factory({"blog_id": "wp.test", "token": str(lane.token), "media": "wordpress",
                             "principal": PRINCIPAL}, principals.load_principal(PRINCIPAL))
    assert bad.whoami()["error"] == "wordpress_auth_rejected"


def test_管理者の鍵は断らずに編集者の鍵を勧める(lane):
    lane.wp.roles = ["administrator"]
    said = put_key(lane)
    assert "編集者" in said and "管理者の鍵" in said


def test_httpは手元の試しだけ_柵は緩めない(lane, monkeypatch):
    assert not principals.valid_site_url("http://wp.example.com", "wp.example.com")
    assert not principals.valid_site_url("https://other.example.com", "wp.example.com")
    assert principals.valid_site_url("https://wp.example.com", "wp.example.com")
    assert principals.valid_site_url("http://127.0.0.1:8080", "wp.test")
    put_key(lane)
    lane.wp.calls.clear()
    monkeypatch.delenv("THTH_TEST_ALLOW_HTTP")
    adapter = wordpress.factory({"blog_id": "wp.test", "token": str(lane.token), "media": "wordpress",
                                 "principal": PRINCIPAL}, principals.load_principal(PRINCIPAL))
    assert adapter.whoami()["error"] == "wordpress_https_required"
    assert lane.wp.calls == []   # 鍵を http で送っていない


# ------------------------------------------------------------------ 本文の部分集合

def test_本文の見出しは題のh1と重ならないよう1段下げる():
    assert wp_html.render("# 一") == "<h2>一</h2>"
    assert wp_html.render("##### 五") == "<h6>五</h6>"
    assert wp_html.render("###### 六") == "<h6>六</h6>"
    assert "<h1>" not in wp_html.render("# a\n\n## b\n\n### c")

def test_本文は狭い部分集合だけをHTMLにする():
    out = wp_html.render("## 見出し\n\n本文 **太字** と *斜め* & <\n次\n\n- a\n- b\n\n1. c\n\n"
                         "[x](https://e.example)\n\n```py\nx = '<a>'\n```\n`c<b>`")
    assert out == ("<h3>見出し</h3>\n<p>本文 <strong>太字</strong> と <em>斜め</em> &amp; &lt;<br />\n次</p>\n"
                   "<ul><li>a</li><li>b</li></ul>\n<ol><li>c</li></ol>\n"
                   "<p><a href=\"https://e.example\">x</a></p>\n"
                   "<pre><code class=\"language-py\">x = '&lt;a&gt;'</code></pre>\n<p><code>c&lt;b&gt;</code></p>")


@pytest.mark.parametrize("text, code", [
    ("> 引用", "wp_markup_unsupported"), ("<b>x</b>", "wp_markup_unsupported"),
    ("<script>alert(1)</script>", "wp_markup_unsupported"), ("![a](https://e.example/a.png)", "wp_markup_unsupported"),
    ("| a | b |", "wp_markup_unsupported"), ("---", "wp_markup_unsupported"),
    ("- a\n  - b", "wp_markup_unsupported"), ("[x](javascript:alert)", "wp_link_scheme_unsupported"),
    ("[x](javascript:alert(1))", "wp_markup_unsupported"),
    ("```\n閉じない", "wp_markup_unsupported"),
])
def test_外れる記法は送らずに断る(text, code):
    with pytest.raises(wp_html.WpMarkupError) as exc:
        wp_html.render(text)
    assert exc.value.code == code


def test_外れる記法と手元の画像はlintが前向きに断る(lane):
    png = os.path.join(lane.repo, "images", "a.png")
    os.makedirs(os.path.dirname(png), exist_ok=True)
    import base64
    with open(png, "wb") as f:   # 1 画素の PNG
        f.write(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="))
    run_git(lane.repo, ["add", "--", "images/a.png"])
    lane.write("articles/a.md", "# 題\n\n> 引用です\n")
    rc, out, err = call(["lint", lane.queue()])
    assert rc != 0 and "wp_markup_unsupported" in out + err and "引用" in out + err
    lane.write("articles/a.md", "# 題\n\n本文\n\n![絵](../images/a.png)\n")
    rc, out, err = call(["lint", lane.queue("b.md")])
    assert rc != 0 and "wp_image_unsupported" in out + err, out + err


def test_予約と消すはこの版では受けない(lane):
    rc, out, err = call(["lint", lane.queue(extra={"send_as": "future"})])
    assert rc != 0 and "send_as_unsupported" in out + err
    rc, out, err = call(["lint", lane.queue(op="delete", delivery="dl_0123456789ab")])
    assert rc != 0 and "op_unsupported_for_media" in out + err


def test_はてなの口座は今までどおり公開だけ():
    fm = {"send_as": "draft"}
    with pytest.raises(hatena_entry.EntryError) as exc:
        hatena_entry.target(fm, {"media": "hatenablog"}, "create", [], "x")
    assert exc.value.code == "send_as_unsupported"
    assert hatena_entry.target({}, {"media": "hatenablog"}, "create", [], "x")["body"] == "x"


# ------------------------------------------------------------------ 関門

def test_新しいファイルは危ない領域にある():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "release_gate_for_test", os.path.join(os.path.dirname(__file__), "..", "tools", "release_gate.py"))
    gate = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["release_gate_for_test"] = gate
    spec.loader.exec_module(gate)
    for path in ("thth/adapters/wordpress.py", "thth/wp_html.py", "thth/approval.py"):
        assert gate.areas_of(path), path


def test_出し先の表はそろっている():
    from thth import adapters, article_media
    kinds_media = {spec["media"] for spec in principals.ARTICLE_KINDS.values()}
    assert kinds_media == set(article_media.ARTICLE_MEDIA) == set(adapters.ARTICLE_ADAPTERS)


def test_doctorは鍵の利用者と役割を見せ管理者の鍵なら編集者を勧める(lane):
    put_key(lane)
    rc, out, err = call(["doctor", ACCOUNT])
    assert rc == 0, out + err
    assert "鍵の利用者 editor1・役割 editor" in out + err
    lane.wp.roles = ["administrator"]
    rc, out, err = call(["doctor", ACCOUNT])
    assert rc == 0 and "管理者の鍵です" in out + err and "編集者" in out + err
    assert PASSWORD not in out + err


def test_直す原稿の1段目は状態を変えないと見せる(lane):
    put_key(lane)
    path = lane.queue(extra={"send_as": "draft"})
    assert "timer が 下書き で送る" in approve(path)
    throw()
    lane.write("articles/a.md", ARTICLE + "\n追記。\n")
    shown = approve(lane.queue("b.md", op="update", delivery=fm_of(path)["delivery"]))
    assert "timer が 今の公開の状態のまま で送る" in shown
