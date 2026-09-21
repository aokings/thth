"""Signed cleanup observation/recovery. No capability or object IDs leave Worker."""
import json
from . import accounts, admin_log, approval_relay


def _counts(value, fields):
    return type(value) is dict and set(value) == set(fields) and all(
        type(value[key]) is int and value[key] >= 0 for key in fields if key != 'reason')


def observe(account):
    unavailable = dict(pending_count=None, failed_count=None, reason='cleanup_observation_unavailable')
    try:
        if not accounts.name_is_safe(account):return unavailable
        result = approval_relay.signed_request('account', account, 'status', {})
        value = result.get('cleanup')
        if not _counts(value, ('pending_count', 'failed_count', 'reason')):return unavailable
        pending, failed = value['pending_count'], value['failed_count']
        expected = 'cleanup_failed' if failed else 'cleanup_unconfirmed' if pending else None
        if failed > pending or value['reason'] != expected:return unavailable
        return dict(value)
    except (OSError, ValueError, TypeError, approval_relay.RelayError):
        return unavailable


def retry(account, *, by):
    admin_log.actor(by)
    if not accounts.name_is_safe(account):raise ValueError('invalid_account')
    # No active-account requirement: recovery must remain possible after leave.
    result = approval_relay.signed_request('account', account, 'cleanup-retry', {})
    if not _counts(result, ('scheduled_count', 'unavailable_count', 'remaining_count', 'reason')):
        raise ValueError('cleanup_retry_unavailable')
    expected = 'cleanup_retry_unavailable' if result['unavailable_count'] else None
    if result['reason'] != expected:raise ValueError('cleanup_retry_unavailable')
    return result


def command(args):
    try:
        result = retry(args.account, by=args.by)
    except (OSError, ValueError, TypeError, admin_log.AdminLogError, approval_relay.RelayError):
        result = {'reason': 'cleanup_retry_unavailable'}
    print(json.dumps(result, ensure_ascii=False) if args.json else
          'cleanup: ' + str(result.get('reason') or 'scheduled') +
          ' (scheduled=' + str(result.get('scheduled_count', 0)) + ')')
    return 2 if result.get('reason') or result.get('remaining_count') else 0


def register(commands):
    parser = commands.add_parser('media', help='添付の物理削除を観測・回復（再投稿しない）')
    operations = parser.add_subparsers(required=True)
    recovery = operations.add_parser('cleanup-retry', description='期限済みの削除を再試行します。結果不明の書込みは強制解除せず、本口だけでは回復できない場合があります。再投稿はしません。')
    recovery.add_argument('account');recovery.add_argument('--by', required=True)
    recovery.add_argument('--json', action='store_true');recovery.set_defaults(func=command)
