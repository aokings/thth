"""A refusal carries its reason. `invalid_draft: lint_failed` alone is not one.

初回の 3.0 通し運転（2026-09-23）で `thth_draft_put` が `invalid_draft: lint_failed`
とだけ返した。実際の理由は台帳の `hashtags: false` が本文の `#語` を弾いたこと
だったが、呼び手にはそれが見えず、同じ本文を送り直すしかなかった。返すのは
lint 自身の静的な符丁であって、lint の日本語 1 行でも path でもない。
"""
import json
import pytest
from thth import lint, server_writes as writes
from thth.report_service import ReportServiceError
from tests.test_v212_server_writes import env  # noqa: F401


def _request(body):
    return dict(operation='draft_put', account='alpha', body=body,
                publish_at='2030-01-01T12:00:00+09:00')


def _refusal(env, body):
    with pytest.raises(ReportServiceError) as refusal:
        writes.execute(env['context'], _request(body))
    assert str(refusal.value) == 'invalid_draft'
    return refusal.value.reason


def _no_hashtags(env):
    path = env['root'] / 'accounts/alpha.json'
    cfg = json.loads(path.read_text())
    cfg['hashtags'] = False
    path.write_text(json.dumps(cfg))


def test_hashtag_on_a_hashtags_false_account_names_the_ledger(env):
    _no_hashtags(env)
    assert _refusal(env, '本文に #語 を入れる') == 'hashtags_disabled_by_ledger'


def test_too_long_body_names_the_length(env):
    assert _refusal(env, 'あ' * 501) == 'too_long'


def test_every_reason_is_static_and_carries_no_free_text(env):
    _no_hashtags(env)
    for body in ('本文に #語 を入れる', 'あ' * 501):
        reason = _refusal(env, body)
        assert reason in lint.REASONS
        assert reason.isascii() and reason.islower() and str(env['root']) not in reason
    assert lint.REASONS <= writes.DRAFT_REASONS


def test_unknown_lint_text_falls_back_instead_of_leaking_itself():
    free_text = '/srv/thth/repos/_server/alpha/queue/.lint-xyz.tmp を読めません'
    assert lint.reason_code([free_text]) == 'lint_failed'
    assert lint.reason_code(['warning: 450 字を超えています', lint.HASHTAG_DISABLED]) == 'hashtags_disabled_by_ledger'
    assert lint.reason_code([]) == 'lint_failed'


def test_http_and_mcp_both_hand_the_reason_back(env, monkeypatch):
    import http.client
    import threading
    from thth import report_http
    _no_hashtags(env)
    server = report_http.PrivateReportServer(env['path'], port=0)
    monkeypatch.setattr(__import__('socket').socket, 'connect', env['connect'])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=10)
        connection.request('POST', '/write', json.dumps(_request('本文に #語 を入れる')),
                           {'Authorization': 'Bearer ' + env['bearer'], 'Content-Type': 'application/json'})
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=10)
    assert response.status == 400
    assert payload == {'error': 'invalid_draft', 'reason': 'hashtags_disabled_by_ledger'}

    from tests.test_mcp import _load_server_module
    module = _load_server_module()
    monkeypatch.setenv('THTH_REPORT_CREDENTIALS', str(env['path']))
    monkeypatch.setenv('THTH_REPORT_TOKEN', env['bearer'])
    arguments = _request('本文に #語 を入れる'); arguments.pop('operation')
    result = module.server_call('thth_draft_put', arguments)
    assert result['isError']
    assert result['content'][0]['text'] == 'invalid_draft: hashtags_disabled_by_ledger'
