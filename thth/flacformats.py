"""RFC9639 native FLAC structure/privacy inspection; no PCM decoding or rewriting."""
import codecs
from .audioformats import Reader
from .mediaformats import Inspection,FormatError,require,inspect_embedded_cover


def _text(r,a,b):
    decoder=codecs.getincrementaldecoder('utf-8')()
    try:
        for at in range(a,b,65536):decoder.decode(r.read(at,min(65536,b-at)))
        decoder.decode(b'',final=True)
    except UnicodeDecodeError:raise FormatError('invalid_attachment_structure: flac text') from None


def _picture(r,a,b,icons):
    def integer():
        nonlocal a
        require(a+4<=b);value=int.from_bytes(r.read(a,4),'big');a+=4;return value
    def field():
        nonlocal a
        n=integer();require(a+n<=b);start=a;a+=n;return start,a
    kind=integer();require(kind<=20,'location_metadata_unverifiable: flac picture type')
    if kind in (1,2):require(kind not in icons);icons.add(kind)
    x,y=field();mime=r.read(x,y-x);require(all(32<=v<=126 for v in mime))
    require(mime in (b'image/jpeg',b'image/png'),'location_metadata_unverifiable: embedded_cover_remove_cover')
    x,y=field();_text(r,x,y)
    for _ in range(4):integer() # Informational geometry cannot replace image inspection.
    x,y=field();require(a==b);data=r.read(x,y-x)
    require(data.startswith(b'\xff\xd8') if mime==b'image/jpeg' else data.startswith(b'\x89PNG\r\n\x1a\n'))
    inspect_embedded_cover(data)
    if kind==1:
        from .mediaformats import png
        info=png(data);require(mime==b'image/png' and (info.width,info.height)==(32,32))


def _comments(r,a,b):
    def field():
        nonlocal a
        require(a+4<=b);n=int.from_bytes(r.read(a,4),'little');a+=4
        require(a+n<=b);start=a;a+=n;_text(r,start,a);return start,a
    field();require(a+4<=b);count=int.from_bytes(r.read(a,4),'little');a+=4
    require(count<=(b-a)//4)
    for _ in range(count):
        x,y=field();key=bytearray();found=False
        # Key sizes are bounded by the 24-bit metadata block, not an invented tag cap.
        for pos in range(x,y,65536):
            part=r.read(pos,min(65536,y-pos));index=part.find(b'=')
            key.extend(part if index<0 else part[:index])
            if index>=0:found=True;break
        require(found and all(32<=v<=126 and v!=61 for v in key))
        key=bytes(key).lower()
        require(not any(word in key for word in (b'location',b'gpslatitude',b'gpslongitude',b'gpsaltitude',b'geotag')) and key not in (b'gps',b'latitude',b'longitude',b'altitude'),'location_metadata_present')
        # Encoded covers/XML are not human-readable scalar comments. Native
        # PICTURE blocks have the separate inspected path above; never fetch URI.
        require(key not in (b'metadata_block_picture',b'coverart',b'coverartmime',b'xmp',b'exif',b'iptc'),'location_metadata_unverifiable: flac comment extension')
    require(a==b)


def _crc_table(bits,poly):
    values=[];mask=(1<<bits)-1
    for n in range(256):
        value=n<<(bits-8)
        for _ in range(8):value=((value<<1)^poly if value&(1<<(bits-1)) else value<<1)&mask
        values.append(value)
    return tuple(values)


_CRC8=_crc_table(8,0x07);_CRC16=_crc_table(16,0x8005)


def _crc(r,a,b,bits):
    value=0;table=_CRC8 if bits==8 else _CRC16;mask=(1<<bits)-1
    for at in range(a,b,65536):
        for byte in r.read(at,min(65536,b-at)):value=((value<<8)^table[(value>>(bits-8))^byte])&mask
    return value


class _Bits:
    def __init__(self,r,at):self.r,self.pos,self.cache_at,self.cache=r,at*8,-1,b''
    def byte(self,at):
        if not self.cache_at<=at<self.cache_at+len(self.cache):
            self.cache_at=at;self.cache=self.r.read(at,min(65536,self.r.size-at))
        require(bool(self.cache));return self.cache[at-self.cache_at]
    def take(self,n):
        require(0<=n<=64 and self.pos+n<=self.r.size*8);value=0
        while n:
            width=min(n,8-self.pos%8);shift=8-self.pos%8-width
            value=(value<<width)|((self.byte(self.pos//8)>>shift)&((1<<width)-1));self.pos+=width;n-=width
        return value
    def skip(self,n):require(0<=n<=self.r.size*8-self.pos);self.pos+=n
    def unary(self,limit):
        count=0
        while self.pos<self.r.size*8:
            if self.pos%8==0 and self.byte(self.pos//8)==0:self.pos+=8;count+=8
            elif self.take(1):return count
            else:count+=1
            require(count<=limit)
        raise FormatError('invalid_attachment_structure: flac unary')


def _number(bits):
    first=bits.take(8)
    if first<128:return first
    length=0
    while length<8 and first&(128>>length):length+=1
    require(2<=length<=7)
    value=first&((1<<(7-length))-1)
    for _ in range(length-1):
        byte=bits.take(8);require(byte&192==128);value=(value<<6)|(byte&63)
    require(value>=[0,0,128,2048,65536,2097152,67108864,2147483648][length])
    return value


def _residual(bits,block,order):
    method=bits.take(2);require(method in (0,1));width=4+method
    partitions=1<<bits.take(4);require(block%partitions==0 and block//partitions>order)
    for index in range(partitions):
        count=block//partitions-(order if index==0 else 0);param=bits.take(width)
        if param==(1<<width)-1:bits.skip(bits.take(5)*count)
        else:
            for _ in range(count):
                quotient=bits.unary((0xfffffffe)>>param);remainder=bits.take(param)
                require((quotient<<param)|remainder<=0xfffffffe)


def _subframe(bits,block,depth):
    require(bits.take(1)==0);kind=bits.take(6);wasted=bits.take(1)
    if wasted:depth-=bits.unary(depth-1)+1
    require(depth>0)
    if kind==0:bits.skip(depth)
    elif kind==1:bits.skip(block*depth)
    else:
        require(8<=kind<=12 or 32<=kind<=63)
        order=kind-8 if kind<32 else kind-31;require(order<=block);bits.skip(order*depth)
        if kind>=32:
            precision=bits.take(4)+1;require(precision<=15);require(bits.take(5)<16);bits.skip(order*precision)
        _residual(bits,block,order)


def _frames(r,start,stream):
    low,high,minframe,maxframe,rate,channels,depth,total=stream
    at=start;frames=0;samples=0;strategy=None;fixed_block=None;previous_block=None;duration=0.;rates_match=True
    bits=_Bits(r,start)
    while at<r.size:
        if previous_block is not None:require(previous_block>=low)
        require(bits.pos==at*8 and bits.take(15)==0x7ffc);variable=bits.take(1)
        if strategy is None:strategy=variable
        require(strategy==variable)
        block_code=bits.take(4);rate_code=bits.take(4);channel_code=bits.take(4);depth_code=bits.take(3);require(bits.take(1)==0)
        require(block_code>0 and rate_code!=15 and channel_code<=10 and depth_code!=3)
        number=_number(bits);require(number==(samples if variable else frames) and number<(1<<(36 if variable else 31)))
        if block_code==1:block=192
        elif block_code<=5:block=144*(1<<block_code)
        elif block_code in (6,7):block=bits.take(8 if block_code==6 else 16)+1
        else:block=1<<block_code
        if rate_code==0:frame_rate=rate
        elif rate_code<=11:frame_rate=(0,88200,176400,192000,8000,16000,22050,24000,32000,44100,48000,96000)[rate_code]
        else:frame_rate=bits.take(8 if rate_code==12 else 16)*(1000 if rate_code==12 else 10 if rate_code==14 else 1)
        require(frame_rate>0,'unsupported_attachment_structure: flac non_audio')
        bits_per=(depth,8,12,0,16,20,24,32)[depth_code]
        require(_crc(r,at,bits.pos//8,8)==bits.take(8))
        require(block<=high)
        if not variable:
            if fixed_block is None:fixed_block=block
            else:require(previous_block==fixed_block and block<=fixed_block)
        count=channel_code+1 if channel_code<=7 else 2
        for index in range(count):_subframe(bits,block,bits_per+(1 if (channel_code in (8,10) and index==1) or (channel_code==9 and index==0) else 0))
        if bits.pos%8:require(bits.take(8-bits.pos%8)==0)
        end=bits.pos//8;require(_crc(r,at,end,16)==bits.take(16))
        n=bits.pos//8-at;require((minframe==0 or n>=minframe) and (maxframe==0 or n<=maxframe))
        frames+=1;samples+=block;duration+=block/frame_rate;rates_match&=frame_rate==rate;previous_block=block;at=bits.pos//8
    require(frames>0 and (total==0 or total==samples))
    # A zero total is a declared unknown observation, never silently certified
    # by replacing it with an inferred value. Changing-rate streams are parsed.
    return None if total==0 else total/rate if rates_match else duration


def flac(fd,size):
    r=Reader(fd,size);require(r.read(0,4)==b'fLaC');at=4;stream=None;seen=set();notes=set();icons=set()
    while True:
        head=r.read(at,4);last=bool(head[0]&128);kind=head[0]&127;n=int.from_bytes(head[1:],'big');a=at+4;b=a+n;require(b<=size)
        require(stream is not None or kind==0)
        if kind==0:
            require(stream is None and n==34);value=r.read(a,34)
            low=int.from_bytes(value[:2],'big');high=int.from_bytes(value[2:4],'big');packed=int.from_bytes(value[10:18],'big')
            rate=packed>>44;channels=((packed>>41)&7)+1;depth=((packed>>36)&31)+1;total=packed&((1<<36)-1)
            require(16<=low<=high<=65535 and 4<=depth<=32)
            require(rate>0,'unsupported_attachment_structure: flac non_audio')
            stream=(low,high,int.from_bytes(value[4:7],'big'),int.from_bytes(value[7:10],'big'),rate,channels,depth,total)
        elif kind==1:r.zero(a,b)
        elif kind==3:
            require(kind not in seen and n%18==0);prior=-1;placeholder=False
            for pos in range(a,b,18):
                point=int.from_bytes(r.read(pos,8),'big')
                if point==(1<<64)-1:placeholder=True
                else:require(not placeholder and point>prior);prior=point
        elif kind==4:
            require(kind not in seen);_comments(r,a,b);notes.add('non_location_metadata_retained')
        elif kind==6:
            _picture(r,a,b,icons);notes.update(('non_location_metadata_retained','embedded_cover_retained'))
        else:raise FormatError('location_metadata_unverifiable: flac metadata block')
        seen.add(kind);at=b
        if last:break
    duration=_frames(r,at,stream)
    return Inspection('flac','audio',None,None,duration,metadata_notes=tuple(sorted(notes)))
