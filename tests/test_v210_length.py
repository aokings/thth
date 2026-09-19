from pathlib import Path
import pytest
from thth import cli, core, queuefile
from tests.conftest import write_queue_file

@pytest.mark.parametrize('media,text,expected',[('bluesky','か\u3099',1),('threads','茶',1),('mastodon','茶',1)])
def test_send_and_approve_length_pair(isolated_account_factory, capsys, media, text, expected):
    cfg=isolated_account_factory('one',media=media,char_limit=12)
    lines=[];result=core.send_once('one',text=text,log=lines.append)
    assert result.exit_code==0 and result.digest
    wanted=queuefile.length_line(media,text,{'char_limit':12})
    assert f'{expected}/12' in wanted and wanted in lines
    path=write_queue_file(cfg['queue_dir'],'one.md',media=media,body=f'## {media}\n\n{text}\n',fm_overrides={'account':'one','status':'draft'})
    assert cli.main(['approve',path])==1
    output=capsys.readouterr().out
    assert wanted in output and 'digest:' in output

@pytest.mark.parametrize('command',['send','approve'])
def test_over_limit_has_no_digest(isolated_account_factory,capsys,command):
    cfg=isolated_account_factory('one',media='bluesky',char_limit=2)
    if command=='send':
        lines=[];result=core.send_once('one',text='茶'*3,log=lines.append)
        assert result.exit_code!=0 and result.digest is None and not any('digest:' in x for x in lines)
    else:
        path=write_queue_file(cfg['queue_dir'],'one.md',media='bluesky',body='## bluesky\n\n茶茶茶\n',fm_overrides={'account':'one','status':'draft'})
        assert cli.main(['approve',path])!=0
        assert 'digest:' not in capsys.readouterr().out
