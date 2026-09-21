"""第10段: 記録と観測（設計 2.13.0 §5・C6）。

`sent`／`insights` の行が添付を連れて歩き、`analytics-report --by attachment_kind`
がその行を**種類ごとに**数える。元ファイル・alt の文字・source sha は残さない。
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import pytest

from thth import accounts as accounts_mod
from thth import analytics_report, collect as collect_mod, jst, media, sent as sent_mod
from tests.test_after_cli import _insight_path, _write_ndjson
from tests.test_collect_sent import FakeAdapter, 同席専用の台帳, 置き場, _rows

NOW = jst.parse("2026-09-18T12:00:00+09:00")
RECEIPT = ("media=[{'sha256':", "'alt_present':base.alt_present(")


# ------------------------------------------------------------------ 語彙と形

def test_the_record_vocabulary_matches_the_manifest_vocabulary():
    """台帳の語彙が実装より遅れると、層が黙って欠ける。"""
    assert set(sent_mod.ATTACHMENT_KINDS) == (
        set(media.TYPED_KINDS) | {'image', 'video', 'audio', media.CAROUSEL, media.ATTACHMENT_NONE})
    assert sent_mod.ATTACHMENT_NONE_LABEL == media.ATTACHMENT_NONE


@pytest.mark.parametrize('manifest,expected', [
    (None, ['none']),
    ({'files': [], 'attachments': [], 'post_options': {}}, ['none']),
    ({'files': [], 'attachments': [], 'post_options': {'ghost': True}}, ['none']),
    ({'files': [{'role': 'media', 'kind': 'image'}], 'attachments': []}, ['image']),
    ({'files': [{'role': 'media', 'kind': 'image'}, {'role': 'media', 'kind': 'image'}],
      'attachments': []}, ['carousel', 'image']),
    ({'files': [{'role': 'media', 'kind': 'video'}, {'role': 'thumbnail', 'kind': 'image'}],
      'attachments': []}, ['video']),
    ({'files': [{'role': 'media', 'kind': 'audio'}],
      'attachments': [{'type': 'quote'}, {'type': 'link'}]}, ['audio', 'link', 'quote']),
    ({'files': [], 'attachments': [{'type': 'poll'}]}, ['poll']),
    ({'files': [], 'attachments': [{'type': 'gif'}, {'type': 'text'}]}, ['gif', 'text']),
])
def test_attachment_kinds_counts_files_typed_kinds_and_the_carousel(manifest, expected):
    assert media.attachment_kinds(manifest) == expected
    assert all(kind in sent_mod.ATTACHMENT_KINDS for kind in media.attachment_kinds(manifest))


def test_every_delivery_path_derives_alt_present_and_never_stores_the_alt(tmp_path):
    """4 つの送り口すべてが同じ形の受領を作り、`alt_present` は決め打ちでない。"""
    root = Path(media.__file__).resolve().parent / 'adapters'
    for name in ('mastodon_media.py', 'bluesky_media.py', 'bluesky_video.py', 'threads_media.py'):
        text = (root / name).read_text(encoding='utf-8')
        assert all(mark in text for mark in RECEIPT), name
        assert "'alt_present':True" not in text, name
    from thth.adapters import base
    assert base.alt_present({'alt': '湯呑み'}) is True
    assert base.alt_present({'alt': '  '}) is False and base.alt_present({}) is False


def test_the_sent_row_keeps_the_kinds_and_refuses_an_unknown_one(tmp_path):
    state = str(tmp_path)
    path = sent_mod.write(state, post_id='P1', text='本文', body_hash='h', sent_at=jst.iso(NOW),
                          media=[{'sha256': 'a' * 64, 'kind': 'image', 'alt_present': True,
                                  'remote_id': '1'}],
                          attachment_kinds=['poll', 'image'])
    row = json.loads(Path(path).read_text(encoding='utf-8'))
    assert row['attachment_kinds'] == ['image', 'poll']
    assert set(row['media'][0]) == {'sha256', 'kind', 'alt_present', 'remote_id'}
    assert '湯呑み' not in json.dumps(row, ensure_ascii=False)
    for bad in ([], ['image', 'image'], ['sticker'], ['none', 'image'], 'image'):
        with pytest.raises(ValueError) as caught:
            sent_mod.write(state, post_id='P2', text='本文', body_hash='h',
                           sent_at=jst.iso(NOW), attachment_kinds=bad)
        assert str(caught.value) == 'invalid_attachment_kinds'


# ------------------------------------------------------------------ 観測の行

def test_collect_copies_the_kinds_onto_the_insight_row_and_says_unknown_otherwise(
        tmp_path, isolated_account_factory, monkeypatch):
    """記録のある投稿だけが層を持つ。**2.13.0 より前の行に `none` と書かない。**"""
    account = 同席専用の台帳(tmp_path, isolated_account_factory)
    state = accounts_mod.state_dir_for(account['name'])
    sent_mod.write(state, post_id='WITH', text='添付つき', body_hash='h',
                   sent_at='2026-09-14T10:00:00+09:00',
                   media=[{'sha256': 'b' * 64, 'kind': 'image', 'alt_present': True,
                           'remote_id': 'r1'}],
                   attachment_kinds=['image', 'poll'])
    sent_mod.write(state, post_id='PLAIN', text='本文だけ', body_hash='h',
                   sent_at='2026-09-14T09:00:00+09:00', attachment_kinds=['none'])
    sent_mod.write(state, post_id='OLD', text='昔の記録', body_hash='h',
                   sent_at='2026-09-14T08:00:00+09:00')
    now = datetime.datetime(2026, 9, 14, 12, 0, tzinfo=jst.JST)
    collect_mod.run_collect(account['name'], adapter=FakeAdapter(), now=now, log=lambda _l: None)
    folder = 置き場(account['name'])['insights_posts']
    rows = {name: _rows(os.path.join(folder, name + '.ndjson'))[0]
            for name in ('WITH', 'PLAIN', 'OLD')}
    assert rows['WITH']['attachment_kinds'] == ['image', 'poll']
    assert rows['WITH']['media'] == [{'sha256': 'b' * 64, 'kind': 'image',
                                      'alt_present': True, 'remote_id': 'r1'}]
    assert rows['PLAIN']['attachment_kinds'] == ['none'] and rows['PLAIN']['media'] == []
    assert 'attachment_kinds' not in rows['OLD'] and 'media' not in rows['OLD']


# ------------------------------------------------------------------ 層別

def seed(account, post_id, posted, *, value, kinds=None, age=25):
    row = {'account': account['name'], 'post_id': post_id, 'posted_at': jst.iso(posted),
           'collected_at': jst.iso(posted + datetime.timedelta(hours=age)),
           'age_hours': 999, 'marks': [24], 'reply_to': None, 'topic': None,
           'metrics': {'views': value, 'likes': 0, 'replies': 0}}
    if kinds is not None:
        row['attachment_kinds'] = kinds
    _write_ndjson(_insight_path(account, post_id), [row])


@pytest.fixture
def strata(isolated_account_factory):
    account = isolated_account_factory(media='mastodon')
    seed(account, 'both', NOW - datetime.timedelta(days=3), value=10, kinds=['image', 'poll'])
    seed(account, 'image-only', NOW - datetime.timedelta(days=2), value=20, kinds=['image'])
    seed(account, 'plain', NOW - datetime.timedelta(days=1, hours=3), value=30, kinds=['none'])
    seed(account, 'legacy', NOW - datetime.timedelta(days=1, hours=6), value=40)
    seed(account, 'older', NOW - datetime.timedelta(days=10), value=5, kinds=['image'])
    payload = analytics_report.answer(account['name'], now=NOW, compare_previous=True,
                                      min_n=1, by='attachment_kind')
    return payload['by_account'][account['name']]['posts']['stratified']


def test_one_row_counts_in_every_kind_it_carries(strata):
    """`image`＋`poll` の 1 本は**両方の層に出る**（C6・重なる層）。"""
    assert strata['by'] == 'attachment_kind' and strata['attachment_kind_groups_overlap'] is True
    assert strata['strata']['image']['current']['n_total'] == 2
    assert strata['strata']['poll']['current']['n_total'] == 1
    assert strata['strata']['image']['current']['metrics']['views']['median'] == 15
    assert strata['strata']['poll']['current']['metrics']['views']['median'] == 10
    assert strata['strata']['image']['previous']['n_total'] == 1


def test_none_and_unknown_are_separate_groups_and_the_denominators_reconcile(strata):
    assert strata['strata']['none']['current']['n_total'] == 1
    assert strata['strata']['unknown']['current']['n_total'] == 1
    assert strata['strata']['attached']['current']['n_total'] == 2
    # attached / none / unknown はちょうど母集団を分割する。
    assert strata['reconciliation']['current'] == {'sum_n_total': 4, 'n_total': 4}
    assert strata['reconciliation']['previous'] == {'sum_n_total': 1, 'n_total': 1}
    assert 'video' not in strata['strata'] and 'carousel' not in strata['strata']


def test_the_topic_shelf_layer_is_untouched(isolated_account_factory):
    account = isolated_account_factory(media='mastodon')
    seed(account, 'one', NOW - datetime.timedelta(days=2), value=10, kinds=['image'])
    payload = analytics_report.answer(account['name'], now=NOW, compare_previous=True,
                                      min_n=1, by='kind')
    group = payload['by_account'][account['name']]['posts']['stratified']
    assert group['by'] == 'kind' and group['kind_basis'] == 'current_topic_shelf'
    assert 'attachment_kind_groups_overlap' not in group


def test_the_layer_is_offered_by_the_cli_and_the_mcp_tool():
    from thth import cli
    assert 'attachment_kind' in analytics_report.BY_CHOICES
    parser = cli.build_parser()
    action = next(a for a in parser._subparsers._group_actions[0].choices['analytics-report']._actions
                  if a.dest == 'by')
    assert 'attachment_kind' in action.choices
    from tests.test_after_cli import _load_server_module
    server = _load_server_module()
    tool = next(t for t in server.TOOLS if t['name'] == 'analytics_report')
    assert 'attachment_kind' in tool['inputSchema']['properties']['by']['enum']


def test_an_unknown_layer_is_still_refused():
    from thth import after_cli
    with pytest.raises(after_cli.AfterError):
        analytics_report.answer('x', compare_previous=True, by='attachment')
