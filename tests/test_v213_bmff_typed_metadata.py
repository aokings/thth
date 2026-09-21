"""C14 nested/opaque metadata cannot pass as a retained scalar value."""
import struct
import pytest
from thth import media,mediaformats as mf
from tests.test_v213_mastodon_bmff_audio import audio
from tests.test_v213_media_formats import box,mp4
from tests.test_v213_mastodon_media import env,wire


def typed(value_type,value,*,key=b'\xa9nam'):
    return box(b'udta',box(b'meta',bytes(4)+box(b'ilst',box(key,box(b'data',struct.pack('>II',value_type,0)+value)))))


@pytest.mark.parametrize('build',[audio,mp4])
@pytest.mark.parametrize('typ,value',[
    (28,box(b'meta',bytes(4)+box(b'ilst',box(b'\xa9xyz',box(b'data',struct.pack('>II',1,0)+b'+12+34/'))))),
    (27,b'BM uninspected bitmap cover'),(0,b'opaque implicit value'),(255,b'future extension')])
def test_nested_or_unknown_typed_value_refuses_before_provider(env,wire,build,typ,value):
    raw=build(typed(typ,value));(env[2]/'a.mp4').write_bytes(raw)
    with pytest.raises(media.MediaError,match='location_metadata_unverifiable: metadata_value_type'):
        media.manifest_for({'media':[{'file':'a.mp4','alt':'media'}]},env[0])
    assert not wire['calls'] and (env[2]/'a.mp4').read_bytes()==raw


@pytest.mark.parametrize('typ,value',[(1,'曲名'.encode()),(2,'曲名'.encode('utf-16-be')),(3,'曲名'.encode('shift_jis')),(4,b'title'),(5,b'\0t'),(21,b'\0'),(22,b'\0\0\0'),(23,bytes(4)),(24,bytes(8)),(65,bytes(1)),(66,bytes(2)),(67,bytes(4)),(70,bytes(8)),(71,bytes(8)),(72,bytes(16)),(74,bytes(8)),(75,bytes(1)),(76,bytes(2)),(77,bytes(4)),(78,bytes(8)),(79,bytes(72))])
def test_known_text_and_number_values_preserved(env,typ,value):
    raw=audio(typed(typ,value));(env[2]/'a.mp4').write_bytes(raw)
    with media.prepare(env[0]['repo_dir'],{'media':[{'file':'a.mp4','alt':'audio'}]},'mastodon') as (manifest,items):
        assert b''.join(items[0].chunks())==raw and 'non_location_metadata_retained' in manifest['files'][0]['metadata_notes']


@pytest.mark.parametrize('typ,value',[(1,b'\xff'),(2,b'a'),(21,b''),(21,bytes(5)),(23,bytes(8)),(79,bytes(8))])
def test_scalar_structure_checked(env,typ,value):
    (env[2]/'a.mp4').write_bytes(audio(typed(typ,value)))
    with pytest.raises(media.MediaError,match='invalid_attachment_structure'):
        media.manifest_for({'media':[{'file':'a.mp4','alt':'audio'}]},env[0])


@pytest.mark.parametrize('key,value,reason',[(b'name',bytes(4)+b'com.apple.quicktime.location.ISO6709','location_metadata_present'),(b'mean',bytes(4)+b'GPSLatitude','location_metadata_present'),(b'name',b'bad','invalid_attachment_structure'),(b'name',bytes(4)+b'\xff','invalid_attachment_structure')])
def test_freeform_metadata_names_are_not_opaque(env,key,value,reason):
    extra=box(b'udta',box(b'meta',bytes(4)+box(b'ilst',box(b'----',box(key,value)+box(b'data',struct.pack('>II',1,0)+b'value')))))
    (env[2]/'a.mp4').write_bytes(audio(extra))
    with pytest.raises(media.MediaError,match=reason):media.manifest_for({'media':[{'file':'a.mp4','alt':'audio'}]},env[0])


def test_known_freeform_name_keeps_text_without_interpreting_content(env):
    extra=box(b'udta',box(b'meta',bytes(4)+box(b'ilst',box(b'----',box(b'mean',bytes(4)+b'example')+box(b'name',bytes(4)+b'title')+box(b'data',struct.pack('>II',1,0)+b'GPS word in ordinary title')))))
    raw=audio(extra);(env[2]/'a.mp4').write_bytes(raw)
    with media.prepare(env[0]['repo_dir'],{'media':[{'file':'a.mp4','alt':'audio'}]},'mastodon') as (_,items):assert b''.join(items[0].chunks())==raw
