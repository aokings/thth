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

import http.client
import ipaddress
import os
import socket
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


# 試験だけが立てる「loopback 以外へは出ない」印（サブプロセスの `thth` には monkeypatch が効かないので、
# `tests/conftest.run_thth` の既定の env で立てる・監査 S6 中-1）。本番のコードはこの名前を立てない。
TEST_LOOPBACK_ONLY_ENV = 'THTH_TEST_LOOPBACK_ONLY'
_LOOPBACK = ('localhost', '127.0.0.1', '::1')


def _test_loopback_only(url):
    if os.environ.get(TEST_LOOPBACK_ONLY_ENV) == '1' and urllib.parse.urlsplit(url).hostname not in _LOOPBACK:
        raise EndpointRejected('endpoint_test_loopback_only')


class _HTTPOnlyOpener(urllib.request.OpenerDirector):
    def open(self,fullurl,*args,**kwargs):
        request=fullurl if isinstance(fullurl,urllib.request.Request) else None
        validated_url(request.full_url if request else fullurl)
        _test_loopback_only(request.full_url if request else fullurl)
        if request and (request.has_proxy() or getattr(request,'_tunnel_host',None)):
            raise EndpointRejected('endpoint_proxy_forbidden')
        return super().open(fullurl,*args,**kwargs)


# ------------------------------------------------------------------ 内向きのアドレスを断る（5.16.0 監査 直したほうがよい 6）
# 宛先を利用者の台帳が決める口（WordPress のサイト）だけが使う。店の口（BASE・Shopify・STORES）は定数のホストなので
# 今の opener のまま。**名前を引いた答えを全部見て、1 つでも内向きならつながない。つなぐのは確かめた IP そのもの**
# （名前を引き直さないので、引いてからつなぐまでに答えを替える手 rebinding の窓が無い）。TLS の名前（SNI と証明書の
# 照合）と Host は元の名前のまま（http.client が `self.host` を使う）。試験は次の 2 つを偽に替える（外の網へ引かない）。
_getaddrinfo = socket.getaddrinfo
_connect = socket.create_connection
_NAT64 = ipaddress.ip_network('64:ff9b::/96')


def _embedded_v4(ip):
    """IPv6 の中に入った IPv4（::ffff:a.b.c.d・NAT64・6to4・Teredo）。無ければ空。"""
    if ip.version != 6:
        return []
    out = [ip.ipv4_mapped, ip.sixtofour, *(ip.teredo or ())]
    if ip in _NAT64:
        out.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return [v for v in out if v is not None]


# 自前の内向きの表（監査 5.18.1 直したほうがよい 3）。`ipaddress.is_global` の表は Python の版で変わり（2024 年の直しで
# 64:ff9b:1::/48・2002::/16 などが足された）、IPv4 互換の IPv6（::/96）・site-local（fec0::/10）は今の版でも外の網と
# 答える。IANA の特別な用途の表（IPv4・IPv6 Special-Purpose Address Registry・2025 年までの足し）のうち外の網でない
# 範囲と、廃止された範囲を、版によらずここで内向きに数える。is_global の答えと「どちらかが内向きなら内向き」で合わせる。
# 外の網の範囲で、特別な用途でも globally reachable のもの（AS112・AMT など）は is_global に任せる。
# 2001:20::/28（ORCHIDv2）は表では globally reachable だが、場所の番地でなく名札で、ブログの口は無いので内向きに数える。
INTERNAL_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    # IPv4
    '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12',
    '192.0.0.0/24', '192.0.2.0/24', '192.88.99.0/24', '192.168.0.0/16', '198.18.0.0/15', '198.51.100.0/24',
    '203.0.113.0/24', '224.0.0.0/4', '240.0.0.0/4',
    # IPv6
    '::/96',            # 未指定・loopback・IPv4 互換（廃止）
    '::ffff:0:0:0/96',  # IPv4 翻訳（SIIT）
    '64:ff9b:1::/48',   # NAT64 の私用
    '100::/64',         # 捨てる口
    '100:0:0:1::/64',   # 偽の接頭辞
    '2001::/32',        # Teredo（中の IPv4 は別に見る）
    '2001:2::/48',      # ベンチマーク
    '2001:10::/28',     # ORCHID（廃止）
    '2001:20::/28',     # ORCHIDv2
    '2001:db8::/32',    # 文書用
    '2002::/16',        # 6to4（すべて）
    '3fff::/20',        # 文書用
    '5f00::/16',        # SRv6 の SID
    'fc00::/7',         # 私用（ULA）
    'fe80::/10',        # link-local
    'fec0::/10',        # site-local（廃止）
    'ff00::/8',         # マルチキャスト
))


def _in_internal_table(ip) -> bool:
    return any(ip.version == net.version and ip in net for net in INTERNAL_NETWORKS)


def internal_address(text, *, allow_loopback=False) -> bool:
    """外の網（IANA の globally reachable）でないアドレスか。loopback・私用の網・link-local（169.254 の
    メタデータの口を含む）・CGNAT・予約・未指定・マルチキャスト・IPv4 互換の IPv6・site-local。IPv6 の中の IPv4 も見る。
    `is_global` と自前の表（`INTERNAL_NETWORKS`）のどちらかが内向きと言えば内向き。読めない形は内向きに数える。"""
    try:
        ip = ipaddress.ip_address(str(text).split('%', 1)[0])
    except ValueError:
        return True
    for one in (ip, *_embedded_v4(ip)):
        if allow_loopback and one.is_loopback:
            continue
        if not one.is_global or one.is_multicast or _in_internal_table(one):
            return True
    return False


def ip_literal(host) -> bool:
    """ホストが IP の形か（`192.168.1.10`・`[::1]`・`0x7f.0.0.1`・`127.1` のように OS が IP と読む形も）。"""
    text = str(host or '').strip('[]')
    try:
        ipaddress.ip_address(text.split('%', 1)[0])
        return True
    except ValueError:
        pass
    try:
        socket.inet_aton(text)   # 網へは出ない（字の形だけを読む）
        return True
    except (OSError, ValueError):
        return False


def _public_connect(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kwargs):
    host, port = address
    allow_loopback = os.environ.get('THTH_TEST_ALLOW_HTTP') == '1'
    try:
        answers = _getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except UnicodeError:
        raise EndpointRejected('endpoint_invalid') from None
    targets = []
    for family, _type, _proto, _name, sockaddr in answers:
        if family not in (socket.AF_INET, socket.AF_INET6):
            raise EndpointRejected('endpoint_internal_address')
        if internal_address(sockaddr[0], allow_loopback=allow_loopback):
            raise EndpointRejected('endpoint_internal_address')
        if (sockaddr[0], sockaddr[1]) not in targets:
            targets.append((sockaddr[0], sockaddr[1]))
    if not targets:
        raise OSError('no address')
    last = None
    for target in targets:
        try:
            return _connect(target, timeout, source_address)
        except OSError as exc:
            last = exc
    raise last


class _PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _public_connect


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _public_connect


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PublicHTTPConnection, req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


def build_opener(redirect_handler=None, *, public_only=False):
    """Explicit HTTP(S) transport; urllib defaults never add file/FTP/data/proxy.

    `public_only=True` は、名前を引いた先が内向きならつながず、確かめた IP へつなぐ（WordPress の口）。"""
    result=_HTTPOnlyOpener()
    plain, tls = ((_PublicHTTPHandler(), _PublicHTTPSHandler()) if public_only
                  else (urllib.request.HTTPHandler(), urllib.request.HTTPSHandler()))
    for handler in (plain, tls,
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


_public_opener = None


def public_opener() -> urllib.request.OpenerDirector:
    """内向きのアドレスへつながない opener（宛先を台帳が決める口・今は WordPress）。"""
    global _public_opener
    if _public_opener is None:_public_opener=build_opener(public_only=True)
    return _public_opener


def urlopen(req, *, timeout: float, via=None):
    """Same-origin redirects, explicit HTTP(S), and account stop lease.

    `via` は `build_opener` で作った別の opener（店を読む口の「リダイレクトを追わない」opener・
    `thth/shop/wire.py`）。無ければ今の同じホストだけ追う opener。**`build_opener` の作った型だけを受ける**
    （素の urllib の opener を渡すと、http と https だけ・proxy を断る・試験の loopback の印の守りが外れるため・
    監査 5.17.0 直したほうがよい 2）。
    """
    from . import leave_gate
    if via is not None and type(via) is not _HTTPOnlyOpener:
        raise EndpointRejected('endpoint_opener_invalid')
    return leave_gate.urlopen((via or opener()).open, req, timeout=timeout)
