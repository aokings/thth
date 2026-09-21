"""Publication URI grammar is independent of the HTTP endpoint allow policy."""
import json
import pytest
from thth import bluesky_metadata as md
from thth.adapters import base
from tests.test_v213_bluesky_media import env,wire,invoke,calls
from tests.test_v213_bluesky_metadata import feature,facet


INVALID=[
    'https://[::1','https://host.invalid:abc/x','https://host.invalid/a[b',
    'https://host.invalid/#x#y','https://u@@host.invalid/x','https://[not-ip]/',
    'https://host.invalid/[x]','https://[::1]tail/','https://::1/',
    'urn:x:%','urn:x:%GG','urn:x:?a[b','urn:x:#a[b','https://[v1.]/',
    'https://[fe80::1%25eth0]/','urn:x:\nnext','urn:x:茶',
    'relative/path','//host.invalid/path',
]
VALID=[
    'urn:example:animal:ferret:nose','did:example:a','ipfs://synthetic/path',
    'mailto:fake@example.invalid','custom:','custom:/path?x#y','custom:?#',
    'custom:///path','custom://','custom://user:pass@host:999999/a',
    'custom://host:/a','custom://%41/%5B%5D?x=/?:@#x?/a',
    'https://[2001:db8::1]/a%20b','custom://[::ffff:192.0.2.1]:/',
    'custom://[v1.a:b!]/a','custom://[VF.future]/',
]


@pytest.mark.parametrize('value',INVALID)
def test_invalid_uri_before_session(env,wire,value):
    assert not md.uri(value)
    result,_,_=invoke(env,fm={'post_options':{'facets':[facet(0,4,[feature('link',value)])]}},post=base.Post('body'))
    assert result.error=='metadata_invalid: link_uri' and wire['calls']==[]


@pytest.mark.parametrize('value',VALID)
def test_generic_uri_kept_without_lookup(env,wire,value):
    assert md.uri(value)
    expected=facet(0,4,[feature('link',value)])
    result,_,_=invoke(env,fm={'post_options':{'facets':[expected]}},post=base.Post('body'))
    assert result.post_id
    assert json.loads(calls(wire,'createRecord')[0][1])['record']['facets']==[expected]
    assert [c[0] for c in wire['calls']]==['com.atproto.server.createSession','com.atproto.repo.createRecord']


@pytest.mark.parametrize('size',[8191,8192,8193])
def test_eight_kib_boundary(env,wire,size):
    value='urn:x:'+'a'*(size-6)
    result,_,_=invoke(env,fm={'post_options':{'facets':[facet(0,4,[feature('link',value)])]}},post=base.Post('body'))
    assert bool(result.post_id)==(size<=8192)
    if size>8192:assert result.error=='metadata_invalid: link_uri' and wire['calls']==[]


def test_did_percent_policy_unchanged():
    for value in ['did:example:a%GGb','did:example:a%b']:
        md.validate({'facets':[facet(0,4,[feature('mention',value)])]})
