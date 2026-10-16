"""4.0 S3 の試験（`tests/test_v400_s3_*.py`）が共有する、偽物のフォトライフと画像つきの記事。

- HTTP は `tests/hatena_s2_http.py` の `fake_http`（`httpsafe.urlopen` の差し替え）を広げ、
  `https://f.hatena.ne.jp` への呼び出しを偽物のフォトライフ（`Fotolife`）に、ブログへの呼び出しを
  S2 の偽物のブログ（`Blog`）に渡す。**本物のはてなには 1 回も出ない。**
- adapter は本物（`hatenablog.factory`・`fotolife.factory`）。S1 の `lane` が差し込む偽物のフォトライフ
  （`FakeFotolife`）は外す。
- 画像は試験の中で作る小さなバイト列（先頭のバイト列だけ本物）。秘密の値は明らかな偽物だけ。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import pytest

from tests.hatena_s1_flow_helpers import ACCOUNT, NOW, approve, call, fm_of, lane  # noqa: F401
from tests.hatena_s1_helpers import world  # noqa: F401
from tests.hatena_s2_http import (BLOG, COLLECTION, FAKE_CONSUMER, FAKE_TOKEN, HATENA_ID, fake_http,  # noqa: F401
                                  put_secrets)
from tests.test_v400_s2_adapter import Blog
from thth import core, deliveries, hatena_fotolife, inflight, sent

FOTOLIFE = "https://f.hatena.ne.jp"
ATOM03 = "http://purl.org/atom/ns#"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF fake jpeg"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake png body"
GIF = b"GIF89a" + b"fake gif body"
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 fake"
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
ARTICLE = "# アラビカ\n\n![豆](../images/bean.jpg)\n\n本文。\n\n![葉](../images/leaf.png \"葉\")\n"


def photo_xml(fid, *, title="t", hatena_id=HATENA_ID, ext="j", syntax=None, with_edit=True,
              misspelled_subject=False, broken=False, doctype=False, with_syntax=True,
              namespace=ATOM03) -> bytes:
    """公式の例の形（Atom 0.3・`service.edit`・`hatena:syntax`・`hatena:imageurl`）の 1 枚。"""
    head = '<?xml version="1.0" encoding="utf-8"?>\n'
    if doctype:
        head += '<!DOCTYPE entry [<!ENTITY x "y">]>\n'
    subject_tag = "dc:subjetct" if misspelled_subject else "dc:subject"
    subject_close = "dc:subject" if broken else subject_tag
    syntax = syntax or f"f:id:{hatena_id}:{fid}{ext}:image"
    return (head + f'<entry xmlns="{namespace}" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            f'xmlns:hatena="http://www.hatena.ne.jp/info/xmlns#">'
            f"<title>{escape(title)}</title>"
            f'<link rel="alternate" type="text/html" href="http://f.hatena.ne.jp/{hatena_id}/{fid}"/>'
            + (f'<link rel="service.edit" type="application/x.atom+xml" '
               f'href="http://f.hatena.ne.jp/atom/edit/{fid}" title="{escape(title)}"/>' if with_edit else "")
            + f"<issued>2026-09-09T10:00:00+09:00</issued><author><name>{hatena_id}</name></author>"
            f'<generator url="https://thth.me/" version="1">thth</generator>'
            f"<{subject_tag}>thth</{subject_close}>"
            f"<id>tag:hatena.ne.jp,2005:fotolife-{hatena_id}-{fid}</id>"
            f"<hatena:imageurl>http://f.hatena.ne.jp/images/fotolife/l/{hatena_id}/20260909/{fid}.jpg</hatena:imageurl>"
            f"<hatena:imageurlsmall>http://f.hatena.ne.jp/images/fotolife/l/{hatena_id}/20260909/{fid}_m.gif"
            "</hatena:imageurlsmall>"
            + (f"<hatena:syntax>{syntax}</hatena:syntax>" if with_syntax else "")
            + "</entry>").encode("utf-8")


def photo_feed(photos) -> bytes:
    body = "".join(re.sub(r"^<\?xml[^>]*\?>\s*", "", p.decode("utf-8")) for p in photos)
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="{ATOM03}" '
            f'xmlns:hatena="http://www.hatena.ne.jp/info/xmlns#"><title>fotolife</title>{body}</feed>').encode("utf-8")


def posted(body: bytes) -> dict:
    """送った Atom 0.3 の XML の欄。"""
    root = ET.fromstring(body)
    content = root.find("{%s}content" % ATOM03)
    return {"root": root.tag, "title": root.find("{%s}title" % ATOM03).text,
            "mode": content.get("mode"), "type": content.get("type"), "content": content.text,
            "subject": root.find("{http://purl.org/dc/elements/1.1/}subject").text,
            "generator": root.find("{%s}generator" % ATOM03).text}


class Fotolife:
    """写真を覚えている偽物のフォトライフ。`post`・`get`・`feed` に答えを差し込める。

    各差し込みは `(status, bytes)`・例外・`callable(body) -> (status, bytes)` のどれか。
    """

    def __init__(self, hatena_id=HATENA_ID):
        self.hatena_id = hatena_id
        self.photos = {}
        self.next_id = 20260909100000
        self.post = None
        self.get = None
        self.feed = None

    def _answer(self, value, body):
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            value = value(body)
        if isinstance(value, BaseException):
            raise value
        status, raw = value
        return status, {"Content-Type": "application/xml"}, raw

    def create(self, title):
        fid = str(self.next_id)
        self.next_id += 1
        self.photos[fid] = photo_xml(fid, title=title, hatena_id=self.hatena_id)
        return fid

    def __call__(self, method, url, headers, body):
        if method == "POST" and url == f"{FOTOLIFE}/atom/post":
            if self.post is not None:
                return self._answer(self.post, body)
            fid = self.create(posted(body)["title"])
            return 201, {"Location": f"{FOTOLIFE}/atom/edit/{fid}"}, self.photos[fid]
        if method == "GET" and url == f"{FOTOLIFE}/atom/feed":
            if self.feed is not None:
                return self._answer(self.feed, body)
            return 200, {}, photo_feed(list(reversed(list(self.photos.values()))))
        match = re.fullmatch(re.escape(FOTOLIFE) + r"/atom/edit/([0-9]+)", url)
        if method == "GET" and match:
            if self.get is not None:
                return self._answer(self.get, body)
            fid = match.group(1)
            if fid not in self.photos:
                return 404, {}, b"Not Found"
            return 200, {}, self.photos[fid]
        raise AssertionError(f"unexpected fotolife call {method} {url}")


def route(blog, photo):
    def handler(method, url, headers, body):
        if url.startswith(FOTOLIFE + "/"):
            return photo(method, url, headers, body)
        return blog(method, url, headers, body)
    return handler


@pytest.fixture
def photo_lane(lane, fake_http, monkeypatch):  # noqa: F811
    """画像 2 枚（JPEG・PNG）の記事・偽物の token と consumer・本物の adapter（偽物の HTTP）。"""
    monkeypatch.setattr(hatena_fotolife, "ADAPTER_FACTORY", None)
    lane.write("images/bean.jpg", JPEG, commit=False)
    lane.write("images/leaf.png", PNG, commit=False)
    lane.write("articles/a.md", ARTICLE, commit=False)
    from tests.conftest import run_git
    run_git(lane.repo, ["add", "-A"])
    run_git(lane.repo, ["commit", "-m", "s3 article"])
    run_git(lane.repo, ["push"])
    put_secrets(lane.world)
    lane.blog = Blog()
    lane.photo = Fotolife()
    lane.http = fake_http(route(lane.blog, lane.photo))
    return lane


def throw(*, logs=None):
    return core.throw_once(ACCOUNT, production_flag=True, adapter_factory=None, now=NOW,
                           log=(logs.append if logs is not None else None))


def approved(lane, name="a.md", **kwargs):
    path = lane.queue(name, **kwargs)
    approve(path)
    return path, fm_of(path)["delivery"]


def states(delivery):
    return [row["state"] for row in deliveries.read(ACCOUNT, delivery)["history"]]


def photo_calls(http):
    return [(c["method"], c["url"][len(FOTOLIFE):]) for c in http.calls if c["url"].startswith(FOTOLIFE)]


def blog_calls(http):
    return [(c["method"], c["url"]) for c in http.calls if not c["url"].startswith(FOTOLIFE)]


def ledger(world, principal="hatena-main"):
    rows, broken = hatena_fotolife.list_records(principal)
    assert broken == []
    return rows


def sha_of(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def nothing_written(lane, delivery):
    """画像の段で止まった run は inflight・sent・配信の台帳の history を 1 行も増やさない（T-S3-5d）。"""
    assert inflight.read(lane.state) is None
    assert sent.records(lane.state) == []
    assert states(delivery) == ["wanted"]


__all__ = ["ACCOUNT", "NOW", "approve", "call", "fm_of", "lane", "world", "fake_http", "put_secrets",
           "BLOG", "COLLECTION", "FAKE_CONSUMER", "FAKE_TOKEN", "HATENA_ID", "Blog", "FOTOLIFE", "ATOM03",
           "JPEG", "PNG", "GIF", "WEBP", "SVG", "ARTICLE", "photo_xml", "photo_feed", "posted", "Fotolife",
           "route", "photo_lane", "throw", "approved", "states", "photo_calls", "blog_calls", "ledger",
           "sha_of", "nothing_written"]
