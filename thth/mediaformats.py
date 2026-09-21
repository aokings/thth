"""Bounded container/metadata validation, not a general pixel/video decoder.

No codec conversion. Images retain rendering headers, ICC and Orientation only;
GIF and ISO BMFF bytes stay untouched. Provider codec support is checked later.
"""
from __future__ import annotations

import binascii
import dataclasses
import os
import struct
import zlib


# Shared visual sample-entry classification for privacy and observation readers.
VIDEO_SAMPLE_CODECS=frozenset((b'avc1',b'avc3',b'hvc1',b'hev1',b'vp09',b'av01',b'mp4v',b'jpeg',b'mjpa',b'mjpb'))


class FormatError(ValueError):
    pass


def require(ok, reason='invalid_attachment_structure'):
    if not ok:
        raise FormatError(reason)


@dataclasses.dataclass(frozen=True)
class Inspection:
    format: str
    kind: str
    width: int | None
    height: int | None
    duration: float | None
    orientation: int | None = None
    public_bytes: bytes | None = dataclasses.field(default=None, repr=False)


def orientation_exif(data, *, has_icc=False, requirements=None):
    """Validate TIFF directories/offsets, discard every tag except Orientation."""
    require(len(data) >= 8 and data[:2] in (b'II', b'MM'))
    endian = '<' if data[:2] == b'II' else '>'
    def number(offset, code):
        size = struct.calcsize(code)
        require(0 <= offset <= len(data)-size)
        return struct.unpack_from(endian+code, data, offset)[0]
    require(number(2, 'H') == 42)
    sizes = {1:1, 2:1, 3:2, 4:4, 5:8, 6:1, 7:1, 8:2, 9:4, 10:8, 11:4, 12:8, 13:4}
    seen = set(); pending = [(number(4, 'I'), True)]; orientation = None
    while pending:
        offset, primary = pending.pop()
        if offset == 0:
            continue
        require(offset >= 8 and offset not in seen)
        seen.add(offset)
        count = number(offset, 'H')
        require(offset + 2 + count*12 + 4 <= len(data))
        tags = set()
        for i in range(count):
            at = offset+2+i*12
            tag = number(at, 'H'); typ = number(at+2, 'H'); n = number(at+4, 'I')
            require(tag not in tags and typ in sizes)
            tags.add(tag); length = sizes[typ]*n
            value_at = at+8 if length <= 4 else number(at+8, 'I')
            require(0 <= value_at <= len(data)-length)
            if tag in (0x8769, 0x8825, 0xa005):
                require(typ in (4, 13) and n == 1)
                pending.append((number(value_at, 'I'), False))
            if tag == 0xa001:
                require(typ == 3 and n == 1)
                if number(value_at, 'H') != 1:
                    if requirements is not None: requirements.add('icc')
                    else: require(has_icc, 'sanitize_unverifiable: Exif ColorSpace')
            if tag == 0x112 and primary:
                require(typ == 3 and n == 1)
                orientation = number(value_at, 'H')
                require(1 <= orientation <= 8)
        pending.append((number(offset+2+count*12, 'I'), False))
    minimal = None
    # Explicit TIFF entry: count, tag, type, item count, inline SHORT, next IFD.
    if orientation is not None:
        minimal = b'II*\x00\x08\x00\x00\x00' + struct.pack('<HHHIH', 1, 0x112, 3, 1, orientation) + b'\0\0' + b'\0'*4
    return orientation, minimal


def jpeg(data):
    require(data[:2] == b'\xff\xd8'); out = bytearray(data[:2]); pos = 2
    shape = None; orientation = None; seen_exif = False; scan = False; icc = {}; requirements = set()
    while pos < len(data):
        require(data[pos] == 255); begin = pos
        while pos < len(data) and data[pos] == 255:
            pos += 1
        require(pos < len(data)); marker = data[pos]; pos += 1
        if marker == 0xd9:
            require(shape and scan and pos == len(data)); out.extend(b'\xff\xd9'); break
        require(marker not in (0, 0xd8, 1) and not 0xd0 <= marker <= 0xd7)
        require(pos+2 <= len(data)); size = int.from_bytes(data[pos:pos+2], 'big')
        require(size >= 2 and pos+size <= len(data)); payload = data[pos+2:pos+size]; pos += size
        kept = payload
        if marker == 0xe1:
            kept = None
            if not payload.startswith((b'Exif\0\0', b'http://ns.adobe.com/xap/1.0/\0', b'http://ns.adobe.com/xmp/extension/\0')):
                raise FormatError('sanitize_unverifiable: jpeg APP1')
            if payload.startswith(b'Exif\0\0'):
                require(not seen_exif); seen_exif = True
                orientation, minimal = orientation_exif(payload[6:], requirements=requirements)
                if minimal is not None: kept = b'Exif\0\0'+minimal
        elif marker == 0xe2:
            kept = None
            if payload.startswith(b'ICC_PROFILE\0'):
                require(len(payload) > 14 and 1 <= payload[12] <= payload[13])
                require(payload[12] not in icc); icc[payload[12]] = payload[13]; kept = payload
            else:
                raise FormatError('sanitize_unverifiable: jpeg APP2')
        elif marker == 0xe0:
            kept = None
            if payload.startswith(b'JFIF\0'):
                require(len(payload) >= 14 and len(payload) == 14 + 3*payload[12]*payload[13])
                # Retain colour interpretation/density; discard embedded thumbnail.
                kept = payload[:12]+b'\0\0'
            elif payload.startswith(b'JFXX\0'):
                kept = None
            else:
                raise FormatError('unsupported_attachment_structure: jpeg APP0')
        elif marker == 0xee:
            require(len(payload) == 12 and payload[:5] == b'Adobe' and payload[-1] in (0,1,2))
        elif marker in (0xed, 0xfe):
            kept = None
        elif 0xe0 <= marker <= 0xef:
            raise FormatError('sanitize_unverifiable: jpeg APP')
        elif marker in (0xc0, 0xc1, 0xc2):
            require(shape is None and len(payload) >= 6)
            depth, height, width, count = struct.unpack('>BHHB', payload[:6])
            require(depth == 8 and width > 0 and height > 0 and count in (1,3,4) and len(payload) == 6+3*count)
            ids = [payload[i] for i in range(6, len(payload), 3)]
            require(len(set(ids)) == count)
            require(all(1 <= payload[i] >> 4 <= 4 and 1 <= payload[i] & 15 <= 4 and payload[i+1] <= 3 for i in range(7, len(payload), 3)))
            shape = (width, height, ids)
        elif marker == 0xdb:
            q = 0
            while q < len(payload):
                info = payload[q]; require(info >> 4 in (0,1) and info & 15 <= 3)
                q += 1+64*(1+(info >> 4))
            require(q == len(payload) and q > 0)
        elif marker == 0xc4:
            q = 0
            while q < len(payload):
                require(q+17 <= len(payload) and payload[q] >> 4 in (0,1) and payload[q] & 15 <= 3)
                counts=payload[q+1:q+17]; n = sum(counts); require(n <= 256)
                slots=1
                for count in counts:
                    slots=slots*2-count; require(slots>=0)
                q += 17+n
            require(q == len(payload) and q > 0)
        elif marker == 0xdd:
            require(len(payload) == 2)
        elif marker == 0xda:
            require(shape and len(payload) >= 6)
            count = payload[0]; require(1 <= count <= len(shape[2]) and len(payload) == 4+2*count)
            require(all(payload[1+2*i] in shape[2] for i in range(count)))
            scan = True
        else:
            raise FormatError('unsupported_attachment_structure: jpeg marker')
        if kept is not None:
            out.extend(bytes((255, marker))+struct.pack('>H', len(kept)+2)+kept)
        if marker == 0xda:
            start = pos
            while True:
                end = data.find(b'\xff', pos); require(end >= 0)
                pos = end+1
                while pos < len(data) and data[pos] == 255: pos += 1
                require(pos < len(data))
                if data[pos] == 0 or 0xd0 <= data[pos] <= 0xd7:
                    pos += 1; continue
                out.extend(data[start:end]); pos = end; break
    else:
        raise FormatError('invalid_attachment_structure')
    require(not requirements or icc, 'sanitize_unverifiable: Exif ColorSpace')
    if icc:
        require(len(set(icc.values())) == 1 and set(icc) == set(range(1, next(iter(icc.values()))+1)))
    return Inspection('jpeg', 'image', shape[0], shape[1], None, orientation, bytes(out))


def _chunk(kind, payload):
    return struct.pack('>I', len(payload))+kind+payload+struct.pack('>I', binascii.crc32(kind+payload)&0xffffffff)


def _inflate(parts, expected=None):
    decoder = zlib.decompressobj(); total = 0
    try:
        for part in parts:
            while part:
                data = decoder.decompress(part, 65536); total += len(data)
                require(expected is None or total <= expected)
                part = decoder.unconsumed_tail
            require(not decoder.unused_data)
        total += len(decoder.flush())
    except zlib.error as exc:
        raise FormatError('invalid_attachment_structure') from exc
    require(decoder.eof and not decoder.unused_data and (expected is None or total == expected))


def png(data):
    signature = b'\x89PNG\r\n\x1a\n'; require(data[:8] == signature)
    pos = 8; chunks = []; seen = set(); shape = None; orientation = None; idat = []
    requirements = set(); frames = []; sequence = 0; animation = None; current = None; ended_idat = False
    render = {b'PLTE', b'IDAT', b'IEND', b'tRNS', b'gAMA', b'cHRM', b'sRGB', b'cICP', b'sBIT', b'bKGD', b'pHYs', b'mDCV', b'cLLI', b'hIST', b'sPLT', b'acTL', b'fcTL', b'fdAT'}
    while pos < len(data):
        require(pos+12 <= len(data)); length = int.from_bytes(data[pos:pos+4], 'big'); kind = data[pos+4:pos+8]
        require(all(65 <= x <= 90 or 97 <= x <= 122 for x in kind) and 65 <= kind[2] <= 90)
        require(pos+12+length <= len(data)); payload = data[pos+8:pos+8+length]
        require(binascii.crc32(kind+payload)&0xffffffff == int.from_bytes(data[pos+8+length:pos+12+length], 'big'))
        pos += length+12; kept = payload
        if shape is None: require(kind == b'IHDR')
        if kind not in (b'IDAT', b'fdAT', b'fcTL', b'tEXt', b'zTXt', b'iTXt'): require(kind not in seen)
        seen.add(kind)
        if kind == b'IHDR':
            require(length == 13)
            w,h,depth,color,compression,filtering,interlace = struct.unpack('>IIBBBBB',payload)
            require(0 < w <= 0x7fffffff and 0 < h <= 0x7fffffff and color in (0,2,3,4,6) and compression == filtering == 0 and interlace in (0,1))
            require(depth in {0:(1,2,4,8,16),2:(8,16),3:(1,2,4,8),4:(8,16),6:(8,16)}[color])
            shape = (w,h,depth,color,interlace)
        elif kind == b'PLTE':
            require(not idat and 0 < length <= 768 and length % 3 == 0)
        elif kind == b'IDAT':
            require(not ended_idat); idat.append(payload)
        elif kind == b'IEND':
            require(length == 0 and pos == len(data) and idat)
        elif kind == b'eXIf':
            orientation, kept = orientation_exif(payload, requirements=requirements)
        elif kind == b'iCCP':
            split = payload.find(b'\0'); require(1 <= split <= 79 and payload[split+1:split+2] == b'\0')
            require(not idat and b'sRGB' not in seen)
            _inflate([payload[split+2:]])
        elif kind in (b'tRNS', b'gAMA', b'cHRM', b'sRGB', b'cICP', b'sBIT', b'bKGD', b'pHYs', b'mDCV', b'cLLI', b'hIST', b'sPLT'):
            require(not idat)
            color=shape[3]
            lengths={b'gAMA':4,b'cHRM':32,b'sRGB':1,b'cICP':4,b'pHYs':9,b'mDCV':24,b'cLLI':8}
            if kind in lengths: require(length == lengths[kind])
            if kind==b'sRGB': require(payload[0]<=3 and b'iCCP' not in seen)
            if kind==b'gAMA': require(int.from_bytes(payload,'big')>0)
            if kind==b'tRNS':
                require((color==0 and length==2) or (color==2 and length==6) or (color==3 and b'PLTE' in seen and 0<length<=256))
            if kind==b'sBIT': require(length=={0:1,2:3,3:3,4:2,6:4}[color] and all(0<x<=(8 if color==3 else shape[2]) for x in payload))
            if kind==b'bKGD': require(length=={0:2,2:6,3:1,4:2,6:6}[color])
            if kind==b'pHYs': require(payload[-1]<=1)
            if kind==b'hIST': require(b'PLTE' in seen and length%2==0 and 0<length<=512)
            if kind==b'sPLT':
                split=payload.find(b'\0'); require(1<=split<=79 and split+2<length and payload[split+1] in (8,16))
                require((length-split-2)%(6 if payload[split+1]==8 else 10)==0)
        elif kind == b'acTL':
            require(length == 8 and not idat); animation = int.from_bytes(payload[:4],'big'); require(animation > 0)
        elif kind == b'fcTL':
            require(animation and length == 26)
            seq,fw,fh,x,y,num,den,dispose,blend = struct.unpack('>IIIIIHHBB',payload)
            require(seq == sequence and fw > 0 and fh > 0 and x+fw <= shape[0] and y+fh <= shape[1] and dispose <= 2 and blend <= 1)
            sequence += 1
            if current is not None: frames.append(current)
            current = (fw,fh,num/(den or 100),[])
            if not idat: require((fw,fh,x,y) == (shape[0],shape[1],0,0))
        elif kind == b'fdAT':
            require(animation and current and idat and length > 4 and int.from_bytes(payload[:4],'big') == sequence)
            sequence += 1; current[3].append(payload[4:])
        elif kind not in render:
            require(kind[0] & 32, 'unsupported_attachment_structure: png critical chunk')
            require(kind in (b'tEXt', b'iTXt', b'zTXt', b'tIME'), 'sanitize_unverifiable: png chunk')
            kept = None
        if kind != b'IDAT' and idat: ended_idat = True
        if kept is not None: chunks.append(_chunk(kind,kept))
    require(not requirements or b'iCCP' in seen, 'sanitize_unverifiable: Exif ColorSpace')
    require(b'IEND' in seen and (shape[3] != 3 or b'PLTE' in seen))
    def expected(w,h):
        channels = {0:1,2:3,3:1,4:2,6:4}[shape[3]]; depth = shape[2]
        passes = [(0,0,1,1)] if not shape[4] else [(0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)]
        return sum(((pw*channels*depth+7)//8+1)*ph for x,y,dx,dy in passes if (pw:=max(0,(w-x+dx-1)//dx)) and (ph:=max(0,(h-y+dy-1)//dy)))
    _inflate(idat,expected(shape[0],shape[1]))
    if current is not None: frames.append(current)
    if animation:
        require(len(frames) == animation)
        for i, frame in enumerate(frames):
            if frame[3]: _inflate(frame[3],expected(frame[0],frame[1]))
            else: require(i == 0)
    duration = sum(x[2] for x in frames) if animation else None
    return Inspection('png','image',shape[0],shape[1],duration,orientation,signature+b''.join(chunks))


def _riff_chunks(data, start, end):
    pos = start
    while pos < end:
        require(pos+8 <= end); kind = data[pos:pos+4]; size = int.from_bytes(data[pos+4:pos+8],'little')
        stop = pos+8+size; require(stop+(size&1) <= end)
        if size&1: require(data[stop] == 0)
        yield kind, data[pos+8:stop]
        pos = stop+(size&1)
    require(pos == end)


def _riff_chunk(kind,payload):
    return kind+struct.pack('<I',len(payload))+payload+(b'\0' if len(payload)&1 else b'')


def _webp_frame(kind,payload):
    if kind == b'VP8 ':
        require(len(payload) >= 10 and payload[0]&1 == 0 and payload[3:6] == b'\x9d\x01\x2a')
        width = int.from_bytes(payload[6:8],'little')&0x3fff; height = int.from_bytes(payload[8:10],'little')&0x3fff
    else:
        require(kind == b'VP8L' and len(payload) >= 5 and payload[0] == 0x2f and payload[4]>>5 == 0)
        bits = int.from_bytes(payload[1:5],'little'); width = 1+(bits&0x3fff); height = 1+((bits>>14)&0x3fff)
    require(width > 0 and height > 0)
    return width,height


def webp(data):
    require(data[:4] == b'RIFF' and data[8:12] == b'WEBP' and int.from_bytes(data[4:8],'little')+8 == len(data))
    chunks = list(_riff_chunks(data,12,len(data))); require(chunks)
    seen = set(); output = []; shape = None; orientation = None; frames = 0; duration = 0; extended = None; rank = 0
    for i,(kind,payload) in enumerate(chunks):
        if kind != b'ANMF': require(kind not in seen)
        seen.add(kind); kept = payload
        required_order={b'VP8X':0,b'ICCP':1,b'ANIM':2,b'ANMF':3,b'ALPH':2,b'VP8 ':3,b'VP8L':3}
        if kind in required_order:
            require(required_order[kind]>=rank); rank=required_order[kind]
        if kind == b'VP8X':
            require(i == 0 and len(payload) == 10 and not payload[0]&0xc1 and payload[1:4] == b'\0'*3)
            shape = (1+int.from_bytes(payload[4:7],'little'),1+int.from_bytes(payload[7:10],'little')); extended = payload[0]
        elif kind in (b'VP8 ',b'VP8L'):
            actual = _webp_frame(kind,payload); require(not frames and not ({b'VP8 ',b'VP8L'}-{kind})&seen and (extended is None or not extended&2) and (shape is None or actual == shape)); shape = actual
        elif kind == b'ANIM':
            require(extended is not None and extended&2 and len(payload) == 6)
        elif kind == b'ANMF':
            require(b'ANIM' in seen and len(payload) >= 24 and payload[15]&~3 == 0)
            x,y,w,h,t = [int.from_bytes(payload[j:j+3],'little') for j in (0,3,6,9,12)]
            w+=1; h+=1; require(x*2+w <= shape[0] and y*2+h <= shape[1])
            frame_chunks = list(_riff_chunks(payload,16,len(payload)))
            require([k for k,_ in frame_chunks] in ([b'VP8 '],[b'VP8L'],[b'ALPH',b'VP8 ']))
            require(_webp_frame(*frame_chunks[-1]) == (w,h)); frames += 1; duration += t/1000
        elif kind == b'EXIF':
            orientation,kept = orientation_exif(payload[6:] if payload.startswith(b'Exif\0\0') else payload, has_icc=any(k==b'ICCP' for k,_ in chunks))
        elif kind == b'XMP ':
            kept = None
        elif kind == b'ICCP':
            require(bool(payload))
        elif kind == b'ALPH':
            require(bool(payload) and payload[0]&0xc0 == 0)
        else:
            raise FormatError('sanitize_unverifiable: webp chunk')
        if kept is not None: output.append((kind,kept))
    require(shape and (frames > 0 if extended is not None and extended&2 else bool(seen & {b'VP8 ',b'VP8L'})))
    if extended is not None:
        require(bool(extended&0x20) == (b'ICCP' in seen) and bool(extended&8) == (b'EXIF' in seen) and bool(extended&4) == (b'XMP ' in seen))
        p = output[0][1]; flags = p[0]&~12
        if orientation is not None: flags |= 8
        output[0] = (b'VP8X',bytes((flags,))+p[1:])
    else:
        require(len(chunks) == 1)
    body = b'WEBP'+b''.join(_riff_chunk(k,p) for k,p in output)
    return Inspection('webp','image',shape[0],shape[1],duration if frames else None,orientation,b'RIFF'+struct.pack('<I',len(body))+body)


def gif(data):
    require(data[:6] in (b'GIF87a',b'GIF89a') and len(data) >= 14)
    width,height,flags = struct.unpack('<HHB',data[6:11]); require(width and height)
    pos = 13+(3*(1<<((flags&7)+1)) if flags&128 else 0); frames = 0; duration = 0; delay = 0
    def blocks(at):
        while True:
            require(at < len(data)); n = data[at]; at += 1; require(at+n <= len(data)); at += n
            if n == 0: return at
    while pos < len(data):
        kind = data[pos]; pos += 1
        if kind == 0x3b:
            require(pos == len(data) and frames); break
        if kind == 0x21:
            require(pos < len(data)); label = data[pos]; pos += 1
            if label == 0xf9:
                require(pos+6 <= len(data) and data[pos] == 4 and data[pos+5] == 0)
                require(not data[pos+1]&0xe0)
                delay = int.from_bytes(data[pos+2:pos+4],'little')/100; pos += 6
            elif label in (0xfe,0xff,0x01):
                if label in (0xff,0x01): require(data[pos:pos+1] == bytes((11 if label==0xff else 12,)))
                pos = blocks(pos)
            else: raise FormatError('unsupported_attachment_structure: gif extension')
        elif kind == 0x2c:
            require(pos+9 < len(data)); x,y,w,h,packed = struct.unpack('<HHHHB',data[pos:pos+9]); pos += 9
            require(w and h and x+w <= width and y+h <= height and not packed&0x18)
            if packed&128: pos += 3*(1<<((packed&7)+1))
            require(pos < len(data) and 2 <= data[pos] <= 8); pos = blocks(pos+1)
            frames += 1; duration += delay; delay = 0
        else: raise FormatError('invalid_attachment_structure')
    else: raise FormatError('invalid_attachment_structure')
    return Inspection('gif','image',width,height,duration if frames>1 else None)


def _bmff_metadata_scalar(value_type,read,start,end):
    # Apple well-known types28 (nested metadata) and27 (BMP) are not scalars.
    # Reserved/implicit or unknown types cannot establish location absence.
    codecs={1:'utf-8',2:'utf-16-be',3:'shift_jis',4:'utf-8',5:'utf-16-be'}
    sizes={21:(1,2,3,4),22:(1,2,3,4),23:(4,),24:(8,),65:(1,),66:(2,),67:(4,),70:(8,),71:(8,),72:(16,),74:(8,),75:(1,),76:(2,),77:(4,),78:(8,),79:(72,)}
    if value_type in codecs:
        import codecs as codec_module
        decoder=codec_module.getincrementaldecoder(codecs[value_type])()
        try:
            for at in range(start,end,65536):decoder.decode(read(at,min(65536,end-at)))
            decoder.decode(b'',final=True)
        except UnicodeDecodeError:raise FormatError('invalid_attachment_structure: metadata text') from None
    elif value_type in sizes:require(end-start in sizes[value_type])
    else:raise FormatError('location_metadata_unverifiable: metadata_value_type')


def bmff(fd,size,*,allow_edit_lists=False):
    """Walk bounded boxes without loading video payloads or rewriting bytes."""
    def read(at,n):
        require(0 <= at <= size-n); result = os.pread(fd,n,at); require(len(result)==n); return result
    def boxes(start,end, *, top=False):
        at=start
        while at < end:
            require(at+8 <= end); header = read(at,8); n = int.from_bytes(header[:4],'big'); kind=header[4:]; prefix=8
            if n==1: n=int.from_bytes(read(at+8,8),'big'); prefix=16
            if n==0:
                require(top); n=end-at
            require(n>=prefix and at+n<=end)
            yield kind,at+prefix,at+n
            at+=n
        require(at==end)
    brand=None; duration=None; dimensions=[]; video=False; audio=False; moov=False; mdat=False
    containers={b'moov',b'trak',b'mdia',b'minf',b'stbl',b'udta',b'edts',b'dinf',b'mvex',b'moof',b'traf'}
    # Known binary structure is not a container for arbitrary metadata. Unknown
    # boxes cannot be skipped as if location absence had been established.
    structure={b'mdhd',b'vmhd',b'smhd',b'hmhd',b'nmhd',b'dref',b'stts',
               b'ctts',b'cslg',b'stsc',b'stsz',b'stz2',b'stco',b'co64',b'stss',
               b'stsh',b'padb',b'stdp',b'sbgp',b'sgpd',b'subs',b'sdtp',b'stps',
               b'elng',b'mehd',b'trex',b'mfhd',b'tfhd',b'tfdt',b'trun',b'sidx'}
    def padding(a,b):
        while a<b:
            n=min(1024*1024,b-a)
            require(not any(read(a,n)),'location_metadata_unverifiable');a+=n
    def sample_entries(a,b):
        require(b-a>=8 and read(a,4)==b'\0'*4)
        count=int.from_bytes(read(a+4,4),'big');seen=0
        for codec,sa,sb in boxes(a+8,b):
            seen+=1
            if codec in VIDEO_SAMPLE_CODECS:
                prefix=78
                allowed={b'avcC',b'hvcC',b'av1C',b'vpcC',b'esds',b'pasp',b'clap',b'colr',b'btrt',b'fiel',b'gama'}
            elif codec in (b'mp4a',b'ac-3',b'ec-3',b'Opus',b'fLaC',b'alac',b'sowt',b'twos'):
                require(sb-sa>=28,'location_metadata_unverifiable')
                version=int.from_bytes(read(sa+8,2),'big')
                require(version in (0,1),'location_metadata_unverifiable')
                prefix=28+(16 if version else 0)
                allowed={b'esds',b'dac3',b'dec3',b'dOps',b'dfLa',b'alac',b'btrt',b'chan'}
            else:raise FormatError('location_metadata_unverifiable')
            require(sb-sa>=prefix,'location_metadata_unverifiable')
            for ext,ea,eb in boxes(sa+prefix,sb):
                if ext in (b'free',b'skip'):padding(ea,eb)
                elif ext not in allowed:raise FormatError('location_metadata_unverifiable')
        require(seen==count)
    def walk(start,end,depth=0,metadata=False):
        nonlocal brand,duration,video,audio,moov,mdat
        require(depth <= 32,'invalid_attachment_structure: box nesting')
        for kind,a,b in boxes(start,end,top=depth==0):
            if kind in (b'\xa9xyz',b'loci',b'xyz '): raise FormatError('location_metadata_present')
            if kind==b'ftyp':
                require(brand is None and b-a>=8 and (b-a)%4==0); brand=read(a,4)
                require(brand in (b'isom',b'iso2',b'iso4',b'iso5',b'iso6',b'iso8',b'mp41',b'mp42',b'avc1',b'qt  ',b'M4V ',b'M4A '),'unsupported_attachment: bmff brand')
            elif kind==b'uuid': raise FormatError('location_metadata_unverifiable')
            elif kind in (b'free',b'skip'):padding(a,b)
            elif kind==b'mdat': mdat=True
            elif kind==b'mvhd':
                require(b-a >= 20); version=read(a,1)[0]; require(version in (0,1))
                offset=20 if version else 12; require(a+offset+(12 if version else 8)<=b)
                scale=int.from_bytes(read(a+offset,4),'big'); ticks=int.from_bytes(read(a+offset+4,8 if version else 4),'big')
                require(scale>0 and ticks!=(2**(64 if version else 32)-1),'duration_unavailable'); duration=ticks/scale
            elif kind==b'tkhd':
                require(b-a>=84); version=read(a,1)[0]; expected=96 if version else 84
                require(version in (0,1) and b-a>=expected)
                w=int.from_bytes(read(a+expected-8,4),'big')/65536; h=int.from_bytes(read(a+expected-4,4),'big')/65536
                if w and h: dimensions.append((w,h))
            elif kind==b'hdlr':
                require(b-a>=12); handler=read(a+8,4)
                if handler==b'vide': video=True
                elif handler==b'soun': audio=True
                elif handler in (b'meta',b'mdta',b'mdir'):
                    # A static meta handler is walked separately; a timed metadata
                    # track can contain GPS samples in mdat and is not proven safe.
                    raise FormatError('location_metadata_unverifiable')
            elif kind==b'stsd':sample_entries(a,b)
            elif kind==b'meta':
                require(b-a>=4 and read(a,4)==b'\0'*4)
                for mk,ma,mb in boxes(a+4,b):
                    if mk==b'keys':
                        require(mb-ma>=8); count=int.from_bytes(read(ma+4,4),'big'); pos=ma+8
                        for _ in range(count):
                            require(pos+8<=mb); n=int.from_bytes(read(pos,4),'big'); require(n>=8 and pos+n<=mb)
                            key=read(pos+8,n-8)
                            if b'location' in key.lower() or key in (b'\xa9xyz',b'loci'): raise FormatError('location_metadata_present')
                            pos+=n
                        require(pos==mb)
                    elif mk==b'ilst':
                        for ik,ia,ib in boxes(ma,mb):
                            if ik in (b'\xa9xyz',b'loci') or b'location' in ik.lower(): raise FormatError('location_metadata_present')
                            # Validate nested value boxes, without interpreting mdat.
                            for vk,va,vb in boxes(ia,ib):
                                if vk in (b'free',b'skip'):padding(va,vb)
                                elif vk not in (b'data',b'mean',b'name'):raise FormatError('location_metadata_unverifiable')
                                elif vk==b'data':
                                    require(vb-va>=8)
                                    # This video-only boundary does not inspect binary covers.
                                    # Nested metadata, implicit data and all unknown types fail closed.
                                    _bmff_metadata_scalar(int.from_bytes(read(va,4),'big'),read,va+8,vb)
                                else:
                                    require(vb-va>=4 and read(va,4)==bytes(4))
                                    key=read(va+4,vb-va-4)
                                    try:key.decode('utf-8')
                                    except UnicodeDecodeError:raise FormatError('invalid_attachment_structure: metadata key') from None
                                    require(not any(word in key.lower() for word in (b'location',b'gpslatitude',b'gpslongitude',b'gpsaltitude')),'location_metadata_present')
                    elif mk==b'hdlr': require(mb-ma>=12 and read(ma,4)==b'\0'*4)
                    elif mk in containers: walk(ma,mb,depth+1)
                    elif mk in (b'free',b'skip'):padding(ma,mb)
                    else:raise FormatError('location_metadata_unverifiable')
            elif kind==b'elst' and allow_edit_lists:
                # Threads C11: a structurally valid edit list is a provider
                # warning. It never exempts adjacent metadata from this walk.
                require(b-a>=8);version=read(a,1)[0]
                require(version in (0,1) and read(a+1,3)==b'\0'*3)
                count=int.from_bytes(read(a+4,4),'big')
                require(b-a==8+count*(20 if version else 12))
            elif kind in (b'moof',b'mvex',b'elst'):
                raise FormatError('duration_unverifiable')
            elif kind in containers:
                if kind==b'moov': require(not moov); moov=True
                walk(a,b,depth+1,metadata=metadata or kind==b'udta')
            elif metadata and kind in (b'\xa9nam',b'\xa9ART',b'\xa9alb',b'\xa9day',b'\xa9too',b'\xa9cmt',b'cprt',b'name'):pass
            elif metadata or kind not in structure:
                raise FormatError('location_metadata_unverifiable')
    walk(0,size)
    require(brand is not None and moov and mdat and duration is not None and (video or audio))
    require(not video or dimensions,'dimensions_unavailable')
    w,h=max(dimensions,key=lambda s:s[0]*s[1]) if dimensions else (None,None)
    return Inspection('mov' if brand==b'qt  ' else 'mp4','video' if video else 'audio',w,h,duration)


def _webm_header(fd,size):
    """Name a bounded EBML DocType only; no WebM acceptance or codec parsing."""
    data=os.pread(fd,min(size,65536),0)
    def vint(at,identifier=False):
        if at>=len(data) or not data[at]:raise ValueError
        width=9-data[at].bit_length()
        if width>8 or at+width>len(data):raise ValueError
        value=int.from_bytes(data[at:at+width],'big')
        if not identifier:value&=(1<<(7*width))-1
        return value,at+width
    try:
        length,at=vint(4);end=at+length
        if end>len(data):return False
        found=[]
        while at<end:
            key,at=vint(at,True);length,at=vint(at)
            if at+length>end:return False
            if key==0x4282:found.append(data[at:at+length])
            at+=length
        return found==[b'webm']
    except ValueError:return False


def inspect(fd,size,*,allow_edit_lists=False):
    head=os.pread(fd,16,0)
    if len(head)>=8 and head[4:8] in (b'ftyp',b'free',b'wide',b'moov',b'mdat'): return bmff(fd,size,allow_edit_lists=allow_edit_lists)
    if head.startswith(b'\x1aE\xdf\xa3'):
        raise FormatError('unsupported_attachment: '+('webm' if _webm_header(fd,size) else 'unknown format'))
    data=os.pread(fd,size,0); require(len(data)==size)
    if head.startswith(b'\xff\xd8'): return jpeg(data)
    if head.startswith(b'\x89PNG\r\n\x1a\n'): return png(data)
    if head.startswith(b'RIFF') and head[8:12]==b'WEBP': return webp(data)
    if head[:6] in (b'GIF87a',b'GIF89a'): return gif(data)
    raise FormatError('unsupported_attachment: unknown format')


def video_metrics(fd,size):
    """First video stream's sample dimensions and average rate (no transcoding).

    Non-fragmented ISO BMFF stts counts / mdhd timescale. VFR uses the average,
    not the largest instantaneous rate. Missing or inconsistent sample timing
    is unknown; this does not invent ffprobe's codec-derived r_frame_rate.
    """
    from fractions import Fraction
    def read(at,n):
        require(0<=at<=size-n,'video_rate_unverifiable');b=os.pread(fd,n,at);require(len(b)==n);return b
    def boxes(a,b):
        while a<b:
            require(a+8<=b);header=read(a,8);n=int.from_bytes(header[:4],'big');prefix=8
            if n==1:n=int.from_bytes(read(a+8,8),'big');prefix=16
            require(n>=prefix and a+n<=b);yield header[4:],a+prefix,a+n;a+=n
    def children(a,b,kind):return [(x,y) for k,x,y in boxes(a,b) if k==kind]
    def one(a,b,kind):
        values=children(a,b,kind);require(len(values)==1,'video_rate_unverifiable');return values[0]
    ma,mb=one(0,size,b'moov')
    for ta,tb in children(ma,mb,b'trak'):
        da,db=one(ta,tb,b'mdia');ha,hb=one(da,db,b'hdlr');require(hb-ha>=12)
        if read(ha+8,4)!=b'vide':continue
        a,b=one(da,db,b'mdhd');v=read(a,1)[0];require(v in (0,1));offset=20 if v else 12
        require(a+offset+(12 if v else 8)<=b);scale=int.from_bytes(read(a+offset,4),'big');duration=int.from_bytes(read(a+offset+4,8 if v else 4),'big');require(scale>0 and duration>0,'video_rate_unverifiable')
        na,nb=one(da,db,b'minf');sa,sb=one(na,nb,b'stbl');a,b=one(sa,sb,b'stts')
        require(b-a>=8 and read(a,4)==b'\0'*4);count=int.from_bytes(read(a+4,4),'big');require(b-a==8+count*8)
        samples=ticks=0
        for at in range(a+8,b,8):
            n,d=struct.unpack('>II',read(at,8));require(n>0 and d>0,'video_rate_unverifiable');samples+=n;ticks+=n*d
        require(samples>0 and ticks==duration,'video_rate_unverifiable')
        za,zb=one(sa,sb,b'stsz');require(zb-za>=12 and read(za,4)==b'\0'*4)
        unit,total=struct.unpack('>II',read(za+4,8));require(total==samples and zb-za==(12 if unit else 12+4*total),'video_rate_unverifiable')
        a,b=one(sa,sb,b'stsd');require(b-a>=8 and read(a,4)==b'\0'*4 and int.from_bytes(read(a+4,4),'big')==1,'video_dimensions_unverifiable')
        entries=list(boxes(a+8,b));require(len(entries)==1)
        codec,a,b=entries[0];require(codec in (b'avc1',b'avc3',b'hvc1',b'hev1',b'vp09',b'av01',b'mp4v') and b-a>=78,'video_dimensions_unverifiable')
        width,height=struct.unpack('>HH',read(a+24,4));require(width>0 and height>0,'video_dimensions_unverifiable')
        return width,height,Fraction(samples*scale,ticks)
    raise FormatError('video_rate_unverifiable')


def threads_video_info(fd,size):
    """Observable BMFF headers only; no bitstream codec certification.

    Call after the privacy/structure walk. Movie time is already in Inspection;
    unknown bitstream GOP/chroma/bitrate is not invented from container labels.
    """
    def read(a,n):
        require(0<=a<=size-n);v=os.pread(fd,n,a);require(len(v)==n);return v
    def boxes(a,b):
        while a<b:
            require(a+8<=b);h=read(a,8);n=int.from_bytes(h[:4],'big');prefix=8
            if n==1:n=int.from_bytes(read(a+8,8),'big');prefix=16
            if n==0:n=b-a
            require(n>=prefix and a+n<=b);yield h[4:],a+prefix,a+n;a+=n
    top=list(boxes(0,size));kinds=[k for k,_,_ in top]
    facts={'moov_at_front':kinds.index(b'moov')<kinds.index(b'mdat'),'edit_lists':False,'video_codecs':[],'audio':[],'progressive':[],'bitrates':[]}
    def walk(a,b):
        for k,x,y in boxes(a,b):
            if k==b'elst':facts['edit_lists']=True
            elif k in (b'moov',b'trak',b'mdia',b'minf',b'stbl',b'edts'):walk(x,y)
            elif k==b'stsd':
                require(y-x>=8)
                for codec,ea,eb in boxes(x+8,y):
                    video=codec in VIDEO_SAMPLE_CODECS
                    if video:
                        facts['video_codecs'].append(codec.decode('ascii'));prefix=78
                    else:
                        require(eb-ea>=28);version=int.from_bytes(read(ea+8,2),'big');prefix=28+(16 if version else 0)
                        channels=int.from_bytes(read(ea+16,2),'big');rate=int.from_bytes(read(ea+24,4),'big')/65536
                        facts['audio'].append({'codec':codec.decode('ascii'),'channels':channels or None,'sample_rate':rate or None,'bitrate_declared':None})
                    for ext,xa,xb in boxes(ea+prefix,eb):
                        if video and ext==b'fiel':
                            require(xb-xa==2);fields=read(xa,2)[0]
                            facts['progressive'].append(True if fields==1 else False if fields==2 else None)
                        elif ext==b'btrt':
                            require(xb-xa==12)
                            declared={'max':int.from_bytes(read(xa+4,4),'big'),'avg':int.from_bytes(read(xa+8,4),'big')}
                            if video:facts['bitrates'].append(declared)
                            else:facts['audio'][-1]['bitrate_declared']=declared
    walk(0,size)
    return facts
