"""Shared preparation binds bytes, shape, alt and typed intent before provider I/O."""
import copy
import hashlib
import json
import os
from pathlib import Path
import pytest
from thth import accounts, approval, bundle, cli, core, lint, media, queuefile, select, writeback
from tests.test_v213_media_foundation import png, jpeg, v1, v2
from tests.test_v213_media_formats import exif, segment, mp4


@pytest.fixture
def setup(tmp_path,monkeypatch):
    root=tmp_path.resolve()/'repo';(root/'docs').mkdir(parents=True)
    (root/'docs/red.png').write_bytes(png());(root/'docs/grey.jpg').write_bytes(jpeg())
    cfg={'account':'demo','media':'threads','repo_dir':str(root),'production':True,'hashtags':False}
    monkeypatch.setattr(accounts,'load_account',lambda name:cfg)
    monkeypatch.setattr(accounts,'state_dir_for',lambda name:str(tmp_path/'state'))
    monkeypatch.setattr(core,'_append_run',lambda *a,**kw:None)
    return root,cfg


def test_prepared_public_bytes_exact_and_both_hashes(setup):
    root,cfg=setup;path=root/'docs/grey.jpg'
    source=jpeg()[:2]+segment(0xe1,b'Exif\0\0'+exif())+jpeg()[2:];path.write_bytes(source)
    with pytest.raises(media.MediaError,match="source_changed"):
        with media.prepare(root,{'media':[{'file':'docs/grey.jpg','alt':'色の説明'}]},'threads') as (manifest,prepared):
            raw=b''.join(prepared[0].chunks()); row=manifest['files'][0]
            assert row['source_sha256']==hashlib.sha256(source).hexdigest()
            assert row['public_sha256']==hashlib.sha256(raw).hexdigest()!=row['source_sha256']
            assert b'PRIVATE' not in raw and row['orientation']==6
            assert path.read_bytes()==source
            path.write_bytes(jpeg())
            with pytest.raises(media.MediaError,match='source_changed'): prepared[0].verify()
            # Restore does not restore inode timestamps: context correctly rejects it too.
            path.write_bytes(source)


def test_approval_hash_changes_on_source_only_metadata_change(setup):
    root,cfg=setup;path=root/'docs/grey.jpg';q=root/'q.md'
    q.write_text(v1().replace('docs/red.png','docs/grey.jpg'))
    path.write_bytes(jpeg()[:2]+segment(0xfe,b'private-a')+jpeg()[2:])
    first,_=cli._prepare_one(str(q));assert first
    path.write_bytes(jpeg()[:2]+segment(0xfe,b'private-b')+jpeg()[2:])
    second,_=cli._prepare_one(str(q));assert second
    left=first['media_manifest']['files'][0];right=second['media_manifest']['files'][0]
    assert left['public_sha256']==right['public_sha256']
    assert left['source_sha256']!=right['source_sha256']
    assert first['approved_sha']!=second['approved_sha']
    q.write_text(writeback.front_matter_text(q.read_text(),{'status':'approved','approved_sha':first['approved_sha']}))
    parsed=queuefile.parse(str(q));parsed.verified=True
    result=select._validate_all([parsed],account_name='demo',account_cfg=cfg,recent_texts=set())
    assert not result[0] and result[1][0].reason=='approval_stale'


def test_v1_empty_body_with_real_attachment_preview_and_approval(setup,capsys):
    root,cfg=setup;q=root/'q.md';q.write_text(v1().replace('本文',''))
    assert not [e for e in lint.lint_file(str(q)) if not lint.is_warning(e)]
    shown=lint.preview_file(str(q));assert '1×1' in shown and 'public SHA256:' in shown and '赤い点' in shown
    prepared,reason=cli._prepare_one(str(q));assert reason is None and prepared['text']==''
    cli._show_first_stage([prepared],prepared['digest'],as_json=True)
    actual=json.loads(capsys.readouterr().out)
    assert actual['files'][0]['media_manifest']==prepared['media_manifest']
    q.write_text(v1('media: []').replace('本文',''))
    assert lint.lint_file(str(q)) and cli._prepare_one(str(q))[0] is None


def test_bundle_per_segment_manifest_and_writeback(setup,monkeypatch):
    root,cfg=setup;q=root/'bundle.md'
    raw=v2().replace('status: draft','publish_at: 2030-01-01T12:00:00+09:00\ncontinue_until: 2030-01-01T13:00:00+09:00\nstatus: draft').replace('本文\n','\n')
    q.write_text(raw)
    from thth import threadrun
    monkeypatch.setattr(threadrun,'unreadable_runs',lambda:[])
    monkeypatch.setattr(threadrun,'find_latest',lambda *a:None)
    assert not [e for e in lint.lint_file(str(q)) if not lint.is_warning(e)]
    prepared,reason=cli._prepare_one(str(q));assert reason is None
    assert len(prepared['media_manifests'])==2 and prepared['segments'][0]==''
    before=prepared['approved_sha']
    q.write_text(raw.replace('赤い点','違う説明'))
    other,_=cli._prepare_one(str(q));assert other['approved_sha']!=before
    changed=bundle.set_post_fields(raw,2,{'post_id':'123'})
    assert [p['media'] for p in bundle.parse_text(changed,'q').posts]==[p['media'] for p in bundle.parse_text(raw,'q').posts]


@pytest.mark.parametrize('medium,fm',[
    ('mastodon',{'attachments':[{'type':'poll','options':['はい','いいえ'],'expires_in':300,'multiple':False}],'post_options':{'visibility':'private','sensitive':True}}),
    ('bluesky',{'attachments':[{'type':'quote','uri':'at://did:plc:abc/app.bsky.feed.post/a','cid':'bafysynthetic'}],'post_options':{'languages':['ja'],'labels':['sexual']}}),
    ('threads',{'attachments':[{'type':'text','text':'長い添付文','styles':[{'offset':0,'length':1,'styling_info':['bold']}]}],'post_options':{'ghost':True}}),
])
def test_typed_declarations_every_value_enters_digest(setup,medium,fm):
    root,cfg=setup;cfg['media']=medium
    first=media.manifest_for(fm,cfg)
    assert first['attachments']==fm['attachments'] and first['post_options']==fm['post_options']
    args=dict(text='body',account='demo',topic=None,reply_to=None)
    a=approval.compute_send_digest(**args,media_manifest=first)
    other=copy.deepcopy(first);other['post_options']={}
    assert a!=approval.compute_send_digest(**args,media_manifest=other)
    assert a!=approval.compute_send_digest(**args)


@pytest.mark.parametrize('medium,fm',[
 ('mastodon',{'attachments':[{'type':'poll','options':['a','b'],'expires_in':300}],'media':[{'file':'docs/red.png','alt':'a'}]}),
 ('bluesky',{'attachments':[{'type':'poll','options':['a','b']}]}),
 ('threads',{'attachments':[{'type':'quote','uri':'a','surprise':'b'}]}),
 ('threads',{'post_options':{'topic':'duplicate'}}),
 ('threads',{'post_options':{'reply_to':'duplicate'}}),
 ('threads',{'post_options':{'location_id':'duplicate'}}),
 ('threads',{'post_options':{'share_to_instagram':True}}),
 ('threads',{'post_options':{'ghost':'true'}}),
 ('bluesky',{'attachments':[{'type':'link','url':'https://example.invalid/a','title':'a','description':'b','thumbnail_file':'docs/red.png'}]}),
 ('mastodon',{'attachments':[{'type':'text','text':'wrong-medium'}]}),
 ('bluesky',{'post_options':{'facets':[{'index':{'byteStart':0,'byteEnd':1},'features':[{'$type':'unknown','uri':'a'}]}]}}),
])
def test_typed_unknown_or_conflicting_inputs_loud_reject(setup,medium,fm):
    with pytest.raises(media.MediaError):media.manifest_for(fm,{**setup[1],'media':medium})


def test_structured_v1_v2_parser_preserves_typed_values():
    block='attachments:\n  - type: poll\n    options: ["はい", "いいえ"]\n    expires_in: 300\npost_options:\n  sensitive: true'
    q=queuefile.parse_text(v1(block),'q');assert not q.malformed
    assert q.get('attachments')[0]['options']==['はい','いいえ'] and q.get('post_options')=={'sensitive':True}
    raw=v2();start=raw.index('    media:');end=raw.index('  - index: 2',start)
    raw=raw[:start]+'\n'.join('    '+line for line in block.splitlines())+'\n'+raw[end:]
    b=bundle.parse_text(raw,'q');assert not b.malformed
    assert b.posts[0]['attachments']==q.get('attachments')
    changed=bundle.set_post_fields(raw,1,{'post_id':'123'})
    parsed=bundle.parse_text(changed,'q');assert parsed.posts[0]['post_id']=='123' and parsed.posts[0]['post_options']=={'sensitive':True}


@pytest.mark.parametrize('block',['attachments: null','attachments: {}','attachments: [{"type":"quote","uri":"x","uri":"y"}]','post_options: []','captions: null','attachments:\n - type: quote\n   uri: a'])
def test_structured_parse_rejects_ambiguous_types(block):
    assert queuefile.parse_text(v1(block),'q').malformed


def test_captions_sha_and_video_index(setup):
    root,cfg=setup;cfg['media']='bluesky';(root/'docs/video.mp4').write_bytes(mp4());(root/'docs/ja.vtt').write_text('WEBVTT\n\n00:00.000 --> 00:01.000\n字幕\n')
    fm={'media':[{'file':'docs/video.mp4','alt':'動画'}],'captions':[{'media_index':1,'file':'docs/ja.vtt','lang':'ja'}]}
    first=media.manifest_for(fm,cfg);assert first['files'][1]['kind']=='caption'
    (root/'docs/ja.vtt').write_text('WEBVTT\n\n00:00.000 --> 00:01.000\n変更\n')
    assert media.prepared_component(first)!=media.prepared_component(media.manifest_for(fm,cfg))
    fm['captions'][0]['media_index']=2
    with pytest.raises(media.MediaError):media.manifest_for(fm,cfg)


def test_send_cli_multiple_media_no_stdin_and_no_provider(setup,monkeypatch,capsys):
    root,cfg=setup
    monkeypatch.setattr('sys.stdin',type('NoRead',(),{'read':lambda self:pytest.fail('image-only must not wait stdin')})())
    # Threads is now supported; an adapter without prepared-media capability must
    # still refuse without entering its provider method.
    from thth.adapters.base import Adapter
    class Incapable(Adapter):
        def publish(self,*a,**kw):pytest.fail('incapable provider called')
    monkeypatch.setattr(core,'_default_adapter_factory',lambda *a:Incapable())
    from thth import read_coordination
    monkeypatch.setattr(read_coordination,'invoke',lambda args,name,fn=None:fn() if fn else args.func(args))
    argv=['send','demo','--media','docs/red.png','--alt','赤','--media','docs/grey.jpg','--alt','灰']
    assert cli.main(argv)==0
    output=capsys.readouterr().out;assert output.count('public SHA256:')==2
    digest=next(x.split(': ',1)[1] for x in output.splitlines() if x.startswith('digest:'))
    assert cli.main(argv+['--production','--confirm',digest])==1
    assert 'media_provider_unavailable' in capsys.readouterr().out
    assert cli.main(['send','demo','--media','docs/red.png'])==2


def test_empty_manifest_preserves_all_legacy_hashes():
    args=dict(section='text',account='demo',reply_to=None,topic=None,publish_at='2030-01-01T12:00:00+09:00')
    assert approval.compute_approved_sha(**args)==approval.compute_approved_sha(**args,media_manifest=None)
    assert approval.compute_bundle_sha(segments=['a','b'],account='demo',topic=None,publish_at=args['publish_at'],continue_until=args['publish_at'])==approval.compute_bundle_sha(segments=['a','b'],account='demo',topic=None,publish_at=args['publish_at'],continue_until=args['publish_at'],media_manifest=[None,None])


def test_unchanged_public_stream_is_private_snapshot_not_live_source(setup):
    import stat
    from tests.test_v213_media_formats import gif
    root,cfg=setup;path=root/'docs/a.gif';original=gif();path.write_bytes(original)
    public_fd=None
    with pytest.raises(media.MediaError,match='source_changed'):
        with media.prepare(root,{'media':[{'file':'docs/a.gif','alt':'説明'}]},'mastodon') as (manifest,items):
            item=items[0];public_fd=item._public_fd
            st=os.fstat(public_fd)
            assert stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode)==0o600
            assert st.st_nlink==0
            stream=item.chunks(chunk_size=9);emitted=[next(stream)]
            path.write_bytes(b'X'*len(original))
            with pytest.raises(media.MediaError,match='source_changed'):
                while True:emitted.append(next(stream))
            assert b''.join(emitted)==original
            assert hashlib.sha256(b''.join(emitted)).hexdigest()==manifest['files'][0]['public_sha256']
    with pytest.raises(OSError):os.fstat(public_fd)


@pytest.mark.parametrize('fm',[{'media':None},{'attachments':None},{'post_options':None},{'captions':None}])
def test_direct_manifest_api_does_not_silently_ignore_null(setup,fm):
    with pytest.raises(media.MediaError):media.manifest_for(fm,setup[1])


def test_actual_cli_lint_and_preview_metadata(setup,capsys,monkeypatch):
    root,cfg=setup;path=root/'q.md';path.write_text(v1())
    from thth import read_coordination
    monkeypatch.setattr(read_coordination,'invoke',lambda args,name,fn=None:fn() if fn else args.func(args))
    assert cli.main(['lint',str(path),'--json'])==0
    actual=json.loads(capsys.readouterr().out)
    row=actual['media_manifest']['files'][0]
    assert row['size']>0 and row['public_size']>0 and row['width']==1
    assert cli.main(['preview',str(path)])==0
    text=capsys.readouterr().out
    assert f"public {row['public_size']} bytes" in text and row['public_sha256'] in text


def test_public_sha_and_alt_order_change_digest(setup):
    root,cfg=setup
    manifest=media.manifest_for({'media':[{'file':'docs/red.png','alt':'赤'},{'file':'docs/grey.jpg','alt':'灰'}]},cfg)
    args=dict(text='本文',account='demo',reply_to=None,topic=None)
    expected=approval.compute_send_digest(**args,media_manifest=manifest)
    for key,value in [('public_sha256','f'*64),('alt','別の説明'),('public_size',1)]:
        other=copy.deepcopy(manifest);other['files'][0][key]=value
        assert approval.compute_send_digest(**args,media_manifest=other)!=expected
    swapped=copy.deepcopy(manifest);swapped['files'].reverse()
    assert approval.compute_send_digest(**args,media_manifest=swapped)!=expected


@pytest.mark.parametrize('hint',['default','gif','future-presentation-hint'])
def test_bluesky_presentation_is_open_string_bound_to_digest(setup,hint):
    root,cfg=setup;cfg['media']='bluesky'
    manifest=media.manifest_for({'post_options':{'presentation':hint}},cfg)
    assert manifest['post_options']['presentation']==hint
    assert hint in media.display(manifest)
    args=dict(text='body',account='demo',topic=None,reply_to=None)
    other=media.manifest_for({'post_options':{'presentation':hint+'-changed'}},cfg)
    assert approval.compute_send_digest(**args,media_manifest=manifest)!=approval.compute_send_digest(**args,media_manifest=other)


@pytest.mark.parametrize('hint',[None,False,42,[],{}])
def test_bluesky_presentation_rejects_non_string(setup,hint):
    root,cfg=setup;cfg['media']='bluesky'
    with pytest.raises(media.MediaError):
        media.manifest_for({'post_options':{'presentation':hint}},cfg)
