"""Scoped server requests. Publishing, deletion and scheduling happen directly (design 3.13.0)."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from . import accounts, admin_log, approval, relay, core, jst, managed_repo, queuefile, server_files, writeback
from .report_service import ReportContext, ReportServiceError

WRITE_OPERATIONS = frozenset(('draft_put','send_request','retract_request','schedule_request',
                              'media_upload_url','media_complete',
                              # 4.0 S1 §7.3・S4: はてなブックマークの 2 段の承認と送信（url・op: delete も）。
                              'bookmark_request',
                              # 4.1（設計 S4 §20.9）: ブックマークのまとめて承認（2 段）と待ちの札の取り消し（1 段）。
                              'bookmark_batch_request','bookmark_batch_cancel'))
# 公開・削除・予約に至る依頼。承認ページは無い（設計 3.13.0）: どれもその場で行う（安全装置は効く）。
# 3.12.0 までの `approval_request` は受け付けない（`unsupported_operation`）。
PUBLISHING_OPERATIONS = frozenset(('send_request','retract_request','schedule_request'))
READ_OPERATIONS = frozenset(('draft_list','queue'))
DRAFT_ID = re.compile(r'[0-9a-f]{64}\Z')
SAFE_ERRORS = frozenset(('invalid_request','unsupported_operation','invalid_scope','invalid_options','scope_unavailable',
    'writes_not_allowed','invalid_draft','draft_changed','draft_not_editable','managed_repo_required','production_disabled',
    'credential_changed','draft_commit_unconfirmed','draft_not_verified','account_stopped','account_leaving',
    'credential_unavailable','write_unavailable'))
from .lint import REASONS as LINT_REASONS
# `invalid_draft` に添える理由。**静的な符丁だけ**——lint の日本語 1 行や
# path をそのままサーバの口から出さない（2.14.1）。
DRAFT_REASONS = frozenset(('body_not_representable','media_not_representable','publish_at_past')) | LINT_REASONS
from .media_uploads import REASONS as MEDIA_REASONS
# managed repo をモードで断ったときだけは `write_unavailable` で終わらせない——
# 運用者が chmod で直せる唯一の理由なので、静的な名前のまま上げる。
REPO_REASONS = frozenset(('managed_git_store_unsafe_mode',))
# 承認なしの道と安全装置（設計 3.12.0 §3.2・§3.3）。静的な名前だけ。
from .guard import REFUSALS as GUARD_REFUSALS, STOP_REASONS as GUARD_STOP_REASONS, GuardRefused
DIRECT_REASONS = frozenset(('account_busy','publication_failed','publication_unconfirmed','deletion_failed',
                            'permission_unavailable','schedule_commit_unconfirmed',
                            # `scheduled: false` の口座（timer に載っていない）で予約・猶予を求めた（段 3・裁定 (b)）。
                            'schedule_unavailable',
                            # 4.0 S1: はてなの口座の送信の口（adapter）がまだ無い・自分の記事でない・digest の食い違い。
                            'unsupported_medium','bookmark_target_not_own','comment_too_long','comment_required',
                            'too_many_tags'))
# 4.0 S4: ブックマークの対象・柵・送信の断り（`hatena_bookmark.REASONS`・静的な語だけ）。
from .hatena_bookmark import REASONS as BOOKMARK_REASONS
DIRECT_REASONS = DIRECT_REASONS | BOOKMARK_REASONS
# 4.1: まとめて承認の断り（`bookmark_batch.REASONS`・静的な語だけ）。
from .bookmark_batch import REASONS as BOOKMARK_BATCH_REASONS
DIRECT_REASONS = DIRECT_REASONS | BOOKMARK_BATCH_REASONS
from .account_settings import REASONS as SETTINGS_REASONS
# 遠くの道の inflight（見る・解く・3.14.3 穴 3）の断り。手元の `thth inflight` と同じ符丁。
INFLIGHT_REASONS = frozenset(('no_inflight','inflight_unreadable','media_inflight_unsupported','post_id_invalid',
                              'published_known','post_id_differs','decision_required','confirm_mismatch',
                              'inflight_changed','inflight_writeback_failed'))
# `publication_unconfirmed` に添える理由（前の公開の結果が分かっていない＝inflight が残っている）。
UNCONFIRMED_REASONS = frozenset(('inflight',))
# 利用者 scope の読む口（設計 3.14.0 §3.2・`thth/server_reads.py`）。
from .server_reads import REASONS as READ_REASONS
# 4.0 S5 §5.3: 衝突を見る・解く（`hatena_conflict.REASONS`・手元の `thth conflict` と同じ符丁）。
from .hatena_conflict import REASONS as CONFLICT_REASONS
SAFE_ERRORS = (SAFE_ERRORS | MEDIA_REASONS | REPO_REASONS | GUARD_REFUSALS | DIRECT_REASONS | SETTINGS_REASONS
               | READ_REASONS | INFLIGHT_REASONS | CONFLICT_REASONS)
# 断りに添えてよい詳細（止まった理由）。
GUARD_DETAILS = GUARD_STOP_REASONS | frozenset(('guard_state_unreadable',))


def error(reason, detail=None):
    exc = ReportServiceError(reason)
    exc.reason = detail
    raise exc


def current(context, account, *, write=False):
    """Reauthenticate the trusted capability; no request field creates identity."""
    if type(context) is ReportContext:
        from .report_service import check_excluded
        check_excluded(context,account)
    if type(context) is not ReportContext or context.scope != 'user' or account not in context.allowed_accounts:
        error('scope_unavailable')
    if write and (not context.writes or not context.actor or not context.credential_digest or not context.credentials_path):
        error('writes_not_allowed')
    if context.credentials_path:
        from .report_http import load_credentials
        root, credentials = load_credentials(Path(context.credentials_path))
        found = next((item[3] for item in credentials if item[0] == context.credential_digest
                      and not item[2] and datetime.now(timezone.utc) < item[1]), None)
        from dataclasses import replace
        if found is not None:check_excluded(found,account)
        if (found is None or account not in found.allowed_accounts
                or found.allowed_accounts[account] != context.allowed_accounts[account]
                or found != replace(context,allowed_accounts=found.allowed_accounts)):
            error('credential_changed')
        from .report_isolation import validate_environment
        validate_environment(root, found.allowed_accounts)
    from . import leave_gate
    if leave_gate.stopped(account):
        try:leave_gate.check_busy(account)
        except accounts.AccountLeaving:error('account_leaving')
        error('account_stopped')
    cfg = accounts.load_account(account)
    if cfg.get('project') != context.allowed_accounts[account]: error('scope_unavailable')
    return cfg


def _schema(request, keys, required):
    if type(request) is not dict or set(request)-set(keys)-{'operation','account'} or not set(required)|{'account','operation'} <= set(request):
        error('invalid_request')
    for name in required:
        if not isinstance(request[name],str) or not request[name].strip(): error('invalid_request')
    if not isinstance(request['account'],str): error('invalid_scope')


def _queue(cfg):
    repo = Path(cfg['repo_dir']).absolute()
    relative=Path(cfg['queue_dir'])
    if relative.is_absolute() or not relative.parts or any(p.startswith('.') for p in relative.parts): error('invalid_draft')
    queue = repo / relative
    if os.path.commonpath([repo, queue.absolute()]) != str(repo) or '..' in queue.parts:
        error('invalid_draft')
    return repo, queue


def _id(name):
    return hashlib.sha256(name.encode()).hexdigest()


def _rows(cfg, account):
    repo, directory = _queue(cfg)
    tree = writeback.upstream_sha(str(repo))
    rows = []
    with server_files.directory(directory) as fd:
        for name in sorted(os.listdir(fd)):
            if not name.endswith('.md'): continue
            raw = server_files.read_at(fd, name)
            q = queuefile.parse_text(raw.decode(), str(directory/name))
            if q.malformed or q.front_matter.get('account') != account: continue
            verified = writeback.matches_synced_commit(str(repo), str(directory/name), tree_sha=tree, disk_bytes=raw)
            rows.append((name, raw, q, verified))
    return rows


def _draft(cfg, account, draft_id, *, verified=True):
    if not isinstance(draft_id,str) or not DRAFT_ID.fullmatch(draft_id): error('invalid_request')
    for name, raw, q, synced in _rows(cfg,account):
        if _id(name)==draft_id:
            if verified and not synced: error('draft_not_verified')
            return name, raw, q
    error('scope_unavailable')


def _sync(cfg):
    ok, _, sha = writeback.sync_repo(cfg['repo_dir'])
    if not ok or not sha: error('draft_not_verified')


def draft_put(context, request, via):
    _schema(request, ('body','topic','publish_at','reply_to','draft_id','expected_revision','media',
                      # 4.0 S4 §9.5: SNS の告知（結ぶ配信 id と記事の名）。本文の `{article_url}` は本文の一部。
                      'announces','article'), ('body','publish_at'))
    account=request['account'];cfg=current(context,account,write=True)
    clone, _ = managed_repo.locations(account)
    _, queue = _queue(cfg)
    if cfg['repo_dir'] != str(clone): error('managed_repo_required')
    if ('draft_id' in request) != ('expected_revision' in request): error('invalid_request')
    for key in ('topic','reply_to','publish_at','announces','article'):
        value=request.get(key)
        if value is not None and (not isinstance(value,str) or writeback.has_control_chars(value)): error('invalid_draft')
    if len(request['body'].encode())>48000: error('invalid_draft')
    rows=None
    if 'media' in request:
        from . import media_uploads
        rows=media_uploads.draft_rows(account,context.actor,request['media'])
    with server_files.account_locks(account,cfg):
        current(context,account,write=True)
        managed_repo.initialize(account,cfg);_sync(cfg)
        name=secrets.token_hex(16)+'.md';old=None
        if 'draft_id' in request:
            name,old,q=_draft(cfg,account,request['draft_id'])
            if q.front_matter.get('status')!='draft' or q.front_matter.get('post_id'): error('draft_not_editable')
            if request['expected_revision']!=hashlib.sha256(old).hexdigest(): error('draft_changed')
        text='---\nthth: 1\naccount: '+account+'\nstatus: draft\npublish_at: '+request['publish_at']+'\n'
        for key in ('topic','reply_to','announces','article'):
            if request.get(key): text+=key+': '+request[key]+'\n'
        if rows is not None:
            # Alt is quoted as JSON so any legal alt survives the round trip;
            # the file is the repo path a completed upload already committed.
            text+='media:\n'+''.join('  - file: '+row['file']+'\n    alt: '+json.dumps(row['alt'],ensure_ascii=False)+'\n' for row in rows)
        text+='---\n\n## '+cfg['media']+'\n\n'+request['body'].strip()+'\n'
        q=queuefile.parse_text(text,'draft')
        if queuefile.extract_section(q.body,cfg['media']).strip()!=request['body'].strip(): error('invalid_draft','body_not_representable')
        if rows is not None and q.front_matter.get('media')!=rows: error('invalid_draft','media_not_representable')
        if old == text.encode():
            return {'draft_id':_id(name),'account':account,'status':'draft','revision':hashlib.sha256(old).hexdigest()}
        # Existing lint is the authority. A private temporary leaf is never queued.
        from . import lint
        with server_files.directory(queue) as fd:
            temp='.lint-'+secrets.token_hex(16)+'.tmp'
            try:
                server_files.replace_at(fd,temp,text.encode(),new=True)
                problems=lint.lint_file(str(queue/temp))
                # 断るなら理由まで返す。呼び手は何を直せばよいか判らないまま
                # 同じ本文を送り直す（初回の 3.0 通し運転・2026-09-23）。
                if any(not lint.is_warning(item) for item in problems): error('invalid_draft',lint.reason_code(problems))
            finally:
                try: os.unlink(temp,dir_fd=fd)
                except FileNotFoundError: pass
            server_files.replace_at(fd,name,text.encode(),expected=old,new=old is None)
        def unchanged():
            with server_files.directory(queue) as fd:
                return server_files.read_at(fd,name)==text.encode()
        ok,_=writeback.commit_and_push(str(clone),rel_path=os.path.relpath(queue/name,clone),
                                      message=f'draft by={context.actor} via={via}',validate=unchanged)
        if not ok: error('draft_commit_unconfirmed')
        return {'draft_id':_id(name),'account':account,'status':'draft','revision':hashlib.sha256(text.encode()).hexdigest()}


# --------------------------------------------------------------------------
# 公開・削除・予約はその場で行う（設計 3.13.0・3.12.0 §3.2 の道が唯一の道）。
# どの道も安全装置（`thth/guard.py`）を**ロックの中で**もう一度通してから出す・消す・刻む。
# --------------------------------------------------------------------------

def _origin(context, via):
    from . import guard
    return {'via':via,'credential':guard.credential_id(context)}


def _require_scheduled(cfg):
    """timer に載っていない口座（`scheduled: false`）では予約も猶予も「刻んでも出ない」。黙らず断る。"""
    if cfg.get('scheduled',True) is False: error('schedule_unavailable')


def _guarded(context, account, kind, origin, **extra):
    from . import guard
    def check(cfg):
        guard.check(account,cfg,kind,actor=context.actor,via=origin['via'],credential=origin['credential'],**extra)
    return check


def _refuse_if_stopped(account):
    from .guard import stopped, GuardRefused
    halted=stopped(account)
    if halted is not None: raise GuardRefused('account_stopped',reason=halted.get('reason'))


def direct_send(context, request, via):
    """その場で公開する。点検は `core.send_once` と同じ（rehearsal で digest を出し、同じ digest で出す）。"""
    _schema(request,('body','topic','reply_to','media'),('body',) if 'media' not in request else ())
    # 添付（確定済みの `[{media_id, alt}]`・10/15 点検 B-18）があれば本文は空でもよい（手元の `thth send --media` と同じ）。
    if not isinstance(request.get('body'),str): error('invalid_request')
    if len(request['body'].encode())>48000: error('invalid_draft')
    for key in ('topic','reply_to'):
        if request.get(key) is not None and not isinstance(request[key],str): error('invalid_request')
    account=request['account'];cfg=current(context,account,write=True)
    if cfg.get('production') is not True: error('production_disabled')
    rows={}
    if 'media' in request:
        from . import media_uploads
        rows={'media_rows':media_uploads.draft_rows(account,context.actor,request['media'])}
    from . import postid
    reply=postid.for_account(cfg,request.get('reply_to'),account);topic=request.get('topic')
    # 返信には最短間隔を掛けない（裁定 (a)）。上限と burst には数える。返信の burst は口座を止めず、
    # 窓が空く時刻の順番待ちに並べる（3.25.0・`burst_queued_send`）。
    origin=_origin(context,via)
    from . import burst_queue
    def check(locked_cfg):
        pending=burst_queue.pending(account,locked_cfg) if reply else None
        _guarded(context,account,'publish',origin,reply=bool(reply),pending_replies=pending)(locked_cfg)
    try:
        check(cfg)
    except GuardRefused as refused:
        return _burst_or_raise(context,request,via,cfg,refused,reply)
    dry=core.send_once(account,text=request['body'],topic=topic,reply_to=reply,log=lambda _:None,**rows)
    # 前の公開の結果が分かっていない（inflight）なら、本文の断りに丸めない（3.14.3 穴 3）。
    # 次の一手は `thth inflight <口座>`（見る）と `… resolve`（解く）。
    if dry.action=='inflight': error('publication_unconfirmed','inflight')
    if dry.action=='locked': error('account_busy')
    # 媒体がアカウント側の断りを返して止めた口座（3.19.2・`publish_hold`）。詳細の語は添えない
    # （Worker の知っている止めの語は burst・owner だけ）。戻すのは運営者の CLI。
    if dry.action=='publish_held': error('account_stopped')
    if dry.exit_code or not dry.digest: error('invalid_draft')
    def before(locked_cfg):
        if current(context,account,write=True)!=locked_cfg: error('credential_changed')
        check(locked_cfg)
        return accounts.load_token(locked_cfg)
    try:
        result=core.send_once(account,text=request['body'],topic=topic,reply_to=reply,production_flag=True,
                              confirm=dry.digest,log=lambda _:None,before_execute=before,
                              lock_context=server_files.account_locks(account,cfg),origin=origin,**rows)
    except GuardRefused as refused:
        # ロックの中で数え直したら burst に当たった（その間に別の公開が出た）。ロックの外で並べる。
        return _burst_or_raise(context,request,via,cfg,refused,reply)
    if result.action=='locked': error('account_busy')
    if result.action=='publish_held': error('account_stopped')
    if result.action=='inflight': error('publication_unconfirmed')
    if result.exit_code or result.mode!='production' or not result.post_id: error('publication_failed')
    try:
        with admin_log.transaction():
            admin_log.append('sent',account,cfg,by=context.actor,via=via,diff={'published':[False,True]})
    except Exception:
        # 出たことの正本は sent/ と runs。変更ログに残せなくても「出ていない」とは答えない。
        pass
    permalink=result.url
    if not permalink:
        try:
            from . import retract_cli
            permalink=retract_cli._lookup_url(cfg,accounts.load_token(cfg),result.post_id)
        except Exception:
            permalink=None
    return {'account':account,'status':'published','post_id':result.post_id,'permalink':permalink,'via':via}


def direct_retract(context, request, via):
    """その場で削除する（DELETE は `retract_cli._do_retract` の 1 回だけ）。"""
    _schema(request,('post_id','reason'),('post_id','reason'))
    account=request['account'];cfg=current(context,account,write=True)
    from types import SimpleNamespace
    from . import adapters, postid, retract_cli
    if not postid.is_usable(request['post_id']) or writeback.has_control_chars(request['reason']): error('invalid_request')
    cls=adapters.adapter_class(cfg['media'])
    if not getattr(cls,'DELETE_PERMISSION',None): error('unsupported_operation')
    record=retract_cli._find_record(cfg,account,request['post_id'])
    if not record or record.get('front_matter',{}).get('retracted_at'): error('scope_unavailable')
    if record['source']=='queue':
        owned=[item for item in _rows(cfg,account) if str(_queue(cfg)[1]/item[0])==record['path']]
        if len(owned)!=1 or not owned[0][3]: error('draft_not_verified')
    if cfg.get('production') is not True: error('production_disabled')
    token=accounts.load_token(cfg)
    if not cls.has_token(token) or cls.missing_permissions(token,[cls.DELETE_PERMISSION]): error('permission_unavailable')
    origin=_origin(context,via);check=_guarded(context,account,'retract',origin)
    check(cfg)
    def before(locked_cfg):
        if current(context,account,write=True)!=locked_cfg: error('credential_changed')
        check(locked_cfg)
        return accounts.load_token(locked_cfg)
    outcome={}
    retract_cli._do_retract(SimpleNamespace(account=account,wait=0,json=False,result_sink=outcome.update),
            cfg,cls,token,record,request['post_id'],reason=request['reason'],by=context.actor,url=record.get('url'),
            before_execute=before,lock_context=server_files.account_locks(account,cfg),origin=origin)
    if outcome.get('retracted') is not True: error('deletion_failed')
    # permalink は手元の道の答えと同じ名前（10/15 調べ_JSONの欄 §5 1-3・足すだけ）。
    return {'account':account,'status':'retracted','post_id':request['post_id'],'retracted_at':outcome.get('retracted_at'),
            'permalink':outcome.get('permalink'),'push_pending':bool(outcome.get('push_error')),'via':via}


# 予約の時刻がこれより古ければ断る（時計のずれと打つ間の分だけ許す・`thth/remote.py` と同じ値）。
PAST_GRACE_MINUTES = 5


def _publish_time(value):
    try: return queuefile.parse_publish_at(value)
    except (ValueError,TypeError): return None


def _hatena(cfg):
    from . import principals
    return principals.uses_principal(cfg)


def schedule(context, request, via, extra_fields=None):
    """下書きを queue に「出してよい」として刻む（timer が `publish_at` 以降に拾う）。

    刻む front matter は `thth approve` と同じ `approval.approved_fields`（timer が指紋を照合する）。
    `approved_by` は資格の actor、足すのは `approved_via`（mcp・cli・http）と `approved_credential`。

    **はてなの口座は 2 段**（設計 4.0 S1 §6.4）: `confirm` が無ければ `{stage: 1, digest, preview}` を
    返して何も書かない。同じ `confirm` で刻む（違えば `confirm_mismatch`）。刻むときに配信の台帳を
    作り（`wanted`）、`approved_kind`・`delivery` を書き戻す。SNS の口座は今までどおり 1 段。
    """
    account=request.get('account') if type(request) is dict else None
    if isinstance(account,str) and _hatena(current(context,account,write=True)):
        _schema(request,('draft_id','confirm'),('draft_id',))
        if request.get('confirm') is not None and not isinstance(request['confirm'],str): error('invalid_request')
    else:
        _schema(request,('draft_id',),('draft_id',))
    account=request['account'];cfg=current(context,account,write=True)
    if cfg.get('production') is not True: error('production_disabled')
    _require_scheduled(cfg)
    origin=_origin(context,via)
    _refuse_if_stopped(account)
    with server_files.account_locks(account,cfg):
        locked=current(context,account,write=True)
        _sync(locked)
        name,raw,q=_draft(locked,account,request['draft_id'])
        if q.front_matter.get('status')!='draft' or q.front_matter.get('post_id'): error('draft_not_editable')
        from .cli import _prepare_one
        path=_queue(locked)[1]/name
        value,problem=_prepare_one(str(path))
        if problem or not value or value.get('bundle'): error('invalid_draft')
        # 読めない時刻を「今」に倒さない（10/15 調べ_道ごとの意味の差 §1-3 の 12）。lint が先に断るが、
        # ここでも fail-closed に。過去の時刻は timer の次の run ですぐ出るので、`PAST_GRACE_MINUTES` より古ければ断る
        # （「後で出すつもりが今出る」を防ぐ・手元の CLI の `--at` と同じ線・10/16）。猶予の内は「今」として、1 日の上限も
        # 今日の分として数える。
        publish_at=_publish_time(value.get('publish_at'))
        if publish_at is None: error('invalid_draft','invalid_publish_at')
        now=jst.now_jst()
        if jst.to_jst(publish_at)<now-timedelta(minutes=PAST_GRACE_MINUTES): error('invalid_draft','publish_at_past')
        if jst.to_jst(publish_at)<now: publish_at=now
        hatena=value.get('kind')=='hatena_entry'
        if hatena and not request.get('confirm'):
            # 1 段目: 原稿も台帳も書かない（`inflight_resolve` と同じ 2 段の形）。見せるのは手元の 1 段目と同じ材料。
            # 監査 高-1: 見せた本文と配信の台帳の seq を控える（2 段目までに衝突を解いた・見つけたら断る）。
            from . import hatena_entry
            hatena_entry.record_first_stage(value)
            return {'account':account,'draft_id':request['draft_id'],'status':'draft','stage':1,
                    'digest':value['digest'],'preview':'\n'.join(value['display_lines'][:-1]),'via':via}
        # ↑ 最後の 1 行（手元の `thth approve <VM のパス> --confirm …`）は出さない（サーバのパスを口から出さない）。
        if hatena and request['confirm']!=value['digest']: error('confirm_mismatch')
        day=jst.to_jst(publish_at).date()
        # 4.3.0（監査 中-1）: 返信の原稿は返信だけの日の上限（`reply.daily`）、投稿の原稿は投稿の日の上限
        # （`post.daily`）で数える。その日に刻んだ数も同じ種類だけを足す（猶予・返信の順番待ちもこの道）。
        from .select import is_reply
        reply=is_reply(q.front_matter)
        same_day=0
        for _name,_raw,other,_verified in _rows(locked,account):
            fm=other.front_matter;when=_publish_time(fm.get('publish_at'))
            if fm.get('status')=='approved' and not fm.get('post_id') and when is not None and jst.to_jst(when).date()==day \
                    and is_reply(fm)==reply:
                same_day+=1
        _guarded(context,account,'schedule',origin,publish_at=publish_at,scheduled_same_day=same_day,reply=reply)(locked)
        at=jst.iso()
        if hatena:
            from . import hatena_entry
            hatena_entry.assign_delivery(value,by=context.actor,at=at,origin=origin)
        fields=approval.approved_fields(value,context.actor,at)
        fields['approved_via']=via
        if origin['credential']: fields['approved_credential']=origin['credential']
        fields.update(extra_fields or {})
        updated=writeback.front_matter_text(raw.decode(),fields)
        with server_files.directory(_queue(locked)[1]) as fd:
            server_files.replace_at(fd,name,updated.encode(),expected=raw)
        def unchanged():
            with server_files.directory(_queue(locked)[1]) as fd:
                return server_files.read_at(fd,name)==updated.encode()
        ok,_=writeback.commit_and_push(locked['repo_dir'],rel_path=os.path.relpath(path,locked['repo_dir']),
                                      message=f'schedule by={context.actor} via={via}',validate=unchanged)
        if not ok: error('schedule_commit_unconfirmed')
    return {'account':account,'draft_id':request['draft_id'],'status':'approved','publish_at':value.get('publish_at'),'via':via}


def held_send(context, request, via, minutes):
    """`hold_minutes` が 1 以上の口座の「今すぐ」: その分だけ先の予約にする（その間は持ち主が取り消せる）。"""
    import datetime as _dt
    _schema(request,('body','topic','reply_to','media'),('body',) if 'media' not in request else ())
    _require_scheduled(current(context,request['account'],write=True))
    publish_at=jst.iso(jst.now_jst()+_dt.timedelta(minutes=minutes))
    put={'operation':'draft_put','account':request['account'],'body':request.get('body'),'publish_at':publish_at}
    for key in ('topic','reply_to','media'):
        if request.get(key): put[key]=request[key]
    made=draft_put(context,put,via)
    # `held_minutes` は /activity が「猶予中」と「予約」を見分ける印（出す時刻には効かない）。
    done=schedule(context,{'operation':'schedule_request','account':request['account'],'draft_id':made['draft_id']},via,
                  extra_fields={'held_minutes':str(minutes)})
    return {**done,'status':'held','hold_minutes':minutes}


def _burst_or_raise(context, request, via, cfg, refused, reply):
    """返信の burst（`burst_limit` に窓が空く時刻つき）なら順番待ちに並べる。並べられなければそのまま断る。

    並べられないのは timer に載っていない口座（`scheduled: false`）と添付のある返信（3.25.0）。
    どちらも口座は止めず、`burst_limit`（詳細 `burst`・`next_at`）で断る。
    """
    from . import burst_queue
    if (reply and str(refused)=='burst_limit' and refused.next_at and burst_queue.can_queue(cfg)
            and not request.get('media')):
        return burst_queued_send(context,request,via,refused.next_at)
    raise refused


def burst_queued_send(context, request, via, publish_at):
    """返信の burst（3.25.0）: その 1 件を `publish_at`（窓が空く時刻）の承認済みの予約にする。

    `held_send` と同じ draft_put ＋ schedule。印 `queued_for: burst` は /activity と `thth schedule` が
    「順番待ち」を見分けるため（指紋には入らない・出す時刻には効かない）。1 日の上限は schedule の
    安全装置が返信だけの日の上限（`reply.daily`・4.3.0）で、その日の返信と刻んだ返信を合わせて数える
    （並べずに `daily_limit`・詳細 `reply_daily` で断る）。
    """
    from . import burst_queue
    put={'operation':'draft_put','account':request['account'],'body':request.get('body'),'publish_at':publish_at}
    for key in ('topic','reply_to'):
        if request.get(key): put[key]=request[key]
    made=draft_put(context,put,via)
    done=schedule(context,{'operation':'schedule_request','account':request['account'],'draft_id':made['draft_id']},via,
                  extra_fields={burst_queue.MARK_KEY:burst_queue.MARK_VALUE})
    return {**done,'status':'queued','reason':'burst','publish_at':publish_at}


def direct(context, request, via, cfg):
    op=request['operation']
    if op=='schedule_request':
        return schedule(context,request,via)
    if _hatena(cfg):
        # はてなの口座（設計 4.0 S1 §6.4）: 本文をその場で出す口・その場で消す口は無い。
        # 出すのも消すのも配信原稿（op: create・update・delete）を予約で刻む道だけ。
        error('unsupported_operation')
    if op=='retract_request':
        return direct_retract(context,request,via)
    minutes=accounts.guard_limits(cfg)['hold_minutes']
    return held_send(context,request,via,minutes) if minutes else direct_send(context,request,via)


def bookmark(context, request, via):
    """はてなブックマーク（設計 4.0 S1 §7.3・S4 §6・§8・§11）。2 段: `confirm` が無ければ `{stage: 1, digest, preview}`。

    対象は `delivery`（自分の記事）か `url`（参照先）の**どちらか 1 つ**。`op` は `add`（既定）か `delete`。
    2 段目はロックの中で安全装置と 3 つの柵を通して 1 回だけ打つ。`reconcile`・`list` はここに無い（CLI だけ）。
    """
    _schema(request,('delivery','url','comment','tags','confirm','op'),())
    for key in ('delivery','url','comment','confirm','op'):
        if request.get(key) is not None and not isinstance(request[key],str): error('invalid_request')
    if request.get('tags') is not None and not isinstance(request['tags'],(str,list)): error('invalid_request')
    if bool(request.get('delivery')) == bool(request.get('url')): error('invalid_request')
    account=request['account'];cfg=current(context,account,write=True)
    if not _hatena(cfg): error('unsupported_operation')
    from . import hatena_bookmark
    op=request.get('op') or 'add'
    if op=='add' and not (request.get('comment') or '').strip(): error('invalid_request')
    edit=op=='edit'
    def prepared(locked_cfg,check_remote=True):
        try:
            if edit:
                # 4.2（S4 §21）: 直し。comment・tags は省けば台帳の今の値のまま（None のまま渡す）。
                return hatena_bookmark.prepare_edit(account,locked_cfg,delivery=request.get('delivery'),
                                                    url=request.get('url'),comment=request.get('comment'),
                                                    tags=request.get('tags'),check_remote=check_remote)
            return hatena_bookmark.prepare(account,locked_cfg,delivery=request.get('delivery'),url=request.get('url'),
                                           comment=request.get('comment') or '',tags=request.get('tags'),op=op,
                                           check_remote=check_remote)
        except hatena_bookmark.BookmarkError as exc:
            error(exc.code if exc.code in SAFE_ERRORS else 'invalid_request')
    first=prepared(cfg)
    if not request.get('confirm'):
        if edit:
            return {'account':account,'status':'not_edited','stage':1,'digest':first['digest'],
                    'preview':hatena_bookmark.preview_edit(first),'via':via}
        return {'account':account,'status':'not_bookmarked','stage':1,'digest':first['digest'],
                'preview':hatena_bookmark.preview(first),'via':via}
    if request['confirm']!=first['digest']: error('confirm_mismatch')
    if cfg.get('production') is not True: error('production_disabled')
    origin=_origin(context,via)
    with server_files.account_locks(account,cfg):
        locked=current(context,account,write=True)
        again=prepared(locked,check_remote=False)
        if again['digest']!=request['confirm']: error('confirm_mismatch')
        try:
            run=hatena_bookmark.edit_locked if edit else hatena_bookmark.execute_locked
            done=run(account,locked,again,actor=context.actor,via=origin['via'],credential=origin['credential'])
        except hatena_bookmark.BookmarkError as exc:
            code=exc.code
            # `hatena_http_<番号>` は数が開いているので `publication_failed` に畳む（静的な語だけを口から出す）。
            error(code if code in SAFE_ERRORS else ('deletion_failed' if op=='delete' else 'publication_failed'))
    return {**done,'via':via}


def bookmark_batch_request(context, request, via):
    """ブックマークのまとめて承認（設計 S4 §20・4.1）。`text` は札のファイルの中身そのまま（JSON Lines か配列）。

    2 段: `confirm` が無ければ `{stage: 1, digest, preview}`（何も書かない）。同じ digest でロックの中で
    作り直して照合し、口座の state に順番待ちとして置く。承認した人は資格の actor。付けるのは timer。
    """
    _schema(request,('text','confirm'),('text',))
    if request.get('confirm') is not None and not isinstance(request['confirm'],str): error('invalid_request')
    account=request['account'];cfg=current(context,account,write=True)
    if not _hatena(cfg): error('unsupported_operation')
    from . import bookmark_batch
    def prepared(locked_cfg):
        try:
            return bookmark_batch.prepare(account,locked_cfg,request['text'])
        except bookmark_batch.BatchError as exc:
            error(exc.code if exc.code in SAFE_ERRORS else 'invalid_request')
    first=prepared(cfg)
    if not request.get('confirm'):
        return {'account':account,'status':'not_queued','stage':1,'digest':first['digest'],'preview':first,'via':via}
    if request['confirm']!=first['digest']: error('confirm_mismatch')
    if cfg.get('production') is not True: error('production_disabled')
    origin=_origin(context,via)
    with server_files.account_locks(account,cfg):
        locked=current(context,account,write=True)
        again=prepared(locked)
        if again['digest']!=request['confirm']: error('confirm_mismatch')
        try:
            done=bookmark_batch.approve_locked(account,locked,again,actor=context.actor,via=origin['via'],
                                               credential=origin['credential'])
        except bookmark_batch.BatchError as exc:
            error(exc.code if exc.code in SAFE_ERRORS else 'write_unavailable')
    return {**done,'via':via}


def bookmark_batch_cancel(context, request, via):
    """待ちの札の取り消し（1 段・止める向き）。取り消した人は資格の actor。"""
    _schema(request,('batch','item'),('batch',))
    if request.get('item') is not None and type(request['item']) is not int: error('invalid_request')
    account=request['account'];cfg=current(context,account,write=True)
    if not _hatena(cfg): error('unsupported_operation')
    from . import bookmark_batch
    with server_files.account_locks(account,cfg):
        try:
            done=bookmark_batch.cancel_locked(account,request['batch'],by=context.actor,item=request.get('item'))
        except bookmark_batch.BatchError as exc:
            error(exc.code if exc.code in SAFE_ERRORS else 'write_unavailable')
    return {**done,'via':via}


def cancel_schedule(account, draft_id, *, by, via='http'):
    """予約（猶予中を含む）を取り消して draft に戻す（持ち主の /activity から・段 3）。

    `thth revoke` と同じ書き戻し（status: draft・承認の 3 項目を空・revoked_* を残す）。本文には
    触らない。既に出たもの（post_id あり）・承認済みでないものは断る。何度呼んでも同じ結果。
    戻り値は `cancelled`（取り消した）か `already_draft`（既に draft）。
    """
    admin_log.actor(by)
    cfg=accounts.load_account(account)
    with server_files.account_locks(account,cfg):
        cfg=accounts.load_account(account)
        _sync(cfg)
        name,raw,q=_draft(cfg,account,draft_id)
        fm=q.front_matter
        if fm.get('post_id'): error('draft_not_editable')
        if fm.get('status')=='draft': return 'already_draft'
        if fm.get('status')!='approved': error('draft_not_editable')
        path=_queue(cfg)[1]/name
        updated=writeback.front_matter_text(raw.decode(),{'status':'draft','approved_sha':None,'approved_at':None,
            'approved_by':None,'revoked_at':jst.iso(),'revoked_by':by,'revoked_reason':'owner_cancel_via_'+via})
        with server_files.directory(_queue(cfg)[1]) as fd:
            server_files.replace_at(fd,name,updated.encode(),expected=raw)
        def unchanged():
            with server_files.directory(_queue(cfg)[1]) as fd:
                return server_files.read_at(fd,name)==updated.encode()
        ok,_=writeback.commit_and_push(cfg['repo_dir'],rel_path=os.path.relpath(path,cfg['repo_dir']),
                                      message=f'cancel by={by} via={via}',validate=unchanged)
        if not ok: error('schedule_commit_unconfirmed')
    return 'cancelled'


def recover_repo(account, cfg):
    """記録置き場の無いまま `data/` だけが書かれた招待の口座を、口座のロックの中で初期化する（3.14.3 穴 1）。

    `.git` があれば何もしない（毎回の依頼で Git の置き場を歩かない）。直せない途中の状態には触らず、
    そのあとの `initialize`／`validate` が今までどおり断る。ロックが取れなければ次の依頼に回す。
    """
    from .lock import LockBusy
    clone,_=managed_repo.locations(account)
    if cfg.get('repo_dir')!=str(clone) or os.path.isdir(clone/'.git'): return
    try:
        with server_files.account_locks(account,cfg):
            managed_repo.recover(account,cfg)
    except LockBusy:
        return


def execute(context, request, *, via='http'):
    if type(request) is not dict or request.get('operation') not in WRITE_OPERATIONS: error('unsupported_operation')
    if not isinstance(request.get('account'),str): error('invalid_scope')
    try:
        cfg=current(context,request['account'],write=True)
        recover_repo(request['account'],cfg)
        if request['operation'] in ('media_upload_url','media_complete'):
            from . import media_uploads
            return media_uploads.execute(context,request,via)
        if request['operation']=='draft_put':
            return draft_put(context,request,via)
        if request['operation']=='bookmark_request':
            _refuse_if_stopped(request['account'])
            return bookmark(context,request,via)
        if request['operation']=='bookmark_batch_request':
            _refuse_if_stopped(request['account'])
            return bookmark_batch_request(context,request,via)
        if request['operation']=='bookmark_batch_cancel':
            # 取り消しは止める向きなので、止まった口座でも受ける。
            return bookmark_batch_cancel(context,request,via)
        # 止まった口座（安全装置）は公開・削除・予約を受けない。
        _refuse_if_stopped(request['account'])
        # timer に載っていない口座では予約を刻んでも出ない（裁定 (b)）。
        if request['operation']=='schedule_request': _require_scheduled(cfg)
        return direct(context,request,via,cfg)
    except ReportServiceError: raise
    except (OSError,ValueError,TypeError,KeyError,accounts.AccountError,relay.RelayError,admin_log.AdminLogError) as exc:
        if str(exc) in REPO_REASONS: error(str(exc))
        error('write_unavailable')


def serve(context, request, *, via):
    """遠くの道（設計 3.14.0 §3.1）の受け付け: operation で書く口か読む口へ渡す。名前の表はここ（呼び手は持たない）。

    書く口は `execute`、下書きの読む口は `READ_OPERATIONS`（`read`）、§3.2 の読む口は `server_reads`、
    状態と設定は `account_settings`（MCP と同じ関数）。知らない名前は `unsupported_operation`。
    """
    operation=request.get('operation') if type(request) is dict else None
    if operation in WRITE_OPERATIONS: return execute(context,request,via=via)
    if operation in READ_OPERATIONS: return read(context,request)
    # 段 1 の読む口（設計 §3.2・`thth/server_reads.py`）と、状態・設定（MCP と同じ関数）。
    from . import server_reads
    if operation in server_reads.OPERATIONS: return server_reads.execute(context,request)
    if operation in INFLIGHT_OPERATIONS: return inflight(context,request,via)
    if operation in CONFLICT_OPERATIONS: return conflict(context,request,via)
    if operation in ('settings','account_status'):
        from . import account_settings
        return (account_settings.mcp_settings if operation=='settings' else account_settings.mcp_status)(context,request)
    error('unsupported_operation')


# --------------------------------------------------------------------------
# 遠くの道の inflight（3.14.3 穴 3）。手元の `thth inflight <口座> [resolve]` と同じ見せ方・同じ二段確認。
# 解いた人は資格の actor（依頼の欄から名乗らせない）。
# --------------------------------------------------------------------------

INFLIGHT_OPERATIONS = frozenset(('inflight_status','inflight_resolve'))


def inflight(context, request, via):
    from . import inflight as inflight_mod, inflight_cli
    op=request.get('operation')
    if op=='inflight_status': _schema(request,(),())
    else:
        _schema(request,('decision','post_id','confirm'),('decision',))
        if request['decision'] not in ('published','not_published'): error('decision_required')
        for key in ('post_id','confirm'):
            if request.get(key) is not None and not isinstance(request[key],str): error('invalid_request')
    account=request['account'];cfg=current(context,account,write=op=='inflight_resolve')
    state_dir=accounts.state_dir_for(account)
    try: record=inflight_mod.read(state_dir)
    except (OSError,ValueError): error('inflight_unreadable')
    if op=='inflight_status':
        if record is None: return {'account':account,'inflight':None}
        info=inflight_cli.summary(record)
        out={'account':account,'inflight':info,'next':inflight_cli._next_step(account,info)}
        # 4.0 S5 §8.3: はてなの inflight は一覧の候補（GET 1 回・自動では解かない）。
        found=inflight_cli.hatena_candidates(account,cfg,record)
        if found is not None:
            out.update(candidates=found['candidates'],candidates_checked=found['checked'],
                       candidates_error=found['error'],candidates_op=found['op'],candidates_gone=found['gone'])
        return out
    decision=request['decision'];post_id=(request.get('post_id') or '').strip() or None
    if decision=='published' and post_id is None: error('post_id_invalid')
    refused=inflight_cli.refusal(account,record,decision,post_id)
    if refused: error(refused[1])
    expected=inflight_cli.digest(account,record,decision,post_id)
    if not request.get('confirm'):
        return {'account':account,'resolved':False,'stage':1,'inflight':inflight_cli.summary(record),
                'decision':decision,'post_id':post_id,'digest':expected}
    if request['confirm']!=expected: error('confirm_mismatch')
    try:
        resolution,_path,logged=inflight_cli.apply(account,cfg,state_dir,decision,post_id,context.actor,
                                                   request['confirm'],via=via if via in ('mcp','http','api') else 'api')
    except inflight_cli.Refused as exc:
        if exc.code=='account_locked': error('account_busy')
        if exc.code in INFLIGHT_REASONS: error(exc.code)
        error('inflight_writeback_failed')
    return {'account':account,'resolved':True,'resolution':resolution,'post_id':post_id,'admin_log':logged}


def read(context, request):
    _schema(request,(),())
    cfg=current(context,request['account'])
    from . import leave_gate
    try:
        with leave_gate.lease(request['account']):
            rows=[]
            try:
                for name,raw,q,verified in _rows(cfg,request['account']):
                    fm=q.front_matter
                    rows.append(dict(draft_id=_id(name),revision=hashlib.sha256(raw).hexdigest(),status=fm.get('status'),
                                     body=queuefile.extract_section(q.body,cfg['media']),topic=fm.get('topic') or None,
                                     publish_at=fm.get('publish_at'),reply_to=fm.get('reply_to') or None,verified=verified))
            except FileNotFoundError: pass
            return {'account':request['account'],'drafts':rows}
    except accounts.AccountLeaving:
        error('account_leaving')


# --------------------------------------------------------------------------
# 遠くの道の衝突（4.0 S5 §5.3）。手元の `thth conflict <口座>` と同じ中身（`hatena_conflict`）・同じ 2 段。
# 解いた人は資格の actor（依頼の欄から名乗らせない）。どれも媒体に書き込まない（`--adopt` は GET だけ）。
# --------------------------------------------------------------------------

CONFLICT_OPERATIONS = frozenset(('conflict_status','conflict_resolve'))


def conflict(context, request, via):
    from . import hatena_conflict, hatena_entry
    op=request.get('operation')
    if op=='conflict_status':
        _schema(request,('delivery','check'),())
        if request.get('check') is not None and type(request['check']) is not bool: error('invalid_request')
        # 監査 低-3: 遠くの道の check は 1 件だけ（口座のロックを長く持たない）。
        if request.get('check') and not request.get('delivery'): error('invalid_request')
    else:
        _schema(request,('delivery','resolution','confirm'),('delivery','resolution'))
        if request['resolution'] not in hatena_conflict.RESOLUTIONS: error('resolution_required')
        if request.get('confirm') is not None and not isinstance(request['confirm'],str): error('invalid_request')
    if request.get('delivery') is not None and not isinstance(request['delivery'],str): error('invalid_request')
    account=request['account'];cfg=current(context,account,write=op=='conflict_resolve' or bool(request.get('check')))
    if not hatena_entry.is_hatena(cfg): error('unsupported_operation')
    delivery=request.get('delivery')
    try:
        if op=='conflict_status':
            if request.get('check'):
                return hatena_conflict.check(account,cfg,delivery,limit=1)
            if delivery:
                return hatena_conflict.show(account,cfg,delivery)
            return hatena_conflict.listing(account)
        return hatena_conflict.resolve(account,cfg,delivery,request['resolution'],by=context.actor,
                                       confirm=request.get('confirm'),via=via if via in ('mcp','http','api') else 'api')
    except hatena_conflict.ConflictRefused as exc:
        if exc.code=='conflict_busy': error('account_busy')
        if exc.code in hatena_conflict.REASONS: error(exc.code)
        error('write_unavailable')
