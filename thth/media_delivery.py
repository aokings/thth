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
    return cfg.get("media") not in ("mastodon","bluesky","threads")


def error_for(cfg,manifest):
    if not manifest:return None
    if unavailable(cfg):return 'media_provider_unavailable'
    from .adapters import mastodon_media,bluesky_media,threads_media
    return {'bluesky':bluesky_media,'mastodon':mastodon_media,'threads':threads_media}[cfg['media']].intent_error(manifest)


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
    started=False;result=None;durable_phase=None
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
                nonlocal started,durable_phase
                if phase=='uploading':started=True
                try:_progress(cfg['account'],manifest,adapter.service if cfg['media']=='bluesky' else adapter.base_url if cfg['media']=='threads' else adapter.instance,phase,**details)
                except (OSError,ValueError) as exc:raise media.MediaError('media_journal_unavailable') from exc
                durable_phase=phase
            from .adapters import mastodon_media
            from .media_relay import MediaRelay
            bound=dataclasses.replace(post,media_manifest=manifest,media_files=tuple(items),media_progress=progress,media_cache=(lambda cap:mastodon_media.cache(cfg,cap)) if cfg['media']=='mastodon' else None,media_relay=MediaRelay(cfg['account'],'operator') if cfg['media']=='threads' and items else None)
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
        # Context-exit stale detection must not erase a known remote outcome.
        # A successful result is retained only after its published journal saved.
        if isinstance(exc,media.MediaError) and str(exc)=='media: source_changed' and result is not None:
            if result.failure=='media_held' or (result.post_id and result.failure=='none' and durable_phase=='published'):
                return result
        reason=str(exc) if isinstance(exc,media.MediaError) else 'media_journal_unavailable'
        return base.PublishResult(None,None,jst.iso(),error=reason,failure='media_ambiguous' if started else 'publish_vetoed')


def cached_note(cfg):
    if cfg.get('media')!='mastodon':return None
    from .adapters import mastodon_media
    value=mastodon_media.cached(cfg)
    if value is None:return 'media limits: capability_unobserved (latest instance check required before upload)'
    return f"media limits: cached instance={value['instance']} version={value['version']} observed_at={value['observed_at']} (rechecked before upload)"


def lint_notes(cfg,fm,*,text=None):
    """Fresh public limits; unknown or excessive attachments never pass lint."""
    if not cfg or not media.declared(fm):return []
    if cfg.get('media')=='threads':
        from .adapters import threads_media
        try:
            with media.prepare(cfg['repo_dir'],fm,'threads') as (manifest,items):
                reason=threads_media.intent_error(manifest)
                if not reason and not items:threads_media.text_params(manifest,text)
                return [reason] if reason else threads_media.notes(manifest,items)
        except (OSError,ValueError) as exc:return [str(exc) if isinstance(exc,media.MediaError) else 'media_unavailable']
    if cfg.get('media')=='bluesky':
        try:
            with media.prepare(cfg['repo_dir'],fm,'bluesky') as (manifest,_):reason=error_for(cfg,manifest)
            return [reason] if reason else ['warning: bluesky video daily quota/email permission unobserved; rechecked before upload'] if any(r['kind']=='video' for r in manifest['files']) else ['warning: bluesky external thumbnail_alt is a local approval note; provider has no thumbnail alt field'] if any(a['type']=='link' and 'thumbnail_alt' in a for a in manifest['attachments']) else []
        except (OSError,ValueError) as exc:
            return [str(exc) if isinstance(exc,media.MediaError) else 'media_unavailable']
    if cfg.get('media')!='mastodon':return []
    from .adapters import mastodon_media
    from .mediaformats import FormatError
    try:
        # Preserve the existing file-media lint order: unavailable live limits
        # take precedence over opening local media; never fall back to cache.
        special=bool(fm.get('attachments')) or 'quote_approval_policy' in fm.get('post_options',{})
        cap=mastodon_media.observe(cfg) if fm.get('media') and not special else None
        with media.prepare(cfg['repo_dir'],fm,cfg['media']) as (manifest,items):
            reason=mastodon_media.intent_error(manifest)
            if reason:return [reason]
            poll=next((row for row in manifest['attachments'] if row['type']=='poll'),None)
            quote=any(row['type']=='quote' for row in manifest['attachments']) or 'quote_approval_policy' in manifest['post_options']
            mastodon_media.check_text_intent(manifest,text)
            if special and (items or poll is not None or quote):
                body,instance=mastodon_media.observe_instance(cfg);notes=[]
                if poll is not None:
                    limits=mastodon_media.poll_capabilities(body,instance)
                    mastodon_media.check_poll(limits,poll,text)
                    notes.append('warning: poll limits: latest instance version='+limits['version']+' observed_at='+limits['observed_at']+'; rechecked before publish')
                if quote:
                    version=mastodon_media.quote_capabilities(body)
                    notes.append('warning: quote capability: latest instance API='+str(version)+'; target checked before publish')
                if items:
                    limits=mastodon_media.capabilities(body,instance);mastodon_media.check_limits(limits,items)
                    mastodon_media.cache(cfg,limits);notes.append('warning: '+cached_note(cfg))
                return notes
            if items:mastodon_media.check_limits(cap,items)
            else:return ['warning: Mastodon generates a preview from the approved body URL; display is unobserved'] if any(row['type']=='link' for row in manifest['attachments']) else []
        return ['warning: '+cached_note(cfg)]
    except (OSError,ValueError) as exc:
        code=str(exc) if isinstance(exc,(media.MediaError,FormatError)) else 'media_capability_unavailable'
        return [code]
