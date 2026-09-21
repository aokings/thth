"""Stage56 privacy gate: typed metadata is inspected before all video providers."""
import hashlib
import struct
from types import SimpleNamespace
import pytest
from thth import accounts,media,media_delivery,mediaformats
from thth.adapters.base import Post
from tests.test_v213_media_formats import box,mp4,inspect


def item(payload):
    return box(b'udta',box(b'meta',bytes(4)+box(b'ilst',box(b'\xa9nam',payload))))


def typed(kind,value):return item(box(b'data',struct.pack('>II',kind,0)+value))


def nested():
    return typed(28,box(b'meta',bytes(4)+box(b'ilst',box(b'\xa9xyz',box(b'data',struct.pack('>II',1,0)+b'+12+34/')))))


@pytest.mark.parametrize('medium',['mastodon','threads','bluesky'])
@pytest.mark.parametrize('extra',[nested(),typed(0,b'implicit'),typed(27,b'BM opaque'),typed(13,b'\xff\xd8 binary cover'),typed(14,b'\x89PNG\r\n\x1a\n'),typed(255,b'future')])
def test_reject_before_provider_and_keep_original(tmp_path,monkeypatch,medium,extra):
    repo=tmp_path/'repo';repo.mkdir();p=repo/'v.mp4';p.write_bytes(mp4())
    cfg={'account':'alpha','media':medium,'repo_dir':str(repo)}
    fm={'media':[{'file':'v.mp4','alt':'video'}]}
    manifest=media.manifest_for(fm,cfg)
    raw=mp4(extra);p.write_bytes(raw);calls=[]
    def forbidden(*args,**kwargs):calls.append('effect');raise AssertionError('provider or journal reached')
    adapter=SimpleNamespace(prepared_media_supported=True,publish=forbidden)
    monkeypatch.setattr(media_delivery,'_progress',forbidden)
    monkeypatch.setattr(accounts,'state_dir_for',lambda name:str(tmp_path/'state'))
    with pytest.raises(media.MediaError,match='location_metadata_unverifiable: metadata_value_type'):
        media.manifest_for(fm,cfg)
    result=media_delivery.publish(adapter,Post(text='video'),cfg=cfg,fm=fm,manifest=manifest,state_dir=str(tmp_path/'state'))
    assert result.post_id is None and result.failure=='publish_vetoed'
    assert 'metadata_value_type' in result.error and calls==[] and p.read_bytes()==raw


@pytest.mark.parametrize('kind,value',[(1,'曲名'.encode()),(2,'曲名'.encode('utf-16-be')),(3,'曲名'.encode('shift_jis')),(4,b'title'),(5,b'\0t'),(21,b'\0'),(21,bytes(8)),(22,bytes(3)),(22,bytes(8)),(23,bytes(4)),(24,bytes(8)),(65,bytes(1)),(66,bytes(2)),(67,bytes(4)),(70,bytes(8)),(71,bytes(8)),(72,bytes(16)),(74,bytes(8)),(75,bytes(1)),(76,bytes(2)),(77,bytes(4)),(78,bytes(8)),(79,bytes(72)),(1,b'a'*65535+'茶'.encode())])
def test_known_scalar_keeps_bytes(tmp_path,kind,value):
    raw=mp4(typed(kind,value));(tmp_path/'v.mp4').write_bytes(raw)
    with media.prepare(tmp_path,{'media':[{'file':'v.mp4','alt':'video'}]},'threads') as (manifest,items):
        assert b''.join(items[0].chunks())==raw
        row=manifest['files'][0];assert row['source_sha256']==row['public_sha256']==hashlib.sha256(raw).hexdigest()
        # C14 (第 7 段) から、保持した非位置メタデータは notes で申告する。
        # 受け入れたのは「書き換えていない」ことで、無申告であることではない。
        assert row['metadata_notes']==['non_location_metadata_retained'] and row['duration']==2.5


@pytest.mark.parametrize('kind,value',[(1,b'\xff'),(2,b'a'),(21,b''),(21,bytes(5)),(22,bytes(5)),(21,bytes(9)),(23,bytes(8)),(79,bytes(8))])
def test_scalar_width_and_encoding(tmp_path,kind,value):
    with pytest.raises(mediaformats.FormatError,match='invalid_attachment_structure'):inspect(tmp_path,mp4(typed(kind,value)))


@pytest.mark.parametrize('size',range(8))
def test_data_header_required(tmp_path,size):
    with pytest.raises(mediaformats.FormatError,match='invalid_attachment_structure'):inspect(tmp_path,mp4(item(box(b'data',bytes(size)))))


@pytest.mark.parametrize('key',[b'mean',b'name'])
@pytest.mark.parametrize('value,reason',[(bytes(4)+b'com.apple.quicktime.location.ISO6709','location_metadata_present'),(bytes(4)+b'GPSLatitude','location_metadata_present'),(b'bad','invalid_attachment_structure'),(b'\x01\0\0\0title','invalid_attachment_structure'),(bytes(4)+b'\xff','invalid_attachment_structure')])
def test_freeform_structure_and_location(tmp_path,key,value,reason):
    with pytest.raises(mediaformats.FormatError,match=reason):inspect(tmp_path,mp4(item(box(key,value))))


def test_ordinary_freeform_and_mdat_are_not_keyword_scanned(tmp_path):
    payload=box(b'mean',bytes(4)+b'example')+box(b'name',bytes(4)+b'title')+box(b'data',struct.pack('>II',1,0)+b'GPS word is ordinary title')
    raw=mp4(item(payload)).replace(b'synthetic-sample',b'GPSLatitude-mdat')
    assert inspect(tmp_path,raw).public_bytes is None


def keys_box(*names):
    """A `mdta` keys box whose entries are the given metadata key names."""
    entries=b''.join(box(b'mdta',name) for name in names)
    return box(b'meta',bytes(4)+box(b'keys',bytes(4)+struct.pack('>I',len(names))+entries))


@pytest.mark.parametrize('name',[b'GPSLatitude',b'gpslongitude',b'GPSCoordinates',b'com.apple.quicktime.location.ISO6709',b'ISO6709',b'geotag'])
def test_keys_box_location_words_are_one_table(tmp_path,name):
    # 第 5・6 段 P2: `keys` box と freeform が同じ表を見る（部分一致・大小無視）。
    with pytest.raises(mediaformats.FormatError,match='location_metadata_present'):inspect(tmp_path,mp4(keys_box(name)))


@pytest.mark.parametrize('key',[b'mean',b'name'])
@pytest.mark.parametrize('name',[b'GPSCoordinates',b'GPSPosition',b'coordinates',b'geotag',b'ISO6709Value'])
def test_freeform_location_words_are_one_table(tmp_path,key,name):
    payload=box(key,bytes(4)+name)+box(b'data',struct.pack('>II',1,0)+b'+35.6895+139.6917/')
    with pytest.raises(mediaformats.FormatError,match='location_metadata_present'):inspect(tmp_path,mp4(item(payload)))


def test_geometry_like_key_is_refused_but_a_location_word_value_is_accepted(tmp_path):
    # `geo` は `geometry` にも当たる。値側の「Georgia」は key ではないので通る。
    with pytest.raises(mediaformats.FormatError,match='location_metadata_present'):
        inspect(tmp_path,mp4(item(box(b'name',bytes(4)+b'geometry'))))
    accepted=inspect(tmp_path,mp4(typed(1,'Georgia'.encode())))
    assert accepted.public_bytes is None and accepted.metadata_notes==('non_location_metadata_retained',)


def test_codecs_name_is_the_hoisted_module_not_a_local_table(tmp_path):
    # 第 5・6 段 P3: `_bmff_metadata_scalar` の局所 dict が `import codecs` を
    # 隠していた。module 直下の名前は stdlib のまま、復号も従来どおり。
    import codecs as stdlib
    assert mediaformats.codecs is stdlib
    for kind,value in ((1,'茶'.encode()),(2,'茶'.encode('utf-16-be')),(3,'茶'.encode('shift_jis'))):
        assert inspect(tmp_path,mp4(typed(kind,value))).public_bytes is None
