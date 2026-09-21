"""Bounded WebM audio/container metadata inspection; no PCM decoding."""
import math
import struct
import zlib
from .audioformats import Reader
from .mediaformats import Inspection,require,inspect_embedded_cover
from .oggformats import Packet,_header,_comments,_audio
from . import vorbisformats


# RFC8794 and pinned Matroska/WebM schema. Unsupported extensions are not skipped.
CRC=0xbf;VOID=0xec
SCHEMA={
 'ebml':{0x4286:'u',0x42f7:'u',0x42f2:'u',0x42f3:'u',0x4282:'s',0x4287:'u',0x4285:'u'},
 'segment':{0x1549a966:'info',0x1654ae6b:'tracks',0x1f43b675:'cluster',0x1c53bb6b:'cues',0x114d9b74:'seek',0x1254c367:'tags',0x1941a469:'attachments'},
 'info':{0x2ad7b1:'u',0x4489:'f',0x4d80:'t',0x5741:'t',0x7ba9:'t',0x4461:'date',0x73a4:'uid',0x3cb923:'uid',0x3eb923:'uid',0x7384:'t',0x3c83ab:'t',0x3e83bb:'t',0x4444:'uid'},
 'tracks':{0xae:'track'},
 'track':{0xd7:'u',0x73c5:'u',0x83:'u',0xb9:'u',0x88:'u',0x55aa:'u',0x9c:'u',0x23e383:'u',0x536e:'t',0x22b59c:'s',0x22b59d:'s',0x86:'s',0x63a2:'private',0x258688:'t',0xe1:'audio',0x56aa:'u',0x56bb:'u',0xaa:'u'},
 'audio':{0xb5:'f',0x78b5:'f',0x9f:'u',0x6264:'u'},
 'cluster':{0xe7:'u',0xa3:'simple',0xa0:'group',0xa7:'u',0xab:'u'},
 'group':{0xa1:'block',0x9b:'u',0xfa:'u',0xfb:'i',0x75a2:'i'},
 'seek':{0x4dbb:'seekentry'},'seekentry':{0x53ab:'seekid',0x53ac:'u'},
 'cues':{0xbb:'cue'},'cue':{0xb3:'u',0xb7:'cuepos'},'cuepos':{0xf7:'u',0xf1:'u',0xf0:'u',0xb2:'u',0x5378:'u',0xea:'u'},
 'tags':{0x7373:'tag'},'tag':{0x63c0:'targets',0x67c8:'tagvalue'},
 'targets':{0x68ca:'u',0x63ca:'t',0x63c5:'u',0x63c9:'u',0x63c4:'u',0x63c6:'u'},
 'tagvalue':{0x45a3:'t',0x447a:'s',0x447b:'s',0x4484:'u',0x4487:'t',0x67c8:'tagvalue'},
 'attachments':{0x61a7:'file'},'file':{0x467e:'t',0x466e:'t',0x4660:'s',0x465c:'cover',0x46ae:'u'},
}
REPEATED={('segment',0x1254c367),('segment',0x1f43b675),('segment',0x114d9b74),('info',0x4444),('tracks',0xae),('cluster',0xa3),('cluster',0xa0),('group',0xfb),('seek',0x4dbb),('cues',0xbb),('cue',0xb7),('tags',0x7373),('tag',0x67c8),('tagvalue',0x67c8),('targets',0x63c5),('targets',0x63c9),('targets',0x63c4),('targets',0x63c6),('attachments',0x61a7)}


class WebM:
    def __init__(self,r):self.r=r;self.notes=set();self.tracks={};self.uid=set();self.file_uid=set();self.blocks=0;self.block_order=[];self.maxsize=8
    def vint(self,at,end,*,ident=False):
        require(at<end);first=self.r.read(at,1)[0];require(first!=0);n=9-first.bit_length();require(n<=(4 if ident else 8) and at+n<=end)
        raw=self.r.read(at,n);value=int.from_bytes(raw,'big');data=value&((1<<(7*n))-1)
        if ident:require(data not in (0,(1<<(7*n))-1))
        return value if ident else data,n,data==(1<<(7*n))-1
    def elements(self,a,b):
        while a<b:
            start=a;ident,n,_=self.vint(a,b,ident=True);a+=n;size,n,unknown=self.vint(a,b);require(n<=self.maxsize);a+=n
            if unknown:require(ident==0x18538067,'location_metadata_unverifiable: webm unknown-sized element');size=b-a
            require(size<=b-a);yield ident,a,a+size,start;a+=size
        require(a==b)
    def zero(self,a,b):
        for at in range(a,b,65536):require(not any(self.r.read(at,min(65536,b-at))),'location_metadata_unverifiable: webm padding')
    def text(self,a,b,ascii=False,capture=False):
        import codecs
        decoder=codecs.getincrementaldecoder('ascii' if ascii else 'utf-8')();value='';tail='';long=False;terminated=False;present=False;location=False
        def consume(chunk):
            nonlocal value,tail,long,terminated,present,location
            for c in chunk:
                if c=='\0':terminated=True;continue
                require(not terminated);present=True
                if ascii:require(32<=ord(c)<=126)
                if capture=='key':
                    if not c.isalnum():continue
                    c=c.lower();tail=(tail+c)[-32:]
                    if any(x in tail for x in ('location','gpslatitude','gpslongitude','gpsaltitude','geotag')):location=True
                if capture and not long:
                    value+=c
                    if len(value)>64:long=True;value=''
        try:
            for at in range(a,b,65536):consume(decoder.decode(self.r.read(at,min(65536,b-at))))
            consume(decoder.decode(b'',final=True))
        except UnicodeError:require(False)
        if capture=='key' and location:return 'location'
        if long:return '__other__'
        return value if value or not present else '__other__'
    def scalar(self,kind,a,b,capture=False):
        n=b-a
        if kind in ('u','i'):require(n<=8);return int.from_bytes(self.r.read(a,n),'big',signed=kind=='i')
        if kind=='f':
            require(n in (0,4,8));value=0.0 if n==0 else struct.unpack('>f' if n==4 else '>d',self.r.read(a,n))[0];require(math.isfinite(value));return value
        if kind=='date':require(n in (0,8));return None
        if kind=='uid':require(n==16);return None
        if kind=='seekid':
            _,length,_=self.vint(a,b,ident=True);require(length==n);return None
        if kind in ('s','t'):return self.text(a,b,ascii=kind=='s',capture=capture)
        return (a,b)
    def fields(self,a,b,kind,depth=0):
        require(depth<64,'location_metadata_unverifiable: webm nesting');out={};checksum=None
        for ident,x,y,start in self.elements(a,b):
            if ident==VOID:self.zero(x,y);continue
            if ident==CRC:require(checksum is None and y-x==4);checksum=(start,y,self.r.read(x,4));continue
            typ=SCHEMA[kind].get(ident);require(typ is not None,'location_metadata_unverifiable: webm extension')
            require(ident not in out or (kind,ident) in REPEATED)
            if typ in SCHEMA:value=self.fields(x,y,typ,depth+1)
            else:value=self.scalar(typ,x,y,capture='key' if ident==0x45a3 else ident in (0x4282,0x86,0x4660))
            if typ in ('simple','block'):self.block_order.append((x,y,typ=='simple'))
            out.setdefault(ident,[]).append(value)
        if checksum:
            start,end,expected=checksum;crc=0
            for x,y in ((a,start),(end,b)):
                for at in range(x,y,65536):crc=zlib.crc32(self.r.read(at,min(65536,y-at)),crc)
            require(crc.to_bytes(4,'little')==expected)
        if kind=='tagvalue':
            name=self.one(out,0x45a3);require(isinstance(name,str) and name)
            require(self.one(out,0x4484,1) in (0,1))
            key=''.join(c.lower() for c in name if c.isalnum())
            require(not any(x in key for x in ('location','gpslatitude','gpslongitude','gpsaltitude','geotag')) and key not in ('gps','latitude','longitude','altitude'),'location_metadata_present: webm tag')
            require(key not in ('xmp','exif','iptc','coverart','metadatablockpicture'),'location_metadata_unverifiable: webm tag value');self.notes.add('non_location_metadata_retained')
        if kind=='file':
            uid=self.one(out,0x46ae);require(type(uid)is int and uid>0 and uid not in self.file_uid and 0x466e in out);self.file_uid.add(uid)
            mime=self.one(out,0x4660);cover=self.one(out,0x465c);require(mime in ('image/jpeg','image/png') and cover,'location_metadata_unverifiable: embedded_cover_remove_cover')
            data=self.r.read(cover[0],cover[1]-cover[0]);require(data.startswith(b'\xff\xd8') if mime=='image/jpeg' else data.startswith(b'\x89PNG\r\n\x1a\n'))
            inspect_embedded_cover(data);self.notes.add('embedded_cover_retained')
        if kind in ('info','track','file') and any(SCHEMA[kind].get(x) in ('s','t','date') for x in out):self.notes.add('non_location_metadata_retained')
        required={'info':(0x4d80,0x5741),'seek':(0x4dbb,),'seekentry':(0x53ab,0x53ac),'tracks':(0xae,),'cues':(0xbb,),'cue':(0xb3,0xb7),'cuepos':(0xf7,0xf1),'group':(0xa1,),'tags':(0x7373,),'tag':(0x63c0,0x67c8),'attachments':(0x61a7,)}
        require(all(x in out for x in required.get(kind,())))
        if kind=='track':
            require(all(self.one(out,x,1) in (0,1) for x in (0xb9,0x88,0x9c,0xaa)))
            require(self.one(out,0x55aa,0) in (0,1))
            if 0x23e383 in out:require(self.one(out,0x23e383)>0)
        if kind=='targets':require(self.one(out,0x68ca,50)>0)
        if kind=='audio' and 0x6264 in out:require(self.one(out,0x6264)>0)
        return out
    @staticmethod
    def one(fields,ident,default=None):return fields.get(ident,[default])[0]
    def packet(self,a,b):p=Packet(self.r);p.append(a,b-a);return p
    def track(self,row):
        number=self.one(row,0xd7);uid=self.one(row,0x73c5);require(type(number)is int and number>0 and type(uid)is int and uid>0 and number not in self.tracks and uid not in self.uid)
        require(self.one(row,0x83)==2,'unsupported_attachment_structure: webm non-audio track')
        codec=self.one(row,0x86);cp=self.one(row,0x63a2);require(cp and codec in ('A_OPUS','A_VORBIS'),'unsupported_attachment_structure: webm audio codec')
        audio=self.one(row,0xe1,{});channels=self.one(audio,0x9f,1);require(type(channels)is int and channels>0)
        rate=self.one(audio,0xb5,8000);require(rate>0);output=self.one(audio,0x78b5,rate);require(output>0)
        p=self.packet(*cp);state={'codec':codec,'seen':0,'lacing':self.one(row,0x9c,1)}
        if codec=='A_OPUS':state['streams'],_= _header(p);require(p.read(9,1)[0]==channels)
        else:
            require(p.read(0,1)==b'\x02');at=1;lengths=[]
            for _ in range(2):
                n=0
                while True:
                    value=p.read(at,1)[0];at+=1;n+=value
                    if value<255:break
                lengths.append(n)
            require(sum(lengths)<=p.size-at);lengths.append(p.size-at-sum(lengths));headers=[]
            for length in lengths:headers.append(self.packet(cp[0]+at,cp[0]+at+length));at+=length
            state['vorbis']=vorbisformats.header(headers[0]);require(state['vorbis']['channels']==channels)
            _comments(headers[1],self.notes,vorbis=True);vorbisformats.setup(headers[2],state['vorbis'])
        self.uid.add(uid);self.tracks[number]=state
    def block(self,a,b,*,simple):
        number,n,_=self.vint(a,b);require(number in self.tracks);a+=n;require(a+3<=b);flags=self.r.read(a+2,1)[0];a+=3
        require(flags&0x70==0 if simple else flags&0xf1==0);lace=(flags>>1)&3;require(not lace or self.tracks[number]['lacing']);lengths=[]
        if lace:
            require(a<b);count=self.r.read(a,1)[0]+1;a+=1
            if lace==1:
                for _ in range(count-1):
                    length=0
                    while True:
                        require(a<b);value=self.r.read(a,1)[0];a+=1;length+=value
                        if value<255:break
                    lengths.append(length)
            elif lace==2:require((b-a)%count==0);lengths=[(b-a)//count]*(count-1)
            else:
                if count>1:
                    value,n,_=self.vint(a,b);a+=n;lengths=[value]
                    for _ in range(count-2):
                        value,n,_=self.vint(a,b);a+=n;lengths.append(lengths[-1]+value-((1<<(7*n-1))-1));require(lengths[-1]>=0)
        lengths.append(b-a-sum(lengths));require(all(n>=0 for n in lengths));state=self.tracks[number]
        for n in lengths:
            packet=self.packet(a,a+n)
            if state['codec']=='A_OPUS':_audio(packet,state['streams'])
            else:vorbisformats.audio(packet,state['vorbis'])
            a+=n;state['seen']+=1;self.blocks+=1
        require(a==b)


def webm(fd,size):
    w=WebM(Reader(fd,size));roots=[]
    for row in w.elements(0,size):
        if row[0]==VOID:w.zero(row[1],row[2])
        else:roots.append(row)
    require(len(roots)==2 and roots[0][0]==0x1a45dfa3 and roots[1][0]==0x18538067)
    header=w.fields(roots[0][1],roots[0][2],'ebml');get=w.one
    require(get(header,0x4282)=='webm','unsupported_attachment_structure: ebml doctype')
    require(get(header,0x4286,1)==1 and get(header,0x42f7,1)==1 and get(header,0x42f2,4)==4 and 1<=get(header,0x42f3,8)<=8 and 1<=get(header,0x4285,1)<=4 and get(header,0x4287,1)>=get(header,0x4285,1),'unsupported_attachment_structure: webm version')
    w.maxsize=get(header,0x42f3,8)
    for _ in w.elements(0,size):pass
    # Container metadata is parsed before any codec packet; typed binary leaves
    # keep spans, so sample payload is not accumulated in memory.
    segment=w.fields(roots[1][1],roots[1][2],'segment');info=get(segment,0x1549a966);tracks=get(segment,0x1654ae6b);require(info is not None and tracks is not None)
    for row in tracks.get(0xae,[]):w.track(row)
    require(w.tracks)
    for cluster in segment.get(0x1f43b675,[]):
        require(get(cluster,0xe7) is not None)
    for a,b,simple in w.block_order:w.block(a,b,simple=simple)
    require(w.blocks>0 and all(t['seen']>0 for t in w.tracks.values()))
    scale=get(info,0x2ad7b1,1000000);require(scale>0);duration=get(info,0x4489)
    if duration is not None:require(duration>0);duration=duration*scale/1e9;require(math.isfinite(duration))
    return Inspection('webm','audio',None,None,duration,metadata_notes=tuple(sorted(w.notes)))
