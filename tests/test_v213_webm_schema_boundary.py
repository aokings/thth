"""Pinned CELLAR maxOccurs/range semantics, with original-byte wire controls."""
import pytest
from thth import media,mediaformats
from tests.test_v213_mastodon_webm_audio import webm,inspect,master,uint,text,tags,configure
from tests.test_v213_mastodon_media import env,wire,invoke,posts


def target(value):
    return master(0x1254c367,master(0x7373,master(0x63c0,*(() if value is None else (uint(0x68ca,value),))),master(0x67c8,text(0x45a3,'TITLE'),text(0x4487,'synthetic'))))


@pytest.mark.parametrize('count',[1,2,3])
def test_repeated_tags_same_bytes_on_public_wire(env,wire,count):
    raw=webm(extra=b''.join(tags('TITLE',str(i)) for i in range(count)));fm=configure(env,wire,raw)
    result,journal,m=invoke(env,fm)
    assert result.post_id and journal['media']['phase']=='published'
    assert raw in posts(wire,'/api/v2/media')[0][2]
    assert m['files'][0]['source_sha256']==m['files'][0]['public_sha256']


@pytest.mark.parametrize('value',[None,1,50,255])
def test_target_type_positive_and_default(tmp_path,value):
    assert inspect(tmp_path,webm(extra=target(value))).kind=='audio'


def test_zero_target_rejected_before_network(env,wire):
    raw=webm(extra=target(0));fm=configure(env,wire,raw)
    with pytest.raises(media.MediaError,match='invalid_attachment_structure'):media.manifest_for(fm,env[0])
    assert not wire['calls'] and (env[2]/'sound.webm').read_bytes()==raw


def test_second_tags_privacy_still_checked(env,wire):
    raw=webm(extra=tags('TITLE','ordinary')+tags('GPSLatitude','1'));fm=configure(env,wire,raw)
    with pytest.raises(media.MediaError,match='location_metadata_present'):media.manifest_for(fm,env[0])
    assert not wire['calls']


def test_singleton_info_duplicate_still_invalid(tmp_path):
    with pytest.raises(mediaformats.FormatError):inspect(tmp_path,webm(info_extra=uint(0x2ad7b1,1)))
