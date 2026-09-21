"""Explicit post metadata, fixed Lexicons 7870a59c and Unicode 17 clusters.

Syntax validation never resolves a mention, URI, language registry or labeler.
Automatic legacy facets remain unchanged unless explicit facets are present.
"""
from __future__ import annotations
import json
import ipaddress
import re
from . import graphemes,media

FIELDS=frozenset(('languages','labels','facets','tags'))
_GRANDFATHERED=frozenset('en-gb-oed i-ami i-bnn i-default i-enochian i-hak i-klingon i-lux i-mingo i-navajo i-pwn i-tao i-tay i-tsu sgn-be-fr sgn-be-nl sgn-ch-de art-lojban cel-gaulish no-bok no-nyn zh-guoyu zh-hakka zh-min zh-min-nan zh-xiang'.split())


def require(value,reason):
    if not value:raise media.MediaError(reason)


def language(value):
    """RFC5646 section2.1 syntax plus duplicate variant/singleton rejection."""
    if type(value) is not str or re.fullmatch('[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*',value) is None:return False
    words=value.lower().split('-')
    if value.lower() in _GRANDFATHERED:return True
    if any(len(w)>8 for w in words):return False
    if words[0]=='x':return len(words)>1
    if not words[0].isalpha() or not 2<=len(words[0])<=8:return False
    i=1
    if len(words[0])<=3:
        for _ in range(3):
            if i<len(words) and len(words[i])==3 and words[i].isalpha():i+=1
            else:break
    if i<len(words) and len(words[i])==4 and words[i].isalpha():i+=1
    if i<len(words) and ((len(words[i])==2 and words[i].isalpha()) or (len(words[i])==3 and words[i].isdigit())):i+=1
    variants=set();singletons=set()
    while i<len(words) and (len(words[i])>=5 or len(words[i])==4 and words[i][0].isdigit()):
        if words[i] in variants:return False
        variants.add(words[i]);i+=1
    while i<len(words) and len(words[i])==1 and words[i]!='x':
        if words[i] in singletons:return False
        singletons.add(words[i]);i+=1;start=i
        while i<len(words) and len(words[i])>=2:i+=1
        if i==start:return False
    if i<len(words) and words[i]=='x':return i+1<len(words)
    return i==len(words)


def _text(value,reason,*,limit=None,clusters=None):
    require(type(value) is str,reason)
    try:raw=value.encode('utf-8')
    except UnicodeError:raise media.MediaError(reason) from None
    if limit is not None:require(len(raw)<=limit,reason)
    if clusters is not None:require(graphemes.count(value,stop_after=clusters)<=clusters,reason)


def uri(value):
    """RFC3986 absolute URI syntax, not scheme-specific endpoint policy.

    No decoding, normalization, lookup or fetch. Lexicon uri limits ASCII
    syntax to 8192 bytes. Empty authority/port and IPvFuture are generic URI
    forms; endpoint restrictions must not be imported into publication data.
    """
    if type(value) is not str or not value.isascii() or len(value)>8192:return False
    match=re.fullmatch(r'[A-Za-z][A-Za-z0-9+.-]*:([^?#]*)(?:\?([^#]*))?(?:#([^#]*))?',value)
    if match is None:return False
    hierarchy,query,fragment=match.groups()
    atom=r"(?:[A-Za-z0-9._~!$&'()*+,;=-]|%[0-9A-Fa-f]{2})"
    pchar=rf'(?:{atom}|[:@])'
    for part in (query,fragment):
        if part is not None and re.fullmatch(rf'(?:{pchar}|[/?])*',part) is None:return False
    if hierarchy.startswith('//'):
        authority,separator,tail=hierarchy[2:].partition('/')
        path='/'+tail if separator else ''
        # Lexicon uri explicitly includes AT URIs. Their DID authority has
        # unescaped colons by design (AT URI Generic URI Compliance), not a
        # host:port pair. This is not the restricted at-uri strong-ref type.
        if value.startswith('at://') and authority.startswith('did:'):
            return (len(authority)<=2048 and re.fullmatch(r'did:[a-z]+:[a-zA-Z0-9._:%-]*[a-zA-Z0-9._-]',authority) is not None
                    and re.fullmatch(rf'(?:{atom}|:)*',authority) is not None
                    and re.fullmatch(rf'(?:{pchar}|/)*',path) is not None)
        if authority.count('@')>1:return False
        if '@' in authority:
            userinfo,authority=authority.split('@',1)
            if re.fullmatch(rf'(?:{atom}|:)*',userinfo) is None:return False
        if authority.startswith('['):
            close=authority.find(']')
            if close<0:return False
            host=authority[1:close];port=authority[close+1:]
            if port and re.fullmatch(r':[0-9]*',port) is None:return False
            if re.fullmatch(r"[vV][0-9A-Fa-f]+\.[A-Za-z0-9._~!$&'()*+,;=:-]+",host) is None:
                if '%' in host:return False  # zone IDs are not RFC3986 IP-literals
                try:ipaddress.IPv6Address(host)
                except ValueError:return False
        else:
            host,colon,port=authority.partition(':')
            if colon and re.fullmatch(r'[0-9]*',port) is None:return False
            if re.fullmatch(rf'{atom}*',host) is None:return False
    else:path=hierarchy
    return re.fullmatch(rf'(?:{pchar}|/)*',path) is not None


def validate(options):
    for key,cap in (('languages',3),('labels',10),('tags',8)):
        if key not in options:continue
        values=options[key];require(type(values) is list and len(values)<=cap,'metadata_limit_exceeded: '+key)
        for value in values:
            if key=='languages':require(language(value),'metadata_invalid: language')
            elif key=='labels':_text(value,'metadata_limit_exceeded: label_bytes',limit=128)
            else:_text(value,'metadata_limit_exceeded: tag',limit=640,clusters=64)
    if 'facets' not in options:return
    require(type(options['facets']) is list,'metadata_invalid: facets')
    for facet in options['facets']:
        require(type(facet) is dict and set(facet)=={'index','features'} and type(facet['index']) is dict and set(facet['index'])=={'byteStart','byteEnd'},'metadata_invalid: facet')
        begin,end=facet['index']['byteStart'],facet['index']['byteEnd']
        require(type(begin) is int and type(end) is int and 0<=begin<end and type(facet['features']) is list,'metadata_invalid: facet_range')
        for feature in facet['features']:
            require(type(feature) is dict,'metadata_invalid: feature')
            require(type(feature.get('$type')) is str,'metadata_invalid: feature')
            key={'app.bsky.richtext.facet#link':'uri','app.bsky.richtext.facet#mention':'did','app.bsky.richtext.facet#tag':'tag'}.get(feature.get('$type'))
            require(key is not None and set(feature)=={'$type',key},'metadata_invalid: feature')
            value=feature[key];_text(value,'metadata_invalid: '+key)
            if key=='tag':_text(value,'metadata_limit_exceeded: facet_tag',limit=640,clusters=64)
            elif key=='did':require(len(value)<=2048 and re.fullmatch(r'did:[a-z]+:[a-zA-Z0-9._:%-]*[a-zA-Z0-9._-]',value),'metadata_invalid: mention_did')
            else:
                # URI-valued publication data, not an endpoint opened by thth.
                require(uri(value),'metadata_invalid: link_uri')


def merged_facets(text,automatic,explicit):
    boundaries={0};offset=0
    for char in text:offset+=len(char.encode('utf-8'));boundaries.add(offset)
    # Equality is semantic JSON equality. Feature order remains as declared.
    seen=set();rows=[]
    for facet in [*automatic,*explicit]:
        begin,end=facet['index']['byteStart'],facet['index']['byteEnd']
        require(begin in boundaries and end in boundaries and begin<end,'metadata_invalid: facet_utf8_range')
        features=[];feature_seen=set()
        for feature in facet['features']:
            feature_key=json.dumps(feature,sort_keys=True,ensure_ascii=False,separators=(',',':'))
            if feature_key not in feature_seen:features.append(feature);feature_seen.add(feature_key)
        facet={'index':dict(facet['index']),'features':features}
        signature=json.dumps(facet,sort_keys=True,ensure_ascii=False,separators=(',',':'))
        if signature in seen:continue
        seen.add(signature);rows.append(facet)
    rows.sort(key=lambda f:(f['index']['byteStart'],f['index']['byteEnd']))
    previous_end=0;previous_range=None
    for facet in rows:
        current=(facet['index']['byteStart'],facet['index']['byteEnd'])
        require(current!=previous_range,'metadata_conflict: same_range')
        require(current[0]>=previous_end,'metadata_conflict: overlap')
        previous_end=current[1];previous_range=current
    return rows


def apply(record,options,*,has_embed,hashtags_allowed=True):
    if not FIELDS.intersection(options):return
    validate(options)
    require(has_embed or bool(record['text'].strip()),'status_text_required: bluesky')
    require(hashtags_allowed or not (options.get('tags') or any(f.get('$type')=='app.bsky.richtext.facet#tag' for row in options.get('facets',[]) for f in row['features'])),'hashtags_disabled_by_ledger')
    if 'languages' in options:record['langs']=list(options['languages'])
    if 'labels' in options:record['labels']={'$type':'com.atproto.label.defs#selfLabels','values':[{'val':v} for v in options['labels']]}
    if 'tags' in options:record['tags']=list(options['tags'])
    if 'facets' in options:record['facets']=merged_facets(record['text'],record.get('facets',[]),options['facets'])


def lint(cfg,fm,text):
    """Use the same effective hashtags text and automatic facets as publish."""
    options=fm.get('post_options',{})
    if not FIELDS.intersection(options):return
    validate(options)
    require(type(text) is str,'metadata_text_unavailable')
    from . import tags
    from .adapters.bluesky import build_facets
    allowed=bool(cfg.get('hashtags',True));topic=fm.get('topic')
    effective=tags.prepared('bluesky',text,topic,hashtags=allowed)
    record={'text':effective,'facets':build_facets(effective,topic=topic if allowed else None,include_tags=allowed)}
    apply(record,options,has_embed=bool(fm.get('media') or fm.get('attachments')),hashtags_allowed=allowed)
