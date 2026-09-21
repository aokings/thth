"""Byte-preserving MPEG audio envelope inspection, not a PCM decoder.

ID3v2.3/2.4 known scalar frames and inspected JPEG/PNG covers; unknown
extensions refuse. MPEG LayerIII headers/side information delimit audio
samples, which are never searched as if they were metadata.
"""
import codecs
import hashlib
from fractions import Fraction
from .audioformats import Reader
from .mediaformats import Inspection,FormatError,require,inspect_embedded_cover


_TEXT23=set(b'TALB TBPM TCOM TCON TCOP TDAT TDLY TENC TEXT TFLT TIME TIT1 TIT2 TIT3 TKEY TLAN TLEN TMED TOAL TOFN TOLY TOPE TORY TOWN TPE1 TPE2 TPE3 TPE4 TPOS TPUB TRCK TRDA TRSN TRSO TSIZ TSRC TSSE TYER'.split())
_TEXT24=(_TEXT23-set(b'TDAT TIME TORY TRDA TSIZ TYER'.split()))|set(b'TDEN TDOR TDRC TDRL TDTG TIPL TMCL TMOO TPRO TSOA TSOP TSOT TSST'.split())
_URLS=set(b'WCOM WCOP WOAF WOAR WOAS WORS WPAY WPUB'.split())


def is_adts(head):
    """ADTS AAC shares the 12-bit 0xFFF syncword with MPEG audio.

    The two-bit layer field is `00` for ADTS and is *reserved* — never valid —
    in MPEG-1/2/2.5 audio, so this test names the container without parsing it.
    """
    return len(head)>=2 and head[0]==0xff and head[1]&0xf6==0xf0


def _sync(raw):
    require(len(raw)==4 and all(x<128 for x in raw));value=0
    for x in raw:value=(value<<7)|x
    return value


def _key(value):
    value=''.join(c for c in value.casefold() if c.isalnum())
    require(not any(s in value for s in ('location','gps','latitude','longitude','geotag')) and value not in ('gps','latitude','longitude','altitude'),'location_metadata_present: id3 field')
    require(value not in ('xmp','exif','iptc','coverart','coverartmime','metadata block picture'.replace(' ','')),'location_metadata_unverifiable: id3 extension')


def _string(r,a,b,encoding,*,terminated=False,capture=False):
    """Decode in bounded chunks; UTF16 terminators must align to code units."""
    require(encoding in (0,1,2,3));width=2 if encoding in (1,2) else 1
    codec={0:'latin1',1:'utf-16',2:'utf-16-be',3:'utf-8'}[encoding]
    if encoding==1:
        require(b-a>=2 and r.read(a,2) in (b'\xff\xfe',b'\xfe\xff'))
    decoder=codecs.getincrementaldecoder(codec)();values=[];at=a;done=False
    try:
        while at<b:
            n=min(65536,b-at);raw=r.read(at,n)
            cut=None
            if terminated:
                for i in range(0,len(raw)-width+1,width):
                    if raw[i:i+width]==bytes(width):cut=i;break
            if cut is not None:
                text=decoder.decode(raw[:cut],final=True);at+=cut+width;done=True
                if capture:values.append(text)
                break
            text=decoder.decode(raw)
            if capture:values.append(text)
            at+=n
        if not done:
            text=decoder.decode(b'',final=True)
            if capture:values.append(text)
        require(not terminated or done)
    except UnicodeError:raise FormatError('invalid_attachment_structure: id3 text') from None
    return at,''.join(values)


def _id3(r,start,limit,notes):
    header=r.read(start,10);require(header[:3]==b'ID3');version,revision,flags=header[3:6]
    require(version in (3,4) and revision==0,'location_metadata_unverifiable: id3 version')
    require(flags&~(0xe0 if version==3 else 0xf0)==0)
    require(flags&0xe0==0,'location_metadata_unverifiable: id3 tag transform')
    length=_sync(header[6:10]);a=start+10;b=a+length;footer=bool(flags&16)
    require(b+(10 if footer else 0)<=limit)
    if footer:require(r.read(b,10)==b'3DI'+header[3:])
    seen=set();icons=set();frames=0
    while a<b:
        if r.read(a,1)==b'\0':
            require(not footer);r.zero(a,b);a=b;break
        require(a+10<=b);h=r.read(a,10);ident=h[:4]
        require(all(65<=x<=90 or 48<=x<=57 for x in ident))
        n=_sync(h[4:8]) if version==4 else int.from_bytes(h[4:8],'big')
        require(n>0 and a+10+n<=b)
        require(h[8]&~(0xe0 if version==3 else 0x70)==0)
        require(h[9]==0,'location_metadata_unverifiable: id3 frame transform')
        x=a+10;y=x+n;frames+=1;identity=(ident,b'')
        if ident in (_TEXT23 if version==3 else _TEXT24):
            enc=r.read(x,1)[0];require(enc in ((0,1) if version==3 else (0,1,2,3)))
            _string(r,x+1,y,enc)
        elif ident in _URLS:
            _string(r,x,y,0)
            # WOAR/WCOM can occur with different URLs; never fetch them.
            if ident in (b'WOAR',b'WCOM'):identity=(ident,_hash(r,x,y))
        elif ident in (b'TXXX',b'WXXX',b'COMM',b'USLT',b'APIC'):
            enc=r.read(x,1)[0];require(enc in ((0,1) if version==3 else (0,1,2,3)));x+=1
            language=b''
            if ident in (b'COMM',b'USLT'):
                require(x+3<=y);language=r.read(x,3);require(all(65<=c<=90 or 97<=c<=122 for c in language));x+=3
            if ident==b'APIC':
                x,mime=_string(r,x,y,0,terminated=True,capture=True)
                require(x<y);picture_type=r.read(x,1)[0];x+=1
                require(picture_type<=20,'location_metadata_unverifiable: id3 picture type')
                x,description=_string(r,x,y,enc,terminated=True,capture=True)
                require(mime in ('image/jpeg','image/png'),'location_metadata_unverifiable: embedded_cover_remove_cover')
                raw=r.read(x,y-x);require(raw.startswith(b'\xff\xd8') if mime=='image/jpeg' else raw.startswith(b'\x89PNG\r\n\x1a\n'))
                inspect_embedded_cover(raw)
                if picture_type in (1,2):require(picture_type not in icons);icons.add(picture_type)
                if picture_type==1:
                    from .mediaformats import png
                    require(mime=='image/png');image=png(raw);require((image.width,image.height)==(32,32))
                notes.add('embedded_cover_retained');identity=(ident,description)
            else:
                x,description=_string(r,x,y,enc,terminated=True,capture=True);_key(description)
                _string(r,x,y,0 if ident==b'WXXX' else enc)
                identity=(ident,language,description)
        elif ident==b'PCNT':require(n>=4)
        else:raise FormatError('location_metadata_unverifiable: id3 frame')
        require(identity not in seen);seen.add(identity);a=y
    require(a==b and frames>0)
    notes.add('non_location_metadata_retained')
    return b+(10 if footer else 0)


def _hash(r,a,b):
    h=hashlib.sha256()
    for at in range(a,b,65536):h.update(r.read(at,min(65536,b-at)))
    return h.digest()


def _bounds(r,notes):
    a=0;b=r.size
    while a+3<=b and r.read(a,3)==b'ID3':a=_id3(r,a,b,notes)
    if b-a>=128 and r.read(b-128,3)==b'TAG':
        # ID3v1/v1.1 fixed-width scalar fields, no object/cover payload.
        b-=128;notes.add('non_location_metadata_retained')
    if b-a>=10 and r.read(b-10,3)==b'3DI':
        foot=r.read(b-10,10);require(foot[3:5]==b'\x04\0' and foot[5]&16)
        start=b-20-_sync(foot[6:]);require(start>=a)
        require(_id3(r,start,b,notes)==b);b=start
    require(a<b)
    return a,b


class _Bits:
    def __init__(self,raw):self.raw,self.at=raw,0
    def take(self,n):
        require(self.at+n<=len(self.raw)*8);value=0
        for _ in range(n):value=(value<<1)|((self.raw[self.at//8]>>(7-self.at%8))&1);self.at+=1
        return value


def _side(raw,lower,channels):
    bits=_Bits(raw);back=bits.take(8 if lower else 9)
    bits.take(channels if lower else (5 if channels==1 else 3))
    if not lower:bits.take(4*channels)
    total=0
    for _ in range((1 if lower else 2)*channels):
        total+=bits.take(12);require(bits.take(9)<=288);bits.take(8);bits.take(9 if lower else 4)
        if bits.take(1):
            require(bits.take(2)!=0);bits.take(1)
            tables=[bits.take(5) for _ in range(2)];bits.take(9)
        else:
            tables=[bits.take(5) for _ in range(3)];bits.take(4);bits.take(3)
        require(not any(t in (4,14) for t in tables))
        if not lower:bits.take(1)
        bits.take(2)
    require(bits.at==len(raw)*8)
    return back,total


def _crc16(raw):
    value=0xffff
    for byte in raw:
        value^=byte<<8
        for _ in range(8):value=((value<<1)^(0x8005 if value&0x8000 else 0))&0xffff
    return value


def mp3(fd,size):
    r=Reader(fd,size);notes=set();a,b=_bounds(r,notes);duration=Fraction(0);previous=0;used_until=0;count=0
    while a<b:
        require(a+4<=b);raw=r.read(a,4);h=int.from_bytes(raw,'big')
        if raw[:3] in (b'TAG',b'APE') or raw==b'APET':raise FormatError('location_metadata_unverifiable: mpeg audio trailer')
        require(h>>21==0x7ff)
        version=(h>>19)&3;layer=(h>>17)&3;index=(h>>12)&15;rate_index=(h>>10)&3
        require(version!=1 and layer!=0 and index!=15 and rate_index!=3 and h&3!=2)
        require(layer==1,'unsupported_attachment_structure: mpeg layer')
        require(index!=0,'unsupported_attachment_structure: mp3 free bitrate')
        lower=version!=3;rate=(44100,48000,32000)[rate_index]>>(0 if version==3 else 1 if version==2 else 2)
        bitrate=((0,8,16,24,32,40,48,56,64,80,96,112,128,144,160) if lower else (0,32,40,48,56,64,80,96,112,128,160,192,224,256,320))[index]
        length=bitrate*(72000 if lower else 144000)//rate+((h>>9)&1)
        crc=not bool(h&(1<<16));channels=1 if (h>>6)&3==3 else 2
        side_size=(9 if channels==1 else 17) if lower else (17 if channels==1 else 32)
        prefix=4+(2 if crc else 0);require(length>=prefix+side_size and a+length<=b)
        side=r.read(a+prefix,side_size)
        if crc:require(_crc16(raw[2:]+side)==int.from_bytes(r.read(a+4,2),'big'))
        back,bits=_side(side,lower,channels);main_size=length-prefix-side_size
        require(back<=previous and (previous-back)*8>=used_until and bits<=(back+main_size)*8)
        used_until=(previous-back)*8+bits;previous+=main_size
        duration+=Fraction(576 if lower else 1152,rate);count+=1;a+=length
    require(a==b and count>0)
    return Inspection('mp3','audio',None,None,float(duration),metadata_notes=tuple(sorted(notes)))
