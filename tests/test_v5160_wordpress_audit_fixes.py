"""5.16.0 監査（docs/監査_5.16.0_wordpress_2026-11-12.md）の直しの柵。

- 危ない 1: 私用の字（U+E000）で変換が止まらない・リンクの文字の中のコードで例外にならない（例外は WpMarkupError だけ）
- 危ない 2: 題の生の HTML を断り、`<`・`>`・`&` はエスケープして送る。送る題が指紋に入り、変換の規則が変われば古い承認は通らない
- 直したほうがよい 1: 下書きで出した配信を告知に使わない
- 直したほうがよい 2: WordPress の口の断りに、はてなの文が出ない（符丁がすべて目録にある）
- 直したほうがよい 4: 日本語に続く斜体・打ち消し・字下げのコード・ショートコード・URL だけの行
- 直したほうがよい 5: リンクの行き先の制御の字は変換だけで断る
"""
from __future__ import annotations

import contextlib
import random
import re
import signal
from pathlib import Path

import pytest

from tests.test_v5160_wordpress import (ACCOUNT, ARTICLE, approve, call, fm_of, lane, posts_sent, put_key,  # noqa: F401
                                        throw, wp)
from thth import announce, deliveries, hatena_entry, refusal, select, wp_html
from thth.adapters import wordpress

ROOT = Path(__file__).resolve().parent.parent


class Hang(Exception):
    pass


@contextlib.contextmanager
def within(seconds=3):
    def ring(*_):
        raise Hang(f"{seconds} 秒で返らない")
    old = signal.signal(signal.SIGALRM, ring)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


# ------------------------------------------------------------------ 危ない 1

@pytest.mark.parametrize("text", ["本文 \ue000 です", "\ue000", "本文 \ue0005\ue001 です", "\ue001", "`\ue000`",
                                  "[x\ue000](https://e.example)", "\uf8ff"])
def test_柵_私用の字は断りになり時間の内に返る(text):
    with within(3):
        with pytest.raises(wp_html.WpMarkupError) as exc:
            wp_html.render(text)
    assert exc.value.code == "wp_markup_unsupported" and "私用の字" in exc.value.detail


@pytest.mark.parametrize("text, want", [
    ("[`a`](https://e.example)", '<p><a href="https://e.example"><code>a</code></a></p>'),
    ("- [`a`](https://e.example)\n- b", '<ul><li><a href="https://e.example"><code>a</code></a></li><li>b</li></ul>'),
    ("前 `x` と [`y` と **z**](https://e.example) 後",
     '<p>前 <code>x</code> と <a href="https://e.example"><code>y</code> と <strong>z</strong></a> 後</p>'),
    ("**[x](https://e.example)**", '<p><strong><a href="https://e.example">x</a></strong></p>'),
])
def test_柵_リンクの文字の中のコードは例外にならず変換される(text, want):
    with within(3):
        assert wp_html.render(text) == want


def test_柵_どんな入力でも有限の時間で終わり例外はWpMarkupErrorだけ():
    rng = random.Random(5160)
    alphabet = ["`", "[", "]", "(", ")", "*", "\\", "\ue000", "\ue001", "0", "1", "a", "あ", "https://e.x/",
                "-", " ", "\n", "<", ">", "!", "#", "_", "|", "\t", "    ", ":", "\x01", "[gallery]"]
    with within(10):
        for _ in range(4000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 40)))
            try:
                out = wp_html.render(text)
            except wp_html.WpMarkupError:
                continue
            assert "\ue000" not in out and "\ue001" not in out, text


def test_柵_承認のあとに私用の字を足してもtimerは止まらず送らない(lane):
    put_key(lane)
    path = lane.queue()
    approve(path)
    lane.write("articles/a.md", ARTICLE + "\nアイコン \ue000 の字。\n")
    with within(5):
        result = throw()
    assert result.action != "post"
    assert posts_sent(lane.wp) == []
    with within(5):
        rc, out, err = call(["lint", path])
    assert rc != 0 and "wp_markup_unsupported" in out + err and "私用の字" in out + err


def test_リンクの文字の中のコードはlintを通る(lane):
    lane.write("articles/a.md", "# 題\n\n[`name`](https://e.example/a) を見る。\n")
    rc, out, err = call(["lint", lane.queue()])
    assert rc == 0, out + err


# ------------------------------------------------------------------ 危ない 2

@pytest.mark.parametrize("title", ["<b>太字</b>の題", "<img src=x onerror=alert(1)>", "vector<int> の話"])
def test_柵_題の生のHTMLはlintと承認で断られPOSTが出ない(lane, title):
    put_key(lane)
    path = lane.queue(title=title)
    rc, out, err = call(["lint", path])
    assert rc != 0 and "wp_markup_unsupported" in out + err and "題の中の HTML" in out + err
    rc, out, err = call(["approve", path])
    assert rc not in (0, 1) or "digest: " not in out, out + err
    assert throw().action != "post"
    assert posts_sent(lane.wp) == []


def test_柵_題の山かっことアンパサンドはエスケープして送り台帳も同じ字(lane):
    put_key(lane)
    path = lane.queue(title="A&B < C の値動き")
    shown = approve(path)
    assert "A&amp;B &lt; C の値動き" in shown   # 1 段目は送る題を見せる
    assert throw().action == "post"
    (post,) = posts_sent(lane.wp)
    assert post[2]["title"] == "A&amp;B &lt; C の値動き"
    record = deliveries.read(ACCOUNT, fm_of(path)["delivery"])
    assert record["title"] == "A&amp;B &lt; C の値動き" == record["readback_title"]
    assert throw().action == "none"


def test_柵_送る題が指紋に入りはてなの題は変えない(lane):
    put_key(lane)
    path = lane.queue(title="A&B")
    from thth import accounts, queuefile
    fm = queuefile.parse(path).front_matter
    cfg = accounts.load_account(ACCOUNT)
    m = hatena_entry.material(fm, cfg, ACCOUNT)
    assert m["title"] == "A&amp;B"
    raw = dict(account=ACCOUNT, site_url=m["site_url"], blog_id=m["blog_id"], op="create", entry_id=None,
               base_readback_sha=None, title="A&B", categories=m["categories"], body=m["body"],
               article=m["article"], form=m["form"], status="publish", publish_at=m["publish_at"])
    from thth import approval
    assert m["approved_sha"] == approval.compute_wp_entry_sha(**dict(raw, title="A&amp;B"))
    assert m["approved_sha"] != approval.compute_wp_entry_sha(**raw)
    # はてなの出し先は題を変換しない（はてなの指紋の材料は今までどおり）。
    assert hatena_entry.target({"title": "A&B"}, {"media": "hatenablog"}, "create", [], "x")["title"] == "A&B"


@pytest.mark.parametrize("table, rule", [("TITLE_RENDER", lambda t: t + "（新）"),
                                         ("BODY_RENDER", lambda b: wp_html.render(b) + "\n<p>新しい規則</p>")])
def test_柵_変換の規則が変われば古い承認は通らない(lane, monkeypatch, table, rule):
    put_key(lane)
    path = lane.queue(title="A&B")
    approve(path)
    monkeypatch.setitem(getattr(hatena_entry, table), "wordpress", rule)
    assert throw().action != "post"
    assert posts_sent(lane.wp) == []


# ------------------------------------------------------------------ 直したほうがよい 1

DELIVERY = "dl_0123456789ab"


def _target(monkeypatch, record):
    monkeypatch.setattr(announce, "find_delivery", lambda cfg, delivery: (ACCOUNT, record))


def test_柵_下書きで出した配信は告知が解決せずheldになる(monkeypatch):
    _target(monkeypatch, {"state": "published", "published_url": "https://wp.test/?p=41", "send_as": "draft",
                          "article": "lane-w/arabica"})
    told = announce.resolve({"announces": DELIVERY}, {"project": "lane-w"})
    assert told.url is None and told.reason == "announces_unresolved: target_draft"
    assert select.held_category(told.reason) == "announces_unresolved"
    assert select.waiting_target(told.reason) is None
    _target(monkeypatch, {"state": "published", "published_url": "https://wp.test/2026/a/", "article": "lane-w/arabica"})
    assert announce.resolve({"announces": DELIVERY}, {"project": "lane-w"}).url == "https://wp.test/2026/a/"


def test_柵_下書きの配信を結ぶ告知はlintが前向きに断る(monkeypatch):
    _target(monkeypatch, {"state": "wanted", "send_as": "draft", "article": "lane-w/arabica"})
    errors = announce.lint_errors({"announces": DELIVERY}, "読む {article_url}", {"project": "lane-w"}, hatena=False)
    (line,) = [e for e in errors if e.startswith("announces_target_draft: ")]
    said = refusal.parts("announces_target_draft")
    assert said and "WordPress の画面で記事を公開" in said["fix"] and said["better"]


# ------------------------------------------------------------------ 直したほうがよい 2

_CODE_SITES = re.compile(r'(?:error=|WordPressError\(|_unavailable\(|ValueError\(|return |else )"([a-z][a-z0-9_]+)"')
# 口が返すが配信の道（hatena_publish）が受けて扱う内向きの符丁。
_HANDLED = {"wordpress_route_unknown"}


def _adapter_codes():
    src = (ROOT / "thth" / "adapters" / "wordpress.py").read_text(encoding="utf-8")
    return {c for c in _CODE_SITES.findall(src) if "_" in c and c not in _HANDLED} | {"wordpress_http_500"}


def test_柵_WordPressの口の符丁はすべて目録にありはてなの文が出ない():
    codes = _adapter_codes()
    assert {"wordpress_redirect_blocked", "wordpress_token_missing", "entry_id_invalid", "edit_uri_mismatch",
            "wordpress_response_invalid", "wordpress_rate_limited"} <= codes
    wrong = []
    for code in sorted(codes):
        said = refusal.parts(code)
        if said is None:
            wrong.append(f"{code}: 目録に無い")
            continue
        text = " ".join(filter(None, (said["why"], said["fix"], said["better"])))
        if ("はてな" in text and "WordPress" not in text) or "bookmark" in text or "ブックマーク" in text:
            wrong.append(f"{code}: はてなの文 {text[:60]}")
        if not said["better"]:
            wrong.append(f"{code}: better が無い")
    assert wrong == []
    plain = {c for c in codes if c.startswith("wordpress_") and c != "wordpress_http_500"}
    assert plain - {"wordpress_route_unknown"} <= set(wordpress.WP_REFUSALS)


def test_番号の付くWordPressのHTTPの答えは1つの項目で3つを返す():
    for code in ("wordpress_http_400", "wordpress_http_500", "wordpress_http_502"):
        said = refusal.parts(code)
        assert said and said["code"] == code and "wordpress_http_<番号>" in said["why"]


def test_鍵の無いWordPressの口座は自分の符丁で断る(lane):
    rc, out, err = call(["doctor", ACCOUNT])
    assert "wordpress_token_missing" in out + err and "はてな ID" not in out + err


# ------------------------------------------------------------------ 直したほうがよい 4

@pytest.mark.parametrize("text, want", [
    ("**太字**と*斜体*", "<p><strong>太字</strong>と<em>斜体</em></p>"),
    ("値は*大事*です", "<p>値は<em>大事</em>です</p>"),
    ("2*3*4 と a*b*c", "<p>2*3*4 と a*b*c</p>"),
    ("\\*字\\* と \\_ と C:\\Users", "<p>*字* と _ と C:\\Users</p>"),
    ('[gallery ids="1,2"]', '<p>&#91;gallery ids="1,2"]</p>'),
    ("`[gallery]` の書き方", "<p><code>&#91;gallery]</code> の書き方</p>"),
    ("```\n[gallery]\n```", "<pre><code>&#91;gallery]</code></pre>"),
    ("https://www.youtube.com/watch?v=x",
     '<p><a href="https://www.youtube.com/watch?v=x">https://www.youtube.com/watch?v=x</a></p>'),
    ("1) 一\n2) 二", "<ol><li>一</li><li>二</li></ol>"),
])
def test_柵_部分集合の外を黙って字のまま送らない_変換するもの(text, want):
    assert wp_html.render(text) == want


@pytest.mark.parametrize("text, detail", [
    ("段落\n\n    x = 1", "字下げのコード"), ("\tx = 1", "字下げのコード"),
    ("[x](https://e.example/`a`)", "リンクの行き先の中"),
])
def test_柵_部分集合の外を黙って字のまま送らない_断るもの(text, detail):
    with pytest.raises(wp_html.WpMarkupError) as exc:
        wp_html.render(text)
    assert exc.value.code == "wp_markup_unsupported" and detail in exc.value.detail


# ------------------------------------------------------------------ 直したほうがよい 5

@pytest.mark.parametrize("url", ["\x01javascript:alert%281%29", "\x00javascript:x", "java\x0bscript:x",
                                 "\x7fjavascript:x", "\x85javascript:x", "a:b/c", "@x", "(x"[1:] + ":y"])
def test_柵_リンクの行き先の制御の字と怪しい相対は変換だけで断る(url):
    with pytest.raises(wp_html.WpMarkupError) as exc:
        wp_html.render(f"[x]({url})")
    # 空白の仲間の制御の字（\x0b）はリンクの形が崩れて先に断られる（どちらでも送らない）。
    assert exc.value.code == ("wp_markup_unsupported" if url[4:5] == "\x0b" else "wp_link_scheme_unsupported")


@pytest.mark.parametrize("url", ["https://e.example/a", "/a/b", "#h", "./a", "../a", "?q=1", "記事/a", "a/b:c",
                                 "mailto:a@e.example"])
def test_通る行き先(url):
    assert f'href="{url}"' in wp_html.render(f"[x]({url})")
