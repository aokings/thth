"""Strict declarations, pinned originals and immutable prepared attachments.

No provider I/O. Original and public SHA are separate approval inputs.
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import re
import stat
import tempfile

UNAVAILABLE = 'media_preparation_unavailable'


class MediaError(ValueError):
    pass


def _scalar(raw):
    raw = raw.strip()
    if raw.startswith('"'):
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise MediaError('media: invalid quoted string') from exc
        if not isinstance(value, str):
            raise MediaError('media: string required')
        return value
    if raw.startswith("'"):
        if len(raw) < 2 or not raw.endswith("'"):
            raise MediaError('media: invalid quoted string')
        return raw[1:-1].replace("''", "'")
    if not raw or raw in ('null', '~', 'true', 'false') or raw[:1] in '|>[{&*!':
        raise MediaError('media: nonempty literal string required')
    return raw


def validate_path(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise MediaError('media: invalid relative file')
    if '\\' in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise MediaError('media: invalid relative file')
    parts = value.split('/')
    if any(p in ('', '.', '..', '.git') for p in parts) or re.match(r'^[A-Za-z]:', value):
        raise MediaError('media: repo-relative file required')
    return parts


def validate(rows):
    if not isinstance(rows, list):
        raise MediaError('media: array required')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) not in ({'file', 'alt'}, {'file', 'alt', 'thumbnail_file', 'thumbnail_alt'}):
            raise MediaError('media: exactly file and alt required, with optional paired thumbnail fields')
        validate_path(row['file'])
        alt = row['alt']
        if not isinstance(alt, str) or not alt.strip() or any(ord(c) < 32 or ord(c) == 127 for c in alt):
            raise MediaError('media: nonempty alt without control characters required')
        if row['file'] in seen:
            raise MediaError('media: duplicate file')
        seen.add(row['file'])
        if 'thumbnail_file' in row:
            validate_path(row['thumbnail_file'])
            validate([{'file':row['thumbnail_file'],'alt':row['thumbnail_alt']}])
            if row['thumbnail_file'] in seen:raise MediaError('media: duplicate file')
            seen.add(row['thumbnail_file'])
    return rows


def parse_block(lines, start, indent):
    """Parse only the documented media list, with exact indentation."""
    header = lines[start]
    if header != ' ' * indent + 'media:':
        if header == ' ' * indent + 'media: []':
            return [], start + 1
        raise MediaError('media: use an indented file/alt array')
    rows = []
    current = None
    i = start + 1
    while i < len(lines):
        raw = lines[i]
        if not raw.strip():
            i += 1
            continue
        spaces = len(raw) - len(raw.lstrip(' '))
        if spaces <= indent and not raw.startswith('\t'):
            break
        if '\t' in raw[:spaces + 1]:
            raise MediaError('media: tabs are not indentation')
        if raw.startswith(' ' * (indent + 2) + '- ') and spaces == indent + 2:
            current = {}
            rows.append(current)
            item = raw[spaces + 2:]
        elif spaces == indent + 4 and current is not None:
            item = raw[spaces:]
        else:
            raise MediaError('media: invalid indentation')
        key, colon, value = item.partition(':')
        if not colon or key not in ('file', 'alt', 'thumbnail_file', 'thumbnail_alt') or key in current:
            raise MediaError('media: unknown or duplicate field')
        current[key] = _scalar(value)
        i += 1
    if not rows:
        raise MediaError('media: empty block; use media: []')
    return validate(rows), i


def _identity(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _open_dir(path):
    path = os.path.abspath(os.fspath(path))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.split('/')[1:]:
            if not part:
                continue
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError:
                # Preserve NOFOLLOW; distinguish account repo configuration
                # from a bad attachment leaf without resolving the alias.
                if stat.S_ISLNK(os.stat(part,dir_fd=fd,follow_symlinks=False).st_mode):
                    raise MediaError('media: repo_dir_symlink') from None
                raise
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_file(root_fd, relative):
    parts = validate_path(relative)
    directory = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise MediaError('media: single-link regular file required')
            return fd
        except BaseException:
            # Ownership transfers only on return, including failed fstat().
            os.close(fd)
            raise
    finally:
        os.close(directory)


@dataclasses.dataclass(frozen=True)
class Source:
    file: str
    alt: str
    source_sha256: str
    size: int
    fd: int = dataclasses.field(repr=False, compare=False)
    _root_fd: int = dataclasses.field(repr=False, compare=False)
    _stat: tuple = dataclasses.field(repr=False, compare=False)

    def verify(self):
        """Detect replacement/mutation; callers keep this pin through use."""
        if _identity(os.fstat(self.fd)) != self._stat:
            raise MediaError('media: source_changed')
        other = _open_file(self._root_fd, self.file)
        try:
            if _identity(os.fstat(other)) != self._stat:
                raise MediaError('media: source_changed')
        finally:
            os.close(other)

    def fingerprint(self):
        return {'file': self.file, 'alt': self.alt,
                'source_sha256': self.source_sha256, 'size': self.size}


@contextlib.contextmanager
def pin_sources(repo_dir, rows):
    """No writes, network or format claims; hash exactly the pinned source bytes."""
    validate(rows)
    if any(set(row)!={'file','alt'} for row in rows):raise MediaError('media: source pins require flattened files')
    sources = []
    root_fd = None
    try:
        root_fd = _open_dir(repo_dir)
        for row in rows:
            fd = _open_file(root_fd, row['file'])
            try:
                before = _identity(os.fstat(fd))
                digest = hashlib.sha256()
                while data := os.read(fd, 1024 * 1024):
                    digest.update(data)
                if _identity(os.fstat(fd)) != before:
                    raise MediaError('media: source_changed')
                os.lseek(fd, 0, os.SEEK_SET)
                source = Source(row['file'], row['alt'], digest.hexdigest(), before[2], fd, root_fd, before)
                source.verify()
                sources.append(source)
            except BaseException:
                os.close(fd)
                raise
        yield tuple(sources)
        for source in sources:
            source.verify()
    except OSError as exc:
        raise MediaError('media: source_unreadable') from exc
    finally:
        for source in sources:
            os.close(source.fd)
        if root_fd is not None:
            os.close(root_fd)


def fingerprint_component(manifest):
    """Opt-in source manifest, never mistaken for sanitized public bytes."""
    if manifest is None or manifest == []:
        return ''
    if not isinstance(manifest, list):
        raise MediaError('media: fingerprint array required')
    for row in manifest:
        if not isinstance(row, dict) or set(row) != {'file', 'alt', 'source_sha256', 'size'}:
            raise MediaError('media: invalid source fingerprint')
        if not isinstance(row['source_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['source_sha256']):
            raise MediaError('media: invalid source sha256')
        if type(row['size']) is not int or row['size'] < 0:
            raise MediaError('media: invalid source size')
    validate([{'file': row['file'], 'alt': row['alt']} for row in manifest])
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


STRUCTURED_KEYS = frozenset(('media', 'attachments', 'post_options', 'captions'))


def declared(fm):
    return any(fm.get(key) for key in STRUCTURED_KEYS)


def _pairs(pairs):
    out = {}
    for key,value in pairs:
        if key in out: raise MediaError('attachments: duplicate field')
        out[key] = value
    return out


def _value(raw):
    raw=raw.strip()
    if not raw: raise MediaError('attachments: value required')
    if raw[0] in '[{"' or raw in ('true','false','null') or re.fullmatch('-?[0-9]+',raw):
        try: return json.loads(raw,object_pairs_hook=_pairs,parse_constant=lambda x: (_ for _ in ()).throw(MediaError('attachments: nonfinite number')))
        except ValueError as exc: raise MediaError('attachments: invalid JSON value') from exc
    return _scalar(raw)


def parse_structured(lines,start,indent,key):
    """Strict two-space mapping/list subset, or explicit inline JSON.

    No implicit YAML typing, aliases, folded blocks or parser-dependent coercion.
    Media keeps its original file/alt grammar; additional declarations support
    nested typed JSON values (e.g. options: ["one", "two"]).
    """
    if key=='media': return parse_block(lines,start,indent)
    prefix=' '*indent+key+':'
    if not lines[start].startswith(prefix): raise MediaError('attachments: invalid indentation')
    rest=lines[start][len(prefix):].strip()
    if rest:
        value=_value(rest); end=start+1
    else:
        end=start+1
        while end<len(lines) and (not lines[end].strip() or len(lines[end])-len(lines[end].lstrip(' '))>indent): end+=1
        content=[line for line in lines[start+1:end] if line.strip()]
        if not content: raise MediaError('attachments: empty block; use [] or {}')
        if key=='post_options':
            value={}
            for line in content:
                if len(line)-len(line.lstrip(' '))!=indent+2: raise MediaError('attachments: invalid indentation')
                name,sep,raw=line.strip().partition(':')
                if not sep or name in value: raise MediaError('attachments: unknown or duplicate field')
                value[name]=_value(raw)
        else:
            value=[]; current=None
            for line in content:
                spaces=len(line)-len(line.lstrip(' '))
                if spaces==indent+2 and line[spaces:].startswith('- '):
                    current={};value.append(current); item=line[spaces+2:]
                elif spaces==indent+4 and current is not None: item=line[spaces:]
                else: raise MediaError('attachments: invalid indentation')
                name,sep,raw=item.partition(':')
                if not sep or name in current: raise MediaError('attachments: unknown or duplicate field')
                current[name]=_value(raw)
    if type(value) is not (dict if key=='post_options' else list): raise MediaError(f'{key}: wrong container type')
    return value,end


def _text(value,field,empty=False):
    if not isinstance(value,str) or (not empty and not value.strip()) or any(ord(x)<32 and x not in '\n\t' or ord(x)==127 for x in value):
        raise MediaError(f'attachments: invalid {field}')
    return value


def _https(value,field):
    from urllib.parse import urlsplit
    _text(value,field)
    if any(ord(x)<=32 or ord(x)==127 for x in value): raise MediaError(f'attachments: invalid {field}')
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password: raise MediaError(f'attachments: HTTPS {field} required')


def _strong_ref(value):
    """Lexicon strongRef syntax only; never fetch or rewrite referenced records."""
    import base64
    import re
    if type(value) is not dict or set(value)!={'uri','cid'}: raise MediaError('attachments: invalid strongRef')
    uri,cid=value['uri'],value['cid']
    if type(uri) is not str or len(uri)>8192 or not uri.startswith('at://'): raise MediaError('attachments: invalid strongRef uri')
    parts=uri[5:].split('/')
    if len(parts)!=3: raise MediaError('attachments: invalid strongRef uri')
    authority,nsid,rkey=parts
    label=r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    did=len(authority)<=2048 and re.fullmatch(r'did:[a-z]+:[a-zA-Z0-9._:%-]*[a-zA-Z0-9._-]',authority)
    handle=len(authority)<=253 and re.fullmatch(label+r'(?:\.'+label+r')*\.[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?',authority)
    domain,_,name=nsid.rpartition('.')
    nsid_ok=len(nsid)<=317 and len(domain)<=253 and re.fullmatch(r'[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.'+label+r')+',domain) and re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,62}',name)
    if not (did or handle) or not nsid_ok or rkey in ('.','..') or not re.fullmatch(r'[A-Za-z0-9._~:-]{1,512}',rkey): raise MediaError('attachments: invalid strongRef uri')
    if type(cid) is not str or not re.fullmatch(r'b[a-z2-7]{58}',cid): raise MediaError('attachments: invalid strongRef cid')
    raw=base64.b32decode(cid[1:].upper()+'='*((-len(cid[1:]))%8))
    if len(raw)!=36 or raw[:4]!=b'\x01\x71\x12\x20' or 'b'+base64.b32encode(raw).decode().lower().rstrip('=')!=cid: raise MediaError('attachments: invalid strongRef cid')


def validate_declarations(fm,medium):
    """No provider calls or guessed limits. Every accepted effect enters digest."""
    rows=fm.get('media',[]); validate(rows)
    if medium!='mastodon' and any('thumbnail_file' in row for row in rows):raise MediaError('media: parent thumbnail requires mastodon')
    attachments=fm.get('attachments',[]); options=fm.get('post_options',{}); captions=fm.get('captions',[])
    if type(attachments) is not list or type(options) is not dict or type(captions) is not list: raise MediaError('attachments: wrong container type')
    fields={
        'quote':({'type','uri'},{'cid'}),
        'link':({'type','url'},{'title','description','thumbnail_file','thumbnail_alt','associated_refs'}),
        'poll':({'type','options'},{'expires_in','multiple','hide_totals'}),
        'text':({'type','text'},{'link','styles'}),
        'gif':({'type','provider','id'},set()),
    }
    allowed={'threads':{'quote','link','poll','text','gif'},'mastodon':{'quote','link','poll'},'bluesky':{'quote','link'}}
    seen=set()
    for row in attachments:
        if not isinstance(row,dict) or not isinstance(row.get('type'),str) or row['type'] not in fields: raise MediaError('attachments: unknown type')
        kind=row['type'];required,optional=fields[kind]
        if not required<=set(row) or set(row)-required-optional: raise MediaError('attachments: unknown or missing field')
        if kind in seen:raise MediaError(f'duplicate_attachment_type: {medium}/{kind}')
        if kind not in allowed.get(medium,set()): raise MediaError(f'unsupported_attachment: {medium}/{kind}')
        seen.add(kind)
        if kind=='quote':
            _text(row['uri'],'uri')
            if 'cid' in row: _text(row['cid'],'cid')
            if medium=='bluesky' and ('cid' not in row or not row['uri'].startswith('at://')): raise MediaError('attachments: Bluesky quote needs at:// uri and cid')
        elif kind=='link':
            _https(row['url'],'url')
            for key in ('title','description'):
                if key in row: _text(row[key],key,empty=True)
            if medium=='bluesky' and not {'title','description'}<=set(row): raise MediaError('attachments: Bluesky card title/description required')
            if 'associated_refs' in row:
                if medium!='bluesky' or type(row['associated_refs']) is not list: raise MediaError('attachments: invalid associated_refs')
                for ref in row['associated_refs']: _strong_ref(ref)
            if ('thumbnail_file' in row)!=('thumbnail_alt' in row): raise MediaError('attachments: thumbnail file and alt required together')
            if 'thumbnail_file' in row: validate([{'file':row['thumbnail_file'],'alt':row['thumbnail_alt']}])
        elif kind=='poll':
            if type(row['options']) is not list or len(row['options'])<2: raise MediaError('attachments: poll needs options')
            for value in row['options']: _text(value,'poll option')
            if len(set(row['options']))!=len(row['options']): raise MediaError('attachments: duplicate poll option')
            if medium=='mastodon' and 'expires_in' not in row: raise MediaError('attachments: poll expires_in required')
            if 'expires_in' in row and (type(row['expires_in']) is not int or row['expires_in']<=0): raise MediaError('attachments: invalid poll expiry')
            for key in ('multiple','hide_totals'):
                if key in row and type(row[key]) is not bool: raise MediaError('attachments: invalid poll boolean')
        elif kind=='text':
            _text(row['text'],'text')
            if 'link' in row: _https(row['link'],'link')
            if 'styles' in row:
                if type(row['styles']) is not list: raise MediaError('attachments: styles array required')
                for style in row['styles']:
                    if not isinstance(style,dict) or set(style)!={'offset','length','styling_info'} or type(style['offset']) is not int or type(style['length']) is not int or style['offset']<0 or style['length']<=0 or not isinstance(style['styling_info'],list): raise MediaError('attachments: invalid style')
                    for item in style['styling_info']: _text(item,'style')
        elif kind=='gif':
            _text(row['provider'],'provider'); _text(row['id'],'id')
    if medium=='mastodon' and rows and 'poll' in seen: raise MediaError('attachments: poll/media mutually exclusive')
    if medium=='threads' and (('poll' in seen and seen&{'link','text'}) or (rows and seen&{'poll','gif','text'})): raise MediaError('attachments: incompatible Threads types')
    if medium=='bluesky' and rows and 'link' in seen: raise MediaError('attachments: media/card mutually exclusive')
    option_fields={
        'mastodon':{'visibility','language','sensitive','spoiler_text','quote_approval_policy','focus'},
        'bluesky':{'languages','labels','presentation','gallery','facets','tags'},
        'threads':{'text_spoiler','media_spoiler','ghost','reply_control','reply_approvals'},
    }
    if set(options)-option_fields.get(medium,set()): raise MediaError('post_options: unknown, duplicate legacy or unsupported field')
    booleans={'sensitive','gallery','text_spoiler','media_spoiler','ghost','reply_approvals'}
    for key,value in options.items():
        if key=='text_spoiler' and type(value) is list:
            for span in value:
                if type(span) is not dict or set(span)!={'offset','length'} or type(span['offset']) is not int or type(span['length']) is not int or span['offset']<0 or span['length']<=0:raise MediaError('post_options: invalid text_spoiler range')
        elif key in booleans:
            if type(value) is not bool: raise MediaError(f'post_options: boolean {key} required')
        elif key in ('languages','labels','tags'):
            if type(value) is not list: raise MediaError(f'post_options: array {key} required')
            for item in value:_text(item,key,empty=key in ('labels','tags'))
        elif key=='focus':
            if type(value) is not list or len(value)!=len(rows): raise MediaError('post_options: focus needs one coordinate pair per file')
            for pair in value:
                if type(pair) is not list or len(pair)!=2 or any(type(x) not in (int,float) or not -1<=x<=1 for x in pair):raise MediaError('post_options: invalid focus')
        elif key=='facets':
            if type(value) is not list:raise MediaError('post_options: facets array required')
            for facet in value:
                if not isinstance(facet,dict) or set(facet)!={'index','features'} or not isinstance(facet['index'],dict) or set(facet['index'])!={'byteStart','byteEnd'}:raise MediaError('post_options: invalid facet')
                begin,end=facet['index']['byteStart'],facet['index']['byteEnd']
                if type(begin) is not int or type(end) is not int or not 0<=begin<end or type(facet['features']) is not list:raise MediaError('post_options: invalid facet range')
                for feature in facet['features']:
                    if not isinstance(feature,dict):raise MediaError('post_options: invalid feature')
                    typ=feature.get('$type')
                    if type(typ) is not str:raise MediaError('post_options: unknown feature')
                    field={'app.bsky.richtext.facet#link':'uri','app.bsky.richtext.facet#mention':'did','app.bsky.richtext.facet#tag':'tag'}.get(typ)
                    if field is None or set(feature)!={'$type',field}:raise MediaError('post_options: unknown feature')
                    _text(feature[field],field,empty=field=='tag')
        else:_text(value,key,empty=key=='spoiler_text')
    for key,values in {'visibility':{'public','unlisted','private','direct'},'quote_approval_policy':{'public','followers','nobody'},'reply_control':{'everyone','accounts_you_follow','mentioned_only','parent_post_author_only','followers_only'}}.items():
        if key in options and options[key] not in values:raise MediaError(f'post_options: invalid {key}')
    seen_captions=set()
    for caption in captions:
        if medium!='bluesky' or not isinstance(caption,dict) or set(caption)!={'media_index','file','lang'}:raise MediaError('captions: unsupported or unknown field')
        index=caption['media_index']
        if type(index) is not int or not 1<=index<=len(rows):raise MediaError('captions: invalid media_index')
        validate_path(caption['file']); _text(caption['lang'],'lang')
        if not re.fullmatch('[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*',caption['lang']):raise MediaError('captions: invalid lang')
        key=(index,caption['lang'])
        if key in seen_captions:raise MediaError('captions: duplicate language')
        seen_captions.add(key)
    return attachments,options,captions


@dataclasses.dataclass(frozen=True)
class Prepared:
    manifest: dict
    source: Source = dataclasses.field(repr=False,compare=False)
    public_bytes: bytes | None = dataclasses.field(repr=False,compare=False)
    _public_fd: int = dataclasses.field(repr=False,compare=False)

    def verify(self):
        self.source.verify()

    def chunks(self,chunk_size=1024*1024):
        self.verify()
        if self.public_bytes is not None:
            for at in range(0,len(self.public_bytes),chunk_size):yield self.public_bytes[at:at+chunk_size]
        else:
            at=0
            while at<self.source.size:
                data=os.pread(self._public_fd,min(chunk_size,self.source.size-at),at)
                if not data:raise MediaError('media: source_changed')
                at+=len(data);yield data
        self.verify()


@contextlib.contextmanager
def prepare(repo_dir,fm,medium):
    """Shared exact bytes and metadata; pins remain live for the caller's use."""
    from . import mediaformats
    attachments,options,captions=validate_declarations(fm,medium)
    media_rows=fm.get('media',[])
    files=[{'file':row['file'],'alt':row['alt']} for row in media_rows]; roles=[('media',i+1,None) for i in range(len(files))]
    for index,row in enumerate(media_rows,1):
        if 'thumbnail_file' in row:
            files.append({'file':row['thumbnail_file'],'alt':row['thumbnail_alt']});roles.append(('thumbnail',index,index))
    for index,row in enumerate(attachments,1):
        if 'thumbnail_file' in row:
            files.append({'file':row['thumbnail_file'],'alt':row['thumbnail_alt']}); roles.append(('thumbnail',index,None))
    for index,row in enumerate(captions,1):
        files.append({'file':row['file'],'alt':row['lang']}); roles.append(('caption',index,None))
    prepared=[]
    try:
        with pin_sources(repo_dir,files) as sources, contextlib.ExitStack() as snapshots:
            for source,(role,index,parent) in zip(sources,roles):
                # Anonymous, private snapshot: no provider ever reads mutable repo bytes.
                snapshot=snapshots.enter_context(tempfile.TemporaryFile(mode='w+b'))
                snapshot_hash=hashlib.sha256(); offset=0
                while offset<source.size:
                    chunk=os.pread(source.fd,min(1024*1024,source.size-offset),offset)
                    if not chunk:raise MediaError('media: source_changed')
                    snapshot.write(chunk);snapshot_hash.update(chunk);offset+=len(chunk)
                snapshot.flush(); source.verify()
                if snapshot_hash.hexdigest()!=source.source_sha256:raise MediaError('media: source_changed')
                if role=='caption':
                    data=os.pread(snapshot.fileno(),source.size,0)
                    try:text=data.decode('utf-8-sig')
                    except UnicodeError as exc:raise MediaError('captions: invalid UTF-8') from exc
                    if not (text.startswith('WEBVTT\n') or text.startswith('WEBVTT\r\n') or text.startswith('WEBVTT ') or text.startswith('WEBVTT\t')) or '\x00' in text:raise MediaError('captions: invalid WebVTT')
                    info=mediaformats.Inspection('vtt','caption',None,None,None)
                else:
                    # Threads takes MP4/MOV only: WebM is named and refused here,
                    # before any structure parsing or provider call.
                    info=mediaformats.inspect(snapshot.fileno(),source.size,**({'allow_edit_lists':True,'allow_webm':False} if medium=='threads' else {}))
                    if role=='thumbnail' and info.kind!='image':raise MediaError('attachments: image thumbnail required')
                public=info.public_bytes
                if public is not None:
                    checked=getattr(mediaformats, info.format)(public)
                    if checked.public_bytes != public or (checked.width,checked.height,checked.duration,checked.orientation)!=(info.width,info.height,info.duration,info.orientation):
                        raise MediaError('media: public structure is not stable')
                sha=hashlib.sha256(public).hexdigest() if public is not None else source.source_sha256
                row={**source.fingerprint(),'role':role,'index':index,'public_sha256':sha,'public_size':len(public) if public is not None else source.size,'format':info.format,'kind':info.kind,'width':info.width,'height':info.height,'duration':info.duration,'orientation':info.orientation}
                if parent is not None:row['parent']=parent
                if info.metadata_notes:row['metadata_notes']=list(info.metadata_notes)
                source.verify();prepared.append(Prepared(row,source,public,snapshot.fileno()))
            media_files=[x.manifest for x in prepared if x.manifest['role']=='media']
            for item in prepared:
                if 'parent' in item.manifest and media_files[item.manifest['parent']-1]['kind'] not in ('audio','video'):raise MediaError('media: thumbnail parent must be audio or video')
            if medium=='bluesky' and len({x['kind'] for x in media_files})>1:raise MediaError('attachments: incompatible Bluesky media types')
            for caption in captions:
                if media_files[caption['media_index']-1]['kind']!='video':raise MediaError('captions: video required')
            manifest={'files':[x.manifest for x in prepared],'attachments':attachments,'post_options':options,'captions':captions}
            yield manifest,tuple(prepared)
            for item in prepared:item.verify()
    except mediaformats.FormatError as exc:
        reason=str(exc)
        if reason.startswith('unsupported_attachment: '): reason='unsupported_attachment: '+str(medium)+'/'+reason.split(': ',1)[1]
        raise MediaError(reason) from exc


def manifest_for(fm,cfg):
    if any(key in fm for key in STRUCTURED_KEYS):
        validate_declarations(fm, (cfg or {}).get('media'))
    if not declared(fm):return None
    root=(cfg or {}).get('repo_dir')
    if not root:raise MediaError('media: account repo_dir required')
    with prepare(root,fm,cfg.get('media')) as (manifest,_):return manifest


def display(manifest):
    if not manifest:return ''
    lines=[]
    for row in manifest['files']:
        shape=f"{row['width']}×{row['height']}" if row['width'] is not None else '寸法: 適用外'
        seconds=f" / {row['duration']}秒" if row['duration'] is not None else ' / 秒数: 未取得' if row['kind'] in ('audio','video') else ''
        lines.append(f"添付 {row['role']} {row['index']}: {row['file']} / {row['format']} / {shape}{seconds}")
        lines.append(f"size: source {row['size']} bytes / public {row['public_size']} bytes")
        label="lang" if row["role"]=="caption" else "alt"
        lines.append(f"{label}: {row['alt']}")
        if 'parent' in row:lines.append(f"parent: {row['parent']} / warning: thumbnail_alt is approval-only (no provider alt field)")
        for note in row.get('metadata_notes',[]):lines.append('warning: '+note)
        lines.append(f"source SHA256: {row['source_sha256']}")
        lines.append(f"public SHA256: {row['public_sha256']}")
    for key in ('attachments','post_options','captions'):
        if manifest[key]:lines.append(key+': '+json.dumps(manifest[key],ensure_ascii=False,sort_keys=True))
    return '\n'.join(lines)


def prepared_component(manifest):
    if manifest is None:return ''
    if not isinstance(manifest,dict) or set(manifest)!={'files','attachments','post_options','captions'}:raise MediaError('media: invalid prepared manifest')
    # This API accepts only the manifest produced by prepare, never binary bytes.
    if type(manifest['files']) is not list or type(manifest['attachments']) is not list or type(manifest['post_options']) is not dict or type(manifest['captions']) is not list:
        raise MediaError('media: invalid prepared container')
    thumbnail_parents=set()
    for row in manifest['files']:
        if not isinstance(row,dict) or set(row)-{'metadata_notes','parent'}!={'file','alt','source_sha256','size','role','index','public_sha256','public_size','format','kind','width','height','duration','orientation'}:raise MediaError('media: invalid prepared file')
        if 'parent' in row:
            parent=row['parent'];parents=[x for x in manifest['files'] if isinstance(x,dict) and x.get('role')=='media' and x.get('index')==parent]
            if type(parent)is not int or parent<1 or row['role']!='thumbnail' or row['kind']!='image' or row['index']!=parent or len(parents)!=1 or parents[0].get('kind') not in ('audio','video') or parent in thumbnail_parents:raise MediaError('media: invalid thumbnail parent')
            thumbnail_parents.add(parent)
        if 'metadata_notes' in row and (type(row['metadata_notes']) is not list or any(note not in ('non_location_metadata_retained','embedded_cover_retained') for note in row['metadata_notes'])):raise MediaError('media: invalid metadata notes')
        fingerprint_component([{k:row[k] for k in ('file','alt','source_sha256','size')}])
        if not isinstance(row['public_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',row['public_sha256']) or type(row['public_size']) is not int or row['public_size']<0:raise MediaError('media: invalid public fingerprint')
    try:
        return json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    except (ValueError,TypeError) as exc:
        raise MediaError('media: invalid prepared value') from exc
