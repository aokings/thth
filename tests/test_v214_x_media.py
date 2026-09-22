"""2.14.0: X の添付（chunked upload・STATUS・alt・media_ids）と durable intent。

偽の X は loopback（`tests/helpers/fake_x.py`）。**本物の api.x.com は叩かない。**
"""
import copy
import json
import pathlib
import secrets
import struct

import pytest

from thth import accounts, budget_x_posts, inflight, media, media_delivery, server_files
from thth.adapters import base, x as x_adapter, x_media
from tests.helpers.fake_x import calls, serve
from tests.test_v213_media_foundation import png
from tests.test_v213_media_formats import box, gif, mp4


def set_post_budget(monthly):
    value = budget_x_posts.empty_policy()
    value['policy'] = budget_x_posts.policy(monthly)
    with server_files.directory(budget_x_posts.admin_folder(), create=True, private=True) as fd:
        budget_x_posts._save_policy(fd, value)


def big_mp4(size):
    """mdat を膨らませた合成 mp4（**bytes は変わらない**ので chunk 数がそのまま出る）。"""
    raw = mp4()
    tail = box(b'free', b'\0' * max(0, size - len(raw) - 8))
    return raw + tail


def big_png(width=1414):
    """本物の大きさの PNG（IDAT は宣言した寸法どおりの生画素を非圧縮で持つ）。

    `width=1414` で約 6 MB——5 MB の chunk 境界をまたぐので append が 2 回になる。
    """
    import os
    import zlib
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    raw = os.urandom((width * 3 + 1) * width)
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, width, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 0)) + chunk(b'IEND', b''))


@pytest.fixture
def wire():
    state, url, shutdown = serve()
    state['url'] = url
    try:
        yield state
    finally:
        shutdown()


@pytest.fixture
def env(tmp_path, monkeypatch, wire):
    root = tmp_path.resolve() / 'root'
    root.mkdir(mode=0o700)
    repo = root / 'repo'
    repo.mkdir()
    (repo / 'a.png').write_bytes(png())
    (repo / 'b.png').write_bytes(png())
    monkeypatch.setenv('THTH_ROOT', str(root))
    monkeypatch.delenv('THTH_ACCOUNTS_DIR', raising=False)
    monkeypatch.setattr(x_media, 'POLL_INTERVAL', 0)
    cfg = {'account': 'alpha', 'media': 'x', 'repo_dir': str(repo), 'production': True,
           'hashtags': False, 'handle': 'demo'}
    monkeypatch.setattr(accounts, 'load_account', lambda n: cfg)
    set_post_budget(10)
    adapter = x_adapter.XAdapter(base_url=wire['url'], access_token=secrets.token_urlsafe(30),
                                 user_id='123', username='demo')
    adapter.auth_account = 'alpha'
    return cfg, adapter, repo, wire


def invoke(env, fm=None, veto=None, post=None):
    cfg, adapter, repo, _ = env
    fm = fm or {'media': [{'file': 'a.png', 'alt': '赤い点 🌿'}]}
    manifest = media.manifest_for(fm, cfg)
    state = accounts.state_dir_for('alpha')
    inflight.write(state, file='fixture', started='2030-01-01T00:00:00+09:00')
    result = media_delivery.publish(adapter, post or base.Post('本文'), cfg=cfg, fm=fm,
                                    manifest=manifest, state_dir=state, before_publish=veto)
    return result, inflight.read(state), manifest


# ----- wire の順序と本体 -----------------------------------------------------

def test_single_image_walks_initialize_append_finalize_metadata_then_tweet(env, wire):
    result, journal, _ = invoke(env)
    assert result.post_id == wire['next_post'] and result.failure == 'none'
    assert journal['media']['phase'] == 'published'
    order = [row[1] for row in wire['calls']]
    assert order == ['/2/media/upload/initialize', '/2/media/upload/101/append',
                     '/2/media/upload/101/finalize', '/2/media/metadata', '/2/tweets']
    initialize = calls(wire, 'POST', '/initialize')[0][2]
    assert initialize == {'media_type': 'image/png', 'total_bytes': len(png()),
                          'media_category': 'tweet_image'}
    assert calls(wire, 'POST', '/2/media/metadata')[0][2] == {
        'id': '101', 'alt_text': {'text': '赤い点 🌿'}}
    assert wire['tweets'] == [{'text': '本文', 'media': {'media_ids': ['101']}}]
    assert wire['uploads']['101'] == png()
    assert result.media == [{'sha256': journal['media']['manifest']['files'][0]['public_sha256'],
                             'kind': 'image', 'alt_present': True, 'remote_id': '101'}]
    # 秘密も付与 URL も journal に出ない。
    assert env[1].access_token not in json.dumps(journal, ensure_ascii=False)


def test_a_reply_carries_only_the_reply_reference_and_never_a_quote(env, wire):
    result, _, _ = invoke(env, post=base.Post('本文', reply_to='1900000000000000000'))
    assert result.post_id
    assert wire['tweets'] == [{'text': '本文',
                               'reply': {'in_reply_to_tweet_id': '1900000000000000000'},
                               'media': {'media_ids': ['101']}}]
    # 組み立てる側の source にも綴りが無い（tier の理由で送らない・設計 §0）。
    assert "'quote_tweet_id'" not in pathlib.Path(x_adapter.__file__).read_text()
    assert "'quote_tweet_id'" not in pathlib.Path(x_media.__file__).read_text()


@pytest.mark.parametrize('kind,category,cap', [('image', 'tweet_image', 8_000_000),
                                               ('video', 'tweet_video', None)])
def test_six_megabytes_are_sent_as_two_segments(env, wire, monkeypatch, kind, category, cap):
    if cap is not None:
        monkeypatch.setattr(x_media, 'MAX_IMAGE_BYTES', cap)
    name = 'big.png' if kind == 'image' else 'big.mp4'
    raw = big_png() if kind == 'image' else big_mp4(6_000_000)
    (env[2] / name).write_bytes(raw)
    result, journal, manifest = invoke(env, {'media': [{'file': name, 'alt': '大きいもの'}]})
    assert result.post_id and journal['media']['phase'] == 'published'
    appended = calls(wire, 'POST', '/append')
    assert [row[2]['segment_index'] for row in appended] == ['0', '1']
    assert [row[2]['bytes'] for row in appended] == [x_media.CHUNK_BYTES,
                                                     manifest['files'][0]['public_size'] - x_media.CHUNK_BYTES]
    assert calls(wire, 'POST', '/initialize')[0][2]['media_category'] == category
    assert wire['uploads']['101'] == (manifest['files'][0]['public_size'] * b'\0')[:0] + wire['uploads']['101']
    assert len(wire['uploads']['101']) == manifest['files'][0]['public_size']


def test_status_is_polled_with_check_after_secs_before_the_tweet(env, wire):
    (env[2] / 'v.mp4').write_bytes(mp4())
    wire['status']['101'] = [{'state': 'in_progress', 'check_after_secs': 0},
                             {'state': 'succeeded'}]
    result, journal, _ = invoke(env, {'media': [{'file': 'v.mp4', 'alt': '動画'}]})
    assert result.post_id and journal['media']['phase'] == 'published'
    order = [row[1] for row in wire['calls']]
    assert order.count('/2/media/upload') == 2
    assert order.index('/2/tweets') > max(i for i, p in enumerate(order) if p == '/2/media/upload')


def test_a_failed_processing_state_holds_the_media_and_never_posts(env, wire):
    (env[2] / 'v.mp4').write_bytes(mp4())
    wire['status']['101'] = [{'state': 'failed'}]
    result, journal, _ = invoke(env, {'media': [{'file': 'v.mp4', 'alt': '動画'}]})
    assert result.failure == 'media_held' and result.error == 'media_processing_failed'
    assert journal['media']['phase'] == 'held' and journal['media']['remote_ids'] == ['101']
    assert not wire['tweets']


@pytest.mark.parametrize('name,category', [('g.gif', 'tweet_gif'), ('v.mp4', 'tweet_video')])
def test_gif_and_video_choose_their_category(env, wire, name, category):
    (env[2] / name).write_bytes(gif() if name.endswith('gif') else mp4())
    result, _, _ = invoke(env, {'media': [{'file': name, 'alt': '説明'}]})
    assert result.post_id
    assert calls(wire, 'POST', '/initialize')[0][2]['media_category'] == category


def test_media_ids_follow_the_manuscript_order(env, wire):
    (env[2] / 'c.png').write_bytes(png() + b'')
    rows = [{'file': 'b.png', 'alt': '二'}, {'file': 'a.png', 'alt': '一'}]
    result, _, _ = invoke(env, {'media': rows})
    assert wire['tweets'][0]['media']['media_ids'] == ['101', '102']
    assert [row[2]['alt_text']['text'] for row in calls(wire, 'POST', '/2/media/metadata')] == ['二', '一']
    assert [row['remote_id'] for row in result.media] == ['101', '102']


@pytest.mark.parametrize('count,ok', [(1, True), (4, True), (5, False)])
def test_four_images_pass_and_five_are_refused_before_any_request(env, wire, count, ok):
    for index in range(count):
        (env[2] / f'i{index}.png').write_bytes(png())
    rows = [{'file': f'i{index}.png', 'alt': f'画像 {index}'} for index in range(count)]
    result, _, _ = invoke(env, {'media': rows})
    assert (result.post_id is not None) is ok
    if not ok:
        assert result.error == 'media_limit_exceeded: count' and wire['calls'] == []


def test_mixing_kinds_is_refused_before_any_request(env, wire):
    (env[2] / 'v.mp4').write_bytes(mp4())
    result, journal, _ = invoke(env, {'media': [{'file': 'a.png', 'alt': '点'},
                                                {'file': 'v.mp4', 'alt': '動画'}]})
    assert result.error == 'unsupported_attachment: x/media_mixed_kinds'
    assert result.failure == 'publish_definite' and wire['calls'] == []


def test_an_image_above_the_provisional_cap_never_reaches_the_provider(env, wire):
    (env[2] / 'big.png').write_bytes(big_png())
    result, _, _ = invoke(env, {'media': [{'file': 'big.png', 'alt': '大きい'}]})
    assert result.error.startswith('media_limit_exceeded: bytes (provisional SI 5000000')
    assert wire['calls'] == []


def test_video_alt_refusal_is_a_warning_and_the_post_still_goes_out(env, wire):
    (env[2] / 'v.mp4').write_bytes(mp4())
    wire['metadata_status']['101'] = 400
    result, journal, _ = invoke(env, {'media': [{'file': 'v.mp4', 'alt': '動画の説明'}]})
    assert result.post_id and journal['media']['phase'] == 'published'
    assert journal['media']['warnings'] == ['x_video_alt_unsupported']


def test_image_alt_refusal_stops_the_post(env, wire):
    wire['metadata_status']['101'] = 400
    result, journal, _ = invoke(env)
    assert result.post_id is None and result.failure == 'media_held'
    assert result.error == 'media_describing_http_400' and not wire['tweets']


# ----- 429・不明・held ------------------------------------------------------

def test_429_keeps_the_reset_in_the_journal_and_does_not_wait(env, wire, monkeypatch):
    wire['reply']['/2/tweets'] = {'x-rate-limit-reset': '1790000000', 'retry-after': '900'}
    def refuse():
        raise AssertionError
    slept = []
    monkeypatch.setattr(x_media.time, 'sleep', lambda s: slept.append(s))
    original = x_media.x_adapter.request
    def request(adapter, method, path, **kwargs):
        if path == '/2/tweets':
            import urllib.error
            import io
            raise urllib.error.HTTPError(adapter.base_url + path, 429, 'Too Many Requests',
                                         {'x-rate-limit-reset': '1790000000', 'retry-after': '900'},
                                         io.BytesIO(b'{}'))
        return original(adapter, method, path, **kwargs)
    monkeypatch.setattr(x_media.x_adapter, 'request', request)
    result, journal, _ = invoke(env)
    assert result.error == 'provider_rate_limited' and result.failure == 'media_held'
    assert journal['media']['phase'] == 'held'
    assert journal['media']['rate_limit_reset'] == 1790000000
    assert journal['media']['retry_after_seconds'] == 900
    assert slept == [] and not wire['tweets']


def test_a_five_hundred_leaves_the_outcome_unknown(env, wire, monkeypatch):
    original = x_media.x_adapter.request
    def request(adapter, method, path, **kwargs):
        if path == '/2/tweets':
            import io
            import urllib.error
            raise urllib.error.HTTPError(adapter.base_url + path, 503, 'Unavailable', {},
                                         io.BytesIO(b'{}'))
        return original(adapter, method, path, **kwargs)
    monkeypatch.setattr(x_media.x_adapter, 'request', request)
    result, journal, _ = invoke(env)
    assert result.failure == 'media_ambiguous' and journal['media']['phase'] == 'unknown'
    assert result.error == 'media_publishing_http_503' and not wire['tweets']


def test_a_source_change_after_upload_holds_instead_of_publishing(env, wire):
    def veto():
        # upload が終わって alt を入れる直前に原稿の bytes が変わる。**次の関門で
        # 気づき、投稿はしない**——upload し直しもしない（held）。
        if calls(wire, 'POST', '/finalize') and not calls(wire, 'POST', '/2/media/metadata'):
            (env[2] / 'a.png').write_bytes(png() + b'\0')
        return None
    result, journal, _ = invoke(env, veto=veto)
    assert result.failure == 'media_held' and journal['media']['phase'] == 'held'
    assert journal['media']['remote_ids'] == ['101'] and not wire['tweets']


# ----- 意図の検査（要求を 1 本も出さない） -----------------------------------

def manifest_of(env, fm):
    return media.manifest_for(fm, env[0])


def test_poll_and_media_cannot_share_one_post(env):
    fm = {'media': [{'file': 'a.png', 'alt': '点'}],
          'attachments': [{'type': 'poll', 'options': ['は', 'いいえ'], 'duration_minutes': 60}]}
    assert x_media.intent_error(manifest_of(env, fm)) == 'unsupported_attachment: x/poll_media_exclusive'


@pytest.mark.parametrize('options,reason', [
    (['は', 'いいえ'], None),
    (['は'], 'media_limit_exceeded: poll_options'),
    (['1', '2', '3', '4', '5'], 'media_limit_exceeded: poll_options'),
    (['は' * 26, 'いいえ'], 'media_limit_exceeded: poll_option_characters'),
])
def test_poll_shape(env, options, reason):
    base_fm = {'attachments': [{'type': 'poll', 'options': ['は', 'いいえ'], 'duration_minutes': 60}]}
    manifest = copy.deepcopy(manifest_of(env, base_fm))
    manifest['attachments'][0]['options'] = options
    assert x_media.intent_error(manifest) == reason


def test_a_declaration_with_one_option_never_reaches_the_adapter(env):
    with pytest.raises(media.MediaError, match='poll needs options'):
        manifest_of(env, {'attachments': [{'type': 'poll', 'options': ['は'], 'duration_minutes': 60}]})


@pytest.mark.parametrize('minutes', [4, 10081])
def test_poll_duration_outside_the_range_is_refused_at_declaration(env, minutes):
    fm = {'attachments': [{'type': 'poll', 'options': ['は', 'いいえ'], 'duration_minutes': minutes}]}
    with pytest.raises(media.MediaError, match='duration_minutes'):
        manifest_of(env, fm)


def test_quote_is_refused_with_the_tier_reason_in_the_table(env):
    from thth import media_capabilities as caps
    with pytest.raises(media.MediaError, match='unsupported_attachment: x/quote'):
        manifest_of(env, {'attachments': [{'type': 'quote', 'uri': '1900000000000000001'}]})
    assert caps.CAPABILITIES['x']['quote'] == 'unsupported: provider_tier_enterprise_only'


def test_webm_is_refused_by_name_without_any_request(env, wire):
    raw = b'webm'
    header = b'\x42\x82' + bytes([0x80 + len(raw)]) + raw
    (env[2] / 'v.webm').write_bytes(b'\x1aE\xdf\xa3' + bytes([0x80 + len(header)]) + header)
    with pytest.raises(media.MediaError, match='x/webm'):
        manifest_of(env, {'media': [{'file': 'v.webm', 'alt': '動画'}]})
    assert wire['calls'] == []
