"""A blocking shared-lease regression must fail within a hard child deadline."""
import contextlib
import json
from pathlib import Path
import selectors
import subprocess
import sys

import pytest
from tests.test_v212_server_writes import env


@contextlib.contextmanager
def holder():
    code = "from thth.leave_gate import lease\nimport sys\nwith lease('alpha',exclusive=True):\n print('ready',flush=True)\n sys.stdin.readline()\n"
    child = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            assert ready.select(timeout=3), 'exclusive holder did not start'
        assert child.stdout.readline().strip() == 'ready'
        yield
    finally:
        try:
            _, err = child.communicate('\n', timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=3)
            pytest.fail('exclusive holder failed to exit')
        assert child.returncode == 0 and not err


@pytest.mark.parametrize('operation', ['replies', 'send'])
def test_reader_child_deadline_fails_blocking_lease(env, tmp_path, operation):
    text = tmp_path / 'body.txt'
    text.write_text('synthetic dry send')
    marker = tmp_path / 'provider-called'
    command = ['replies', 'alpha', '--json'] if operation == 'replies' else ['send', 'alpha', '--text-file', str(text)]
    code = """import json,sys
from pathlib import Path
from thth import cli,adapters
def forbidden(*a,**k):
 Path(sys.argv[2]).write_text('called')
 raise AssertionError('provider must not run')
adapters.make_adapter=forbidden
raise SystemExit(cli.main(json.loads(sys.argv[1])))
"""
    with holder():
        # subprocess.run kills and waits for the child on timeout. A regression
        # cannot trap this pytest process before an elapsed-time assertion.
        result = subprocess.run([sys.executable, '-c', code, json.dumps(command), str(marker)],
                                capture_output=True, text=True, timeout=3)
    assert result.returncode == 2 and result.stderr.strip() == 'account_leaving\n' + __import__('thth.report_inbox').report_inbox.CHANNEL_LINE  # 3.1.2 §3.5: 断りの理由行の後ろに受け口の案内 1 行
    if operation == 'replies':
        assert json.loads(result.stdout) == {'error': 'account_leaving', 'cannot_say': ['account_leaving']}
    assert not marker.exists()
    assert not (env['root'] / 'state/alpha/sent').exists()
