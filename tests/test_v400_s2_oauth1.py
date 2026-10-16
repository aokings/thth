"""4.0 S2 §2.2: OAuth 1.0a の署名（`thth/oauth1.py`・標準ライブラリだけ）。T-S2-1a〜1c。

固定の答えは RFC 5849 の原文（https://www.rfc-editor.org/rfc/rfc5849.txt を 10/16 に取って写した）:

- §3.4.1.1 の base string（`POST http://example.com/request?b5=…`）。この例は秘密が書かれていないので
  署名は比べられない——base string だけを縛る。
- §1.2 の 3 本（initiate・token・photos の GET）の HMAC-SHA1 の署名。秘密が書かれている。
  この 3 本は `oauth_version` を送っていないので、`base_string`＋`sign_hmac_sha1` で直に比べる。
"""
from __future__ import annotations

import pytest

from thth import oauth1

# RFC 5849 §3.4.1.1（改行は表示のためだけ。原文の 5 行を連結した）。
RFC_BASE = ("POST&http%3A%2F%2Fexample.com%2Frequest&a2%3Dr%2520b%26a3%3D2%2520q"
            "%26a3%3Da%26b5%3D%253D%25253D%26c%2540%3D%26c2%3D%26oauth_consumer_"
            "key%3D9djdj82h48djs9d2%26oauth_nonce%3D7d8f3e4a%26oauth_signature_m"
            "ethod%3DHMAC-SHA1%26oauth_timestamp%3D137131201%26oauth_token%3Dkkk"
            "9d7dh3k39sjv7")
CLIENT_SECRET = "kd94hf93k423kf44"   # RFC 5849 §1.2 の例の値（本物の秘密ではない）


# ---------------------------------------------------------------- T-S2-1a

@pytest.mark.parametrize("raw, encoded", [
    ("~", "~"), (" ", "%20"), ("*", "%2A"), ("-._", "-._"), ("abcXYZ019", "abcXYZ019"),
    ("+", "%2B"), ("/", "%2F"), ("=", "%3D"), ("%", "%25"),
    ("日", "%E6%97%A5"),       # UTF-8 の %XX・大文字
])
def test_T_S2_1a_percent_encode(raw, encoded):
    """柵: 16 進を小文字にすると赤（`日` と `*`）。"""
    assert oauth1.percent_encode(raw) == encoded


# ---------------------------------------------------------------- T-S2-1b

def test_T_S2_1b_RFC5849_3_4_1_1のbase_string():
    """柵: 並べ替え（`sorted`）を消すと赤。"""
    body = [("c2", ""), ("a3", "2 q")]       # body `c2&a3=2+q` を form として decode したもの
    oauth = [("oauth_consumer_key", "9djdj82h48djs9d2"), ("oauth_token", "kkk9d7dh3k39sjv7"),
             ("oauth_signature_method", "HMAC-SHA1"), ("oauth_timestamp", "137131201"),
             ("oauth_nonce", "7d8f3e4a")]
    url = "http://example.com/request?b5=%3D%253D&a3=a&c%40=&a2=r%20b"
    assert oauth1.base_string("POST", url, oauth + body) == RFC_BASE


@pytest.mark.parametrize("method, url, token_secret, params, signature", [
    # §1.2 Temporary Credential Request
    ("POST", "https://photos.example.net/initiate", None,
     [("oauth_consumer_key", "dpf43f3p2l4k3l03"), ("oauth_signature_method", "HMAC-SHA1"),
      ("oauth_timestamp", "137131200"), ("oauth_nonce", "wIjqoS"),
      ("oauth_callback", "http://printer.example.com/ready")],
     "74KNZJeDHnMBp0EMJ9ZHt/XKycU="),
    # §1.2 Token Request
    ("POST", "https://photos.example.net/token", "hdhd0244k9j7ao03",
     [("oauth_consumer_key", "dpf43f3p2l4k3l03"), ("oauth_token", "hh5s93j4hdidpola"),
      ("oauth_signature_method", "HMAC-SHA1"), ("oauth_timestamp", "137131201"),
      ("oauth_nonce", "walatlh"), ("oauth_verifier", "hfdp7dh39dks9884")],
     "gKgrFCywp7rO0OXSjdot/IHF7IU="),
    # §1.2 保護された資源の GET（query が base に入る）
    ("GET", "http://photos.example.net/photos?file=vacation.jpg&size=original", "pfkkdhi9sl3r4s00",
     [("oauth_consumer_key", "dpf43f3p2l4k3l03"), ("oauth_token", "nnch734d00sl2jdk"),
      ("oauth_signature_method", "HMAC-SHA1"), ("oauth_timestamp", "137131202"),
      ("oauth_nonce", "chapoH")],
     "MdpQcU8iPSUjWoN/UDMsK2sui9I="),
])
def test_T_S2_1b_RFC5849_1_2の署名(method, url, token_secret, params, signature):
    base = oauth1.base_string(method, url, params)
    assert oauth1.sign_hmac_sha1(base, CLIENT_SECRET, token_secret) == signature


def test_base_string_uriは小文字と既定のポートとqueryを落とす():
    assert oauth1.base_string_uri("HTTPS://Blog.Hatena.NE.JP:443/a/b?x=1#f") == "https://blog.hatena.ne.jp/a/b"
    assert oauth1.base_string_uri("http://example.com:80/r") == "http://example.com/r"
    assert oauth1.base_string_uri("https://example.com:8443/r") == "https://example.com:8443/r"


# ---------------------------------------------------------------- T-S2-1c

def test_T_S2_1c_authorization_headerの鍵とbodyのformだけがbaseに入る():
    header = oauth1.authorization_header(
        consumer_key="ck", consumer_secret="cs", token="tk", token_secret="ts", method="POST",
        url="https://www.hatena.com/oauth/initiate", body_params=[("scope", "read_public,write_public")],
        extra_oauth={"oauth_callback": "oob"}, nonce="n0nce", timestamp=1700000000)
    assert header.startswith('OAuth realm="", ')
    fields = oauth1.parse_header(header)
    assert set(fields) == {"realm", "oauth_consumer_key", "oauth_nonce", "oauth_signature",
                           "oauth_signature_method", "oauth_timestamp", "oauth_token", "oauth_version",
                           "oauth_callback"}
    assert (fields["oauth_version"], fields["oauth_signature_method"], fields["realm"]) == ("1.0", "HMAC-SHA1", "")
    # 署名は body の form（scope）を含めた base から
    params = [(k, v) for k, v in fields.items() if k not in ("realm", "oauth_signature")]
    with_body = oauth1.sign_hmac_sha1(
        oauth1.base_string("POST", "https://www.hatena.com/oauth/initiate",
                           params + [("scope", "read_public,write_public")]), "cs", "ts")
    without_body = oauth1.sign_hmac_sha1(
        oauth1.base_string("POST", "https://www.hatena.com/oauth/initiate", params), "cs", "ts")
    assert fields["oauth_signature"] == with_body != without_body
    # 秘密はヘッダに入らない
    values = [v for k, v in fields.items() if k != "oauth_signature"]
    assert "cs" not in values and "ts" not in values


def test_T_S2_1c_XMLのbodyはbaseに入らない_同じ入力で同じ署名():
    kw = dict(consumer_key="ck", consumer_secret="cs", token="tk", token_secret="ts", method="PUT",
              url="https://blog.hatena.ne.jp/x/y.hatenablog.com/atom/entry/1", nonce="n", timestamp=1)
    assert oauth1.authorization_header(**kw) == oauth1.authorization_header(**kw)
    a = oauth1.parse_header(oauth1.authorization_header(**kw))
    params = [(k, v) for k, v in a.items() if k not in ("realm", "oauth_signature")]
    assert a["oauth_signature"] == oauth1.sign_hmac_sha1(oauth1.base_string("PUT", kw["url"], params), "cs", "ts")


def test_nonceとtimestampは既定で毎回作る():
    a = oauth1.parse_header(oauth1.authorization_header(consumer_key="ck", consumer_secret="cs",
                                                        method="GET", url="https://example.com/"))
    b = oauth1.parse_header(oauth1.authorization_header(consumer_key="ck", consumer_secret="cs",
                                                        method="GET", url="https://example.com/"))
    assert a["oauth_nonce"] != b["oauth_nonce"] and len(a["oauth_nonce"]) == 32
    assert a["oauth_timestamp"].isdigit() and "oauth_token" not in a


def test_extra_oauthはoauth_で始まる鍵だけ():
    with pytest.raises(ValueError):
        oauth1.oauth_params(consumer_key="ck", extra_oauth={"scope": "x"})
