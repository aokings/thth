"""利用者 scope の読む口（設計 3.14.0 §3.2）。

書く口（`server_writes.execute`）と同じ資格・同じ口座の範囲で、今の CLI の読む命令を
呼ぶだけ。**判断・整形を足さない**——返すのは CLI の `--json` と同じ形。足すのは次だけ:

- 資格の範囲（範囲外は `scope_unavailable`〔書く口と同じ〕・退出中は `account_leaving`）。止まった口座
  （安全装置）でも読むのは許す。
- 10/15（点検 B-17・B-18）から、枝（`thread_read`）・絡みに行く先（`where_to_appear`）・仮名の履歴
  （`who_is_this`）・予定の一覧（`schedule_list`）も同じ作法で通す。本文は全文。答えが thth.me の
  1 件の上限（`api_requests.RESULT_MAX`）に当たるときは、後ろから落として続きの印を付ける。
- `limit`（省略 20・1〜100）で一覧を先頭から切る。
- `collect` は口座ごとに 10 分に 1 回まで（`collect_too_soon` と `next_at`）。
- 断りは静的な符丁。媒体の API の失敗は `upstream_unavailable` と短い理由
  （秘密・URL・path を落とした 1 行）。自由文の欄（取り直しの `errors` 等）も同じく落とす。

記録: 読むだけの口は口座の記録に書かない。`collect`・`refresh` は CLI と同じく記録を
更新し、`runs-*.ndjson` への 1 行も CLI と同じ（`collect`・`mentions`）。
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import re
import sys
from pathlib import Path

from . import accounts, jst
from .report_service import ReportContext, ReportServiceError

OPERATIONS = frozenset(('posts', 'replies', 'measured', 'collect', 'mentions',
                        'topics_search', 'profile', 'location_search',
                        'thread_read', 'where_to_appear', 'who_is_this', 'schedule_list',
                        'events', 'summary',
                        # 4.0 S5 §3.4: 正本の修正の検出（読むだけ・台帳と repo の HEAD・媒体は呼ばない）。
                        'article_diff',
                        # 4.0 S6 §6.2: 記事の反応を束ねる（読むだけ・台帳から・媒体は呼ばない・fetch は受けない）。
                        'article_reactions',
                        # 4.1（設計 S4 §20.9）: まとめて承認したブックマークの札の状態（読むだけ・媒体は呼ばない）。
                        'bookmark_batch_status',
                        # 5.0 段 2: 会話の中のダッシュボードの中身（読むだけ・既存の読む口を束ねる・媒体は呼ばない）。
                        'dashboard'))
# 断りの符丁（`server_writes.SAFE_ERRORS` に足す）。
REASONS = frozenset(('collect_too_soon', 'upstream_unavailable', 'read_unavailable'))
# `upstream_unavailable` に添える静的な理由（媒体の返した文を写せないとき）。
UPSTREAM_DETAILS = frozenset(('standard_access', 'collect_failed'))

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
COLLECT_INTERVAL = _dt.timedelta(minutes=10)
REASON_CHARS = 160

# operation → (受ける欄, 必須の欄)。`account` と `operation` は常に受ける。
_FIELDS = {
    'posts': (('limit', 'refresh'), ()),
    'replies': (('limit', 'refresh', 'post_id'), ()),
    'measured': (('limit', 'post_id'), ()),
    'collect': ((), ()),
    'mentions': (('limit', 'refresh', 'since'), ()),
    'topics_search': (('query', 'limit', 'recent', 'since', 'until', 'media'), ('query',)),
    'profile': (('username',), ('username',)),
    'location_search': (('query',), ('query',)),
    'thread_read': (('post_id', 'since', 'max_messages', 'limit'), ('post_id',)),
    'where_to_appear': (('words', 'recent', 'limit', 'since', 'until', 'media', 'exclude_engaged', 'max_per_author', 'also',
                         'aggregate', 'lexicon', 'suggest', 'tag', 'author', 'keep', 'ja', 'question',
                         'no_replies'), ()),
    'who_is_this': (('author_key', 'username', 'profile'), ()),
    'schedule_list': (('days',), ()),
    # 出来事の流れと数の要約（4.0 の柱 1・読むだけ・記録から並べるだけで媒体は呼ばない）。
    'events': (('since', 'after', 'limit'), ()),
    'summary': ((), ()),
    'article_diff': (('article', 'delivery', 'draft'), ()),
    'article_reactions': (('article', 'delivery', 'comments'), ('article',)),
    'bookmark_batch_status': (('batch',), ()),
    'dashboard': (('level', 'id', 'full'), ()),
}
# 欄の型（範囲と中身の検査は CLI の関数が言う。ここは型だけ）。
_TYPES = {
    'limit': int, 'refresh': bool, 'post_id': str, 'query': str, 'username': str, 'since': str,
    'recent': bool, 'max_messages': int, 'words': list, 'exclude_engaged': bool, 'max_per_author': int,
    'also': list, 'aggregate': bool, 'lexicon': dict, 'suggest': bool, 'tag': bool, 'author': str,
    'keep': bool, 'ja': bool, 'question': bool, 'no_replies': bool, 'author_key': str, 'profile': bool,
    'days': int, 'until': str, 'media': str, 'after': str,
    'article': str, 'delivery': str, 'draft': bool, 'comments': bool, 'batch': str,
    'level': str, 'id': str, 'full': bool,
}
# 答えの大きさの目安（JSON の UTF-16 の長さ）。thth.me の 1 件の上限（128 KB）より少し小さく取る。
RESULT_BUDGET = 120_000
# 枝を落としたあとに足す欄の分の余白（字数）。
FIT_RESERVE = 200
TRIMMED_NOTE = ('答えの大きさの上限（thth.me の 1 件 128 KB）で {n} 件を後ろから落としました'
                '（--limit を下げるか、語を分けて呼んでください）')

_URL = re.compile(r'https?://\S+')
_PATH = re.compile(r'(?<![\w.])(?:~|\.{1,2})?(?:/[\w.@+\-]+){2,}/?')


def _now():
    return jst.now_jst()


def error(reason, detail=None, next_at=None):
    exc = ReportServiceError(reason)
    exc.reason = detail
    exc.next_at = next_at
    raise exc


def short(text):
    """媒体の返した理由を 1 行に（秘密・URL・path を落とす）。空なら None。"""
    if not isinstance(text, str) or not text.strip():
        return None
    from . import redact
    value = redact.redact(text) or ''
    value = _URL.sub('<url>', value)
    value = _PATH.sub('<path>', value)
    value = ' '.join(value.split())
    return value[:REASON_CHARS] or None


def _validate(request):
    operation = request.get('operation')
    keys, required = _FIELDS[operation]
    if set(request) - set(keys) - {'operation', 'account'}:
        error('invalid_request')
    if not isinstance(request.get('account'), str):
        error('scope_unavailable')
    for name in required:
        if not isinstance(request.get(name), str) or not request[name].strip():
            error('invalid_request')
    if 'limit' in request and (type(request['limit']) is not int or not 1 <= request['limit'] <= MAX_LIMIT):
        error('invalid_options')
    for name, kind in _TYPES.items():
        if name in request and type(request[name]) is not kind:
            error('invalid_request')
    if 'post_id' in request and not request['post_id'].strip():
        error('invalid_request')
    if 'media' in request:
        # 媒体の種類は手元の旗と同じ選択肢だけ（任意の文字を API へ渡さない）。
        from . import threads_read_cli
        if request['media'] not in threads_read_cli._media_choices():
            error('invalid_request')
    for name in ('query', 'username', 'since', 'until', 'media', 'author', 'author_key', 'post_id'):
        if name in request and len(request[name]) > 200:
            error('invalid_request')
    if 'after' in request and len(request['after']) > 20_000:
        error('invalid_request')   # カーソルは覚えた event_id の分だけ長い（上限 500 件）


def _scope(context, account):
    """範囲外は `scope_unavailable`。退出中は `account_leaving`（`current` が言う）。"""
    if type(context) is not ReportContext or context.scope != 'user':
        error('scope_unavailable')
    from .report_service import check_excluded
    check_excluded(context, account)
    if account not in context.allowed_accounts:
        error('scope_unavailable')
    from .server_writes import current
    return current(context, account)


def _limit(request):
    return request.get('limit', DEFAULT_LIMIT)


# --------------------------------------------------------------------------
# 断りの写し
# --------------------------------------------------------------------------

def _refuse(kind, message):
    if kind == 'unsupported':
        error('unsupported_operation')
    if kind in ('no_token', 'not_granted'):
        error('permission_unavailable')
    if kind == 'narrowed':
        error('upstream_unavailable', 'standard_access')
    if kind == 'account':
        error('read_unavailable')
    error('upstream_unavailable', short(message))


def _scrub_refresh(refresh):
    """取り直しの自由文（repo の path・push の理由が混ざりうる）を 1 行に落とす。形は同じ。"""
    if not isinstance(refresh, dict):
        return refresh
    out = dict(refresh)
    out['errors'] = [short(item) for item in refresh.get('errors') or []]
    out['failed'] = [dict(item, reason=short(item.get('reason'))) for item in refresh.get('failed') or []]
    return out


# --------------------------------------------------------------------------
# 口ごとの中身（CLI の関数を呼ぶだけ）
# --------------------------------------------------------------------------

def _posts(account, request, cfg):
    from . import account_report
    # `thth posts` は毎回 API から引く（記録は突合にだけ使う）。`refresh` は同じ動き。
    result = account_report.recent_posts(account, limit=_limit(request))
    if result.get('error'):
        error('upstream_unavailable', short(result['error']))
    return result


def _replies(account, request, cfg):
    from . import cli, postid
    post = request.get('post_id')
    if post:
        try:
            post = postid.for_account(cfg, post, account)
        except postid.PostIdError:
            error('invalid_request')
    result, refresh = cli.replies_json(account, post=post, refresh=request.get('refresh', False),
                                       wait=0, log=lambda _line: None)
    result = dict(result, replies=result['replies'][:_limit(request)])
    if refresh is not None:
        result['refresh'] = _scrub_refresh(refresh)
    return result


def _measured(account, request, cfg):
    from . import cli
    result = cli.measured_json(account, post=request.get('post_id'))
    return dict(result, posts=result['posts'][:_limit(request)])


def _collect_slot(account):
    """口座ごとに 10 分に 1 回まで。印は `state/<口座>/reads/collect.json`（0600）。

    時刻は採る**前に**刻む——失敗した採取も 1 回に数える（連打で媒体の上限を食わない）。
    同じ口座の採取が重なれば `account_busy`。
    """
    from . import server_files
    directory = Path(accounts.state_dir_for(account)) / 'reads'
    stack = contextlib.ExitStack()
    fd = stack.enter_context(server_files.directory(directory, create=True, private=True))
    try:
        stack.enter_context(server_files.lock_at(fd, 'collect.lock'))
    except BlockingIOError:
        stack.close()
        error('account_busy')
    try:
        now = _now()
        try:
            last = json.loads(server_files.read_at(fd, 'collect.json', private=True)).get('at')
            last = _dt.datetime.fromisoformat(last) if isinstance(last, str) else None
        except FileNotFoundError:
            last = None
        except (ValueError, AttributeError, TypeError):
            last = None
        if last is not None and last.tzinfo is not None and now < last + COLLECT_INTERVAL:
            error('collect_too_soon', next_at=jst.iso(last + COLLECT_INTERVAL))
        server_files.replace_at(fd, 'collect.json', server_files.encode({'at': jst.iso(now)}), private=True)
    except BaseException:
        stack.close()
        raise
    return stack


def _collect(account, request, cfg):
    from . import collect as collect_mod
    lines = []
    with _collect_slot(account):
        # `thth collect <account>` と同じ入口（runs に 1 行残る）。
        rc = collect_mod.run_collect(account, log=lines.append, trigger=collect_mod.TRIGGER_MANUAL)
    if rc:
        error('upstream_unavailable', short(lines[-1] if lines else None) or 'collect_failed')
    return _measured(account, {'operation': 'measured', 'account': account}, cfg)


def _mentions(account, request, cfg):
    from . import threads_read_cli as reads
    observed = None
    try:
        rows = reads._fetch(account, capability='mentions',
                            call=lambda adapter: reads.mentions_call(adapter, account,
                                                                     since=request.get('since')),
                            narrowed_note=reads.MENTIONS_NARROWED_NOTE)
        observed = len(rows)
    except reads.ReadFailed as failed:
        reads.record_mentions_run(account, observed, failed.rc)
        _refuse(failed.kind, failed.message)
    reads.record_mentions_run(account, observed, 0)
    # `mentions` は毎回 API から引く（記録は持たない）。`refresh` は同じ動き。
    return reads.mentions_json(account, rows[:_limit(request)])


def _topics_search(account, request, cfg):
    from . import threads_read_cli as reads
    query = request['query'].strip()
    limit = _limit(request)
    search_type = 'RECENT' if request.get('recent') else 'TOP'
    narrow = None
    if any(request.get(name) is not None for name in ('since', 'until', 'media')):
        # 手元の `topics --search` と同じ: Threads 以外・読めない期間は断る（黙って普通の検索にしない）。
        if cfg.get('media') != 'threads':
            error('invalid_request')
        try:
            narrow = reads.search_narrowing(since=request.get('since'), until=request.get('until'),
                                            media=request.get('media'))
        except ValueError:
            error('invalid_request')
    try:
        result = reads._fetch(account, capability='keyword_search',
                              call=lambda adapter: reads.search_call(adapter, account, query,
                                                                     search_type=search_type, limit=limit,
                                                                     narrow=narrow),
                              narrowed_note=reads.SEARCH_NARROWED_NOTE)
    except reads.ReadFailed as failed:
        _refuse(failed.kind, failed.message)
    material = reads.search_json(result, account, query, search_type=search_type, limit=limit)
    if narrow:
        material['narrow'] = dict(narrow)
    # 集計と指す先と本文の全文（保存しない）。自由文の理由は 1 行に落とす。
    material['tag_cannot_say'] = short(material.get('tag_cannot_say'))
    material['replied_lookup'] = dict(material['replied_lookup'],
                                      reason=short(material['replied_lookup'].get('reason')))
    return material


def _profile(account, request, cfg):
    from . import threads_read_cli as reads
    username = request['username']
    try:
        profile = reads._fetch(account, capability='profile_lookup',
                               call=lambda adapter: adapter.profile_lookup(username),
                               narrowed_note=reads.STANDARD_ACCESS_NOTE)
    except reads.ReadFailed as failed:
        _refuse(failed.kind, failed.message)
    return reads.profile_json(account, profile)


def _location_search(account, request, cfg):
    from . import retract_cli
    query = request['query']
    try:
        rows = retract_cli.location_rows(account, query)
    except retract_cli.LocationFailed as failed:
        _refuse(failed.kind, failed.message)
    return retract_cli.location_json(account, query, rows)


# --------------------------------------------------------------------------
# 枝・絡みに行く先・仮名の履歴・予定（10/15・点検 B-17・B-18）
# --------------------------------------------------------------------------

def _size(value) -> int:
    from .api_requests import _size as size
    return size(value)


def _invalid(exc):
    """問いが受け取れない（CLI の断りの 1 行）。理由は短くして添える。"""
    error('invalid_request', short(str(exc)))


def _scrub(lines):
    """`cannot_say` の自由文（媒体の断り・path が混ざりうる）を 1 行に落とす。"""
    return [short(line) for line in lines or []]


def _fit_thread(result):
    """大きすぎる枝は後ろから落とし、`provenance.continue_from`（続きを読む `since`）を付ける。"""
    over = _size(result) - RESULT_BUDGET
    messages = result['messages']
    if over <= 0 or not messages:
        return result
    # 落としたあとに足す欄（continue_from・size_trimmed・truncated）の分も空ける（10/15: 足す欄が
    # 上限を越えさせていた）。
    over += FIT_RESERVE
    cut = len(messages)
    while cut > 0 and over > 0:
        cut -= 1
        over -= _size(messages[cut]) + 1
    kept = messages[:cut]
    result = dict(result, messages=kept)
    result['counts'] = dict(result['counts'], messages=len(kept), truncated=True)
    result['provenance'] = dict(result['provenance'],
                                continue_from=kept[-1].get('timestamp') if kept else None,
                                size_trimmed=len(messages) - len(kept))
    return result


def _thread_read(account, request, cfg):
    from . import adapters, thread_read
    from .adapters import base
    media = cfg.get('media')
    if thread_read.CAPABILITY not in adapters.capabilities_for(media):
        error('unsupported_operation')
    if not adapters.adapter_class(media).has_token(accounts.load_token(cfg)):
        error('permission_unavailable')
    post_id = request['post_id']

    def record(**fields):
        # runs には件数だけ（`thth thread` と同じ 1 行・本文と username は書かない）。
        thread_read._record_run(account, media=media, post_id=post_id, **fields)
    try:
        result = thread_read.answer(account, post_id, since=request.get('since'),
                                    max_messages=request.get('max_messages', thread_read.DEFAULT_MAX_MESSAGES),
                                    limit=request.get('limit'))
    except thread_read.ThreadReadError as exc:
        _invalid(exc)
    except base.PermissionMissing:
        record(messages=None, truncated=False, status='error', error='permission_unavailable')
        error('permission_unavailable')
    except RuntimeError as exc:  # AdapterError はこの子
        record(messages=None, truncated=False, status='error', error='thread_read_failed')
        error('upstream_unavailable', short(str(exc)))
    record(messages=result['counts']['messages'], truncated=result['counts']['truncated'],
           status='ok', error=None)
    provenance = dict(result['provenance'], ledgers_unreadable=_scrub(result['provenance'].get('ledgers_unreadable')))
    return _fit_thread(dict(result, provenance=provenance))


def _fit_where(result):
    """大きすぎる答えは、いちばん長い語の一覧の後ろから落とす（語ごとに `trimmed` と `cannot_say` に 1 行）。"""
    over = _size(result) - RESULT_BUDGET
    if over <= 0:
        return result
    entries = [(node, word, entry) for node in result['by_account'].values()
               for word, entry in node['by_word'].items() if entry.get('posts')]
    # 落としたあとに足す欄（語ごとの trimmed と cannot_say の 1 行）の分も空ける（10/15: 足す行が
    # 上限を越えさせていた）。
    over += sum(_size(f'{word}: ' + TRIMMED_NOTE.format(n=99999)) + len('"trimmed":99999,') + 2
                for _node, word, _entry in entries)
    while over > 0 and entries:
        node, word, entry = max(entries, key=lambda item: len(item[2]['posts']))
        if not entry['posts']:
            break
        over -= _size(entry['posts'].pop()) + 1
        entry['trimmed'] = entry.get('trimmed', 0) + 1
    for node, word, entry in entries:
        if entry.get('trimmed'):
            node['cannot_say'].append(f'{word}: ' + TRIMMED_NOTE.format(n=entry['trimmed']))
    return result


def _where(account, request, cfg):
    from . import where_cli
    try:
        lexicon = where_cli.check_lexicon(request['lexicon']) if 'lexicon' in request else None
        result = where_cli.answer(
            account_name=account, words=request.get('words') or [], recent=request.get('recent', False),
            limit=request.get('limit'), since=request.get('since'),
            until=request.get('until'), media=request.get('media'),
            exclude_engaged=request.get('exclude_engaged', False),
            max_per_author=request.get('max_per_author'), also=request.get('also'),
            aggregate_mode=request.get('aggregate', False), lexicon=lexicon,
            suggest=request.get('suggest', False), tag=request.get('tag', False),
            author=request.get('author'), keep=request.get('keep', False), ja=request.get('ja', False),
            question=request.get('question', False), no_replies=request.get('no_replies', False))
    except where_cli.WhereError as exc:
        _invalid(exc)
    # 自由文の理由（媒体の断り・path）は 1 行に落とす。本文と指す先はそのまま（保存しない）。
    result['cannot_say'] = _scrub(result.get('cannot_say'))
    for node in result['by_account'].values():
        node['cannot_say'] = _scrub(node.get('cannot_say'))
        if isinstance(node.get('kept'), dict) and node['kept'].get('cannot_say'):
            node['kept'] = dict(node['kept'], cannot_say=short(node['kept']['cannot_say']))
    return _fit_where(result)


def _who(account, request, cfg):
    from . import who_cli
    try:
        result = who_cli.answer(account_name=account, author_key=request.get('author_key'),
                                username=request.get('username'), profile=request.get('profile', False))
    except who_cli.WhoError as exc:
        _invalid(exc)
    return dict(result, cannot_say=_scrub(result.get('cannot_say')))


def _schedule_list(account, request, cfg):
    from . import report
    days = request.get('days')
    if days is not None and not 1 <= days <= 3650:
        error('invalid_options')
    # `thth schedule <口座> --json` と同じ行（承認済みと下書き・日付順）。
    return {'account': account, 'schedule': report.schedule(account, days=days)}


def _events(account, request, cfg):
    from . import events_cli
    try:
        # 範囲は鍵の口座 1 つだけ（他の口座の記録は読まない）。
        return events_cli.build(account, since=request.get('since'), after=request.get('after'),
                                allowed_names=(account,), limit=_limit(request))
    except events_cli.EventsError as exc:
        error('scope_unavailable' if str(exc) == 'target_unknown' else 'invalid_options')


def _summary(account, request, cfg):
    from . import summary_cli
    try:
        return summary_cli.build(account, allowed_names=(account,))
    except summary_cli.SummaryError:
        error('scope_unavailable')


def _bookmark_batch_status(account, request, cfg):
    """4.1（設計 S4 §20.9）: 手元の `thth bookmark batch-status --json` と同じ形。"""
    from . import bookmark_batch, principals
    if not principals.uses_principal(cfg):
        error('unsupported_operation')
    try:
        return bookmark_batch.status(account, batch=request.get('batch'))
    except bookmark_batch.BatchError as exc:
        error(exc.code if exc.code in bookmark_batch.REASONS else 'read_unavailable')


def _fit_article_diff(result):
    """大きすぎる答えは各配信の `diff_lines` を後ろから落とす（`TRIMMED_NOTE`・4.0 S5 §3.4）。"""
    over = _size(result) - RESULT_BUDGET
    if over <= 0:
        return result
    over += FIT_RESERVE
    rows = [dict(row, diff_lines=list(row['diff_lines'])) for row in result['deliveries']]
    dropped = 0
    for row in reversed(rows):
        while row['diff_lines'] and over > 0:
            over -= _size(row['diff_lines'].pop()) + 1
            dropped += 1
        if over <= 0:
            break
    result = dict(result, deliveries=rows)
    if dropped:
        result['cannot_say'] = list(result.get('cannot_say') or []) + [TRIMMED_NOTE.format(n=dropped)]
        result['trimmed'] = dropped
    return result


def _article_diff(account, request, cfg):
    from . import article_cli
    try:
        # 範囲は鍵の口座 1 つだけ（他の口座の台帳と repo は読まない）。
        result = article_cli.build(account, article=request.get('article'), delivery=request.get('delivery'),
                                   draft=bool(request.get('draft')), allowed_names=(account,))
    except article_cli.ArticleDiffError:
        error('scope_unavailable')
    return _fit_article_diff(result)


def _fit_article_reactions(result):
    """大きすぎる答えは各配信と告知の `rows[]` を古い方から落とす（`TRIMMED_NOTE`・4.0 S6 §6.2）。"""
    over = _size(result) - RESULT_BUDGET
    if over <= 0:
        return result
    over += FIT_RESERVE
    deliveries = [dict(row, rows=list(row['rows']),
                       **({'comments': list(row['comments'])} if 'comments' in row else {}))
                  for row in result['deliveries']]
    announcements = [dict(row, rows=list(row['rows'])) for row in result['announcements']]
    dropped = 0
    # 古い行から落とし、それでも大きければ第三者のコメント（`comments[]`・古い方から）も落とす（監査 低-4）。
    holders = [(h, 'rows') for h in deliveries + announcements] + [(h, 'comments') for h in deliveries
                                                                      if 'comments' in h]
    for holder, key in holders:
        while holder[key] and over > 0:
            over -= _size(holder[key].pop(0)) + 1
            dropped += 1
        if over <= 0:
            break
    result = dict(result, deliveries=deliveries, announcements=announcements)
    if dropped:
        result['cannot_say'] = list(result.get('cannot_say') or []) + [TRIMMED_NOTE.format(n=dropped)]
        result['trimmed'] = dropped
    return result


def _article_reactions(account, request, cfg, context=None):
    from . import article_reactions
    # 鍵の範囲（口座 → project）の全部を渡す。告知の口座が鍵の範囲にあれば読み、無ければ scope_unavailable。
    allowed = tuple(context.allowed_accounts) if context is not None else (account,)
    try:
        result = article_reactions.build(account, article=request.get('article'), delivery=request.get('delivery'),
                                         comments=bool(request.get('comments')), allowed_names=allowed)
    except article_reactions.ArticleReactionsError as exc:
        error('invalid_request' if str(exc) == 'article_required' else 'scope_unavailable')
    return _fit_article_reactions(result)


def _dashboard(account, request, cfg, context=None):
    """5.0 段 2: 手元の `thth dashboard <project> [campaign|series <id>] --json` と同じ形。

    範囲は鍵の口座の project（`context.allowed_accounts`）だけ。宣言（段 1）が読めなければ
    `scenario: {"available": false, "cannot_say": ...}` のまま KPI・要対応・カレンダーは出す。
    """
    from . import dashboard_cli
    allowed = tuple(context.allowed_accounts) if context is not None else (account,)
    level = request.get('level')
    if level is not None and level not in ('campaign', 'series'):
        error('invalid_options')
    try:
        result = dashboard_cli.build(cfg.get('project') or account, level, request.get('id'),
                                     allowed_names=allowed, full=bool(request.get('full')))
    except dashboard_cli.DashboardError as exc:
        error('scope_unavailable' if str(exc) == 'target_unknown' else 'invalid_options')
    if _size(result) > RESULT_BUDGET:
        # 詳細（--full）から落とす。それでも大きければ上限で断る側に任せる。
        result = {k: v for k, v in result.items() if k not in ('campaign_details', 'series_details')}
        result['cannot_say'] = list(result.get('cannot_say') or []) + ['full_trimmed']
    if result.get('scenario', {}).get('available') is False:
        # 遠くの道では repo の宣言を読めないことがある（設計 §5.4）。符丁を 1 つにそろえる。
        result['scenario'] = {'available': False, 'cannot_say': 'no_declarations'}
    return result


_HANDLERS = {
    'events': _events, 'summary': _summary, 'article_diff': _article_diff,
    'article_reactions': _article_reactions, 'bookmark_batch_status': _bookmark_batch_status,
    'posts': _posts, 'replies': _replies, 'measured': _measured, 'collect': _collect,
    'mentions': _mentions, 'topics_search': _topics_search, 'profile': _profile,
    'location_search': _location_search, 'thread_read': _thread_read, 'where_to_appear': _where,
    'who_is_this': _who, 'schedule_list': _schedule_list,
}


def execute(context, request):
    """読む口の入口（`report_service.execute_report` から来る）。"""
    if type(request) is not dict or request.get('operation') not in OPERATIONS:
        error('unsupported_operation')
    _validate(request)
    account = request['account']
    cfg = _scope(context, account)
    from . import leave_gate
    try:
        with leave_gate.lease(account):
            # 記録置き場の無いまま `data/` だけが書かれた口座は、ここで初期化してから読む（3.14.3 穴 1）。
            from .server_writes import recover_repo
            recover_repo(account, cfg)
            # CLI の関数は人向けに print しうる。stdio の MCP では stdout が道なので、
            # 読む口の間は stderr へ逃がす（答えは戻り値だけ）。
            with contextlib.redirect_stdout(sys.stderr):
                if request['operation'] == 'article_reactions':
                    return _article_reactions(account, request, cfg, context)
                if request['operation'] == 'dashboard':
                    return _dashboard(account, request, cfg, context)
                return _HANDLERS[request['operation']](account, request, cfg)
    except accounts.AccountLeaving:
        error('account_leaving')
    except ReportServiceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, accounts.AccountError):
        error('read_unavailable')
