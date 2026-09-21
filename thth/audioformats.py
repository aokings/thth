"""Bounded, byte-preserving audio container inspection (no codec execution)."""
import os
import struct
from .mediaformats import Inspection,FormatError,require


class Reader:
    def __init__(self,fd,size):self.fd,self.size=fd,size
    def read(self,at,n):
        require(0<=at<=self.size-n)
        value=os.pread(self.fd,n,at);require(len(value)==n)
        return value
    def zero(self,at,end):
        for pos in range(at,end,65536):
            require(not any(self.read(pos,min(65536,end-pos))),'location_metadata_unverifiable: audio padding')
    def zstr(self,at,end):
        require(end>at and self.read(end-1,1)==b'\0')
        for pos in range(at,end-1,65536):require(b'\0' not in self.read(pos,min(65536,end-1-pos)))


def wav(fd,size):
    r=Reader(fd,size);head=r.read(0,12)
    require(head[:4]==b'RIFF' and head[8:]==b'WAVE')
    require(int.from_bytes(head[4:8],'little')==size-8)
    def chunks(start,end):
        while start<end:
            require(start+8<=end);header=r.read(start,8);n=int.from_bytes(header[4:],'little')
            a=start+8;b=a+n;require(b+(n&1)<=end)
            if n&1:require(r.read(b,1)==b'\0')
            yield header[:4],a,b
            start=b+(n&1)
        require(start==end)
    fmt=None;data_size=None;fact=None;notes=set()
    # Original IBM/Microsoft RIFF1991 INFO registry. Unknown additions are not
    # silently skipped: they may be opaque metadata/embedded images.
    info=set(b'IART ICMS ICMT ICOP ICRD ICRP IDIM IDPI IENG IGNR IKEY ILGT IMED INAM IPLT IPRD ISBJ ISFT ISHP ISRC ISRF ITCH'.split())
    for kind,a,b in chunks(12,size):
        n=b-a
        if kind==b'fmt ':
            require(fmt is None and data_size is None and n>=16)
            tag,channels,rate,byte_rate,align,bits=struct.unpack('<HHIIHH',r.read(a,16))
            require(channels>0 and rate>0 and align>0 and bits>0 and bits%8==0)
            require(n==16 or n>=18 and int.from_bytes(r.read(a+16,2),'little')==n-18)
            if tag==0xfffe:
                require(n>=40)
                require(n==40,'location_metadata_unverifiable: wav format extension')
                valid,mask=struct.unpack('<HI',r.read(a+18,6));guid=r.read(a+24,16)
                require(0<valid<=bits and (mask==0 or mask.bit_count()==channels))
                require(guid[4:]==bytes.fromhex('00001000800000aa00389b71'),'unsupported_attachment_structure: wav codec')
                tag=int.from_bytes(guid[:4],'little')
            else:require(n in (16,18),'location_metadata_unverifiable: wav format extension')
            require(tag in (1,3),'unsupported_attachment_structure: wav codec')
            require(tag!=3 or bits in (32,64))
            require(align==channels*(bits//8) and byte_rate==rate*align)
            fmt=(rate,align)
        elif kind==b'data':
            require(fmt is not None and data_size is None);data_size=n
            require(n%fmt[1]==0)
            # Never inspect sample bytes for metadata-like strings.
        elif kind==b'fact':
            require(fact is None and n>=4)
            require(n==4,'location_metadata_unverifiable: wav fact extension')
            fact=int.from_bytes(r.read(a,4),'little')
        elif kind==b'LIST':
            require(n>=4)
            require(r.read(a,4)==b'INFO','location_metadata_unverifiable: wav LIST')
            for key,va,vb in chunks(a+4,b):
                require(key!=b'IARL','location_metadata_present: wav archival_location')
                require(key in info,'location_metadata_unverifiable: wav INFO')
                r.zstr(va,vb)
            notes.add('non_location_metadata_retained')
        elif kind==b'CSET':
            require(n==8);notes.add('non_location_metadata_retained')
        elif kind in (b'JUNK',b'PAD '):r.zero(a,b)
        else:
            # ID3, iXML, BWF/UMID, pictures and proprietary chunks need their own
            # bounded privacy inspection; they cannot be called metadata-free.
            raise FormatError('location_metadata_unverifiable: wav chunk')
    require(fmt is not None and data_size is not None)
    frames=data_size//fmt[1]
    require(fact is None or fact==frames)
    return Inspection('wav','audio',None,None,frames/fmt[0],metadata_notes=tuple(sorted(notes)))
