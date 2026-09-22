"""X の添付（chunked upload → alt → `POST /2/tweets`）。設計 2.14.0 §2・§7。

**bytes は 2.13 の sanitize を通ったものだけ**（`media.prepare` の公開 bytes）。
上限は**暫定の SI**（画像 5,000,000・GIF 15,000,000・動画は thth の転送上限
1,000,000,000）——公式の "5 MB" は単位が定義されていないので、**確かめていない
ことを数字の名前で言う**（`media_limit_exceeded: bytes (provisional SI …)`）。

**順序**（§7 の chunked upload）:
`initialize` → `append`（`segment_index`・5 MB 以下）→ `finalize` →
`processing_info` があれば `check_after_secs` で STATUS を問う → `metadata`
（alt・画像と GIF は thth の規律で必須）→ `POST /2/tweets` の `media.media_ids`。

**durable intent**: 要求の前に journal（`media_delivery`）へ phase を落とす。
**結果不明は自動で投げ直さない**・**upload 済みで止まったら `held`**（再 upload
しない）——Threads／Bluesky と同じ骨。
"""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import base, x as x_adapter
from .. import accounts, httpsafe, jst, media

# 種類 → X の `media_category`（§7）。
CATEGORY = {'image': 'tweet_image', 'gif': 'tweet_gif', 'video': 'tweet_video'}
MIME = {'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'mp4': 'video/mp4'}
IMAGE_FORMATS = ('jpeg', 'png')

# **暫定 SI の上限**（設計 §2「公式値の単位が未定義」）。1 か所にまとめる。
MAX_IMAGE_BYTES = 5_000_000
MAX_GIF_BYTES = 15_000_000
MAX_VIDEO_BYTES = 1_000_000_000
# chunk は "at or below 5 MB"（§7。server の上限は 8 MB）。
CHUNK_BYTES = 5_000_000
MAX_IMAGES = 4
ALT_MAX = 1000

IMAGE_POLL_SECONDS = 120.0
VIDEO_POLL_SECONDS = 1800.0
POLL_INTERVAL = 2.0
MAX_CHECK_AFTER_SECONDS = 600
UPLOAD_TIMEOUT_SECONDS = 120.0


def require(ok, reason):
    if not ok:
        raise media.MediaError(reason)


def limit_for(row):
    return (MAX_VIDEO_BYTES if row['kind'] == 'video'
            else MAX_GIF_BYTES if row['kind'] == 'gif' else MAX_IMAGE_BYTES)


def intent_error(manifest):
    """要求を 1 本も出す前に、この意図が X に載るかどうかだけを答える。

    理由の `<key>` は `thth/media_capabilities.py` の表の key（依頼 第 10 段）。
    """
    if manifest['captions']:
        return 'unsupported_attachment: x/captions'
    poll = None
    for row in manifest['attachments']:
        if row['type'] != 'poll':
            return 'unsupported_attachment: x/typed_attachment'
        if set(row) != {'type', 'options', 'duration_minutes'}:
            return 'unsupported_attachment: x/poll_option'
        if not 2 <= len(row['options']) <= 4:
            return 'media_limit_exceeded: poll_options'
        # C20-B と同じ暫定: この欄だけコードポイントで数える（重み付けは未確認）。
        if any(not 1 <= len(option) <= 25 for option in row['options']):
            return 'media_limit_exceeded: poll_option_characters'
        if not 5 <= row['duration_minutes'] <= 10080:
            return 'media_limit_exceeded: poll_duration_minutes'
        poll = row
    options = manifest['post_options']
    if set(options) - {'reply_settings'}:
        return 'unsupported_attachment: x/post_options'
    if 'reply_settings' in options and options['reply_settings'] not in x_adapter.REPLY_SETTINGS:
        return 'unsupported_attachment: x/reply_settings'
    rows = manifest['files']
    if poll is not None and rows:
        # 投票と添付は同じ 1 本に載らない。**要求の前に**断る（設計 §2）。
        return 'unsupported_attachment: x/poll_media_exclusive'
    if not rows:
        if poll is None and not options:
            return 'unsupported_attachment: x/no_media'
        return None
    kinds = set()
    for row in rows:
        if row['role'] != 'media':
            return 'unsupported_attachment: x/thumbnail'
        image = row['kind'] == 'image' and row['format'] in IMAGE_FORMATS
        gif = row['kind'] == 'image' and row['format'] == 'gif'
        video = row['kind'] == 'video' and row['format'] == 'mp4'
        if not (image or gif or video):
            return 'unsupported_attachment: x/' + str(row['format'])
        kind = 'video' if video else 'gif' if gif else 'image'
        kinds.add(kind)
        if type(row['public_size']) is not int or row['public_size'] < 1:
            return 'media_size_unavailable'
        if row['public_size'] > limit_for({'kind': kind}):
            return ('media_limit_exceeded: bytes (provisional SI '
                    + str(limit_for({'kind': kind})) + ')')
        if not isinstance(row['alt'], str) or len(row['alt']) > ALT_MAX:
            return 'media_limit_exceeded: alt_characters'
        width, height = row.get('width'), row.get('height')
        if (type(width) not in (int, float) or type(height) not in (int, float)
                or not math.isfinite(width) or not math.isfinite(height)
                or width <= 0 or height <= 0):
            return 'media_dimensions_unavailable'
        if video:
            duration = row.get('duration')
            if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
                return 'media_duration_unavailable'
    if len(kinds) > 1:
        # 「画像 4 枚まで・GIF 1・動画 1」のどれか 1 つ（§7）。混ぜない。
        return 'unsupported_attachment: x/media_mixed_kinds'
    if 'image' in kinds and len(rows) > MAX_IMAGES:
        return 'media_limit_exceeded: count'
    if kinds & {'gif', 'video'} and len(rows) != 1:
        return 'media_limit_exceeded: count'
    return None


def notes(manifest, items=()):
    """lint が出す但し書き（**値は出さない・静的な語だけ**）。"""
    out = [x_adapter.LENGTH_NOTE]
    if any(row['type'] == 'poll' for row in manifest['attachments']):
        out.append('warning: x poll option characters use provisional Unicode code points; '
                   'live-provider counting unverified')
    for row in manifest['files']:
        if row['kind'] == 'video':
            out.append('warning: x video alt is attempted but unobserved; '
                       'a refusal is recorded as x_video_alt_unsupported')
        out.append('warning: x byte limits are provisional SI figures; '
                   'the provider unit is undocumented')
    return out


def _multipart(segment_index, payload):
    boundary = 'thth-' + uuid.uuid4().hex
    pre = ('--' + boundary + '\r\nContent-Disposition: form-data; name="segment_index"'
           '\r\n\r\n' + str(segment_index) + '\r\n').encode('ascii')
    pre += ('--' + boundary + '\r\nContent-Disposition: form-data; name="media"; '
            'filename="segment"\r\nContent-Type: application/octet-stream\r\n\r\n').encode('ascii')
    end = ('\r\n--' + boundary + '--\r\n').encode('ascii')
    return pre + payload + end, {'Content-Type': 'multipart/form-data; boundary=' + boundary}


def _identifier(value, *, expected=None):
    data = value.get('data')
    require(isinstance(data, dict), 'media_response_invalid')
    identifier = data.get('id')
    require(isinstance(identifier, str) and x_adapter.POST_ID.fullmatch(identifier),
            'media_response_invalid: id')
    require(expected is None or identifier == expected, 'media_response_invalid: id changed')
    return identifier, data


def _processing(data):
    info = data.get('processing_info')
    if info is None:
        return None
    require(isinstance(info, dict), 'media_response_invalid: processing_info')
    state = info.get('state')
    require(isinstance(state, str) and state in ('pending', 'in_progress', 'succeeded', 'failed'),
            'media_response_invalid: processing_state')
    return info


def _check_after(info):
    value = info.get('check_after_secs')
    if type(value) is not int or not 0 <= value <= MAX_CHECK_AFTER_SECONDS:
        return POLL_INTERVAL
    return float(value)


def publish(adapter, post, *, before_publish=None, slot=None):
    ts = jst.iso()
    phase = 'preflight'
    ids = []
    warnings = []
    progress = post.media_progress

    def record(value, **details):
        nonlocal phase
        phase = value
        progress(value, remote_ids=list(ids), **details)

    def veto():
        for item in post.media_files:
            item.verify()
        if before_publish:
            reason = before_publish()
            require(not reason, str(reason))

    try:
        require(callable(progress), 'media_journal_required')
        reason = intent_error(post.media_manifest)
        require(reason is None, reason or '')
        require(len(post.media_files) == len(post.media_manifest['files'])
                and all(item.manifest == row for item, row in
                        zip(post.media_files, post.media_manifest['files'])),
                'media_prepared_mismatch')
        granted = getattr(adapter, 'granted_scopes', None)
        require(not post.media_files or granted is None or 'media.write' in granted,
                'x_scope_missing: media.write; thth app set x --by <名前> のあと thth auth '
                + getattr(adapter, 'auth_account', '<account>') + ' --by <名前>')
        veto()
        for index, item in enumerate(post.media_files):
            row = item.manifest
            kind = ('video' if row['kind'] == 'video'
                    else 'gif' if row['format'] == 'gif' else 'image')
            record('uploading', index=index)
            start = time.monotonic()
            deadline = start + (VIDEO_POLL_SECONDS if kind == 'video' else IMAGE_POLL_SECONDS)
            _, data = _identifier(x_adapter.request(
                adapter, 'POST', '/2/media/upload/initialize',
                json_body={'media_type': MIME[row['format']],
                           'total_bytes': row['public_size'],
                           'media_category': CATEGORY[kind]},
                socket_timeout=UPLOAD_TIMEOUT_SECONDS)[1])
            identifier = data['id']
            ids.append(identifier)
            record('uploading', index=index)
            for segment, payload in enumerate(item.chunks(CHUNK_BYTES)):
                veto()
                body, headers = _multipart(segment, payload)
                status, _ = x_adapter.request(
                    adapter, 'POST', '/2/media/upload/' + identifier + '/append',
                    data=body, headers=headers, socket_timeout=UPLOAD_TIMEOUT_SECONDS)
                require(200 <= status < 300, 'media_response_invalid: append')
            veto()
            _, data = _identifier(x_adapter.request(
                adapter, 'POST', '/2/media/upload/' + identifier + '/finalize',
                socket_timeout=UPLOAD_TIMEOUT_SECONDS)[1], expected=identifier)
            info = _processing(data)
            if info is not None and info['state'] != 'succeeded':
                record('processing', index=index)
                while True:
                    require(info['state'] != 'failed', 'media_processing_failed')
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, 'media_processing_timeout')
                    time.sleep(min(_check_after(info), max(0.0, remaining)))
                    veto()
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, 'media_processing_timeout')
                    _, data = _identifier(x_adapter.request(
                        adapter, 'GET', '/2/media/upload',
                        query={'command': 'STATUS', 'media_id': identifier},
                        timeout=remaining)[1], expected=identifier)
                    info = _processing(data)
                    require(info is not None, 'media_response_invalid: processing_info')
                    if info['state'] == 'succeeded':
                        break
            record('ready', index=index)
            # alt は画像と GIF では thth の規律で必須。動画は API 上の可否が
            # 未確認なので**試して、断られたら警告として残す**（止めない）。
            veto()
            record('describing', index=index)
            try:
                status, _ = x_adapter.request(
                    adapter, 'POST', '/2/media/metadata',
                    json_body={'id': identifier, 'alt_text': {'text': row['alt']}})
                require(200 <= status < 300, 'media_response_invalid: metadata')
            except (urllib.error.HTTPError, media.MediaError):
                if kind != 'video':
                    raise
                warnings.append('x_video_alt_unsupported')
                record('ready', index=index, warnings=list(warnings))
        veto()
        body = x_adapter.tweet_body(post, post.media_manifest, ids)
        record('publishing', warnings=list(warnings))
        if slot is not None:
            slot.dispatched()
        status, value = x_adapter.request(adapter, 'POST', '/2/tweets', json_body=body)
        identifier = x_adapter.post_id_of(value)
        require(status in (200, 201) and identifier is not None, 'media_response_invalid: post id')
        if slot is not None:
            slot.settle()
        record('published', post_id=identifier, warnings=list(warnings))
        return base.PublishResult(
            identifier, x_adapter.post_url(adapter.username, identifier), ts,
            media=[{'sha256': item.manifest['public_sha256'], 'kind': item.manifest['kind'],
                    'alt_present': base.alt_present(item.manifest), 'remote_id': remote}
                   for item, remote in zip(post.media_files, ids)])
    except accounts.AccountStopped:
        return base.PublishResult(None, None, ts, error='account_stopped',
                                  failure='media_held' if ids and phase not in ('publishing', 'published')
                                  else 'media_ambiguous' if phase != 'preflight' else 'publish_vetoed')
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        http = isinstance(exc, urllib.error.HTTPError)
        endpoint = isinstance(exc, httpsafe.EndpointRejected)
        definite = endpoint or http and 400 <= exc.code < 500
        held = bool(ids) and (phase in ('ready', 'processing', 'describing') or definite)
        uncertain = phase != 'preflight' and not held and not definite
        limits = x_adapter.rate_limit(exc) if http and exc.code == 429 else {}
        reason = ('media_endpoint_rejected' if endpoint
                  else 'provider_rate_limited' if http and exc.code == 429
                  else 'media_' + phase + '_http_' + str(exc.code) if http
                  else str(exc) if isinstance(exc, media.MediaError)
                  else 'media_' + phase + '_failed')
        try:
            if callable(progress):
                record('held' if held else 'unknown' if uncertain else 'failed',
                       reason=reason, warnings=list(warnings), **limits)
        except (OSError, ValueError, accounts.AccountStopped):
            pass
        return base.PublishResult(None, None, ts, error=reason,
                                  failure='media_held' if held else
                                  'media_ambiguous' if uncertain else 'publish_definite')
