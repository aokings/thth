"""2.13.1 実機直し: dry-run は理由を言い、publish が断るものをここで断る。"""
import re
import pytest
from thth import cli, core, read_coordination
from tests.test_v213_media_formats import box, mp4
from tests.test_v213_threads_media import env, wire


@pytest.fixture
def send(env, monkeypatch):
    monkeypatch.setattr(core, '_append_run', lambda *a, **k: None)
    monkeypatch.setattr(read_coordination, 'invoke',
                        lambda args, name, fn=None: fn() if fn else args.func(args))
    return lambda name, alt='説明': cli.main(['send', 'alpha', '--media', name, '--alt', alt])


def test_dry_run_states_the_media_reason_and_a_next_step(env, send, wire, capsys):
    # fragment（moof）＝本当に秒数の無い構造。ffmpeg の edit list とは違う。
    (env[2] / 'v.mp4').write_bytes(mp4() + box(b'moof', b''))
    assert send('v.mp4', '動画') == 2
    shown = capsys.readouterr()
    output = shown.out + shown.err
    # 実機では `mode: rehearsal` の 1 行だけで 2 で終わっていた。
    assert output.splitlines()[0] == 'mode: rehearsal'
    assert 'duration_unverifiable' in output and '+faststart' in output
    # 理由も次の一歩も静的: パスは 1 つも映らない。
    assert str(env[2]) not in output and 'v.mp4' not in output
    assert re.search(r'(^|[\s"\'(])/[A-Za-z]', output, re.M) is None
    assert not wire['calls'] and not env[3]['upload']
