"""媒体の口へ投げる HTTP の**共通の作法**（セキュリティ監査 2026-09-14・P1-1）。

**見つかり方**: アダプタは `urllib.request.urlopen()` をそのまま呼んでいた。
`urllib` の既定の opener は **3xx を黙って追う**——しかも `Request` に足した
ヘッダ（`Authorization: Bearer <token>`）を**別ホストへもそのまま持って行く**。
乗っ取られたインスタンス・間に入った proxy・DNS を握られた経路のどれでも、
`302 Location: https://attacker.example/` の 1 行でトークンが外へ出る
（監査の再現 `redirect_leak.py`: 偽サーバ 2 本で `Authorization` が 2 本目に
届くことを確かめた）。

**直し方**: 追う先が**元と同じ scheme + host** のときだけ追う。違えば
`redirect_request()` が `None` を返す——`urllib` はそれを「この handler では
処理できない」と読んで `HTTPError` を上げるので、**黙って止まらず loud に失敗
する**（作法 5）。同一 origin でも `https` → `http` の格下げは拒む。

**ここは「どこへ投げるか」だけを見る。** 何を投げるか・応答をどう読むかは
アダプタの仕事（設計 §3.4「core は HTTP を解釈しない」の線はそのまま）。
"""
from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request


class RedirectBlocked(urllib.error.HTTPError):
    """別ホスト（または格下げ）へのリダイレクトを追わずに止めた。

    `HTTPError` を継ぐので、アダプタ側の `except urllib.error.HTTPError` は
    これまでどおり捕まえられる（既存の経路の意味を変えない）。
    """


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """scheme + host が変わるリダイレクトを追わない。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        # `Location` は相対でもよい（RFC 7231）。**元の URL を基準に解決してから**
        # 比べる——相対のまま文字列で見ると `//attacker.example/` を取り逃がす。
        new = urllib.parse.urlsplit(urllib.parse.urljoin(req.full_url, newurl))
        if new.scheme != old.scheme or new.netloc != old.netloc:
            raise RedirectBlocked(
                req.full_url, code,
                f"別のホストへのリダイレクトは追いません（{old.netloc} → "
                f"{new.scheme}://{new.netloc}）。**Authorization を持ち越しません。**",
                headers, fp)
        if old.scheme == "https" and new.scheme != "https":
            raise RedirectBlocked(
                req.full_url, code,
                "https から http への格下げリダイレクトは追いません",
                headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class EndpointRejected(ValueError):
    """Static pre-connection refusal; never includes the supplied endpoint."""


def validated_url(value, *, base=False):
    if not isinstance(value,str) or not value or any(ord(c)<=32 or ord(c)==127 for c in value):
        raise EndpointRejected('endpoint_invalid')
    try:
        parsed=urllib.parse.urlsplit(value)
        if parsed.scheme not in ('http','https'):
            raise EndpointRejected('endpoint_scheme_invalid: scheme は https を指定してください')
        if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError
        parsed.port
        if base and parsed.query:raise ValueError
        if parsed.scheme=='http' and not (os.environ.get('THTH_TEST_ALLOW_HTTP')=='1'
              and parsed.hostname in ('localhost','127.0.0.1','::1')):
            raise EndpointRejected('endpoint_http_forbidden: 平文 HTTP は使えません。https を指定してください')
    except EndpointRejected:raise
    except ValueError:raise EndpointRejected('endpoint_invalid') from None
    return value.rstrip('/') if base else value


class _HTTPOnlyOpener(urllib.request.OpenerDirector):
    def open(self,fullurl,*args,**kwargs):
        request=fullurl if isinstance(fullurl,urllib.request.Request) else None
        validated_url(request.full_url if request else fullurl)
        if request and (request.has_proxy() or getattr(request,'_tunnel_host',None)):
            raise EndpointRejected('endpoint_proxy_forbidden')
        return super().open(fullurl,*args,**kwargs)


def build_opener(redirect_handler=None):
    """Explicit HTTP(S) transport; urllib defaults never add file/FTP/data/proxy."""
    result=_HTTPOnlyOpener()
    for handler in (urllib.request.HTTPHandler(),urllib.request.HTTPSHandler(),
                    urllib.request.HTTPDefaultErrorHandler(),
                    redirect_handler or SameOriginRedirectHandler(),
                    urllib.request.HTTPErrorProcessor()):
        result.add_handler(handler)
    return result


_opener = None


def opener() -> urllib.request.OpenerDirector:
    global _opener
    if _opener is None:_opener=build_opener()
    return _opener


def urlopen(req, *, timeout: float):
    """Same-origin redirects, explicit HTTP(S), and account stop lease."""
    from . import leave_gate
    return leave_gate.urlopen(opener().open, req, timeout=timeout)
