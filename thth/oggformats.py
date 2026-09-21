"""Ogg/Opus envelope inspection, without PCM decoding or byte rewriting."""
import base64
import binascii
import bisect
import struct
import tempfile
from fractions import Fraction
from .audioformats import Reader
from .mediaformats import Inspection,FormatError,require
from .flacformats import _text,_picture,_crc_table


_OGG_CRC=_crc_table(32,0x04c11db7)


def _crc(raw):
    value=0
    for byte in raw:value=((value<<8)^_OGG_CRC[(value>>24)^byte])&0xffffffff
    return value


class Packet:
    """Virtual packet spans: no in-memory concatenation of a large tag packet."""
    def __init__(self,r):self.r,self.spans,self.ends,self.size=r,[],[],0
    def append(self,at,n):
        if not n:return
        if self.spans and self.spans[-1][0]+self.spans[-1][1]==at:
            old,length=self.spans[-1];self.spans[-1]=(old,length+n);self.ends[-1]+=n
        else:self.spans.append((at,n));self.ends.append(self.size+n)
        self.size+=n
    def read(self,at,n):
        require(0<=at<=self.size-n);out=[]
        while n:
            i=bisect.bisect_right(self.ends,at);start=0 if i==0 else self.ends[i-1];offset=at-start
            pos,length=self.spans[i];take=min(n,length-offset);out.append(self.r.read(pos+offset,take));at+=take;n-=take
        return b''.join(out)
    def zero(self,a,b):
        for at in range(a,b,65536):require(not any(self.read(at,min(65536,b-at))),'location_metadata_unverifiable: ogg padding')


def _comments(p,notes):
    require(p.read(0,8)==b'OpusTags');a=8
    def field():
        nonlocal a
        require(a+4<=p.size);n=int.from_bytes(p.read(a,4),'little');a+=4;require(a+n<=p.size);x=a;a+=n;_text(p,x,a);return x,a
    field();require(a+4<=p.size);count=int.from_bytes(p.read(a,4),'little');a+=4;require(count<=(p.size-a)//4)
    icons=set();gains=set()
    for _ in range(count):
        x,y=field();key=bytearray();value_at=None
        for at in range(x,y,65536):
            raw=p.read(at,min(65536,y-at));n=raw.find(b'=');key.extend(raw if n<0 else raw[:n])
            if n>=0:value_at=at+n+1;break
        require(value_at is not None and all(32<=c<=126 and c!=61 for c in key))
        key=bytes(key).lower()
        require(not any(s in key for s in (b'location',b'gpslatitude',b'gpslongitude',b'gpsaltitude',b'geotag')) and key not in (b'gps',b'latitude',b'longitude',b'altitude'),'location_metadata_present: ogg field')
        if key in (b'r128_track_gain',b'r128_album_gain'):
            require(key not in gains and 0<y-value_at<=6);gains.add(key);value=p.read(value_at,y-value_at)
            digits=value[1:] if value[:1] in (b'+',b'-') else value
            require(digits and all(48<=v<=57 for v in digits) and -32768<=int(value)<=32767)
        if key==b'metadata_block_picture':
            require((y-value_at)%4==0)
            with tempfile.TemporaryFile() as f:
                for at in range(value_at,y,65536):
                    raw=p.read(at,min(65536,y-at));require(b'=' not in raw if at+len(raw)<y else True)
                    try:decoded=base64.b64decode(raw,validate=True)
                    except (ValueError,binascii.Error):raise FormatError('invalid_attachment_structure: ogg cover base64') from None
                    f.write(decoded)
                size=f.tell();f.flush();_picture(Reader(f.fileno(),size),0,size,icons)
            notes.add('embedded_cover_retained')
        else:require(key not in (b'coverart',b'coverartmime',b'xmp',b'exif',b'iptc'),'location_metadata_unverifiable: ogg comment extension')
    # RFC7845 permits an unspecified binary extension here. Only inspected
    # zero padding is accepted; the extension is not silently called private-free.
    p.zero(a,p.size);notes.add('non_location_metadata_retained')


def _header(p):
    require(p.size>=19 and p.read(0,8)==b'OpusHead','unsupported_attachment_structure: ogg codec')
    raw=p.read(8,11);version,channels,preskip,rate,gain,mapping=struct.unpack('<BBHIhB',raw)
    require(0<version<16,'location_metadata_unverifiable: opus version');require(channels>0)
    if mapping==0:
        require(channels<=2);require(p.size==19,'location_metadata_unverifiable: opus header extension');streams,coupled=1,channels-1
    else:
        require(mapping in (1,255),'location_metadata_unverifiable: opus mapping')
        require(mapping!=1 or channels<=8);require(p.size>=21+channels);require(p.size==21+channels,'location_metadata_unverifiable: opus header extension')
        streams,coupled=p.read(19,2);require(streams>0 and coupled<=streams and streams+coupled<=255)
        require(all(x==255 or x<streams+coupled for x in p.read(21,channels)))
    return streams,preskip


def _opus_packet(p,start,*,self_delimited):
    a=start;end=p.size
    def take():
        nonlocal a
        require(a<end);v=p.read(a,1)[0];a+=1;return v
    def length():
        v=take();return v if v<252 else v+4*take()
    toc=take();config=toc>>3;code=toc&3
    samples=((480,960,1920,2880)[config%4] if config<12 else (480,960)[config%2] if config<16 else (120,240,480,960)[config%4])
    padding=0;lengths=[];vbr=False
    if code==0:count=1
    elif code==1:count=2
    elif code==2:count=2;vbr=True;lengths=[length()]
    else:
        control=take();count=control&63;require(count>0);vbr=bool(control&128)
        if control&64:
            while True:
                v=take();padding+=254 if v==255 else v;require(padding<=end-a)
                if v<255:break
        if vbr:lengths=[length() for _ in range(count-1)]
    require(samples*count<=5760)
    if self_delimited:
        last=length()
        if vbr:lengths.append(last)
        else:lengths=[last]*count
        finish=a+sum(lengths)+padding;require(finish<=end)
    else:
        available=end-a-padding;require(available>=0)
        if vbr:lengths.append(available-sum(lengths))
        else:require(available%count==0);lengths=[available//count]*count
        finish=end
    require(len(lengths)==count and all(0<=n<=1275 for n in lengths))
    p.zero(finish-padding,finish)
    return finish,samples*count


def _audio(p,streams):
    a=0;samples=None
    for index in range(streams):
        a,current=_opus_packet(p,a,self_delimited=index<streams-1)
        require(samples is None or samples==current);samples=current
    require(a==p.size);return samples


def ogg(fd,size):
    r=Reader(fd,size);at=0;active={};completed=set();notes=set();total=Fraction(0);group=[];unknown=False
    while at<size:
        head=r.read(at,27);require(head[:4]==b'OggS' and head[4]==0 and head[5]&~7==0)
        flags=head[5];granule=int.from_bytes(head[6:14],'little',signed=True);serial=int.from_bytes(head[14:18],'little');seq=int.from_bytes(head[18:22],'little')
        laces=r.read(at+27,head[26]);payload=at+27+len(laces);end=payload+sum(laces);require(end<=size)
        raw=head[:22]+bytes(4)+head[26:]+laces+r.read(payload,end-payload)
        require(_crc(raw)==int.from_bytes(head[22:26],'little'))
        if flags&2:
            require(all(item['stage']==1 and item['seq']==1 for item in active.values()))
            require(serial not in active and serial not in completed and seq==0 and not flags&1)
            active[serial]={'seq':0,'packet':Packet(r),'continued':False,'stage':0,'samples':0,'last':None,'offset':None,'group_serial':serial}
        require(serial in active);s=active[serial];require(seq==s['seq'] and bool(flags&1)==s['continued']);s['seq']=(seq+1)&0xffffffff
        before_stage=s['stage'];page_samples=0;finished=0
        for index,n in enumerate(laces):
            s['packet'].append(payload,n);payload+=n;s['continued']=n==255
            if n==255:continue
            p=s['packet'];s['packet']=Packet(r);finished+=1
            if s['stage']==0:
                require(flags&2 and index==len(laces)-1 and not flags&4 and granule==0)
                s['streams'],s['preskip']=_header(p);s['stage']=1
            elif s['stage']==1:
                require(index==len(laces)-1 and not flags&4 and granule==0);_comments(p,notes);s['stage']=2
            else:page_samples+=_audio(p,s['streams'])
        if before_stage==0:require(s['stage']==1)
        if not finished:require(granule==-1)
        elif before_stage>=2:
            require(granule>=0)
            if s['offset'] is None:
                require(granule>=page_samples or flags&4)
                s['offset']=max(0,granule-page_samples)
            else:
                expected=s['last']+page_samples
                require(s['last']<=granule<=expected if flags&4 else granule==expected)
            s['samples']+=page_samples;s['last']=granule
        if flags&4:
            require(s['stage']==2 and not s['continued'] and s['last'] is not None)
            kept=s['last']-s['offset']-s['preskip'];require(kept>=0)
            group.append(Fraction(kept,48000));del active[serial];completed.add(serial)
            if not active:
                if len(group)>1:unknown=True # Multiplexed timelines are not guessed from per-stream lengths.
                else:total+=group[0]
                group=[]
        at=end
    require(at==size and not active and completed)
    return Inspection('ogg','audio',None,None,None if unknown else float(total),metadata_notes=tuple(sorted(notes)))
