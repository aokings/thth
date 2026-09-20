"""Prepared attachment lifetime and durable per-account media intent.

No automatic resume of an uncertain POST. Existing inflight gates stop retries.
"""
from __future__ import annotations
import dataclasses
import hashlib
import json
import os
import stat
import uuid
from . import accounts, handoff_cursor, jst, media
from .adapters import base


def unavailable(cfg):
    return cfg.get("media")!="mastodon"


def error_for(cfg,manifest):
    if not manifest:return None
    if unavailable(cfg):return 'media_provider_unavailable'
    from .adapters import mastodon_media
    return mastodon_media.intent_error(manifest)


def _save(name,data):
    directory=handoff_cursor._directory(name,create=True)
    temporary='.media-inflight-'+uuid.uuid4().hex
    try:
        try:
            old=os.stat('inflight.json',dir_fd=directory,follow_symlinks=False)
            if not stat.S_ISREG(old.st_mode) or old.st_nlink!=1 or old.st_uid!=os.geteuid():raise ValueError('media_journal_unavailable')
        except FileNotFoundError:pass
        payload=(json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2)+'\n').encode()
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,'inflight.json',src_dir_fd=directory,dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:os.unlink(temporary,dir_fd=directory)
            except FileNotFoundError:pass
    finally:os.close(directory)


def _progress(name,manifest,instance,phase,**fields):
    from . import leave_gate
    with leave_gate.lease(name):return _progress_locked(name,manifest,instance,phase,**fields)


def _progress_locked(name,manifest,instance,phase,**fields):
    directory=handoff_cursor._directory(name,create=True)
    try:
        fd=os.open('inflight.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid() or info.st_size>1024*1024:raise ValueError('media_journal_unavailable')
            raw=os.read(fd,1024*1024+1);data=json.loads(raw)
        finally:os.close(fd)
    finally:os.close(directory)
    if type(data) is not dict:raise ValueError('media_journal_unavailable')
    data['media']={'schema_version':1,'account':name,'instance':instance,'intent_sha256':hashlib.sha256(media.prepared_component(manifest).encode()).hexdigest(),'manifest':manifest,'phase':phase,**fields}
    _save(name,data)


def publish(adapter,post,*,cfg,fm,manifest,state_dir,before_publish=None,on_container_created=None):
    if not manifest:
        kwargs={'dry_run':False,'on_container_created':on_container_created}
        if before_publish is not None:kwargs['before_publish']=before_publish
        return adapter.publish(post,**kwargs)
    reason=error_for(cfg,manifest)
    if reason:return base.PublishResult(None,None,jst.iso(),error=reason,failure='publish_definite')
    if getattr(adapter,'prepared_media_supported',False) is not True:
        return base.PublishResult(None,None,jst.iso(),error='media_provider_unavailable',failure='publish_definite')
    started=False
    try:
        if os.path.realpath(state_dir)!=os.path.realpath(accounts.state_dir_for(cfg['account'])):raise media.MediaError('media_journal_account_mismatch')
        with media.prepare(cfg['repo_dir'],fm,cfg['media']) as (current,items):
            if media.prepared_component(current)!=media.prepared_component(manifest):raise media.MediaError('approval_stale')
            def veto():
                for item in items:item.verify()
                return before_publish() if before_publish else None
            stopped=veto()
            if stopped:raise media.MediaError(str(stopped))
            def progress(phase,**details):
                nonlocal started
                if phase=='uploading':started=True
                try:_progress(cfg['account'],manifest,adapter.instance,phase,**details)
                except (OSError,ValueError) as exc:raise media.MediaError('media_journal_unavailable') from exc
            from .adapters import mastodon_media
            bound=dataclasses.replace(post,media_manifest=manifest,media_files=tuple(items),media_progress=progress,media_cache=lambda cap:mastodon_media.cache(cfg,cap))
            # No request precedes this durable intent. It also changes the old
            # legacy inflight leaf to a private 0600 file without copying blobs.
            progress('prepared',remote_ids=[])
            result=adapter.publish(bound,dry_run=False,on_container_created=on_container_created,before_publish=veto)
            # Context exit rechecks sources, including modifications after POST.
            # If it fails, the durable published/publishing phase remains.
        return result
    except accounts.AccountStopped:
        return base.PublishResult(None,None,jst.iso(),error="account_stopped",failure="media_ambiguous" if started else "publish_vetoed")
    except (OSError,ValueError) as exc:
        reason=str(exc) if isinstance(exc,media.MediaError) else 'media_journal_unavailable'
        return base.PublishResult(None,None,jst.iso(),error=reason,failure='media_ambiguous' if started else 'publish_vetoed')


def cached_note(cfg):
    if cfg.get('media')!='mastodon':return None
    from .adapters import mastodon_media
    value=mastodon_media.cached(cfg)
    if value is None:return 'media limits: capability_unobserved (latest instance check required before upload)'
    return f"media limits: cached instance={value['instance']} version={value['version']} observed_at={value['observed_at']} (rechecked before upload)"


def lint_notes(cfg,fm):
    """Fresh public limits; unknown or excessive attachments never pass lint."""
    if not cfg or cfg.get('media')!='mastodon' or not fm.get('media'):return []
    from .adapters import mastodon_media
    from .mediaformats import FormatError
    try:
        cap=mastodon_media.observe(cfg)
        with media.prepare(cfg['repo_dir'],fm,cfg['media']) as (_,items):mastodon_media.check_limits(cap,items)
        return ['warning: '+cached_note(cfg)]
    except (OSError,ValueError) as exc:
        code=str(exc) if isinstance(exc,(media.MediaError,FormatError)) else 'media_capability_unavailable'
        return [code]
