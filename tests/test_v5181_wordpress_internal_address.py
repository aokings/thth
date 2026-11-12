"""5.16.0 監査（docs/監査_5.16.0_wordpress_2026-11-12.md）直したほうがよい 6 の柵: WordPress の口は内向きのアドレスへ
鍵つきの要求を出さない（設計 ショップとWordPressの口 §5「VM に載せる日までに」）。

- 台帳の時点: 試しの印（`THTH_TEST_ALLOW_HTTP=1`）が無ければ、site_url の loopback・IP の形のホスト、
  IP の形の blog_id（site_url が無いとき `https://<blog_id>` になる）を `site_url_invalid` で断る。
- 送る時点: 名前を引いた先に内向き（loopback・私用の網・link-local・CGNAT・予約・マルチキャスト・IPv6 の中の
  IPv4 が内向き）が 1 つでもあれば、つながずに `wordpress_endpoint_rejected`。つなぐのは確かめた IP そのもの
  （名前を引き直さない・rebinding の窓を作らない）。TLS の名前（SNI・証明書の照合）と Host は元の名前のまま。
- 外の網へは引かない（名前解決は `httpsafe._getaddrinfo`、接続は `httpsafe._connect` を偽に替える）。
"""
from __future__ import annotations

import socket

import pytest

from thth import httpsafe, principals, refusal_catalog
from thth.adapters import wordpress

PUBLIC_V4 = "93.184.215.14"
PUBLIC_V6 = "2606:4700::6810:84e5"


def infos(*ips, port=443):
    out = []
    for ip in ips:
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        sockaddr = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
        out.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
    return out


class Net:
    """偽の名前解決と接続。引いた名前・つないだ先を覚える（実際にはつながない）。"""

    def __init__(self, monkeypatch, answers):
        self.answers = list(answers)   # 引くたびに 1 つずつ（尽きたら最後の答え）
        self.resolved, self.connected = [], []
        monkeypatch.setattr(httpsafe, "_getaddrinfo", self.getaddrinfo)
        monkeypatch.setattr(httpsafe, "_connect", self.connect)

    def getaddrinfo(self, host, port, *args, **kwargs):
        self.resolved.append(host)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return infos(*answer, port=port)

    def connect(self, address, *args, **kwargs):
        self.connected.append(address)
        raise ConnectionRefusedError("偽の接続（試験はつながない）")


def adapter(blog_id="wp.example.com", site_url=None):
    row = {"blog_id": blog_id, "lanes": ["lane-w"]}
    if site_url:
        row["site_url"] = site_url
    principal = {"principal": "wp-p", "kind": "wordpress", "wp_user": "editor", "blogs": [row]}
    return wordpress.WordPressAdapter({"blog_id": blog_id, "media": "wordpress"}, principal,
                                      token={"application_password": "abcd" * 6})


@pytest.fixture
def prod(monkeypatch):
    """本番の既定（試しの印なし）。"""
    monkeypatch.delenv("THTH_TEST_ALLOW_HTTP", raising=False)
    monkeypatch.delenv(httpsafe.TEST_LOOPBACK_ONLY_ENV, raising=False)


# ------------------------------------------------------------------ 台帳の時点

@pytest.mark.parametrize("url, blog_id", [
    ("https://localhost", "wp.test"), ("https://127.0.0.1", "wp.test"), ("https://[::1]", "wp.test"),
    ("http://127.0.0.1:8080", "wp.test"), ("https://192.168.1.10", "192.168.1.10"),
    ("https://10.0.0.5", "10.0.0.5"), ("https://169.254.169.254", "169.254.169.254"),
    ("https://0x7f.0.0.1", "0x7f.0.0.1"), ("https://8.8.8.8", "8.8.8.8"), ("https://wp.localhost", "wp.localhost"),
])
def test_台帳_試しの印が無ければloopbackとIPの形のsite_urlを断る(prod, url, blog_id):
    assert not principals.valid_site_url(url, blog_id)


def test_台帳_試しの印があればloopbackの偽のWordPressは今までどおり通る():
    assert principals.valid_site_url("http://127.0.0.1:8080", "wp.test")
    assert principals.valid_site_url("http://localhost:8080", "wp.test")
    assert principals.valid_site_url("https://wp.example.com/blog", "wp.example.com")
    # 試しの印があっても、私用の網の IP の形は通さない（偽の WordPress は loopback だけ）。
    assert not principals.valid_site_url("https://192.168.1.10", "192.168.1.10")


def _ledger(blog_id, **row):
    return {"principal": "wp-p", "kind": "wordpress", "wp_user": "editor", "token": "/x/wp-p.token",
            "auth": "app_password", "blogs": [{"blog_id": blog_id, "lanes": ["lane-w"], **row}]}


@pytest.mark.parametrize("blog_id", ["192.168.1.10", "10.0.0.5", "169.254.169.254", "127.0.0.1", "0x7f.0.0.1",
                                     "8.8.8.8", "wp.localhost"])
def test_台帳_site_urlが無くてもIPの形のblog_idは断る(prod, blog_id):
    with pytest.raises(principals.PrincipalError) as exc:
        principals.validate("wp-p", _ledger(blog_id))
    assert exc.value.code == "site_url_invalid"


def test_台帳_公開の名前のblog_idは通る(prod):
    principals.validate("wp-p", _ledger("wp.example.com"))
    principals.validate("wp-p", _ledger("wp.example.com", site_url="https://wp.example.com/blog"))


def test_台帳_はてなのblog_idは今までどおり(prod):
    data = {"principal": "h-p", "kind": "hatena", "hatena_id": "someone", "token": "/x/h.token",
            "blogs": [{"blog_id": "someone.hatenablog.com", "lanes": ["lane-h"]}]}
    principals.validate("h-p", data)


# ------------------------------------------------------------------ 送る時点（名前解決）

@pytest.mark.parametrize("answer", [
    ["127.0.0.1"], ["10.0.0.5"], ["172.16.3.4"], ["192.168.1.10"], ["169.254.169.254"], ["100.64.0.1"],
    ["0.0.0.0"], ["224.0.0.1"], ["::1"], ["fe80::1"], ["fc00::1"], ["::ffff:127.0.0.1"], ["::ffff:169.254.169.254"],
    ["64:ff9b::a9fe:a9fe"], ["2002:7f00:1::1"],
    [PUBLIC_V4, "10.0.0.5"],   # 外と内が混ざった答え（どちらにつながるかを相手に選ばせない）
])
def test_名前で書かれても引いた先が内向きなら鍵を送らずに断る(prod, monkeypatch, answer):
    net = Net(monkeypatch, [answer])
    result = adapter().whoami()
    assert result["error"] == "wordpress_endpoint_rejected"
    assert net.resolved == ["wp.example.com"]
    assert net.connected == []   # つないでいない＝鍵は出ていない


def test_外の名前は確かめたIPそのものへつなぐ(prod, monkeypatch):
    net = Net(monkeypatch, [[PUBLIC_V4, PUBLIC_V6]])
    assert adapter().whoami()["error"] == "wordpress_transport_failed"   # 偽の接続は断られる
    assert net.resolved == ["wp.example.com"]
    assert [a[0] for a in net.connected] == [PUBLIC_V4, PUBLIC_V6]       # 名前ではなく IP へ
    assert all(a[1] == 443 for a in net.connected)


def test_rebinding_引き直した答えが内向きに替わっても確かめたIPにしかつながない(prod, monkeypatch):
    # 1 回目の要求: 外。2 回目の要求: 内向きに替わる。どの接続も、その接続のために引いて確かめた IP だけへ。
    net = Net(monkeypatch, [[PUBLIC_V4], ["169.254.169.254"]])
    a = adapter()
    assert a.whoami()["error"] == "wordpress_transport_failed"
    assert a.whoami()["error"] == "wordpress_endpoint_rejected"
    assert [c[0] for c in net.connected] == [PUBLIC_V4]


def test_TLSの名前は元のホストのまま(prod, monkeypatch):
    seen = {}

    class Sock:
        def setsockopt(self, *args):
            pass

        def close(self):
            pass

    def connect(address, *args, **kwargs):
        seen["address"] = address
        return Sock()

    def wrap(self, sock, server_hostname=None, **kwargs):
        seen["sni"] = server_hostname
        raise ConnectionResetError("試験はここで止める")

    monkeypatch.setattr(httpsafe, "_getaddrinfo", lambda host, port, *a, **k: infos(PUBLIC_V4, port=port))
    monkeypatch.setattr(httpsafe, "_connect", connect)
    import ssl
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap)
    assert adapter().whoami()["error"] == "wordpress_transport_failed"
    assert seen == {"address": (PUBLIC_V4, 443), "sni": "wp.example.com"}


def test_試しの印があってもloopback以外の内向きは断る(monkeypatch):
    monkeypatch.delenv(httpsafe.TEST_LOOPBACK_ONLY_ENV, raising=False)
    net = Net(monkeypatch, [["10.0.0.5"]])
    assert adapter().whoami()["error"] == "wordpress_endpoint_rejected"
    assert net.connected == []


def test_試しの印があればloopbackへはつなぐ(monkeypatch):
    net = Net(monkeypatch, [["127.0.0.1"]])
    assert adapter(blog_id="wp.test", site_url="http://127.0.0.1:8080").whoami()["error"] == "wordpress_transport_failed"
    assert net.connected and net.connected[0][0] == "127.0.0.1"


def test_店の口の既定のopenerは内向きの検査を足さない(prod, monkeypatch):
    """BASE・Shopify・STORES は定数のホストで、今の opener のまま（この直しは WordPress の口だけ）。"""
    net = Net(monkeypatch, [["10.0.0.5"]])
    assert httpsafe.opener() is not httpsafe.public_opener()
    assert net.resolved == []


# ------------------------------------------------------------------ 断りの文

def test_断りの文はコードの検査と合う():
    entry = refusal_catalog.get("wordpress_endpoint_rejected")
    for word in ("loopback", "私用の網", "link-local", "IP"):
        assert word in entry["why"] + entry["fix"]["text"], word
    assert "鍵は送っていません" in entry["why"]
    assert entry["fix"]["text"] and entry["fix"]["command"] and entry["better"]
    site = refusal_catalog.get("site_url_invalid")
    assert "IP" in site["why"] and site["fix"] and site["better"]
