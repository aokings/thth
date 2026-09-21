"""Every official Unicode 17.0.0 default extended grapheme test vector."""
from pathlib import Path
import hashlib
import pytest
from thth import graphemes

SOURCE=Path(__file__).parent/'fixtures/GraphemeBreakTest-17.0.0.txt'

def vectors():
    for number,line in enumerate(SOURCE.read_text().splitlines(),1):
        data=line.split('#',1)[0].strip()
        if not data:continue
        chars=[];offsets=[]
        for token in data.split():
            if token=='÷':offsets.append(len(chars))
            elif token!='×':chars.append(chr(int(token,16)))
        yield pytest.param(''.join(chars),offsets,id=str(number))


@pytest.mark.parametrize('text,expected',list(vectors()))
def test_official_grapheme_break_vectors(text,expected):
    assert list(graphemes.boundaries(text))==expected
    assert graphemes.count(text)==len(expected)-1


def test_empty_and_fixed_version():
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()=='e2d134d2c52919bace503ebb6a551c1855fe1a1faec18478c78fff254a1793ec'
    assert graphemes.UNICODE_VERSION=='17.0.0'
    assert list(graphemes.boundaries(''))==[0] and graphemes.count('')==0
    with pytest.raises(TypeError):graphemes.count(None)


def test_long_cluster_is_linear_and_count_capped():
    # Repeated Extend must use bounded state, not backwards scans or substring
    # copies; the output has only two boundaries for a million-codepoint input.
    value='a'+'\u0301'*1_000_000
    assert list(graphemes.boundaries(value))==[0,len(value)]
    assert graphemes.count('a'*1_000_000,stop_after=50)==51


def test_indic_emoji_hangul_and_flags():
    for text in ['क्ष','👩🏽\u200d💻','각','🇯🇵','\r\n']:
        assert graphemes.count(text)==1
    assert graphemes.count('a\u200db')==2
    assert graphemes.count('🇯🇵🇦')==2
