"""Claude stage1 acceptance follow-ups, synthetic inputs and no providers."""
from pathlib import Path
import os
import pytest
from thth import media, mediaformats
from tests.test_v213_media_formats import box,mp4
from tests.test_v213_media_foundation import png


def inspect(tmp_path,raw):
    path=tmp_path/'video.mp4';path.write_bytes(raw);fd=os.open(path,os.O_RDONLY)
    try:return mediaformats.inspect(fd,len(raw))
    finally:os.close(fd)


@pytest.mark.parametrize('raw',[
    mp4(box(b'ZZZZ',box(b'\xa9xyz',b'+35.6+139.7/'))),
    mp4()+box(b'ZZZZ',b'location value'),
    mp4()+box(b'skip',box(b'\xa9xyz',b'+35.6+139.7/')),
    mp4()+box(b'uuid',bytes.fromhex('be7acfcb97a942e89c71999491e3afac')+b'<exif:GPSLatitude>35</exif:GPSLatitude>'),
    mp4()+box(b'uuid',b'\0'*16),
    mp4()+box(b'free',b'not zero'),
    mp4(box(b'free',b'\0\0x')),
    mp4(box(b'meta',b'\0'*4+box(b'skip',b'x'))),
])
def test_bmff_unknown_location_containers_rejected(tmp_path,raw):
    with pytest.raises(mediaformats.FormatError,match='location_metadata_unverifiable'):inspect(tmp_path,raw)


@pytest.mark.parametrize('padding',[b'',b'\0',b'\0'*100001])
@pytest.mark.parametrize('kind',[b'free',b'skip'])
def test_bmff_zero_padding_and_mdat_not_scanned(tmp_path,padding,kind):
    source=mp4(box(kind,padding))+box(kind,padding)+box(b'mdat',b'\xa9xyz exif:GPSLatitude location uuid')
    info=inspect(tmp_path,source)
    assert info.format=='mp4' and info.public_bytes is None and (tmp_path/'video.mp4').read_bytes()==source


def test_repo_ancestor_symlink_specific_reason_without_resolving(tmp_path):
    root=tmp_path/'actual';(root/'repo').mkdir(parents=True);(root/'repo'/'a.png').write_bytes(png())
    alias=tmp_path/'alias';alias.symlink_to(root,target_is_directory=True)
    before=(root/'repo'/'a.png').read_bytes()
    with pytest.raises(media.MediaError,match='^media: repo_dir_symlink$'):
        with media.prepare(alias/'repo',{'media':[{'file':'a.png','alt':'点'}]},'threads'):pytest.fail('unsafe repo')
    assert (root/'repo'/'a.png').read_bytes()==before


@pytest.mark.parametrize('medium,row',[('threads',{'type':'quote','uri':'123'}),('mastodon',{'type':'poll','options':['a','b'],'expires_in':300}),('bluesky',{'type':'quote','uri':'at://did:plc:test/app.bsky.feed.post/a','cid':'fake'})])
def test_duplicate_attachment_type_has_distinct_reason(medium,row):
    with pytest.raises(media.MediaError,match='^duplicate_attachment_type: '):media.validate_declarations({'attachments':[row,row]},medium)


def test_caption_display_language_not_alt(tmp_path):
    root=tmp_path.resolve();(root/'v.mp4').write_bytes(mp4());(root/'ja.vtt').write_text('WEBVTT\n\n00:00.000 --> 00:01.000\n字幕\n')
    with media.prepare(root,{'media':[{'file':'v.mp4','alt':'動画'}],'captions':[{'media_index':1,'file':'ja.vtt','lang':'ja'}]},'bluesky') as (manifest,_):
        shown=media.display(manifest)
        assert 'lang: ja' in shown and 'alt: ja' not in shown and 'alt: 動画' in shown
