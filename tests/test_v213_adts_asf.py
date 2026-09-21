"""Named-before-parsed refusals for standalone AAC/ADTS and ASF (第 5・6 段 P2).

Generated headers only; no provider I/O and no decoder oracle.
"""
import os
import socket
import pytest
from thth import media, media_delivery, mediaformats
from tests.test_v213_mastodon_mp3 import frame

PAYLOAD = 16


def adts_frame(*, identity=0, profile=1, rate_index=4, channels=2, payload=PAYLOAD):
    """One well-formed 7-byte ADTS header (protection absent) and its payload."""
    length = 7 + payload
    fullness = 0x7ff
    head = bytes((
        0xff,
        0xf0 | (identity << 3) | (0 << 1) | 1,                       # layer bits are 00
        (profile << 6) | (rate_index << 2) | (channels >> 2),
        ((channels & 3) << 6) | (length >> 11),
        (length >> 3) & 0xff,
        ((length & 7) << 5) | (fullness >> 6),
        ((fullness & 0x3f) << 2),
    ))
    return head + bytes(payload)


def adts(frames=1):
    return adts_frame() * frames


def write(tmp_path, name, raw):
    path = tmp_path / name
    path.write_bytes(raw)
    return path


def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('network reached')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


def test_adts_and_mpeg_headers_are_distinguished_by_the_layer_bits():
    from thth.mpeg_audio import is_adts
    assert is_adts(adts(1)[:2]) and is_adts(adts(3)[:2])
    # A real MPEG Layer III header shares the 0xFFF syncword but never layer 00.
    assert not is_adts(frame()[:2]) and frame()[0] == 0xff


@pytest.mark.parametrize('medium', ['mastodon', 'threads', 'bluesky'])
@pytest.mark.parametrize('frames', [1, 3])
def test_standalone_aac_is_named_before_parsing(tmp_path, monkeypatch, medium, frames):
    no_network(monkeypatch)
    write(tmp_path, 'sound.aac', adts(frames))
    fm = {'media': [{'file': 'sound.aac', 'alt': '音声の説明'}]}
    with pytest.raises(media.MediaError, match=f'unsupported_attachment: {medium}/aac_adts'):
        with media.prepare(tmp_path, fm, medium):pass


def test_real_mp3_frame_still_takes_the_mp3_path(tmp_path):
    raw = frame()
    path = write(tmp_path, 'sound.mp3', raw)
    fd = os.open(path, os.O_RDONLY)
    try:
        assert mediaformats.inspect(fd, len(raw)).format == 'mp3'
    finally:
        os.close(fd)


@pytest.mark.parametrize('medium', ['mastodon', 'threads', 'bluesky'])
def test_asf_is_named_and_deferred(tmp_path, monkeypatch, medium):
    no_network(monkeypatch)
    write(tmp_path, 'sound.wma', mediaformats.ASF_HEADER_GUID + bytes(64))
    fm = {'media': [{'file': 'sound.wma', 'alt': '音声の説明'}]}
    with pytest.raises(media.MediaError, match=f'unsupported_attachment: {medium}/asf'):
        with media.prepare(tmp_path, fm, medium):pass


@pytest.mark.parametrize('name,suffix,extra', [
    ('sound.aac', 'threads/aac_adts',
     ['warning: privacy_inspection_boundary_unverified', '次の一歩: m4a に入れ直す']),
    ('sound.wma', 'threads/asf', ['warning: deferred_by_ruling_c14']),
])
def test_lint_carries_the_static_note_and_next_step(tmp_path, monkeypatch, name, suffix, extra):
    no_network(monkeypatch)
    payload = adts(1) if name.endswith('.aac') else mediaformats.ASF_HEADER_GUID + bytes(64)
    write(tmp_path, name, payload)
    cfg = {'account': 'alpha', 'media': 'threads', 'repo_dir': str(tmp_path)}
    notes = media_delivery.lint_notes(cfg, {'media': [{'file': name, 'alt': '音声の説明'}]})
    assert notes == ['unsupported_attachment: ' + suffix] + extra
