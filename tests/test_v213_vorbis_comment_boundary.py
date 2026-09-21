"""Vorbis and FLAC intentionally have different normative field-name maxima."""
import pytest
from thth import media
from tests.test_v213_mastodon_media import env, wire
from tests.test_v213_mastodon_ogg_opus import opus, configure as opus_configure
from tests.test_v213_mastodon_ogg_vorbis import vorbis, configure as vorbis_configure
from tests.test_v213_mastodon_flac import flac, comments, configure as flac_configure


@pytest.mark.parametrize('codec', ['opus', 'vorbis', 'flac'])
@pytest.mark.parametrize('key', [b'}', b'~'])
def test_format_specific_comment_key_boundary(env, wire, codec, key):
    field = key + '=~茶=retained'.encode()
    if codec == 'opus':
        raw = opus(extra=[field]); fm = opus_configure(env, wire, raw)
    elif codec == 'vorbis':
        raw = vorbis(extra=[field]); fm = vorbis_configure(env, wire, raw)
    else:
        raw = flac(extra=[(4, comments(field))]); fm = flac_configure(env, wire, raw)
    source = env[2] / fm['media'][0]['file']
    if key == b'~' and codec != 'flac':
        with pytest.raises(media.MediaError, match='invalid_attachment_structure'):
            media.manifest_for(fm, env[0])
    else:
        with media.prepare(env[2], fm, 'mastodon') as (manifest, prepared):
            row = manifest['files'][0]
            assert row['kind'] == 'audio'
            assert row['source_sha256'] == row['public_sha256']
            assert b''.join(prepared[0].chunks()) == raw
    assert source.read_bytes() == raw
    assert wire['calls'] == []
