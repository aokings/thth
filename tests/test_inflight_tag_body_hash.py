"""The inflight record describes the actual public text, including a topic tag."""
import datetime
import hashlib

from tests.conftest import write_queue_file
from thth import accounts, approval, core, inflight
from thth.adapters.base import PublishResult


def test_inflight_hash_is_effective_tagged_text_before_publish(isolated_account_factory):
    account = isolated_account_factory('tagged-bluesky', media='bluesky', production=True, hashtags=True)
    original = '本文です。'
    effective = original + '\n#茶'
    publish_at = '2026-09-09T08:00:00+09:00'
    fingerprint = approval.compute_approved_sha(section=effective, account=account['name'],
                  reply_to=None, topic='茶', publish_at=publish_at)
    write_queue_file(account['queue_dir'], 'tag.md', media='bluesky', body='## bluesky\n\n' + original + '\n',
                     fm_overrides={'account': account['name'], 'topic': '茶', 'publish_at': publish_at,
                                   'approved_sha': fingerprint})
    records = []
    class AmbiguousAdapter:
        def publish(self, post, *, dry_run, on_container_created=None):
            assert not dry_run and post.text == effective
            record = inflight.read(accounts.state_dir_for(account['name']))
            records.append(record)
            assert record['body_hash'] == hashlib.sha256(effective.encode()).hexdigest()
            assert record['body_hash'] != hashlib.sha256(original.encode()).hexdigest()
            assert record['approved_fingerprint'] == fingerprint
            return PublishResult(post_id=None, url=None, ts=None, error='fixture ambiguous publish',
                                 failure='publish_ambiguous')
    result = core.throw_once(account['name'], production_flag=True,
                             adapter_factory=lambda *_: AmbiguousAdapter(),
                             now=datetime.datetime.fromisoformat('2026-09-09T10:00:00+09:00'))
    assert result.exit_code == 1 and result.action == 'inflight'
    assert len(records) == 1
    assert inflight.read(accounts.state_dir_for(account['name'])) == records[0]
