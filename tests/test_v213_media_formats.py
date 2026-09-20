"""Generated format fixtures; no downloaded content or provider I/O."""
import hashlib
import os
import struct
import zlib
import pytest
from thth import mediaformats as f
from tests.test_v213_media_foundation import png, jpeg


def exif(orientation=6):
    # GPS pointer and private ASCII in the original; Orientation is the only survivor.
    return (b'II*\0\x08\0\0\0'+struct.pack('<H',3)+
            struct.pack('<HHII',0x112,3,1,orientation)+
            struct.pack('<HHII',0x10e,2,8,50)+
            struct.pack('<HHII',0x8825,4,1,58)+b'\0'*4+
            b'PRIVATE\0'+b'\0'*6)


def segment(marker,payload):
    return bytes((255,marker))+struct.pack('>H',len(payload)+2)+payload


def test_jpeg_sanitizes_preserves_orientation_and_rendering():
    original=jpeg(); headers=(segment(0xe0,b'JFIF\0\x01\x02\0\0\x01\0\x01\x01\x01abc')+
        segment(0xee,b'Adobe\0\x64\0\0\0\0\x01')+
        segment(0xe1,b'Exif\0\0'+exif())+
        segment(0xe1,b'http://ns.adobe.com/xap/1.0/\0PRIVATE-XMP')+
        segment(0xed,b'PRIVATE-IPTC')+segment(0xfe,b'PRIVATE-COMMENT'))
    result=f.jpeg(original[:2]+headers+original[2:])
    assert (result.width,result.height,result.orientation)==(1,1,6)
    assert b'PRIVATE' not in result.public_bytes and b'abc' not in result.public_bytes
    assert b'Adobe' in result.public_bytes and b'JFIF' in result.public_bytes
    assert f.jpeg(result.public_bytes).public_bytes == result.public_bytes
    assert result.public_bytes.endswith(original[2:])


def test_exif_orientation_only_little_and_big_endian():
    orientation, public=f.orientation_exif(exif())
    assert orientation==6 and len(public)==26 and f.orientation_exif(public)==(6,public)
    big=b'MM\0*\0\0\0\x08'+struct.pack('>HHHIHHI',1,0x112,3,1,8,0,0)
    assert f.orientation_exif(big)[0]==8


@pytest.mark.parametrize('bad', [b'',b'II*\0'+b'\xff'*4,exif(0),exif(9),exif()[:40],exif().replace(struct.pack('<I',58),struct.pack('<I',8),1)])
def test_bad_exif_loud_reject(bad):
    with pytest.raises(f.FormatError): f.orientation_exif(bad)


def test_png_metadata_removed_pixels_and_color_kept():
    source=png(); before=source[:33]; rest=source[33:]
    color=f._chunk(b'gAMA',struct.pack('>I',45455))
    private=f._chunk(b'tEXt',b'GPS\0PRIVATE')+f._chunk(b'eXIf',exif())
    result=f.png(before+color+private+rest)
    assert b'PRIVATE' not in result.public_bytes and color in result.public_bytes
    assert result.orientation==6 and result.width==result.height==1
    assert f.png(result.public_bytes).public_bytes==result.public_bytes
    assert rest==result.public_bytes[-len(rest):]


@pytest.mark.parametrize('change',[lambda b:b[:-1],lambda b:b+b'other',lambda b:b[:40]+bytes([b[40]^1])+b[41:],lambda b:b[:33]+f._chunk(b'zzZZ',b'private')+b[33:],lambda b:b[:33]+f._chunk(b'ABCD',b'private')+b[33:]])
def test_png_broken_or_unverifiable_reject(change):
    with pytest.raises(f.FormatError): f.png(change(png()))


def webp(extra=()):
    # Lossless bitstream header sufficient for container inspection, not decoder proof.
    frame=f._riff_chunk(b'VP8L',b'\x2f\0\0\0\0\x01')
    if not extra: body=b'WEBP'+frame
    else:
        flags=(8 if any(k==b'EXIF' for k,_ in extra) else 0)|(4 if any(k==b'XMP ' for k,_ in extra) else 0)
        body=b'WEBP'+f._riff_chunk(b'VP8X',bytes([flags])+b'\0'*9)+frame+b''.join(f._riff_chunk(k,p) for k,p in extra)
    return b'RIFF'+struct.pack('<I',len(body))+body


def test_webp_sanitizes_and_updates_feature_flags():
    result=f.webp(webp([(b'EXIF',exif()),(b'XMP ',b'PRIVATE')]))
    assert result.orientation==6 and (result.width,result.height)==(1,1)
    assert b'PRIVATE' not in result.public_bytes and b'XMP ' not in result.public_bytes
    assert f.webp(result.public_bytes).public_bytes==result.public_bytes


def test_webp_bad_bounds_unknown_chunk():
    for value in (webp()[:-1],webp([(b'ZZZZ',b'private')])):
        with pytest.raises(f.FormatError): f.webp(value)


def gif():
    return b'GIF89a\x01\0\x01\0\x80\0\0'+b'\0\0\0\xff\xff\xff'+b'\x21\xfe\x07PRIVATE\0'+b'\x2c\0\0\0\0\x01\0\x01\0\0\x02\x02\x44\x01\0\x3b'


def test_gif_structural_only_unchanged_metadata_contract():
    result=f.gif(gif()); assert result.public_bytes is None and result.width==result.height==1
    for bad in (gif()[:-1],gif()+b'other',gif().replace(b'\x07PRIVATE',b'\xffPRIVATE')):
        with pytest.raises(f.FormatError):f.gif(bad)


def box(kind,payload):return struct.pack('>I',len(payload)+8)+kind+payload


def mp4(extra=b'',handler=b'vide'):
    mvhd=b'\0'*12+struct.pack('>II',1000,2500)+b'\0'*80
    tkhd=b'\0'*76+struct.pack('>II',640<<16,480<<16)
    hdlr=b'\0'*8+handler+b'\0'*12
    return box(b'ftyp',b'isom\0\0\0\0isom')+box(b'moov',box(b'mvhd',mvhd)+box(b'trak',box(b'tkhd',tkhd)+box(b'mdia',box(b'hdlr',hdlr)))+extra)+box(b'mdat',b'synthetic-sample')


def inspect(tmp_path,data):
    p=tmp_path/'generated';p.write_bytes(data);fd=os.open(p,os.O_RDONLY)
    try:return f.inspect(fd,len(data))
    finally:os.close(fd)


def test_video_duration_dimensions_bytes_unchanged(tmp_path):
    result=inspect(tmp_path,mp4())
    assert (result.width,result.height,result.duration)==(640,480,2.5)
    assert result.public_bytes is None and result.format=='mp4'


@pytest.mark.parametrize('extra',[box(b'udta',box(b'\xa9xyz',b'+00.0+00.0/')),box(b'meta',b'\0'*4+box(b'keys',b'\0'*4+struct.pack('>I',1)+box(b'mdta',b'com.apple.quicktime.location.ISO6709')))])
def test_video_location_rejected(tmp_path,extra):
    with pytest.raises(f.FormatError,match='location_metadata_present'): inspect(tmp_path,mp4(extra))


def test_video_unknown_metadata_track_rejected(tmp_path):
    with pytest.raises(f.FormatError,match='location_metadata_unverifiable'):inspect(tmp_path,mp4(handler=b'meta'))


@pytest.mark.parametrize('value',[mp4()[:-1],mp4().replace(b'\x00\x00\x00\x14ftyp',b'\xff\xff\xff\xffftyp'),b'unknown'])
def test_broken_video_or_unknown_rejected(tmp_path,value):
    with pytest.raises(f.FormatError):inspect(tmp_path,value)


def test_png_animation_and_transparency_preserved():
    header=f._chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))
    control=f._chunk(b'acTL',struct.pack('>II',2,0))
    first=f._chunk(b'fcTL',struct.pack('>IIIIIHHBB',0,1,1,0,0,1,10,0,0))
    second=f._chunk(b'fcTL',struct.pack('>IIIIIHHBB',1,1,1,0,0,2,10,0,0))
    image=zlib.compress(b'\0\xff\0\0')
    transparent=f._chunk(b'tRNS',b'\0\xff\0\0\0\0')
    data=b'\x89PNG\r\n\x1a\n'+header+transparent+control+first+f._chunk(b'IDAT',image)+second+f._chunk(b'fdAT',struct.pack('>I',2)+image)+f._chunk(b'IEND',b'')
    result=f.png(data)
    assert result.public_bytes==data and result.duration==pytest.approx(.3)
    bad=data.replace(second,f._chunk(b'fcTL',struct.pack('>IIIIIHHBB',4,1,1,0,0,2,10,0,0)))
    with pytest.raises(f.FormatError):f.png(bad)


def test_webp_animation_order_and_flags():
    image=f._riff_chunk(b'VP8L',b'\x2f\0\0\0\0\x01')
    frame=b'\0'*12+(100).to_bytes(3,'little')+b'\0'+image
    chunks=[(b'VP8X',b'\x02'+b'\0'*9),(b'ANIM',b'\0'*6),(b'ANMF',frame)]
    body=b'WEBP'+b''.join(f._riff_chunk(k,p) for k,p in chunks)
    data=b'RIFF'+struct.pack('<I',len(body))+body
    result=f.webp(data);assert result.public_bytes==data and result.duration==.1
    swapped=b'WEBP'+b''.join(f._riff_chunk(k,p) for k,p in [chunks[0],chunks[2],chunks[1]])
    with pytest.raises(f.FormatError):f.webp(b'RIFF'+struct.pack('<I',len(swapped))+swapped)


def test_icc_preserved_in_three_formats():
    profile=b'generated-ICC-profile'
    j=jpeg()[:2]+segment(0xe2,b'ICC_PROFILE\0\x01\x01'+profile)+jpeg()[2:]
    assert profile in f.jpeg(j).public_bytes
    p=png()[:33]+f._chunk(b'iCCP',b'profile\0\0'+zlib.compress(profile))+png()[33:]
    assert f._chunk(b'iCCP',b'profile\0\0'+zlib.compress(profile)) in f.png(p).public_bytes
    body=b'WEBP'+f._riff_chunk(b'VP8X',b'\x20'+b'\0'*9)+f._riff_chunk(b'ICCP',profile)+f._riff_chunk(b'VP8L',b'\x2f\0\0\0\0\x01')
    w=b'RIFF'+struct.pack('<I',len(body))+body
    assert profile in f.webp(w).public_bytes


def test_video_metadata_in_track_and_false_positive_sample(tmp_path):
    private=box(b'trak',box(b'mdia',box(b'meta',b'\0'*4+box(b'keys',b'\0'*4+struct.pack('>I',1)+box(b'mdta',b'com.apple.quicktime.location.ISO6709')))))
    with pytest.raises(f.FormatError,match='location_metadata_present'):inspect(tmp_path,mp4(private))
    # Arbitrary video sample bytes are not keys/boxes.
    assert inspect(tmp_path,mp4().replace(b'synthetic-sample',b'\xa9xyz-location!!!')).duration==2.5


def test_video_bad_timescale_and_nested_zero_size(tmp_path):
    bad=mp4().replace(struct.pack('>II',1000,2500),struct.pack('>II',0,2500))
    with pytest.raises(f.FormatError):inspect(tmp_path,bad)
    with pytest.raises(f.FormatError):inspect(tmp_path,mp4(box(b'udta',b'\0'*4+b'free')))


@pytest.mark.parametrize('kind,payload',[
    (b'tEXt',b'Comment\0PRIVATE'),
    (b'zTXt',b'Comment\0\0'+zlib.compress(b'PRIVATE')),
    (b'iTXt',b'Comment\0\0\0en\0Comment\0PRIVATE'),
])
def test_png_repeated_text_chunks_removed_without_render_changes(kind,payload):
    original=png()
    color=f._chunk(b'gAMA',struct.pack('>I',45455))
    icc=f._chunk(b'iCCP',b'ICC\0\0'+zlib.compress(b'synthetic-profile-bytes'))
    baseline=original[:33]+color+icc+original[33:]
    texts=f._chunk(kind,payload)*2
    source=original[:33]+color+icc+texts+original[33:]
    result=f.png(source)
    assert result.public_bytes==baseline
    assert b'PRIVATE' not in result.public_bytes
    assert result.width==result.height==1 and result.orientation is None
    assert f.png(result.public_bytes).public_bytes==baseline
    broken=bytearray(source);broken[33+len(color)+len(icc)+len(texts)-1]^=1
    with pytest.raises(f.FormatError):f.png(bytes(broken))


@pytest.mark.parametrize('kind',[b'IHDR',b'PLTE',b'IEND'])
def test_png_nonrepeatable_critical_chunks_still_rejected(kind):
    original=png()
    if kind==b'IHDR':bad=original[:33]+original[8:33]+original[33:]
    elif kind==b'PLTE':bad=original[:33]+f._chunk(b'PLTE',b'\0\0\0')*2+original[33:]
    else:bad=original+f._chunk(b'IEND',b'')
    with pytest.raises(f.FormatError):f.png(bad)
