"""The fork limitation is visible and manual token ingestion is not a workaround."""
from pathlib import Path
import pytest
from thth import accounts, oauth
from tests.test_v211_authflow import env
from tests.test_v211_auth_integration import cfg_media
from tests.test_v211_mastodon_auth import env as masto_env
from tests import test_v211_mastodon_auth as masto_tests
from tools import build_site


def test_lifetime_limitation_visible_in_guide_and_generated_site():
    root=Path(__file__).parents[1]
    guide=(root/'docs/導入_承認を押すだけ.md').read_text()
    page=build_site.outputs()['index.html']
    for value in (guide,page):
        assert 'expires_in または refresh_token を返す非標準実装に未対応' in value
        assert '手動 token set も期限・更新情報を受け取らず期限なしとして保存' in value
        assert 'その回避策にはなりません' in value
    assert (root/'callback/public/index.html').read_text()==page


@pytest.mark.parametrize('unexpected',[{'expires_in':3600},{'expires_in':None},{'refresh_token':'generated'}])
def test_fork_response_rejection_remains_loud(masto_env,unexpected):
    masto_tests.test_nonstandard_lifetime_is_not_misreported_as_no_expiry(masto_env,unexpected)
    assert any('mastodon_nonstandard_token_lifetime_unsupported' in s for s in masto_env['lines'])


def test_manual_token_set_does_not_observe_lifetime(env,monkeypatch):
    cfg_media(env,'mastodon')
    class Adapter:
        def whoami(self):return {'user_id':'123','username':'demo'}
    monkeypatch.setattr('thth.adapters.make_adapter',lambda *a,**k:Adapter())
    assert oauth.run_token_set('alpha',stdin=True,by='operator',input_func=lambda:env['access'],log=lambda _:None)==0
    token=accounts.load_token(env['cfg'])
    assert token['no_expiry'] is True and 'expires_in' not in token and 'refresh_token' not in token
