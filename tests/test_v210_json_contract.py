"""Every --json CLI entry point: real error/read handlers, isolated empty ledger."""
import argparse
import json
import socket
from pathlib import Path
import pytest
from thth import cli, topic_cli


def json_parsers():
    found = {}
    def visit(parser, path):
        if any('--json' in a.option_strings for a in parser._actions):
            found[' '.join(path)] = parser
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():visit(child, [*path, name])
    visit(cli.build_parser(), [])
    visit(topic_cli.build_parser(), ['topics'])
    found["account add"] = found["account"]
    found["account migrate"] = found["account"]
    return found

# This list is independent of the parser traversal: a newly added JSON command
# must acquire a table row, rather than quietly escaping the contract.
COMMANDS = '''lint|preview|approve|account|revoke|posts|replies|measured|threads|study-report|analytics-report|after|topics|forms|queue|schedule|throw|handoff-report|board|pull|maintain|doctor|app show|admin inventory|admin account|admin log|admin tokens|admin timers|admin release|admin diff|ask before-you-post|mentions|profile|thread|where|who|retract|location|topics suggest|topics observe|topics record-decision|topics decision|topics observation|topics retract|topics unretract|topics profile|topics adopt-reason|topics adoptions|topics record-vocabulary|topics vocabulary|topics record-review|topics review|topics record-form-spec|topics form-spec|topics form-check|topics improvements|topics impact|topics record-hypothesis|topics hypotheses'''.split('|') + ['account add', 'account migrate', 'unanswered']


def test_json_table_covers_every_parser():
    assert set(COMMANDS) == set(json_parsers())


@pytest.mark.parametrize('command', COMMANDS)
def test_json_cli_stdout_is_one_value_or_empty_error(command, tmp_path, monkeypatch, capsys):
    root=tmp_path/'root';(root/'accounts').mkdir(parents=True)
    monkeypatch.setenv('THTH_ROOT',str(root));monkeypatch.setenv('THTH_ACCOUNTS_DIR',str(root/'accounts'))
    monkeypatch.setenv('HOME',str(root));monkeypatch.setenv('XDG_CONFIG_HOME',str(root/'config'))
    monkeypatch.setattr(socket,'socket',lambda *a,**k:pytest.fail('unexpected network'))
    parser=json_parsers()[command];argv=command.split()
    for action in parser._actions:
        if isinstance(action,argparse._SubParsersAction):continue
        if not action.option_strings and action.required:
            argv.append(str(tmp_path/'missing.md') if action.dest in ('file','path') else
                        str(next(iter(action.choices))) if action.choices else 'missing-json-account')
        elif action.option_strings and action.required:
            flag=next((s for s in action.option_strings if s.startswith('--')),action.option_strings[0])
            argv.append(flag)
            if action.nargs!=0:argv.append(str(next(iter(action.choices))) if action.choices else 'fixture')
    if command=='admin diff':argv.append('--since-last-read')
    argv.append('--json')
    try:rc=cli.main(argv)
    except SystemExit as exc:rc=exc.code
    output=capsys.readouterr()
    if output.out.strip():
        json.loads(output.out)
    else:
        assert rc!=0 and output.err, (command,rc,output)


@pytest.mark.parametrize('command', ['throw', 'maintain', 'account', 'handoff-report'])
def test_json_real_account_read_and_rehearsal(command, isolated_account_factory, capsys, monkeypatch):
    isolated_account_factory('json-one')
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('unexpected network'))
    args=[command,'json-one','--json']
    if command=='maintain':args=[command,'--account','json-one','--check','--json']
    cli.main(args)
    output=capsys.readouterr()
    value=json.loads(output.out)
    assert isinstance(value, dict)
    assert value
