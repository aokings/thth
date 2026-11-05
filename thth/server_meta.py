"""遠くの道（thth.me の鍵・`thth login` のあと）の Meta の口（計画 Meta 全部盛り・審査員が自分で試す道）。

    doctor                      thth doctor <口座>（読み取りの probe だけ・VM の path は返さない）
    page_cta_show|delete        thth page cta show|delete <FB の口座> [<cta_id>]
    live_create|end             thth live create|end <FB の口座> …
    dm_list|read|send           thth dm list|read|send <FB|IG の口座> …
    like|unlike                 thth like|unlike <口座> <投稿>
    edit_post                   thth edit <FB の口座> <post_id> --text …
    reply_hide|unhide           thth reply hide|unhide <口座> <comment_id>

**手元の道の命令の関数をそのまま VM で呼ぶ**（`meta_write`・`edit_cli`・`reaction_cli`）。柵は同じ 1 本:

- 二段確認の digest は VM が作り VM が照合する。一段目も二段目も、VM の関数がその場で媒体を読み直して
  digest を作る（窓の判定・本文・相手・状態が変われば digest が変わり通らない）。CLI は受け取った digest を
  `confirm` に入れて渡すだけで、自分では作らない。
- 資格は `server_writes.current`（鍵・口座の範囲・leave_gate の止め・書く口は writes の資格）。`by` は資格の
  actor（依頼の欄から名乗らせない・`inflight` と同じ）。安全装置の止めの印・`production: true`・24 時間の窓は
  手元の関数が見る。
- 関数が出す文（stdout・stderr）は捕まえて答えの JSON だけを返す（VM のログに本文・相手・宛先を出さない）。
- 断りは静的な符丁（`REASONS`・`server_writes.SAFE_ERRORS`）。自由文で断る手元の口（`edit`・`reply`・反応の
  一部）は `remote_command_refused` と短い理由（`server_reads.short` で URL・path・秘密を落とす）。
- 答えのうち本文・相手・配信の宛先（`SECRET_FIELDS`）は、依頼の印（`api_requests` の `requests/<id>.json`）に
  書かない（`for_record`）。答えは 1 回だけ Worker に渡り、利用者の端末に届く。

MCP には出さない（`server_writes.WRITE_OPERATIONS` に入れない＝MCP の道具の表は変わらない）。
広告（`thth ads …`）は別の口 `thth/server_ads.py`（設計 広告の資格と遠くの道 2026-11-06）。この口は `accounts/` の
台帳の名前だけを通す（`current` の既定の `kind`）。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re

from . import accounts
from .report_service import ReportServiceError

# operation → (受ける欄, 必須の欄)。`account` と `operation` は常に受ける。
_FIELDS = {
    'doctor': ((), ()),
    'page_cta_show': ((), ()),
    'page_cta_delete': (('cta_id', 'confirm'), ('cta_id',)),
    'live_create': (('title', 'description', 'confirm'), ('title',)),
    'live_end': (('live_id', 'confirm'), ('live_id',)),
    'dm_list': ((), ()),
    'dm_read': (('target', 'profile'), ('target',)),
    'dm_send': (('target', 'comment', 'text', 'human_agent', 'confirm'), ()),
    'like': (('post',), ('post',)),
    'unlike': (('post',), ('post',)),
    'edit_post': (('post_id', 'text', 'reason', 'confirm'), ('post_id',)),
    'reply_hide': (('comment_id', 'reason'), ('comment_id',)),
    'reply_unhide': (('comment_id', 'reason'), ('comment_id',)),
}
OPERATIONS = frozenset(_FIELDS)
# 読むだけの口（writes の無い鍵でも通る）。ほかは書く口の資格が要る（一段目も）。
READS = frozenset(('doctor', 'page_cta_show', 'dm_list', 'dm_read'))
_BOOLS = frozenset(('profile', 'human_agent'))
TEXT_MAX = 20_000
FIELD_MAX = 4_000
# 手元の口の符丁のうち、遠くの道でもそのまま言うもの（目録の doors に remote がある）。
REASONS = frozenset((
    'messaging_window_closed', 'private_reply_window_closed', 'dm_text_invalid', 'dm_recipient_unknown',
    'meta_write_unsupported', 'meta_write_permission_missing', 'meta_write_digest_mismatch', 'meta_write_refused',
    'meta_write_unknown', 'meta_write_busy', 'page_cta_not_found', 'live_title_invalid', 'live_not_this_page',
    'remote_command_refused'))
# 依頼の印に書かない欄（本文・相手・相手の属性・配信の宛先）。
SECRET_FIELDS = ('stream_url', 'conversation', 'conversations', 'text', 'previous_text', 'to', 'profile')
# doctor の probe の欄（VM の path・本文を返さない）。
PROBE_FIELDS = ('key', 'label', 'permission', 'ok', 'detail', 'status', 'http_status', 'http', 'reason', 'failure',
                'scopes', 'missing', 'extra')
_CODE = re.compile(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)+\Z')


def error(reason, detail=None):
    exc = ReportServiceError(reason)
    exc.reason = detail
    raise exc


def _validate(request):
    keys, required = _FIELDS[request['operation']]
    if set(request) - set(keys) - {'operation', 'account'}:
        error('invalid_request')
    if not isinstance(request.get('account'), str):
        error('scope_unavailable')
    for name in required:
        if not isinstance(request.get(name), str) or not request[name].strip():
            error('invalid_request')
    for name in keys:
        if name not in request or request[name] is None:
            continue
        if name in _BOOLS:
            if type(request[name]) is not bool:
                error('invalid_request')
        elif not isinstance(request[name], str) or len(request[name]) > (TEXT_MAX if name == 'text' else FIELD_MAX):
            error('invalid_request')


def for_record(operation, result):
    """依頼の印（`requests/<id>.json`）に書く形。本文・相手・宛先は落とし、落とした欄の名前を `not_kept` に。"""
    if operation not in OPERATIONS or type(result) is not dict:
        return result
    dropped = [name for name in SECRET_FIELDS if result.get(name) is not None]
    # 断りの文は Meta の `error.message` を写すことがあり、相手の id や本文が反響しうる（確かめ役 11/06）。
    # 答え（端末へ 1 回）には残し、印には符丁だけを書く。
    if result.get('error') and result.get('reason') is not None:
        dropped.append('reason')
    dropped = sorted(dropped)
    if not dropped:
        return result
    return {**result, **{name: None for name in dropped}, 'not_kept': dropped}


# --------------------------------------------------------------------------
# 手元の命令の引数（`build_parser()` の既定と同じ形）
# --------------------------------------------------------------------------

def _command(operation, account, request, actor):
    from . import edit_cli, meta_write, reaction_cli
    base = dict(account=account, by=actor, json=True, wait=0, confirm=request.get('confirm'))
    if operation.startswith('page_cta_'):
        return meta_write._run(meta_write.cmd_page_cta), dict(base, action=operation[len('page_cta_'):],
                                                              cta_id=request.get('cta_id'))
    if operation.startswith('live_'):
        return meta_write._run(meta_write.cmd_live), dict(base, action=operation[len('live_'):],
                                                          live_id=request.get('live_id'),
                                                          title=request.get('title'),
                                                          description=request.get('description'))
    if operation.startswith('dm_'):
        return meta_write._run(meta_write.cmd_dm), dict(base, action=operation[len('dm_'):],
                                                        target=request.get('target'), comment=request.get('comment'),
                                                        text=request.get('text'), text_file=None,
                                                        human_agent=bool(request.get('human_agent')),
                                                        profile=bool(request.get('profile')))
    if operation in ('like', 'unlike'):
        return reaction_cli.cmd_reaction, dict(base, post=request['post'], reaction_command=operation, via='api')
    if operation == 'edit_post':
        return edit_cli.cmd_edit, dict(base, post_id=request['post_id'], text=request.get('text'), text_file=None,
                                       reason=request.get('reason'))
    return edit_cli.cmd_reply, dict(base, action=operation[len('reply_'):], comment_id=request['comment_id'],
                                    reason=request.get('reason'), confirm=None)


def _call(operation, account, request, actor):
    func, fields = _command(operation, account, request, actor)
    out, err = io.StringIO(), io.StringIO()
    # 手元の関数の文（本文・相手・宛先を含みうる）は VM のログへ出さない。答えは JSON だけ。
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = func(argparse.Namespace(**fields))
    try:
        payload = json.loads(out.getvalue())
    except ValueError:
        payload = None
    if type(payload) is not dict:
        error('write_unavailable')
    if rc == 0 or payload.get('stage') == 1:
        # `edit`・`reply` の `record` は VM の記録の path（利用者に要らない・置き場を見せない）。
        return {**{k: v for k, v in payload.items() if not (k == 'record' and isinstance(v, str))},
                'account': account}
    _refused(payload)


def _refused(payload):
    from .server_reads import short
    from .server_writes import SAFE_ERRORS
    code, message = payload.get('error'), payload.get('message')
    if not (isinstance(code, str) and _CODE.fullmatch(code)):
        code, message = None, code          # `edit`・`reply` は文だけで断る
    if payload.get('result') == 'unknown':
        code = 'meta_write_unknown'
    elif payload.get('guard') == 'stopped':
        code = 'account_stopped'
    if code not in SAFE_ERRORS:
        message = f'{code}: {message}' if code and message else (message or code)
        code = 'remote_command_refused'
    error(code, short(message if isinstance(message, str) else None))


# --------------------------------------------------------------------------
# doctor（`thth doctor --json` から VM の置き場の欄を除いたもの）
# --------------------------------------------------------------------------

def _doctor(account):
    from . import doctor
    from .server_reads import short
    report = doctor.diagnose(account)
    try:
        doctor.record_observation(account, report)
    except (OSError, ValueError, TypeError, accounts.AccountError):
        pass  # 手元の doctor と同じ: 観測を書けなくても診断は返す
    probes = []
    for probe in report.get('probes') or []:
        if isinstance(probe, dict):
            row = {key: probe.get(key) for key in PROBE_FIELDS if key in probe}
            if isinstance(row.get('detail'), str):
                row['detail'] = short(row['detail'])
            probes.append(row)
    out = {'account': account, 'probes': probes, 'error': short(report.get('error'))}
    for key in ('handle', 'username', 'user_id', 'scopes_recorded', 'auth_via', 'auth_observed_at',
                'required_scopes', 'missing_scopes_recorded', 'auth_only'):
        if key in report:
            out[key] = report[key]
    return out


def execute(context, request):
    """遠くの道の Meta の口の入口（`server_writes.serve` から来る）。"""
    if type(request) is not dict or request.get('operation') not in OPERATIONS:
        error('unsupported_operation')
    _validate(request)
    operation, account = request['operation'], request['account']
    from . import leave_gate
    from .server_writes import current, recover_repo
    try:
        cfg = current(context, account, write=operation not in READS)
        with leave_gate.lease(account):
            recover_repo(account, cfg)
            if operation == 'doctor':
                return _doctor(account)
            return _call(operation, account, request, context.actor)
    except accounts.AccountLeaving:
        error('account_leaving')
    except accounts.AccountStopped:
        error('account_stopped')
    except ReportServiceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, accounts.AccountError):
        error('read_unavailable' if operation in READS else 'write_unavailable')
