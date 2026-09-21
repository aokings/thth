"""Vorbis I setup/envelope inspection; no floor/residue or PCM synthesis."""
from .mediaformats import require


class Bits:
    def __init__(self,p,at=0):self.p,self.pos,self.at,self.cache=p,at*8,-1,b''
    def take(self,n):
        require(0<=n<=32 and self.pos+n<=self.p.size*8);value=0;shift=0
        while n:
            at=self.pos//8
            if not self.at<=at<self.at+len(self.cache):self.at=at;self.cache=self.p.read(at,min(4096,self.p.size-at))
            width=min(n,8-self.pos%8);value|=((self.cache[at-self.at]>>(self.pos%8))&((1<<width)-1))<<shift
            self.pos+=width;shift+=width;n-=width
        return value
    def skip(self,n):require(0<=n<=self.p.size*8-self.pos);self.pos+=n
    def finish(self):
        if self.pos%8:require(self.take(8-self.pos%8)==0,'location_metadata_unverifiable: vorbis header padding')
        self.p.zero(self.pos//8,self.p.size)


def header(p):
    require(p.size>=30 and p.read(0,7)==b'\x01vorbis')
    require(p.size==30,'location_metadata_unverifiable: vorbis header extension')
    b=Bits(p,7);require(b.take(32)==0,'unsupported_attachment_structure: vorbis version')
    channels=b.take(8);rate=b.take(32);require(channels>0 and rate>0)
    b.skip(96);small=b.take(4);large=b.take(4);require(6<=small<=large<=13 and b.take(1)==1);b.finish()
    return {'channels':channels,'rate':rate,'blocks':(1<<small,1<<large),'previous':None,'packets':0}


def _power_at_most(base,exponent,limit):
    value=1
    while exponent:
        if exponent&1:
            value*=base
            if value>limit:return False
        exponent>>=1
        if exponent:base=min(limit+1,base*base)
    return True


def _book(b):
    require(b.take(24)==0x564342);dimensions=b.take(16);entries=b.take(24);require(dimensions>0 and entries>0)
    lengths=[0]*33
    if b.take(1):
        length=b.take(5)+1;done=0
        while done<entries:
            require(length<=32);number=b.take((entries-done).bit_length());require(number<=entries-done)
            lengths[length]+=number;done+=number;length+=1
    else:
        sparse=b.take(1)
        for _ in range(entries):
            if not sparse or b.take(1):lengths[b.take(5)+1]+=1
    slots=1
    for n in lengths[1:]:slots=slots*2-n;require(slots>=0)
    # Xiph's 20150226 erratum explicitly preserves a single used entry.
    require(slots==0 or sum(lengths)==1 and lengths[1]==1)
    lookup=b.take(4);require(lookup<=2,'unsupported_attachment_structure: vorbis lookup')
    if lookup:
        b.skip(64);width=b.take(4)+1;b.take(1)
        if lookup==1:
            low,high=1,entries
            while low<high:
                mid=(low+high+1)//2
                if _power_at_most(mid,dimensions,entries):low=mid
                else:high=mid-1
            count=low
        else:count=entries*dimensions
        b.skip(count*width)
    return dimensions,entries,lookup


def setup(p,state):
    require(p.read(0,7)==b'\x05vorbis');b=Bits(p,7);books=[_book(b) for _ in range(b.take(8)+1)]
    for _ in range(b.take(6)+1):require(b.take(16)==0)
    floors=b.take(6)+1
    for _ in range(floors):
        kind=b.take(16);require(kind in (0,1),'unsupported_attachment_structure: vorbis floor')
        if kind==0:
            b.skip(8+16+16+6+8)
            for _ in range(b.take(4)+1):require(b.take(8)<len(books))
        else:
            classes=[b.take(4) for _ in range(b.take(5))];dimensions=[]
            for _ in range(max(classes,default=-1)+1):
                dimensions.append(b.take(3)+1);sub=b.take(2)
                if sub:require(b.take(8)<len(books))
                for _ in range(1<<sub):require(b.take(8)-1<len(books))
            b.take(2);width=b.take(4);values={0,1<<width};count=2
            for cls in classes:
                for _ in range(dimensions[cls]):
                    x=b.take(width);require(x not in values);values.add(x);count+=1;require(count<=65)
    residues=b.take(6)+1
    for _ in range(residues):
        require(b.take(16)<=2,'unsupported_attachment_structure: vorbis residue')
        begin=b.take(24);end=b.take(24);b.take(24);classes=b.take(6)+1;book=b.take(8)
        require(begin<=end and book<len(books));require(_power_at_most(classes,books[book][0],books[book][1]))
        cascade=[]
        for _ in range(classes):
            low=b.take(3);high=b.take(5) if b.take(1) else 0;cascade.append(low|(high<<3))
        for mask in cascade:
            for bit in range(8):
                if mask&(1<<bit):
                    index=b.take(8);require(index<len(books) and books[index][2]!=0)
    mappings=b.take(6)+1
    for _ in range(mappings):
        require(b.take(16)==0,'unsupported_attachment_structure: vorbis mapping')
        submaps=b.take(4)+1 if b.take(1) else 1
        if b.take(1):
            for _ in range(b.take(8)+1):
                width=(state['channels']-1).bit_length();mag=b.take(width);angle=b.take(width)
                require(mag!=angle and mag<state['channels'] and angle<state['channels'])
        require(b.take(2)==0)
        if submaps>1:
            for _ in range(state['channels']):require(b.take(4)<submaps)
        for _ in range(submaps):b.take(8);require(b.take(8)<floors and b.take(8)<residues)
    modes=[]
    for _ in range(b.take(6)+1):
        modes.append(b.take(1));require(b.take(16)==0 and b.take(16)==0 and b.take(8)<mappings)
    require(b.take(1)==1);b.finish();state['modes']=modes


def audio(p,state):
    b=Bits(p);require(b.take(1)==0,'location_metadata_unverifiable: vorbis non-audio packet')
    mode=b.take((len(state['modes'])-1).bit_length());require(mode<len(state['modes']))
    long=state['modes'][mode]
    if long:b.take(1);b.take(1)
    block=state['blocks'][long];previous=state['previous'];state['previous']=block;state['packets']+=1
    # Residue/floor coded data can legally be truncated/padded (spec §1.1.3).
    # No metadata substring scan and no assertion that PCM decoding succeeded.
    return 0 if previous is None else (previous+block)//4
