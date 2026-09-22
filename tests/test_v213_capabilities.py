"""第10段: 媒体ごとの「出せるもの」表と、理由コードとの一致（設計 2.13.0 §0.1）。

表が正本であることを、**実装の source から拾った理由コード**と突き合わせて守る。
表から key を消せば、その key を投げている実装がここで落ちる（変異 M1）。
"""
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import thth
from thth import media, media_capabilities as caps, mediaformats, tool_version

SOURCE = {path: path.read_text(encoding='utf-8')
          for path in sorted(Path(thth.__file__).resolve().parent.rglob('*.py'))}
LITERAL = re.compile(r"unsupported_attachment: (mastodon|bluesky|threads|x)/([a-z0-9_]*)")
BARE = re.compile(r"unsupported_attachment: ([a-z0-9_][a-z0-9_ ]*)['\"]")
INSPECTION = re.compile(r"Inspection\(\s*'([a-z0-9_]+)'")
REQUIRED = ('image', 'video', 'audio', 'carousel', 'poll', 'quote', 'link', 'gif', 'text',
            'thumbnail', 'captions', 'tags', 'labels', 'languages', 'spoiler', 'reply_control',
            'ghost', 'visibility_private', 'aac_adts', 'asf', 'webm', 'offset_styling',
            'carousel_quote')
LEAK = re.compile(r'/Users|/private|/Volumes|/srv')


def raised():
    """`thth/` が投げうる `unsupported_attachment: <媒体>/<key>` の key を媒体ごとに。

    4 つの出どころがある。書き下した `<媒体>/<key>`、`'<媒体>/'+row['format']` の
    形式（`mediaformats.FORMATS`）、`mediaformats` の裸の理由に `media.py` が媒体名を
    前置するもの、`validate_declarations()` がその媒体に通さない型付きの種類。
    """
    literal, dynamic = collections.defaultdict(set), set()
    for text in SOURCE.values():
        for found in LITERAL.finditer(text):
            if found[2]:
                literal[found[1]].add(found[2])
            else:
                dynamic.add(found[1])
    bare = {found[1] for text in SOURCE.values() for found in BARE.finditer(text)}
    # 検出そのものが空振りしていないこと（正規表現が腐ると試験が黙って通る）。
    assert bare >= {'asf', 'aac_adts'} and dynamic == set(caps.MEDIA)
    assert all(literal[name] for name in caps.MEDIA)
    out = {}
    for name in caps.MEDIA:
        keys = set(literal[name]) | bare
        keys |= set(media.TYPED_KINDS) - media.TYPED_ALLOWED[name]
        if name in dynamic:
            keys |= set(mediaformats.FORMATS)
        out[name] = keys
    return out


def test_inspected_format_names_are_all_declared_in_one_place():
    found = {m[1] for text in SOURCE.values() for m in INSPECTION.finditer(text)}
    assert found and found <= set(mediaformats.FORMATS)


@pytest.mark.parametrize('medium', caps.MEDIA)
def test_every_reason_key_the_source_can_raise_is_a_table_key(medium):
    missing = sorted(raised()[medium] - caps.keys(medium))
    assert missing == [], 'unsupported_attachment の理由 key が表にない: ' + medium


@pytest.mark.parametrize('medium', caps.MEDIA)
def test_every_row_uses_the_vocabulary_with_a_static_reason(medium):
    for key, status in caps.CAPABILITIES[medium].items():
        assert caps.valid(status), (medium, key, status)
        head, _, reason = status.partition(': ')
        if head in caps.PREFIXES:
            assert reason and re.fullmatch(r'[a-z0-9_]+', reason), (medium, key, status)
    for key, reason in caps.REFUSALS[medium].items():
        assert reason.strip() and not LEAK.search(reason), (medium, key)


@pytest.mark.parametrize('medium', caps.MEDIA)
def test_the_table_covers_every_ruled_attachment_type_and_option(medium):
    assert set(REQUIRED) <= set(caps.CAPABILITIES[medium])
    assert set(caps.NOTES.get(medium, {})) <= set(caps.CAPABILITIES[medium])


def test_the_rulings_are_written_down_as_given():
    assert caps.CAPABILITIES['threads']['gif'] == 'supported'
    assert 'giphy' in caps.NOTES['threads']['gif']
    assert caps.CAPABILITIES['threads']['offset_styling'] == 'ascii_only: threads_offset_unit_unverified'
    assert caps.CAPABILITIES['threads']['carousel_quote'] == 'unverified: provider_not_measured'
    assert caps.CAPABILITIES['mastodon']['visibility_private'] == 'unsupported: ledger_public_only'
    assert caps.CAPABILITIES['mastodon']['link'] == 'body_only'
    assert caps.CAPABILITIES['bluesky']['webm'] == 'unsupported: provider_format'
    # 2.14: 引用は「API に無い」のではなく tier で使えない（設計 §0・§7）。
    assert caps.CAPABILITIES['x']['quote'] == 'unsupported: provider_tier_enterprise_only'
    assert caps.CAPABILITIES['x']['carousel_quote'] == 'unsupported: provider_tier_enterprise_only'
    assert caps.CAPABILITIES['x']['link'] == 'body_only'
    assert caps.CAPABILITIES['x']['reply_control'] == 'supported'
    for medium in caps.MEDIA:
        assert caps.CAPABILITIES[medium]['aac_adts'] == 'unsupported: privacy_inspection_boundary_unverified'
        assert caps.CAPABILITIES[medium]['asf'] == 'unsupported: deferred_by_ruling_c14'


def test_one_table_reaches_the_tool_summary_the_doctor_and_the_skill():
    summary = tool_version.summary()
    assert summary['capabilities'] == caps.summary()
    assert summary['capabilities']['media'] == caps.table()
    assert summary['capabilities']['read_before_attaching'] == '添付を付ける前に tool.capabilities を読む'
    assert summary['capabilities']['basis'] == 'static_table_not_a_provider_probe'
    skill = (Path(thth.__file__).resolve().parent.parent / 'skills' / 'thth' / 'SKILL.md').read_text(encoding='utf-8')
    assert 'tool.capabilities' in skill and '添付を付ける前' in skill


def test_doctor_text_and_json_carry_the_same_table(isolated_account_factory, monkeypatch):
    account = isolated_account_factory(media='mastodon')
    from thth import doctor
    monkeypatch.setattr(doctor, '_diagnose', lambda name: {'account': name, 'error': 'token', 'probes': []})
    report = doctor.diagnose(account['name'])
    assert report['media_capabilities'] == caps.summary()
    lines = caps.lines('mastodon')
    assert lines[0].startswith('添付の対応表（mastodon')
    body = '\n'.join(lines)
    for key, status in caps.CAPABILITIES['mastodon'].items():
        assert '  ' + key + ': ' + status in body
    assert not LEAK.search(body)


def test_run_doctor_prints_the_row_for_this_account_even_without_a_token(
        tmp_path, isolated_account_factory):
    """probe が 1 本も通らなくても表は出る（静的なので token も網も要らない）。"""
    from thth import doctor as doctor_mod
    account = isolated_account_factory('caps-bsky', media='bluesky',
                                       handle='aoking.bsky.social',
                                       token=str(tmp_path / 'missing.token'))
    lines = []
    doctor_mod.run_doctor(account['name'], log=lines.append)
    out = '\n'.join(lines)
    assert '添付の対応表（bluesky' in out
    assert '  webm: ' + caps.CAPABILITIES['bluesky']['webm'] in out
    assert not LEAK.search(out.replace(str(tmp_path), ''))


def test_no_absolute_path_reaches_the_tool_json(isolated_account_factory):
    account = isolated_account_factory()
    from thth import operations_handoff
    payload = operations_handoff.answer(account['name'])
    for node in (payload['tool'], *[row['tool'] for row in payload['by_account'].values()]):
        raw = json.dumps(node, ensure_ascii=False)
        assert not LEAK.search(raw), raw
    assert payload['tool']['capabilities'] == caps.summary()


def test_the_doc_table_cannot_drift_from_the_python_table():
    root = Path(thth.__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, 'tools/render_media_capabilities.py', '--check'],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    doc = (root / 'docs' / '原稿_添付_2.13.md').read_text(encoding='utf-8')
    for medium in caps.MEDIA:
        assert '| ' + medium + ' ' in doc
    assert caps.CAPABILITIES['threads']['offset_styling'] in doc
