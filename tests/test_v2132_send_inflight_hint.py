"""2.13.2 実機直し: inflight が残っているなら、dry-run でも断って次の一歩を言う。

作成の時間切れ（`media_creating_timeout`）は inflight を `unknown` のまま
残す。そのあと `thth send` を打った人が最初に見るのがここ——2.13.1 までは
`--production` なしだと digest まで出して 0 で終わり、残っていることを一言も
言わなかった。`--production` を付ければ断るが、理由 1 行だけで「どうすれば
いいか」は無かった。
"""
import pytest
from thth import accounts, cli, core, inflight, media, media_delivery, read_coordination
from tests.test_v213_threads_media import env, wire


@pytest.fixture
def send(env, monkeypatch):
    monkeypatch.setattr(core, '_append_run', lambda *a, **k: None)
    monkeypatch.setattr(read_coordination, 'invoke',
                        lambda args, name, fn=None: fn() if fn else args.func(args))
    return lambda *extra: cli.main(['send', 'alpha', '--media', 'a.png', '--alt', '説明', *extra])


def seed_unknown(env, reason='media_creating_timeout'):
    """作成の時間切れが残す journal と同じ形（phase `unknown`・remote_ids 空）。"""
    state = accounts.state_dir_for('alpha')
    manifest = media.manifest_for({'media': [{'file': 'a.png', 'alt': '説明'}]}, env[0])
    inflight.write(state, file='(send)', started='2030-01-01T00:00:00+09:00')
    media_delivery._progress('alpha', manifest, 'https://graph.invalid', 'unknown',
                             remote_ids=[], reason=reason)
    return state


@pytest.mark.parametrize('extra', [(), ('--production',)])
def test_send_refuses_and_says_what_to_do(env, send, wire, capsys, extra):
    seed_unknown(env)
    assert send(*extra) == 1
    shown = capsys.readouterr()
    output = shown.out + shown.err
    assert 'inflight が残っています' in output
    assert inflight.NEXT_STEP in output
    # 断ったのだから digest も本文の下見も出ない。媒体には一度も触らない。
    assert 'digest: ' not in output and not wire['calls'] and not env[3]['upload']
    # 次の一歩は 1 行だけ。
    assert len([line for line in output.splitlines() if line.startswith('次の一歩')]) == 1


def test_the_refusal_stays_static(env, send, capsys):
    seed_unknown(env)
    assert send() == 1
    shown = capsys.readouterr()
    output = shown.out + shown.err
    # repo の実パスもアカウントの state パスも映さない（既存の静的の約束）。
    assert str(env[2]) not in output and str(accounts.state_dir_for('alpha')) not in output


def test_the_inflight_is_left_exactly_as_it_was(env, send):
    state = seed_unknown(env)
    before = inflight.read(state)
    assert send() == 1 and send('--production') == 1
    assert inflight.read(state) == before


def test_without_an_inflight_the_dry_run_still_shows_the_digest(env, send, capsys):
    assert send() == 0
    shown = capsys.readouterr()
    output = shown.out + shown.err
    assert 'digest: ' in output and 'inflight が残っています' not in output
    assert inflight.NEXT_STEP not in output
