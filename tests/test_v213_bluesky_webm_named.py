"""Bluesky cannot take WebM: the container is named before it is parsed.

第 5・6 段 P2 — 媒体ごとの方針表（`media.MEDIUM_POLICY`）。生成 fixture のみ。
"""
import os
import socket
import pytest
from thth import media, mediaformats
from tests.test_v213_mastodon_webm_audio import webm


def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('network reached')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)


def doctype_only():
    """EBML magic and the bounded DocType element, with no Segment at all."""
    body = b'\x42\x82' + bytes([0x80 + 4]) + b'webm'
    return b'\x1aE\xdf\xa3' + bytes([0x80 + len(body)]) + body


@pytest.mark.parametrize('name,raw', [('truncated', doctype_only()), ('valid_audio', webm())])
def test_bluesky_names_webm_before_structure_parsing(tmp_path, monkeypatch, name, raw):
    no_network(monkeypatch)
    (tmp_path / 'sound.webm').write_bytes(raw)
    fm = {'media': [{'file': 'sound.webm', 'alt': '音声の説明'}]}
    with pytest.raises(media.MediaError, match='unsupported_attachment: bluesky/webm'):
        with media.prepare(tmp_path, fm, 'bluesky'):pass


def test_mastodon_still_accepts_the_same_webm(tmp_path):
    raw = webm()
    (tmp_path / 'sound.webm').write_bytes(raw)
    fm = {'media': [{'file': 'sound.webm', 'alt': '音声の説明'}]}
    with media.prepare(tmp_path, fm, 'mastodon') as (manifest, _):
        assert manifest['files'][0]['format'] == 'webm'


def test_policy_table_names_every_medium():
    assert set(media.MEDIUM_POLICY) == {'threads', 'bluesky', 'mastodon'}
    assert media.MEDIUM_POLICY['mastodon']['allow_webm'] is True
    assert media.MEDIUM_POLICY['threads']['allow_edit_lists'] is True
    assert all(p.get('allow_webm') is False for m, p in media.MEDIUM_POLICY.items() if m != 'mastodon')
