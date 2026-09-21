"""Known WebM video container fields, no decoding or byte transformation.

Pinned WebM Project codec-private TLVs and CELLAR schema; encoded frame bodies
are bounded spans, like BMFF mdat, not scanned as arbitrary metadata text.
"""
from fractions import Fraction
from .ebmlaudio import WebM,SCHEMA,parse
from .mediaformats import Inspection,require


class VideoWebM(WebM):
    def __init__(self,r):
        super().__init__(r);self.context={};self.video_order=[]
        self.schema={**SCHEMA,'track':{**SCHEMA['track'],0xe0:'video'},
          'video':{k:'u' for k in (0x9a,0x9d,0x53b8,0x53c0,0xb0,0xba,0x54aa,0x54bb,0x54cc,0x54dd,0x54b0,0x54ba,0x54b2,0x54b3)},
          'colour':{k:'u' for k in range(0x55b1,0x55be)},
          'mastering':{k:'f' for k in range(0x55d1,0x55db)}}
        self.schema['video'][0x55b0]='colour';self.schema['colour'][0x55d0]='mastering'
    def fields(self,a,b,kind,depth=0):
        out=super().fields(a,b,kind,depth);get=self.one
        if kind=='video':
            width,height=get(out,0xb0),get(out,0xba);require(type(width)is int and width>0 and type(height)is int and height>0)
            require(get(out,0x54cc,0)+get(out,0x54dd,0)<width and get(out,0x54aa,0)+get(out,0x54bb,0)<height)
            require(get(out,0x9a,0) in (0,1,2) and get(out,0x9d,2) in (0,1,2,6,9,14))
            require(get(out,0x53b8,0) in range(15) and get(out,0x53c0,0) in (0,1))
            require(get(out,0x54b2,0) in (0,1,2,3,4) and get(out,0x54b3,0) in (0,1,2))
            for field in (0x54b0,0x54ba):
                if field in out:require(get(out,field)>0)
        if kind=='mastering':
            for field,values in out.items():require(0<=values[0]<=1 if field<=0x55d8 else values[0]>=0)
        if kind=='colour':
            for field in (0x55b7,0x55b8):require(get(out,field,0) in (0,1,2))
            require(get(out,0x55b9,0) in (0,1,2,3))
        if kind=='cluster':
            timestamp=get(out,0xe7);require(timestamp is not None)
            for start,end in out.get(0xa3,[]):self.context[(start,end,True)]=(timestamp,None)
            for group in out.get(0xa0,[]):
                start,end=get(group,0xa1);self.context[(start,end,False)]=(timestamp,get(group,0x9b))
        return out
    def track(self,row):
        get=self.one
        if get(row,0x83)!=1:
            require(0xe0 not in row);return super().track(row)
        number,uid=get(row,0xd7),get(row,0x73c5)
        require(type(number)is int and number>0 and type(uid)is int and uid>0 and number not in self.tracks and uid not in self.uid)
        codec=get(row,0x86);require(codec in ('V_VP8','V_VP9'),'unsupported_attachment_structure: webm video codec')
        private=get(row,0x63a2)
        if private:
            a,b=private
            if codec=='V_VP8':require(a==b,'location_metadata_unverifiable: webm VP8 private')
            else:
                values={1:set(range(4)),2:{10,11,20,21,30,31,40,41,50,51,52,60,61,62},3:{8,10,12},4:set(range(4))}
                while a<b:
                    require(a+2<=b);key,length=self.r.read(a,2);a+=2
                    require(key in values,'location_metadata_unverifiable: webm VP9 private')
                    require(length==1 and a+length<=b);require(self.r.read(a,1)[0] in values[key]);a+=length
        video=get(row,0xe0);require(video is not None and 0xe1 not in row)
        self.tracks[number]={'codec':codec,'seen':0,'lacing':get(row,0x9c,1),'width':get(video,0xb0),'height':get(video,0xba),'default_duration':get(row,0x23e383),'timing':[]}
        self.uid.add(uid);self.video_order.append(number)
    def consume_packet(self,state,packet):
        if state['codec'].startswith('V_'):require(packet.size>0)
        else:super().consume_packet(state,packet)
    def block(self,a,b,*,simple):
        number,n,_=self.vint(a,b);require(number in self.tracks);state=self.tracks[number];before=state['seen']
        # The inherited reader checks full block/lacing bounds before use.
        super().block(a,b,simple=simple)
        if state['codec'].startswith('V_'):
            relative=int.from_bytes(self.r.read(a+n,2),'big',signed=True);cluster,duration=self.context[(a,b,simple)]
            state['timing'].append((cluster+relative,state['seen']-before,duration))


def webm(fd,size):
    w,_,_,duration=parse(fd,size,VideoWebM)
    video=w.tracks[w.video_order[0]] if w.video_order else None
    return Inspection('webm','video' if video else 'audio',video['width'] if video else None,video['height'] if video else None,duration,metadata_notes=tuple(sorted(w.notes)))


def _bounded_rate(value,limit=30000):
    """Nearest rational with numerator and denominator bounded by limit.

    Continued-fraction convergents bracket the answer; select the closer bound.
    This models the container demuxer's bounded rational declaration without
    invoking a codec, copying decoder code, or using platform float rounding.
    """
    if max(value.numerator,value.denominator)<=limit:return value
    n,d=value.numerator,value.denominator;p0,q0,p1,q1=0,1,1,0
    while d:
        k=n//d;p2,q2=p0+k*p1,q0+k*q1
        if p2>limit or q2>limit:
            steps=[]
            if p1:steps.append((limit-p0)//p1)
            if q1:steps.append((limit-q0)//q1)
            k=min(steps);candidates=[]
            if q0+k*q1:candidates.append(Fraction(p0+k*p1,q0+k*q1))
            if q1:candidates.append(Fraction(p1,q1))
            return min(candidates,key=lambda x:(abs(x-value),x.denominator))
        p0,q0,p1,q1=p1,q1,p2,q2;n,d=d,n-k*d
    return Fraction(p1,q1)


def metrics(fd,size):
    w,_,scale,_=parse(fd,size,VideoWebM);require(w.video_order,'video_rate_unverifiable')
    row=w.tracks[w.video_order[0]]
    # Matroska's declared DefaultDuration supplies its demuxer average rate.
    # Without it, use the full bounded timeline only when the final duration is
    # known. Neither branch derives a codec-level r_frame_rate or peak VFR rate.
    if row['default_duration'] is not None:
        rate=_bounded_rate(Fraction(1000000000,row['default_duration']));require(rate>0,'video_rate_unverifiable')
        return row['width'],row['height'],rate
    timing=sorted(row['timing']);require(timing,'video_rate_unverifiable')
    starts=[tick*scale for tick,_,_ in timing];require(all(b>a for a,b in zip(starts,starts[1:])),'video_rate_unverifiable')
    last=timing[-1];duration=last[2]*scale if last[2] is not None else (row['default_duration'] or 0)*last[1]
    require(duration>0,'video_rate_unverifiable');elapsed=starts[-1]-starts[0]+duration;count=sum(n for _,n,_ in timing)
    return row['width'],row['height'],Fraction(count*1000000000,elapsed)
