"""2.14.0: `thth admin budget x-posts` の CLI と MCP（管理者・--by 必須）。"""
import json

import pytest

from thth import admin_log, budget_x, budget_x_posts as bp, cli
from tests.test_v212_budget import env, snapshot  # noqa: F401
from tests.test_v212_server_writes import env as mcp_env  # noqa: F401


def test_the_default_is_zero_and_reading_writes_nothing(env, capsys):
    before = snapshot(env)
    assert cli.main(['admin', 'budget', 'x-posts', '--json']) == 0
    value = json.loads(capsys.readouterr().out)
    assert value['kind'] == 'x_posts' and value['monthly'] == 0
    assert value['post_refusal'] == 'post_budget_exhausted'
    assert 'tier_limit_not_observed' in value['cannot_say']
    assert snapshot(env) == before


def test_setting_the_cap_records_presence_only(env, capsys):
    assert cli.main(['admin', 'budget', 'x-posts', '--monthly', '40', '--by', 'masaru', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['monthly'] == 40
    events, broken = admin_log.read(event='budget_set')
    assert not broken and len(events) == 1
    event = events[0]
    assert event['by'] == 'masaru' and event['account'] == 'x-posts' and event['via'] == 'cli'
    assert event['diff'] == {'x_posts_budget': ['absent', 'present']}
    assert bp.monthly_cap() == 40
    # 読取予算（USD）は別の口のまま。
    assert budget_x.read()['policy']['amount'] == '0'


def test_the_cap_cannot_be_set_without_by(env):
    assert cli.main(['admin', 'budget', 'x-posts', '--monthly', '40']) == 2
    assert bp.monthly_cap() == 0 and admin_log.read(event='budget_set')[0] == []


@pytest.mark.parametrize('extra', [['--currency', 'JPY'], ['--rate', '150'],
                                   ['--rate-source', 'somewhere']])
def test_the_money_options_are_not_accepted_for_the_count(env, extra, capsys):
    assert cli.main(['admin', 'budget', 'x-posts', '--monthly', '40', '--by', 'masaru', *extra]) == 2
    assert bp.monthly_cap() == 0


@pytest.mark.parametrize('bad', ['-1', 'とても', '1.5', ''])
def test_an_invalid_count_is_refused_and_nothing_is_written(env, bad):
    before = snapshot(env)
    assert cli.main(['admin', 'budget', 'x-posts', '--monthly', bad, '--by', 'masaru']) == 2
    assert snapshot(env) == before


def test_the_read_budget_command_is_untouched(env, capsys):
    assert cli.main(['admin', 'budget', 'x', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['media'] == 'x'


# ----- MCP ------------------------------------------------------------------

def test_mcp_sets_the_count_with_kind_x_posts(mcp_env, monkeypatch):
    from tests.test_v212_budget_mcp import manager
    from tests.test_mcp import _load_server_module
    manager(mcp_env, monkeypatch)
    server = _load_server_module()
    names = {row['name'] for row in server.server_tools(
        __import__('thth.report_http', fromlist=['x']).load_credentials(mcp_env['path'])[1][0][3])}
    assert 'thth_admin_budget_set' in names
    result = server.call_tool('thth_admin_budget_set',
                              {'monthly': '25', 'by': 'masaru', 'kind': 'x_posts'})
    assert not result.get('isError')
    assert json.loads(result['content'][0]['text'])['monthly'] == 25
    assert bp.monthly_cap() == 25 and budget_x.read()['policy']['amount'] == '0'
    event = admin_log.read(event='budget_set')[0][0]
    assert event['via'] == 'mcp' and event['account'] == 'x-posts' and event['by'] == 'masaru'


def test_mcp_refuses_money_options_alongside_the_count(mcp_env, monkeypatch):
    from tests.test_v212_budget_mcp import manager
    from tests.test_mcp import _load_server_module
    manager(mcp_env, monkeypatch)
    server = _load_server_module()
    for args in ({'monthly': '25', 'by': 'masaru', 'kind': 'x_posts', 'currency': 'JPY'},
                 {'monthly': '25', 'by': 'masaru', 'kind': 'not_a_kind'},
                 {'monthly': '25', 'kind': 'x_posts'}):
        assert server.call_tool('thth_admin_budget_set', args).get('isError')
    assert bp.monthly_cap() == 0 and admin_log.read(event='budget_set')[0] == []
